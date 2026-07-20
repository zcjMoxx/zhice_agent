from __future__ import annotations

import sys
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
        assert status.tool_count == 3
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


def test_empty_runtime_is_disabled_without_thread(tmp_path):
    runtime = McpRuntime([], workspace=tmp_path)
    assert runtime.snapshot().tools == ()
    assert runtime.format_capabilities() == "当前没有可用的 MCP Server。"
    runtime.close()
