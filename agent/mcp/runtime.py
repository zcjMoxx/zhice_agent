"""Workspace-shared MCP connections behind a synchronous Tool facade."""

from __future__ import annotations

import asyncio
import logging
import os
import threading
import uuid
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
    McpInteractionNotifier,
    McpInteractionRequest,
    McpInteractionResponse,
    McpServerSpec,
    McpServerStatus,
    McpToolDescriptor,
)
from agent.protocols.tool import Tool, ToolResult

mcp_logger = logging.getLogger("zcagent.agent.mcp")
MAX_TOOLS_TOTAL = 128


@dataclass
class _CallContext:
    server_id: str
    actor: ActorContext
    files_dir: Path
    interaction_notifier: McpInteractionNotifier | None


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


@dataclass
class _ServerConnection:
    spec: McpServerSpec
    queue: asyncio.Queue[_CallRequest | None]
    initial_ready: asyncio.Event
    task: asyncio.Task[None] | None = None
    status: McpServerStatus | None = None
    descriptors: tuple[McpToolDescriptor, ...] = ()
    active_context: _CallContext | None = None


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

    def tools_for_actor(
        self,
        actor: ActorContext,
        files_dir: Path,
        *,
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
        interaction_notifier: McpInteractionNotifier | None = None,
    ) -> ToolResult:
        """Bridge AgentLoop's synchronous Tool contract to the MCP loop."""

        if self._closed or not self.specs:
            return _error("MCP Server is unavailable", "MCP_SERVER_UNAVAILABLE")
        if not isinstance(args, dict):
            return _error("MCP Tool arguments must be an object", "MCP_SCHEMA_INVALID")
        if not _arguments_match_schema(args, descriptor.input_schema):
            return _error("MCP Tool arguments do not match the input schema", "MCP_SCHEMA_INVALID")
        coroutine = self._enqueue_call(
            descriptor,
            args,
            _CallContext(
                descriptor.server_id,
                actor,
                Path(files_dir).resolve(),
                interaction_notifier,
            ),
        )
        future = asyncio.run_coroutine_threadsafe(coroutine, self._loop)
        timeout = self._spec_timeout(descriptor.server_id)
        try:
            return future.result(timeout=timeout + 5)
        except TimeoutError:
            future.cancel()
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
            await connection.queue.put(None)
        tasks = [connection.task for connection in self._connections.values() if connection.task]
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        for connection in self._connections.values():
            connection.status = McpServerStatus(connection.spec.server_id, "closed")
        self._publish_snapshot()

    async def _server_worker(self, connection: _ServerConnection) -> None:
        first_attempt = True
        while not self._closed:
            connection.status = McpServerStatus(connection.spec.server_id, "connecting")
            self._publish_snapshot()
            try:
                async with self._open_session(connection.spec) as session:
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
                    self._publish_snapshot()
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
            except Exception as exc:  # noqa: BLE001 - isolate one configured Server.
                connection.descriptors = ()
                connection.status = McpServerStatus(
                    connection.spec.server_id,
                    "degraded",
                    error_code=_exception_code(exc),
                )
                self._publish_snapshot()
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
                request = await connection.queue.get()
                if request is None:
                    return
                if not request.future.done():
                    request.future.set_result(
                        _error("MCP Server is unavailable", "MCP_SERVER_UNAVAILABLE")
                    )

    async def _serve_calls(self, connection: _ServerConnection, session: ClientSession) -> bool:
        while not self._closed:
            request = await connection.queue.get()
            if request is None:
                return False
            connection.active_context = request.context
            self._record_activity("mcp.tool_call", request)
            try:
                raw = await asyncio.wait_for(
                    session.call_tool(request.descriptor.remote_name, request.args),
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
                self._record_activity("mcp.tool_error" if result.is_error else "mcp.tool_done", request)
            except TimeoutError:
                if not request.future.done():
                    request.future.set_result(
                        _error(
                            "MCP Tool timed out; the remote outcome may be unknown",
                            "MCP_TOOL_TIMEOUT",
                        )
                    )
                self._record_activity("mcp.tool_error", request, reason_code="MCP_TOOL_TIMEOUT")
                return True
            except Exception as exc:  # noqa: BLE001 - do not replay the current call.
                if not request.future.done():
                    request.future.set_result(
                        _error(
                            "MCP transport failed; the remote outcome may be unknown",
                            "MCP_TRANSPORT_ERROR",
                            error_type=type(exc).__name__,
                        )
                    )
                self._record_activity("mcp.tool_error", request, reason_code="MCP_TRANSPORT_ERROR")
                return True
            finally:
                connection.active_context = None
        return False

    async def _enqueue_call(
        self,
        descriptor: McpToolDescriptor,
        args: dict[str, Any],
        context: _CallContext,
    ) -> ToolResult:
        connection = self._connections.get(descriptor.server_id)
        if connection is None:
            return _error("MCP Tool was not found", "MCP_TOOL_NOT_FOUND")
        future: asyncio.Future[ToolResult] = self._loop.create_future()
        await connection.queue.put(_CallRequest(descriptor, args, context, future))
        return await future

    @asynccontextmanager
    async def _open_session(self, spec: McpServerSpec) -> AsyncIterator[ClientSession]:
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
                )
            )
            await asyncio.wait_for(session.initialize(), timeout=spec.connect_timeout_seconds)
            yield session

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

    def _publish_snapshot(self) -> None:
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
            self._snapshot = McpCatalogSnapshot(tools=tools, servers=servers)

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
