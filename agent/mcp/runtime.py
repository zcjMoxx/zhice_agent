"""Workspace-shared MCP connections behind a synchronous Tool facade."""

from __future__ import annotations

import asyncio
import logging
import os
import threading
import time
import uuid
from collections import deque
from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path
from typing import Any, AsyncIterator

import httpx
from mcp import ClientSession, StdioServerParameters, types
from mcp.client.sse import sse_client
from mcp.client.stdio import stdio_client
from mcp.client.streamable_http import streamable_http_client

from agent.logging_utils import log_event
from agent.mcp.artifacts import McpArtifactGateway
from agent.mcp.auth import McpCredentialManager
from agent.mcp.catalog import build_tool_descriptors
from agent.mcp.result import normalize_mcp_result
from agent.protocols.activity import RuntimeActivityEvent, RuntimeActivitySink
from agent.protocols.auth import ActorContext, AuditEvent, AuditSink
from agent.protocols.mcp import (
    McpCatalogSnapshot,
    McpConnectionEvent,
    McpInteractionNotifier,
    McpInteractionRequest,
    McpInteractionResponse,
    McpRuntimeStatsSnapshot,
    McpServerSpec,
    McpServerStatus,
    McpToolDescriptor,
    McpToolStats,
)
from agent.protocols.tool import Tool, ToolResult

mcp_logger = logging.getLogger("zcagent.agent.mcp")
MAX_TOOLS_TOTAL = 128
_RECONNECT = object()


@dataclass
class _CallContext:
    server_id: str
    actor: ActorContext
    files_dir: Path
    interaction_notifier: McpInteractionNotifier | None
    session_id: str = ""


@dataclass
class _PendingInteraction:
    response: McpInteractionResponse | None = None
    event: threading.Event = field(default_factory=threading.Event)


@dataclass
class _CallRequest:
    descriptor: McpToolDescriptor
    args: dict[str, Any]
    context: _CallContext
    future: asyncio.Future[ToolResult]
    call_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    started_at: float = field(default_factory=time.perf_counter)


@dataclass
class _MutableToolStats:
    call_count: int = 0
    success_count: int = 0
    error_count: int = 0
    cancelled_count: int = 0
    total_duration_ms: int = 0
    max_duration_ms: int = 0
    last_error_code: str = ""


@dataclass
class _ServerConnection:
    spec: McpServerSpec
    queue: asyncio.Queue[_CallRequest | None]
    initial_ready: asyncio.Event
    task: asyncio.Task[None] | None = None
    status: McpServerStatus | None = None
    descriptors: tuple[McpToolDescriptor, ...] = ()
    active_context: _CallContext | None = None
    active_request: _CallRequest | None = None
    active_task: asyncio.Task[Any] | None = None
    session: ClientSession | None = None
    refresh_task: asyncio.Task[bool] | None = None
    refresh_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    reconnect_requested: asyncio.Event = field(default_factory=asyncio.Event)


