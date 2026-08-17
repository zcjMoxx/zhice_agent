from __future__ import annotations

import json

from agent.core.loop import CancellationToken
from agent.prompt_loader import PromptLoader
from agent.protocols.auth import ActorContext
from agent.protocols.llm import ContextBudget, LLMResponse
from agent.protocols.subagent import SubagentProfile, SubagentTask
from agent.protocols.tool import ToolExecutionContext, ToolResult
from agent.session import JsonlSessionStore
from agent.subagents.factory import ChildAgentFactory, ChildRunIdentity


class _AnswerLLM:
    def __init__(self):
        self.calls = 0

    def chat(self, messages, tools=None):
        self.calls += 1
        assert messages[0]["role"] == "system"
        names = [item["function"]["name"] for item in tools or []]
        if self.calls == 1:
            assert "child task" in messages[-1]["content"]
            assert names == ["discover_tools"]
            return LLMResponse(
                content="",
                tool_calls=[
                    {
                        "id": "call-discover",
                        "type": "function",
                        "function": {
                            "name": "discover_tools",
                            "arguments": json.dumps(
                                {"query": "read file", "names": ["read_file"]}
                            ),
                        },
                    }
                ],
            )
        assert names == ["read_file"]
        return LLMResponse(content="child result")


class _PreactivatedAnswerLLM:
    def chat(self, messages, tools=None):
        assert messages[0]["role"] == "system"
        assert [item["function"]["name"] for item in tools or []] == [
            "read_file",
        ]
        return LLMResponse(content="preactivated child result")


class _Tools:
    def definitions(self):
        return [
            {
                "type": "function",
                "function": {
                    "name": "read_file",
                    "description": "read",
                    "parameters": {"type": "object"},
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "exec",
                    "description": "exec",
                    "parameters": {"type": "object"},
                },
            },
        ]

    def execute(self, name, args):
        return ToolResult(f"{name}:{args}")


def _actor():
    return ActorContext(
        actor_type="local_operator",
        user_id=None,
        username="owner",
        display_name="Owner",
        role_keys=frozenset({"owner"}),
        permission_keys=frozenset(),
        channel="cli",
    )


def test_child_factory_uses_independent_loop_session_and_runtime_scope(tmp_path):
    prompts = tmp_path / "prompts"
    prompts.mkdir()
    (prompts / "subagent.md").write_text("child identity", encoding="utf-8")
    parent_tools = _Tools()
    events = []
    factory = ChildAgentFactory(
        prompt_loader=PromptLoader(prompts),
        sessions_root=tmp_path / "sessions",
        parent_tools=parent_tools,
        llm_factory=lambda profile: _AnswerLLM(),
        context_budget=ContextBudget(
            input_token_limit=114688,
            endpoint_names=("cpa_one",),
        ),
        tool_provider_factory=(
            lambda workspace, profile, context, on_event, identity, skills: _Tools()
        ),
    )
    context = ToolExecutionContext(
        actor=_actor(),
        session_id="parent-session",
        turn_id="parent-turn",
        turn_index=1,
        channel="cli",
        root_session_id="parent-session",
        root_turn_id="parent-turn",
    )
    identity = ChildRunIdentity(
        batch_id="batch-1",
        task_id="task-1",
        subagent_id="subagent-1",
        child_session_id="child-1",
        child_turn_id="child-turn-1",
    )
    profile = SubagentProfile(
        name="explorer",
        description="inspect",
        tools=("read_file",),
    )

    output = factory.run_child(
        SubagentTask("task-1", "child task", "explorer"),
        profile,
        context,
        identity,
        tmp_path,
        cancellation_token=CancellationToken(),
        on_event=events.append,
    )

    assert output == "child result"
    child_store = JsonlSessionStore(tmp_path / "sessions" / "_subagents" / "parent-session")
    child_state = child_store.load("child-1")
    assert len(child_state.messages) == 4
    assert all(message.parent_turn_id == "parent-turn" for message in child_state.messages)
    assert JsonlSessionStore(tmp_path / "sessions").list_sessions() == []
    runtime_events = [event for event in events if event.get("protocol_version") == 1]
    assert runtime_events
    assert all(event["agent_id"] == "subagent-1" for event in runtime_events)
    assert all(event["task_id"] == "task-1" for event in runtime_events)
    assert all(event["depth"] == 1 for event in runtime_events)


def test_child_factory_preactivates_only_profile_initial_tools(tmp_path):
    prompts = tmp_path / "prompts"
    prompts.mkdir()
    (prompts / "subagent.md").write_text("child identity", encoding="utf-8")
    factory = ChildAgentFactory(
        prompt_loader=PromptLoader(prompts),
        sessions_root=tmp_path / "sessions",
        parent_tools=_Tools(),
        llm_factory=lambda profile: _PreactivatedAnswerLLM(),
        tool_provider_factory=(
            lambda workspace, profile, context, on_event, identity, skills: _Tools()
        ),
    )
    context = ToolExecutionContext(
        actor=_actor(),
        session_id="parent-session",
        turn_id="parent-turn",
        turn_index=1,
        channel="cli",
        root_session_id="parent-session",
        root_turn_id="parent-turn",
    )
    identity = ChildRunIdentity(
        batch_id="batch-1",
        task_id="task-1",
        subagent_id="subagent-1",
        child_session_id="child-1",
        child_turn_id="child-turn-1",
    )
    profile = SubagentProfile(
        name="explorer",
        description="inspect",
        tools=("read_file", "exec"),
        initial_tools=("read_file",),
        denied_tools=("exec", "delegate_tasks"),
    )

    output = factory.run_child(
        SubagentTask("task-1", "child task", "explorer"),
        profile,
        context,
        identity,
        tmp_path,
        cancellation_token=CancellationToken(),
    )

    assert output == "preactivated child result"
