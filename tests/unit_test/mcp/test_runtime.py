from __future__ import annotations

import sys
import threading
import time
from pathlib import Path

import pytest

from agent.mcp.runtime import McpRuntime
from agent.protocols.auth import ActorContext
from agent.protocols.mcp import McpInteractionResponse, McpServerSpec


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


def _wait_until(predicate, *, timeout: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return predicate()