class McpRuntime:
    """Own shared MCP transports on one dedicated asyncio thread."""

    def __init__(
        self,
        specs: tuple[McpServerSpec, ...] | list[McpServerSpec],
        *,
        workspace: Path | str,
        activity_sink: RuntimeActivitySink | None = None,
        audit_sink: AuditSink | None = None,
    ) -> None:
        self.specs = tuple(specs)
        self.workspace = Path(workspace).resolve()
        self.runtime_root = self.workspace / "state" / "mcp_runtime"
        self.runtime_root.mkdir(parents=True, exist_ok=True)
        self.activity_sink = activity_sink
        self.audit_sink = audit_sink
        self._artifact_gateway = McpArtifactGateway()
        self._credentials = McpCredentialManager()
        self._snapshot = McpCatalogSnapshot()
        self._snapshot_lock = threading.Lock()
        self._stats_lock = threading.Lock()
        self._catalog_version = 0
        self._catalog_refresh_count = 0
        self._list_changed_count = 0
        self._reconnect_count = 0
        self._connection_history: deque[McpConnectionEvent] = deque(maxlen=100)
        self._tool_stats: dict[tuple[str, str], _MutableToolStats] = {}
        self._pending: dict[str, _PendingInteraction] = {}
        self._pending_lock = threading.Lock()
        self._closed = False
        self._loop = asyncio.new_event_loop()
        self._connections: dict[str, _ServerConnection] = {}
        self._thread = threading.Thread(
            target=self._run_loop,
            name="zcagent-mcp-runtime",
            daemon=True,
        )
        if not self.specs:
            return
        self._audit_configured_credentials()
        self._thread.start()
        startup_timeout = max((spec.startup_timeout_seconds for spec in self.specs), default=15)
        future = asyncio.run_coroutine_threadsafe(self._start(), self._loop)
        try:
            future.result(timeout=startup_timeout + 5)
        except TimeoutError:
            log_event(mcp_logger, logging.ERROR, "mcp.runtime_start_timeout")
        log_event(mcp_logger, logging.INFO, "mcp.runtime_started", server_count=len(self.specs))

    def snapshot(self) -> McpCatalogSnapshot:
        """Return the latest immutable Catalog snapshot."""

        with self._snapshot_lock:
            return self._snapshot

    def refresh_catalog(self, server_id: str) -> bool:
        """Discover and atomically replace one Server's valid Catalog."""

        if self._closed or not self._thread.is_alive():
            return False
        future = asyncio.run_coroutine_threadsafe(
            self._refresh_server_catalog(server_id, reason="manual"),
            self._loop,
        )
        try:
            return bool(future.result(timeout=self._spec_timeout(server_id) + 5))
        except (TimeoutError, KeyError):
            future.cancel()
            return False

    def tools_list_changed(self, server_id: str) -> bool:
        """Handle a ``tools/list_changed`` equivalent signal through the same refresh path."""

        if self._closed or not self._thread.is_alive():
            return False
        future = asyncio.run_coroutine_threadsafe(
            self._refresh_server_catalog(server_id, reason="list_changed"),
            self._loop,
        )
        try:
            return bool(future.result(timeout=self._spec_timeout(server_id) + 5))
        except (TimeoutError, KeyError):
            future.cancel()
            return False

    def handle_tools_list_changed(self, server_id: str) -> bool:
        """Compatibility-friendly explicit entrypoint for an equivalent change signal."""

        return self.tools_list_changed(server_id)

    def reload(self, specs: tuple[McpServerSpec, ...] | list[McpServerSpec]) -> bool:
        """Apply validated Server specs while isolating changed connections."""

        normalized = tuple(specs)
        ids = [spec.server_id for spec in normalized]
        if self._closed or len(ids) != len(set(ids)):
            return False
        if not self._thread.is_alive():
            if not normalized:
                self.specs = ()
                return True
            self.specs = normalized
            self._audit_configured_credentials()
            self._thread.start()
            future = asyncio.run_coroutine_threadsafe(self._start(), self._loop)
            timeout = max(spec.startup_timeout_seconds for spec in normalized) + 5
            try:
                future.result(timeout=timeout)
            except TimeoutError:
                future.cancel()
                return False
            return True
        future = asyncio.run_coroutine_threadsafe(self._reload_specs(normalized), self._loop)
        timeout = max((spec.startup_timeout_seconds for spec in normalized), default=5) + 10
        try:
            return bool(future.result(timeout=timeout))
        except TimeoutError:
            future.cancel()
            return False

    def reconnect(self, server_id: str) -> bool:
        """Request one isolated Server reconnect without restarting the Gateway."""

        if self._closed or not self._thread.is_alive():
            return False
        future = asyncio.run_coroutine_threadsafe(self._request_reconnect(server_id), self._loop)
        try:
            return bool(future.result(timeout=5))
        except (TimeoutError, KeyError):
            future.cancel()
            return False

    def cancel_active_calls(
        self,
        server_id: str | None = None,
        *,
        user_id: str | None = None,
        session_id: str | None = None,
    ) -> int:
        """Cancel active remote requests and propagate a stable Tool error."""

        if self._closed or not self._thread.is_alive():
            return 0
        future = asyncio.run_coroutine_threadsafe(
            self._cancel_matching_calls(
                server_id=server_id,
                user_id=user_id,
                session_id=session_id,
            ),
            self._loop,
        )
        try:
            return int(future.result(timeout=5))
        except TimeoutError:
            future.cancel()
            return 0

    def stats_snapshot(self) -> McpRuntimeStatsSnapshot:
        """Return bounded, credential-free health and latency diagnostics."""

        with self._stats_lock:
            tool_stats = tuple(
                McpToolStats(
                    server_id=server_id,
                    tool_name=tool_name,
                    call_count=stats.call_count,
                    success_count=stats.success_count,
                    error_count=stats.error_count,
                    cancelled_count=stats.cancelled_count,
                    total_duration_ms=stats.total_duration_ms,
                    max_duration_ms=stats.max_duration_ms,
                    last_error_code=stats.last_error_code,
                )
                for (server_id, tool_name), stats in sorted(self._tool_stats.items())
            )
            history = tuple(self._connection_history)
            catalog_refresh_count = self._catalog_refresh_count
            list_changed_count = self._list_changed_count
            reconnect_count = self._reconnect_count
        active_calls = sum(
            1 for connection in self._connections.values() if connection.active_request is not None
        )
        oauth = tuple(self._credentials.status_for(spec) for spec in self.specs)
        return McpRuntimeStatsSnapshot(
            catalog_version=self.snapshot().version,
            active_calls=active_calls,
            catalog_refresh_count=catalog_refresh_count,
            list_changed_count=list_changed_count,
            reconnect_count=reconnect_count,
            connection_history=history,
            tools=tool_stats,
            oauth=oauth,
        )

    def tools_for_actor(
        self,
        actor: ActorContext,
        files_dir: Path,
        *,
        session_id: str = "",
        interaction_notifier: McpInteractionNotifier | None = None,
    ) -> list[Tool]:
        """Bind every valid discovered Tool to the current actor."""

        from agent.tools.mcp import McpToolAdapter

        return [
            McpToolAdapter(
                descriptor,
                self,
                actor=actor,
                files_dir=Path(files_dir),
                session_id=session_id,
                interaction_notifier=interaction_notifier,
            )
            for descriptor in self.snapshot().tools
        ]

    def call_tool_sync(
        self,
        descriptor: McpToolDescriptor,
        args: dict[str, Any],
        *,
        actor: ActorContext,
        files_dir: Path,
        session_id: str = "",
        interaction_notifier: McpInteractionNotifier | None = None,
    ) -> ToolResult:
        """Bridge AgentLoop's synchronous Tool contract to the MCP loop."""

        if self._closed or not self.specs:
            return _error("MCP Server is unavailable", "MCP_SERVER_UNAVAILABLE")
        if not isinstance(args, dict):
            return _error("MCP Tool arguments must be an object", "MCP_SCHEMA_INVALID")
        if not _arguments_match_schema(args, descriptor.input_schema):
            return _error("MCP Tool arguments do not match the input schema", "MCP_SCHEMA_INVALID")
        call_id = uuid.uuid4().hex
        coroutine = self._enqueue_call(
            descriptor,
            args,
            _CallContext(
                descriptor.server_id,
                actor,
                Path(files_dir).resolve(),
                interaction_notifier,
                session_id,
            ),
            call_id=call_id,
        )
        future = asyncio.run_coroutine_threadsafe(coroutine, self._loop)
        timeout = self._spec_timeout(descriptor.server_id)
        try:
            return future.result(timeout=timeout + 5)
        except TimeoutError:
            future.cancel()
            asyncio.run_coroutine_threadsafe(self._cancel_call_id(call_id), self._loop)
            return _error(
                "MCP Tool timed out; the remote outcome may be unknown",
                "MCP_TOOL_TIMEOUT",
                server_id=descriptor.server_id,
            )
        except Exception as exc:  # noqa: BLE001 - runtime failures stay out of AgentLoop.
            return _error(
                "MCP Tool execution failed",
                "MCP_TRANSPORT_ERROR",
                server_id=descriptor.server_id,
                error_type=type(exc).__name__,
            )

    def submit_interaction(
        self,
        interaction_id: str,
        response: McpInteractionResponse,
    ) -> bool:
        """Resolve one pending Elicitation request without exposing its content to logs."""

        with self._pending_lock:
            pending = self._pending.get(interaction_id)
            if pending is None or pending.event.is_set():
                return False
            pending.response = response
            pending.event.set()
            return True

    def format_capabilities(self) -> str:
        """Render a credential-free ``/mcp`` summary."""

        snapshot = self.snapshot()
        ready_ids = {item.server_id for item in snapshot.servers if item.state == "ready" and item.tool_count}
        lines = ["当前可用 MCP：", ""]
        found = False
        for server_id in sorted(ready_ids):
            tools = [tool for tool in snapshot.tools if tool.server_id == server_id]
            if not tools:
                continue
            found = True
            lines.append(f"- {server_id}")
            summaries = [tool.description.strip().splitlines()[0] for tool in tools[:6]]
            lines.append("  " + "；".join(item for item in summaries if item)[:600])
            lines.append("")
        return "\n".join(lines).rstrip() if found else "当前没有可用的 MCP Server。"

    def close(self) -> None:
        """Stop new calls and close transports in their owning worker tasks."""

        if self._closed:
            return
        self._closed = True
        with self._pending_lock:
            for pending in self._pending.values():
                pending.response = McpInteractionResponse(action="cancel")
                pending.event.set()
        if self._thread.is_alive():
            future = asyncio.run_coroutine_threadsafe(self._stop(), self._loop)
            try:
                future.result(timeout=10)
            except TimeoutError:
                pass
            self._loop.call_soon_threadsafe(self._loop.stop)
            self._thread.join(timeout=5)
        log_event(mcp_logger, logging.INFO, "mcp.runtime_closed")

    def _run_loop(self) -> None:
        asyncio.set_event_loop(self._loop)
        self._loop.run_forever()
        self._loop.close()

    async def _start(self) -> None:
        for spec in self.specs:
            connection = _ServerConnection(
                spec=spec,
                queue=asyncio.Queue(),
                initial_ready=asyncio.Event(),
                status=McpServerStatus(spec.server_id, "connecting"),
            )
            self._connections[spec.server_id] = connection
            connection.task = asyncio.create_task(self._server_worker(connection))
        self._publish_snapshot()
        await asyncio.gather(
            *(connection.initial_ready.wait() for connection in self._connections.values())
        )

    async def _stop(self) -> None:
        for connection in self._connections.values():
            if connection.refresh_task is not None and not connection.refresh_task.done():
                connection.refresh_task.cancel()
            if connection.active_task is not None and not connection.active_task.done():
                connection.active_task.cancel()
            await connection.queue.put(None)
        tasks = [connection.task for connection in self._connections.values() if connection.task]
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        for connection in self._connections.values():
            connection.status = McpServerStatus(connection.spec.server_id, "closed")
            self._record_connection_state(connection.spec.server_id, "closed")
        self._publish_snapshot()

    async def _server_worker(self, connection: _ServerConnection) -> None:
        first_attempt = True
        while not self._closed:
            connection.status = McpServerStatus(connection.spec.server_id, "connecting")
            self._record_connection_state(connection.spec.server_id, "connecting")
            self._publish_snapshot()
            try:
                async with self._open_session(connection) as session:
                    connection.session = session
                    descriptors, errors = await self._discover_tools(connection.spec, session)
                    connection.descriptors = descriptors
                    state = "ready" if descriptors else "degraded"
                    error_code = "MCP_SCHEMA_INVALID" if errors else ""
                    connection.status = McpServerStatus(
                        connection.spec.server_id,
                        state,  # type: ignore[arg-type]
                        len(descriptors),
                        error_code,
                    )
                    self._publish_snapshot(catalog_changed=True)
                    self._record_connection_state(
                        connection.spec.server_id,
                        state,
                        reason_code=error_code,
                    )
                    log_event(
                        mcp_logger,
                        logging.INFO if state == "ready" else logging.WARNING,
                        f"mcp.server_{state}",
                        server_id=connection.spec.server_id,
                        tool_count=len(descriptors),
                    )
                    if first_attempt:
                        connection.initial_ready.set()
                        first_attempt = False
                    reconnect = await self._serve_calls(connection, session)
                    if not reconnect:
                        return
                    connection.reconnect_requested.clear()
            except Exception as exc:  # noqa: BLE001 - isolate one configured Server.
                connection.descriptors = ()
                connection.status = McpServerStatus(
                    connection.spec.server_id,
                    "degraded",
                    error_code=_exception_code(exc),
                )
                self._publish_snapshot(catalog_changed=True)
                self._record_connection_state(
                    connection.spec.server_id,
                    "degraded",
                    reason_code=_exception_code(exc),
                )
                log_event(
                    mcp_logger,
                    logging.ERROR,
                    "mcp.server_degraded",
                    server_id=connection.spec.server_id,
                    error_type=type(exc).__name__,
                )
                if first_attempt:
                    connection.initial_ready.set()
                    first_attempt = False
                request = await self._next_request(connection)
                if request is None:
                    return
                if request is _RECONNECT:
                    connection.reconnect_requested.clear()
                    continue
                if not request.future.done():
                    request.future.set_result(
                        _error("MCP Server is unavailable", "MCP_SERVER_UNAVAILABLE")
                    )
            finally:
                connection.session = None

    async def _serve_calls(self, connection: _ServerConnection, session: ClientSession) -> bool:
        while not self._closed:
            request = await self._next_request(connection)
            if request is None:
                return False
            if request is _RECONNECT:
                return True
            if request.future.done():
                continue
            connection.active_context = request.context
            connection.active_request = request
            self._record_activity("mcp.tool_call", request)
            try:
                connection.active_task = asyncio.create_task(
                    session.call_tool(request.descriptor.remote_name, request.args)
                )
                raw = await asyncio.wait_for(
                    connection.active_task,
                    timeout=connection.spec.call_timeout_seconds,
                )
                result = normalize_mcp_result(
                    raw,
                    server_id=connection.spec.server_id,
                    files_dir=request.context.files_dir,
                    temp_root=self._temp_root(connection.spec.server_id),
                    artifact_gateway=self._artifact_gateway,
                )
                if not request.future.done():
                    request.future.set_result(result)
                code = str(result.metadata.get("code") or "") if result.is_error else ""
                self._finish_call(request, result=result, reason_code=code)
            except TimeoutError:
                result = _error(
                    "MCP Tool timed out; the remote outcome may be unknown",
                    "MCP_TOOL_TIMEOUT",
                )
                if not request.future.done():
                    request.future.set_result(result)
                self._finish_call(request, result=result, reason_code="MCP_TOOL_TIMEOUT")
                return True
            except asyncio.CancelledError:
                result = _error("MCP Tool call was cancelled", "MCP_TOOL_CANCELLED")
                if not request.future.done():
                    request.future.set_result(result)
                self._finish_call(
                    request,
                    result=result,
                    reason_code="MCP_TOOL_CANCELLED",
                    cancelled=True,
                )
                if connection.reconnect_requested.is_set():
                    return True
            except Exception as exc:  # noqa: BLE001 - do not replay the current call.
                result = _error(
                    "MCP transport failed; the remote outcome may be unknown",
                    "MCP_TRANSPORT_ERROR",
                    error_type=type(exc).__name__,
                )
                if not request.future.done():
                    request.future.set_result(result)
                self._finish_call(request, result=result, reason_code="MCP_TRANSPORT_ERROR")
                return True
            finally:
                connection.active_context = None
                connection.active_request = None
                connection.active_task = None
        return False

    async def _enqueue_call(
        self,
        descriptor: McpToolDescriptor,
        args: dict[str, Any],
        context: _CallContext,
        *,
        call_id: str,
    ) -> ToolResult:
        connection = self._connections.get(descriptor.server_id)
        if connection is None:
            return _error("MCP Tool was not found", "MCP_TOOL_NOT_FOUND")
        future: asyncio.Future[ToolResult] = self._loop.create_future()
        await connection.queue.put(
            _CallRequest(descriptor, args, context, future, call_id=call_id)
        )
        return await future

    async def _next_request(self, connection: _ServerConnection) -> _CallRequest | object | None:
        """Wake a worker for either a queued call, shutdown, or reconnect."""

        queue_task = asyncio.create_task(connection.queue.get())
        reconnect_task = asyncio.create_task(connection.reconnect_requested.wait())
        done, pending = await asyncio.wait(
            {queue_task, reconnect_task},
            return_when=asyncio.FIRST_COMPLETED,
        )
        for task in pending:
            task.cancel()
        if reconnect_task in done and reconnect_task.result():
            if queue_task in done:
                request = queue_task.result()
                if request is not None:
                    connection.queue.put_nowait(request)
            return _RECONNECT
        return queue_task.result()

    async def _request_reconnect(self, server_id: str) -> bool:
        connection = self._connections.get(server_id)
        if connection is None:
            return False
        with self._stats_lock:
            self._reconnect_count += 1
        connection.reconnect_requested.set()
        if connection.active_task is not None and not connection.active_task.done():
            connection.active_task.cancel()
        return True

    async def _reload_specs(self, specs: tuple[McpServerSpec, ...]) -> bool:
        requested = {spec.server_id: spec for spec in specs}
        existing = dict(self._connections)
        changed_ids = {
            server_id
            for server_id, connection in existing.items()
            if requested.get(server_id) != connection.spec
        }
        removed_ids = set(existing) - set(requested)
        for server_id in sorted(changed_ids | removed_ids):
            connection = self._connections.pop(server_id)
            if connection.refresh_task is not None and not connection.refresh_task.done():
                connection.refresh_task.cancel()
            if connection.active_task is not None and not connection.active_task.done():
                connection.active_task.cancel()
            await connection.queue.put(None)
            if connection.task is not None:
                await asyncio.gather(connection.task, return_exceptions=True)
            self._record_connection_state(server_id, "closed", reason_code="MCP_CONFIG_RELOADED")
        self.specs = specs
        new_connections: list[_ServerConnection] = []
        for spec in specs:
            if spec.server_id in self._connections:
                continue
            connection = _ServerConnection(
                spec=spec,
                queue=asyncio.Queue(),
                initial_ready=asyncio.Event(),
                status=McpServerStatus(spec.server_id, "connecting"),
            )
            self._connections[spec.server_id] = connection
            connection.task = asyncio.create_task(self._server_worker(connection))
            new_connections.append(connection)
        self._publish_snapshot(catalog_changed=bool(changed_ids or removed_ids or new_connections))
        if new_connections:
            await asyncio.gather(*(connection.initial_ready.wait() for connection in new_connections))
        return True

    async def _cancel_call_id(self, call_id: str) -> bool:
        for connection in self._connections.values():
            request = connection.active_request
            if request is None or request.call_id != call_id:
                continue
            if connection.active_task is not None and not connection.active_task.done():
                connection.active_task.cancel()
                return True
        return False

    async def _cancel_matching_calls(
        self,
        *,
        server_id: str | None,
        user_id: str | None,
        session_id: str | None,
    ) -> int:
        cancelled = 0
        for connection in self._connections.values():
            request = connection.active_request
            if request is None or (server_id and connection.spec.server_id != server_id):
                continue
            if user_id is not None and request.context.actor.user_id != user_id:
                continue
            if session_id is not None and request.context.session_id != session_id:
                continue
            if connection.active_task is not None and not connection.active_task.done():
                connection.active_task.cancel()
                cancelled += 1
        return cancelled

    async def _refresh_server_catalog(self, server_id: str, *, reason: str) -> bool:
        connection = self._connections.get(server_id)
        if connection is None or connection.session is None:
            return False
        if reason == "list_changed":
            with self._stats_lock:
                self._list_changed_count += 1
        async with connection.refresh_lock:
            try:
                descriptors, errors = await self._discover_tools(
                    connection.spec,
                    connection.session,
                )
            except Exception as exc:  # noqa: BLE001 - keep the previous valid snapshot.
                self._record_connection_state(
                    server_id,
                    "degraded",
                    reason_code=_exception_code(exc),
                )
                return False
            if errors:
                log_event(
                    mcp_logger,
                    logging.WARNING,
                    "mcp.catalog_refresh_rejected",
                    server_id=server_id,
                    reason=reason,
                    error_count=len(errors),
                )
                return False
            connection.descriptors = descriptors
            connection.status = McpServerStatus(
                server_id,
                "ready" if descriptors else "degraded",
                len(descriptors),
            )
            with self._stats_lock:
                self._catalog_refresh_count += 1
            self._publish_snapshot(catalog_changed=True)
            log_event(
                mcp_logger,
                logging.INFO,
                "mcp.catalog_refreshed",
                server_id=server_id,
                reason=reason,
                tool_count=len(descriptors),
                catalog_version=self.snapshot().version,
            )
            return True

    @asynccontextmanager
    async def _open_session(self, connection: _ServerConnection) -> AsyncIterator[ClientSession]:
        spec = connection.spec
        async with AsyncExitStack() as stack:
            headers = await self._credentials.headers_for(spec)
            if spec.transport == "stdio":
                temp_root = self._temp_root(spec.server_id)
                temp_root.mkdir(parents=True, exist_ok=True)
                cwd = temp_root / spec.cwd if spec.cwd else temp_root
                cwd.mkdir(parents=True, exist_ok=True)
                params = StdioServerParameters(
                    command=spec.command,
                    args=list(spec.args),
                    cwd=cwd,
                    env=_stdio_env(temp_root, spec.env),
                    encoding="utf-8",
                    encoding_error_handler="replace",
                )
                errlog = stack.enter_context(open(os.devnull, "w", encoding="utf-8"))
                read_stream, write_stream = await stack.enter_async_context(
                    stdio_client(params, errlog=errlog)
                )
            elif spec.transport == "sse":
                read_stream, write_stream = await stack.enter_async_context(
                    sse_client(
                        spec.url,
                        headers=headers,
                        timeout=spec.connect_timeout_seconds,
                        sse_read_timeout=max(spec.call_timeout_seconds, 300),
                    )
                )
            else:
                client = await stack.enter_async_context(
                    httpx.AsyncClient(headers=headers, timeout=spec.connect_timeout_seconds)
                )
                read_stream, write_stream, _ = await stack.enter_async_context(
                    streamable_http_client(spec.url, http_client=client)
                )
            session = await stack.enter_async_context(
                ClientSession(
                    read_stream,
                    write_stream,
                    read_timeout_seconds=timedelta(seconds=spec.call_timeout_seconds),
                    elicitation_callback=lambda context, params: self._elicitation_callback(
                        spec.server_id, context, params
                    ),
                    message_handler=lambda message: self._message_handler(connection, message),
                )
            )
            await asyncio.wait_for(session.initialize(), timeout=spec.connect_timeout_seconds)
            yield session

    async def _message_handler(self, connection: _ServerConnection, message: Any) -> None:
        """Translate SDK notifications into versioned Catalog refreshes."""

        root = getattr(message, "root", message)
        if not isinstance(root, types.ToolListChangedNotification):
            return
        if connection.refresh_task is not None and not connection.refresh_task.done():
            return
        connection.refresh_task = asyncio.create_task(
            self._refresh_server_catalog(connection.spec.server_id, reason="list_changed")
        )

    async def _discover_tools(
        self,
        spec: McpServerSpec,
        session: ClientSession,
    ) -> tuple[tuple[McpToolDescriptor, ...], tuple[str, ...]]:
        raw_tools: list[Any] = []
        cursor: str | None = None
        while True:
            result = await asyncio.wait_for(
                session.list_tools(cursor=cursor), timeout=spec.connect_timeout_seconds
            )
            raw_tools.extend(result.tools)
            cursor = getattr(result, "nextCursor", None) or getattr(result, "next_cursor", None)
            if not cursor:
                break
        reserved = {
            descriptor.local_name
            for connection in self._connections.values()
            for descriptor in connection.descriptors
            if connection.spec.server_id != spec.server_id
        }
        descriptors, errors = build_tool_descriptors(spec.server_id, raw_tools, reserved_names=reserved)
        current_total = sum(
            len(connection.descriptors)
            for connection in self._connections.values()
            if connection.spec.server_id != spec.server_id
        )
        available = max(0, MAX_TOOLS_TOTAL - current_total)
        if len(descriptors) > available:
            descriptors = descriptors[:available]
            errors = (*errors, "MCP_TOOL_TOTAL_LIMIT_EXCEEDED")
        return descriptors, errors

    async def _elicitation_callback(self, server_id: str, _context, params) -> types.ElicitResult:
        connection = self._connections.get(server_id)
        call_context = connection.active_context if connection is not None else None
        if call_context is None or call_context.interaction_notifier is None:
            return types.ElicitResult(action="cancel")
        interaction_id = "mcp-int-" + uuid.uuid4().hex
        mode = str(getattr(params, "mode", "form") or "form")
        request = McpInteractionRequest(
            interaction_id=interaction_id,
            server_id=call_context.server_id,
            mode="url" if mode == "url" else "form",
            message=str(getattr(params, "message", "") or "")[:4000],
            requested_schema=dict(getattr(params, "requestedSchema", {}) or {}),
            url=str(getattr(params, "url", "") or "")[:2000],
        )
        pending = _PendingInteraction()
        with self._pending_lock:
            self._pending[interaction_id] = pending
        try:
            call_context.interaction_notifier(request)
            log_event(
                mcp_logger,
                logging.INFO,
                "mcp.elicitation_requested",
                server_id=request.server_id,
                interaction_id=interaction_id,
            )
            timeout = self._spec_timeout(request.server_id)
            completed = await asyncio.to_thread(pending.event.wait, timeout)
            response = pending.response if completed else None
            if response is None:
                return types.ElicitResult(action="cancel")
            self._record_interaction_audit(request, response)
            return types.ElicitResult(action=response.action, content=response.content)
        finally:
            with self._pending_lock:
                self._pending.pop(interaction_id, None)

    def _publish_snapshot(self, *, catalog_changed: bool = False) -> None:
        tools = tuple(
            descriptor
            for connection in self._connections.values()
            for descriptor in connection.descriptors
        )
        servers = tuple(
            connection.status or McpServerStatus(connection.spec.server_id, "connecting")
            for connection in self._connections.values()
        )
        with self._snapshot_lock:
            if catalog_changed:
                self._catalog_version += 1
            self._snapshot = McpCatalogSnapshot(
                version=self._catalog_version,
                generated_at=time.time(),
                tools=tools,
                servers=servers,
            )

    def _record_connection_state(
        self,
        server_id: str,
        state: str,
        *,
        reason_code: str = "",
    ) -> None:
        with self._stats_lock:
            self._connection_history.append(
                McpConnectionEvent(
                    server_id=server_id,
                    state=state,
                    timestamp=time.time(),
                    reason_code=reason_code,
                )
            )

    def _finish_call(
        self,
        request: _CallRequest,
        *,
        result: ToolResult,
        reason_code: str = "",
        cancelled: bool = False,
    ) -> None:
        duration_ms = max(0, round((time.perf_counter() - request.started_at) * 1000))
        key = (request.descriptor.server_id, request.descriptor.local_name)
        with self._stats_lock:
            stats = self._tool_stats.setdefault(key, _MutableToolStats())
            stats.call_count += 1
            stats.total_duration_ms += duration_ms
            stats.max_duration_ms = max(stats.max_duration_ms, duration_ms)
            if result.is_error:
                stats.error_count += 1
                stats.last_error_code = reason_code
            else:
                stats.success_count += 1
                stats.last_error_code = ""
            if cancelled:
                stats.cancelled_count += 1
        self._record_activity(
            "mcp.tool_error" if result.is_error else "mcp.tool_done",
            request,
            reason_code=reason_code,
            duration_ms=duration_ms,
        )

    def _temp_root(self, server_id: str) -> Path:
        return self.runtime_root / server_id / "tmp"

    def _spec_timeout(self, server_id: str) -> float:
        for spec in self.specs:
            if spec.server_id == server_id:
                return spec.call_timeout_seconds
        return 60.0

    def _record_activity(
        self,
        action: str,
        request: _CallRequest,
        *,
        reason_code: str = "",
        duration_ms: int | None = None,
    ) -> None:
        if self.activity_sink is None:
            return
        self.activity_sink.record(
            RuntimeActivityEvent(
                action=action,
                actor=request.context.actor,
                channel=request.context.actor.channel,
                decision="allow" if not reason_code else "error",
                reason_code=reason_code,
                metadata={
                    "server_id": request.descriptor.server_id,
                    "tool_name": request.descriptor.local_name,
                    "argument_keys": sorted(request.args)[:50],
                    **({"duration_ms": duration_ms} if duration_ms is not None else {}),
                },
            )
        )

    def _record_interaction_audit(
        self,
        request: McpInteractionRequest,
        response: McpInteractionResponse,
    ) -> None:
        if self.audit_sink is None:
            return
        self.audit_sink.record(
            AuditEvent(
                action="mcp.elicitation_completed",
                resource_type="mcp_interaction",
                resource_id=request.interaction_id,
                decision=response.action,
                metadata={"server_id": request.server_id, "mode": request.mode},
            )
        )

    def _audit_configured_credentials(self) -> None:
        if self.audit_sink is None:
            return
        for spec in self.specs:
            if not spec.headers and not spec.env and spec.oauth is None:
                continue
            self.audit_sink.record(
                AuditEvent(
                    action="mcp.credential_configured",
                    resource_type="mcp_server",
                    resource_id=spec.server_id,
                    decision="allow",
                    metadata={
                        "has_headers": bool(spec.headers),
                        "has_stdio_env": bool(spec.env),
                        "has_oauth": spec.oauth is not None,
                    },
                )
            )


