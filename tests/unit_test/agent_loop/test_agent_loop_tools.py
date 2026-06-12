"""Tests for AgentLoop tool-calling behavior."""

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from agent.message import Message
from agent.protocols.llm import LLMResponse
from agent.protocols.session import SessionState
from agent.protocols.tool import ToolResult


def test_agent_loop_passes_tool_definitions_to_llm(tmp_path):
    """Available tool schemas should be sent to the provider."""

    llm = ScriptedLLM([LLMResponse(content="final")])
    tools = FakeTools()
    loop = _make_loop(tmp_path, llm=llm, tools=tools)

    result = loop.run_turn("default", "hello")

    assert result == "final"
    assert llm.calls[0]["tools"] == tools.definitions()


def test_single_tool_call_executes_and_triggers_second_llm_call(tmp_path):
    """A tool request should be executed, returned as a tool message, then summarized."""

    call = _openai_tool_call("call_1", "read_file", {"path": "README.md"})
    llm = ScriptedLLM([LLMResponse(content="", tool_calls=[call]), LLMResponse(content="done")])
    tools = FakeTools(results=[ToolResult(output="README body")])
    sessions = InMemorySessionStore()
    loop = _make_loop(tmp_path, llm=llm, tools=tools, sessions=sessions)

    result = loop.run_turn("default", "read README")

    assert result == "done"
    assert tools.calls == [("read_file", {"path": "README.md"})]
    assert len(llm.calls) == 2
    second_messages = llm.calls[1]["messages"]
    assert second_messages[-2]["tool_calls"] == [call]
    assert second_messages[-1]["role"] == "tool"
    assert second_messages[-1]["tool_call_id"] == "call_1"
    assert json.loads(second_messages[-1]["content"])["output"] == "README body"
    assert [message.role for message in sessions.appended["default"]] == [
        "user",
        "assistant",
        "tool",
        "assistant",
    ]


def test_multiple_tool_calls_execute_in_order(tmp_path):
    """One assistant message may request multiple tools, executed serially."""

    calls = [
        _openai_tool_call("call_1", "list_dir", {"path": "."}),
        _openai_tool_call("call_2", "grep", {"pattern": "Agent"}),
    ]
    llm = ScriptedLLM([LLMResponse(content="", tool_calls=calls), LLMResponse(content="done")])
    tools = FakeTools(results=[ToolResult(output="list"), ToolResult(output="grep")])
    loop = _make_loop(tmp_path, llm=llm, tools=tools)

    loop.run_turn("default", "inspect")

    assert tools.calls == [("list_dir", {"path": "."}), ("grep", {"pattern": "Agent"})]
    assert [message["tool_call_id"] for message in llm.calls[1]["messages"][-2:]] == [
        "call_1",
        "call_2",
    ]


def test_tool_error_is_returned_to_llm(tmp_path):
    """ToolResult errors should become tool messages rather than loop exceptions."""

    call = _openai_tool_call("call_1", "read_file", {"path": "../secret"})
    result = ToolResult(
        output="Path is outside workspace.",
        is_error=True,
        metadata={"code": "PATH_OUTSIDE_WORKSPACE"},
    )
    llm = ScriptedLLM([LLMResponse(content="", tool_calls=[call]), LLMResponse(content="explained")])
    loop = _make_loop(tmp_path, llm=llm, tools=FakeTools(results=[result]))

    final = loop.run_turn("default", "read outside")

    tool_payload = json.loads(llm.calls[1]["messages"][-1]["content"])
    assert final == "explained"
    assert tool_payload["status"] == "error"
    assert tool_payload["metadata"]["code"] == "PATH_OUTSIDE_WORKSPACE"


def test_malformed_tool_arguments_do_not_crash_loop(tmp_path):
    """Invalid argument JSON should be sent back as a structured tool error."""

    call = {
        "id": "call_bad",
        "type": "function",
        "function": {"name": "read_file", "arguments": "{"},
    }
    llm = ScriptedLLM([LLMResponse(content="", tool_calls=[call]), LLMResponse(content="handled")])
    tools = FakeTools()
    loop = _make_loop(tmp_path, llm=llm, tools=tools)

    result = loop.run_turn("default", "bad args")

    payload = json.loads(llm.calls[1]["messages"][-1]["content"])
    assert result == "handled"
    assert tools.calls == []
    assert payload["metadata"]["code"] == "INVALID_ARGUMENT_JSON"


