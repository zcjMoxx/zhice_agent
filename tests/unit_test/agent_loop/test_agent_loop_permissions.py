from __future__ import annotations

from agent.core.context import ContextBuilder
from agent.core.loop import AgentLoop
from agent.protocols.auth import ActorContext, AuditEvent
from agent.protocols.llm import LLMResponse
from agent.protocols.tool import (
    ToolConfirmationResult,
    ToolExecutionDecision,
    ToolResult,
)
from agent.session import JsonlSessionStore


def test_agent_loop_denied_tool_call_is_persisted_and_not_executed(tmp_path):
    llm = _ToolThenTextLLM()
    tools = _RecordingTools()
    audit = _RecordingAudit()
    loop = AgentLoop(
        llm=llm,
        sessions=JsonlSessionStore(tmp_path / "sessions"),
        context_builder=_ContextBuilder(),
        workspace=tmp_path,
        tools=tools,
        tool_policy=_StaticPolicy("deny"),
        audit_sink=audit,
    )

    result = loop.run_turn("session-1", "hello", actor=_actor())

    assert result == "finished"
    assert tools.calls == []
    state = loop.sessions.load("session-1")
    tool_message = next(message for message in state.messages if message.role == "tool")
    assert tool_message.metadata["code"] == "AUTH_PERMISSION_DENIED"
    assert "tool.call_denied" in [event.action for event in audit.events]


def test_agent_loop_confirmation_approval_executes_tool_and_llm_override_is_turn_local(tmp_path):
    default_llm = _NeverCalledLLM()
    override_llm = _ToolThenTextLLM()
    tools = _RecordingTools()
    broker = _ApproveBroker()
    loop = AgentLoop(
        llm=default_llm,
        sessions=JsonlSessionStore(tmp_path / "sessions"),
        context_builder=_ContextBuilder(),
        workspace=tmp_path,
        tools=tools,
        tool_policy=_StaticPolicy("confirm"),
        confirmation_broker=broker,
    )

    result = loop.run_turn(
        "session-1",
        "hello",
        actor=_actor(),
        llm_override=override_llm,
    )

    assert result == "finished"
    assert default_llm.called is False
    assert tools.calls == [("read_file", {"path": "notes.txt"})]
    assert broker.requests == ["read_file"]


class _ContextBuilder(ContextBuilder):
    def __init__(self):
        pass

    def build(self, *, history, user_message, workspace, session_id):
        return [{"role": "user", "content": user_message.content}]


class _ToolThenTextLLM:
    def __init__(self):
        self.calls = 0

    def chat(self, messages, tools=None):
        self.calls += 1
        if self.calls == 1:
            return LLMResponse(
                content="",
                tool_calls=[
                    {
                        "id": "call-1",
                        "type": "function",
                        "function": {
                            "name": "read_file",
                            "arguments": '{"path":"notes.txt"}',
                        },
                    }
                ],
            )
        return LLMResponse(content="finished")


class _NeverCalledLLM:
    called = False

    def chat(self, messages, tools=None):
        self.called = True
        raise AssertionError("default provider should not be called")


class _RecordingTools:
    def __init__(self):
        self.calls = []

    def definitions(self):
        return [
            {
                "type": "function",
                "function": {
                    "name": "read_file",
                    "description": "read",
                    "parameters": {"type": "object"},
                },
            }
        ]

    def execute(self, name, args):
        self.calls.append((name, args))
        return ToolResult(output="ok")


class _StaticPolicy:
    def __init__(self, action):
        self.action = action

    def decide(self, tool_name, args, context):
        return ToolExecutionDecision(
            action=self.action,
            code="AUTH_PERMISSION_DENIED" if self.action == "deny" else "CONFIRM_REQUIRED",
            message="not allowed" if self.action == "deny" else "confirm",
            permission_key="tool.readonly.use",
            risk_level="high" if self.action == "confirm" else "low",
        )


class _ApproveBroker:
    def __init__(self):
        self.requests = []

    def request(self, decision, context, args, *, on_requested=None, is_cancelled=None):
        self.requests.append(context.tool_name)
        if on_requested:
            on_requested({"confirmation_id": "conf-1"})
        return ToolConfirmationResult(status="approved", confirmation_id="conf-1")


class _RecordingAudit:
    def __init__(self):
        self.events: list[AuditEvent] = []

    def record(self, event):
        self.events.append(event)


def _actor() -> ActorContext:
    return ActorContext(
        actor_type="user",
        user_id="user-1",
        username="tester",
        display_name="Tester",
        role_keys=frozenset({"developer"}),
        permission_keys=frozenset({"tool.readonly.use", "chat.run"}),
        channel="web",
        auth_session_id="auth-1",
    )