def _stdio_env(temp_root: Path, configured: dict[str, str]) -> dict[str, str]:
    """Build a small process environment without inheriting workspace secrets."""

    allowed = ("PATH", "PATHEXT", "SYSTEMROOT", "WINDIR", "COMSPEC", "LANG", "LC_ALL")
    env = {key: os.environ[key] for key in allowed if os.environ.get(key)}
    env.update(configured)
    temp = str(temp_root)
    env.update({"HOME": temp, "USERPROFILE": temp, "TMP": temp, "TEMP": temp})
    return env


def _error(message: str, code: str, **metadata: Any) -> ToolResult:
    return ToolResult(output=message, is_error=True, metadata={"code": code, **metadata})


def _exception_code(exc: Exception) -> str:
    return str(getattr(exc, "code", "") or "MCP_TRANSPORT_ERROR")


def _arguments_match_schema(args: dict[str, Any], schema: dict[str, Any]) -> bool:
    """Apply the stable object/required/basic-type subset before remote dispatch."""

    required = schema.get("required", [])
    if isinstance(required, list) and any(name not in args for name in required if isinstance(name, str)):
        return False
    properties = schema.get("properties", {})
    if not isinstance(properties, dict):
        return True
    type_map = {
        "string": str,
        "object": dict,
        "array": list,
        "boolean": bool,
        "number": (int, float),
        "integer": int,
        "null": type(None),
    }
    for name, value in args.items():
        definition = properties.get(name)
        if not isinstance(definition, dict):
            if schema.get("additionalProperties") is False:
                return False
            continue
        expected = definition.get("type")
        accepted_types = expected if isinstance(expected, list) else [expected]
        python_types = tuple(type_map[item] for item in accepted_types if item in type_map)
        if python_types and not isinstance(value, python_types):
            return False
        if expected == "integer" and isinstance(value, bool):
            return False
        if expected == "number" and isinstance(value, bool):
            return False
    return True