def test_missing_tool_call_id_gets_stable_fallback(tmp_path):
    """Tool call ids are required for tool messages, so missing ids get generated."""

    call = {"type": "function", "function": {"name": "list_dir", "arguments": "{}"}}
    llm = ScriptedLLM([LLMResponse(content="", tool_calls=[call]), LLMResponse(content="done")])
    loop = _make_loop(tmp_path, llm=llm, tools=FakeTools(results=[ToolResult(output="list")]))

    loop.run_turn("default", "list")

    tool_message = llm.calls[1]["messages"][-1]
    payload = json.loads(tool_message["content"])
    assert tool_message["tool_call_id"] == "call_0"
    assert payload["metadata"]["generated_tool_call_id"] is True


def test_tool_iteration_limit_saves_error_marker(tmp_path):
    """The loop should stop infinite tool-calling cycles."""

    call = _openai_tool_call("call_1", "list_dir", {})
    llm = ScriptedLLM(
        [
            LLMResponse(content="", tool_calls=[call]),
            LLMResponse(content="", tool_calls=[call]),
        ]
    )
    sessions = InMemorySessionStore()
    loop = _make_loop(
        tmp_path,
        llm=llm,
        tools=FakeTools(results=[ToolResult(output="list")]),
        sessions=sessions,
        max_tool_iterations=1,
    )

    result = loop.run_turn("default", "loop")

    assert "Tool call limit reached" in result
    assert [message.role for message in sessions.appended["default"]] == [
        "user",
        "assistant",
        "tool",
        "assistant",
        "tool",
        "assistant",
    ]
    assert sessions.appended["default"][-1].metadata["code"] == "TOOL_ITERATION_LIMIT"
    assert sessions.appended["default"][-2].metadata["code"] == "TOOL_ITERATION_LIMIT"


def test_llm_error_after_tool_call_preserves_pending_messages(tmp_path):
    """Provider failures after tool execution should still save the evidence trail."""

    call = _openai_tool_call("call_1", "list_dir", {})
    llm = ScriptedLLM([LLMResponse(content="", tool_calls=[call]), RuntimeError("boom secret")])
    sessions = InMemorySessionStore()
    loop = _make_loop(
        tmp_path,
        llm=llm,
        tools=FakeTools(results=[ToolResult(output="list")]),
        sessions=sessions,
    )

    result = loop.run_turn("default", "list")

    assert "secret" not in result
    assert [message.role for message in sessions.appended["default"]] == [
        "user",
        "assistant",
        "tool",
        "assistant",
    ]
    assert sessions.appended["default"][-1].metadata["is_error"] is True


def _make_loop(
    tmp_path,
    *,
    llm,
    tools,
    sessions=None,
    max_tool_iterations=4,
):
    from agent.loop import AgentLoop

    return AgentLoop(
        llm=llm,
        sessions=sessions or InMemorySessionStore(),
        context_builder=FakeContextBuilder(),
        workspace=tmp_path,
        tools=tools,
        max_tool_iterations=max_tool_iterations,
    )


def _openai_tool_call(call_id: str, name: str, args: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": call_id,
        "type": "function",
        "function": {"name": name, "arguments": json.dumps(args)},
    }


@dataclass
class InMemorySessionStore:
    states: dict[str, SessionState] = field(default_factory=dict)
    appended: dict[str, list[Message]] = field(default_factory=dict)

    def load(self, session_id: str) -> SessionState:
        return self.states.get(session_id, SessionState(session_id=session_id, messages=[]))

    def append(self, session_id: str, messages: list[Message]) -> None:
        self.appended.setdefault(session_id, []).extend(messages)


class FakeContextBuilder:
    def build(
        self,
        history: list[Message],
        user_message: Message,
        workspace: Path,
        session_id: str,
    ) -> list[dict[str, Any]]:
        return [{"role": user_message.role, "content": user_message.content}]


class ScriptedLLM:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    def chat(self, messages, tools=None):
        self.calls.append({"messages": list(messages), "tools": tools})
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


class FakeTools:
    def __init__(self, results=None):
        self.results = list(results or [])
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def definitions(self):
        return [
            {
                "type": "function",
                "function": {
                    "name": "read_file",
                    "description": "Read a file.",
                    "parameters": {"type": "object"},
                },
            }
        ]

    def execute(self, name, args):
        self.calls.append((name, args))
        if self.results:
            return self.results.pop(0)
        return ToolResult(output=f"{name}:{args}")
