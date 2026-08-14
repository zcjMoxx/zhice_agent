from __future__ import annotations

import sys
import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path

import pytest

import agent.mcp.runtime as mcp_runtime_module
from agent.mcp.runtime import McpRuntime, _leaf_exception_type
from agent.protocols.auth import ActorContext
from agent.protocols.mcp import McpInteractionResponse, McpServerSpec, McpToolDescriptor


def _actor() -> ActorContext:
    return ActorContext(
        actor_type="user",
        user_id="user-1",
        username="tester",
        display_name="Tester",
        role_keys=frozenset({"viewer"}),
        permission_keys=frozenset(),
        channel="web",
    )


@pytest.mark.integration
def test_stdio_runtime_discovers_calls_and_closes(tmp_path):
    script = Path(__file__).with_name("fake_stdio_server.py").resolve()
    runtime = McpRuntime(
        [
            McpServerSpec(
                server_id="fake",
                transport="stdio",
                command=sys.executable,
                args=(str(script),),
                startup_timeout_seconds=10,
                connect_timeout_seconds=10,
                call_timeout_seconds=10,
            )
        ],
        workspace=tmp_path,
    )
    try:
        tools = runtime.tools_for_actor(_actor(), tmp_path / "files")
        names = {tool.name for tool in tools}
        assert "mcp__fake__echo" in names
        echo = next(tool for tool in tools if tool.name == "mcp__fake__echo")

        result = echo.execute({"text": "hello"})
        invalid = echo.execute({})

        assert not result.is_error
        assert "echo:hello" in result.output
        assert invalid.is_error
        assert invalid.metadata["code"] == "MCP_SCHEMA_INVALID"
        status = runtime.snapshot().servers[0]
        assert status.state == "ready"
        assert status.tool_count == 4
        sandbox = tmp_path / "state" / "mcp_runtime" / "fake" / "tmp"
        cwd_tool = next(tool for tool in tools if tool.name == "mcp__fake__current_directory")
        assert str(sandbox.resolve()) in cwd_tool.execute({}).output
        interactive_tools = runtime.tools_for_actor(
            _actor(),
            tmp_path / "files",
            interaction_notifier=lambda request: runtime.submit_interaction(
                request.interaction_id,
                McpInteractionResponse(action="accept", content={"code": "1234"}),
            ),
        )
        request_tool = next(
            tool for tool in interactive_tools if tool.name == "mcp__fake__request_code"
        )
        assert "code:1234" in request_tool.execute({}).output
    finally:
        runtime.close()

    assert not runtime._thread.is_alive()


@pytest.mark.integration
def test_runtime_refresh_reconnect_cancel_and_stats(tmp_path):
    script = Path(__file__).with_name("fake_stdio_server.py").resolve()
    spec = McpServerSpec(
        server_id="fake",
        transport="stdio",
        command=sys.executable,
        args=(str(script),),
        startup_timeout_seconds=10,
        connect_timeout_seconds=10,
        call_timeout_seconds=10,
    )
    runtime = McpRuntime([spec], workspace=tmp_path)
    try:
        initial = runtime.snapshot()
        assert initial.version > 0
        assert runtime.refresh_catalog("fake") is True
        refreshed = runtime.snapshot()
        assert refreshed.version > initial.version
        assert runtime.tools_list_changed("fake") is True
        assert runtime.stats_snapshot().list_changed_count == 1

        tools = runtime.tools_for_actor(
            _actor(),
            tmp_path / "files",
            session_id="session-a",
        )
        slow = next(tool for tool in tools if tool.name == "mcp__fake__slow_echo")
        outcome = []
        thread = threading.Thread(
            target=lambda: outcome.append(slow.execute({"text": "wait", "delay_seconds": 5.0}))
        )
        thread.start()
        assert _wait_until(lambda: runtime.stats_snapshot().active_calls == 1)
        assert (
            runtime.cancel_active_calls(
                "fake",
                user_id="user-1",
                session_id="session-a",
            )
            == 1
        )
        thread.join(timeout=5)

        assert not thread.is_alive()
        assert outcome[0].is_error
        assert outcome[0].metadata["code"] == "MCP_TOOL_CANCELLED"
        tool_stats = {
            item.tool_name: item for item in runtime.stats_snapshot().tools
        }
        assert tool_stats["mcp__fake__slow_echo"].cancelled_count == 1

        version = runtime.snapshot().version
        assert runtime.reconnect("fake") is True
        assert _wait_until(
            lambda: runtime.snapshot().version > version
            and runtime.snapshot().servers[0].state == "ready"
        )
        assert runtime.stats_snapshot().reconnect_count == 1
        assert runtime.reload([]) is True
        assert runtime.snapshot().tools == ()
        assert runtime.reload([spec]) is True
        assert runtime.snapshot().servers[0].state == "ready"
    finally:
        runtime.close()


@pytest.mark.integration
def test_invalid_catalog_refresh_keeps_previous_atomic_snapshot(tmp_path):
    script = Path(__file__).with_name("fake_stdio_server.py").resolve()
    runtime = McpRuntime(
        [
            McpServerSpec(
                server_id="fake",
                transport="stdio",
                command=sys.executable,
                args=(str(script),),
                startup_timeout_seconds=10,
                connect_timeout_seconds=10,
                call_timeout_seconds=10,
            )
        ],
        workspace=tmp_path,
    )
    try:
        previous = runtime.snapshot()

        async def invalid_discovery(_spec, _session):
            return (), ("bad:MCP_SCHEMA_INVALID",)

        runtime._discover_tools = invalid_discovery

        assert runtime.refresh_catalog("fake") is False
        current = runtime.snapshot()
        assert current.version == previous.version
        assert current.tools == previous.tools
    finally:
        runtime.close()


def test_empty_runtime_is_disabled_without_thread(tmp_path):
    runtime = McpRuntime([], workspace=tmp_path)
    assert runtime.snapshot().tools == ()
    assert runtime.format_capabilities() == "当前没有可用的 MCP Server。"
    runtime.close()


def test_runtime_automatically_recovers_after_transient_initialization_failure(
    tmp_path, monkeypatch
):
    attempts = 0

    @asynccontextmanager
    async def flaky_open_session(_runtime, _connection):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise ExceptionGroup("transient", [TimeoutError("hidden detail")])
        yield object()

    async def discover(_runtime, _spec, _session):
        return (
            McpToolDescriptor(
                server_id="flaky",
                remote_name="search",
                local_name="mcp__flaky__search",
                description="Search",
                input_schema={"type": "object"},
            ),
        ), ()

    monkeypatch.setattr(McpRuntime, "_open_session", flaky_open_session)
    monkeypatch.setattr(McpRuntime, "_discover_tools", discover)
    monkeypatch.setattr(mcp_runtime_module, "MCP_RECONNECT_BACKOFF_SECONDS", (0.01,))
    runtime = McpRuntime(
        [McpServerSpec(server_id="flaky", transport="streamable_http", url="https://unused")],
        workspace=tmp_path,
    )
    try:
        assert _wait_until(lambda: runtime.snapshot().servers[0].state == "ready")
        assert attempts >= 2
        assert [tool.local_name for tool in runtime.snapshot().tools] == [
            "mcp__flaky__search"
        ]
    finally:
        runtime.close()


def test_exception_group_logging_uses_safe_leaf_type():
    error = ExceptionGroup("outer secret", [ExceptionGroup("inner", [TimeoutError("url")])])

    assert _leaf_exception_type(error) == "TimeoutError"


def _wait_until(predicate, *, timeout: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return predicate()
