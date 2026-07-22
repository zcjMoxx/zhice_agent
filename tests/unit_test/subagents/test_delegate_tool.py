from __future__ import annotations

import json

from agent.core.context import ContextBuilder
from agent.core.loop import AgentLoop
from agent.prompt_loader import PromptLoader
from agent.protocols.auth import ActorContext
from agent.protocols.capability import CapabilityStatus
from agent.protocols.llm import LLMResponse
from agent.protocols.subagent import SubagentTaskResult
from agent.protocols.tool import ToolExecutionContext
from agent.session import JsonlSessionStore
from agent.tools.subagent import (
    AugmentedToolProvider,
    DelegateTasksTool,
    UnavailableDelegateTasksTool,
)


class _BaseProvider:
    def definitions(self):
        return []

    def execute(self, name, args):
        raise AssertionError((name, args))


class _Coordinator:
    def __init__(self):
        self.context = None

    def run_batch(self, request, context):
        self.context = context
        return (
            SubagentTaskResult(
                task_id=request.tasks[0].task_id,
                status="completed",
                code="OK",
                output="checked",
                subagent_id="subagent-1",
                child_session_id="child-1",
                child_turn_id="turn-child-1",
                duration_ms=10,
            ),
        )


class _SequenceLLM:
    def __init__(self):
        self.calls = 0

    def chat(self, messages, tools=None):
        del messages, tools
        self.calls += 1
        if self.calls == 1:
            return LLMResponse(
                content="",
                tool_calls=[
                    {
                        "id": "call-delegate",
                        "type": "function",
                        "function": {
                            "name": "delegate_tasks",
                            "arguments": json.dumps(
                                {
                                    "reason": "explicit_user_request",
                                    "tasks": [
                                        {
                                            "id": "review",
                                            "task": "review code",
                                            "profile": "explorer",
                                        }
                                    ],
                                }
                            ),
                        },
                    }
                ],
            )
        return LLMResponse(content="final synthesis")


class _FailingCoordinator:
    def run_batch(self, request, context):
        del context
        return tuple(
            SubagentTaskResult(
                task_id=task.task_id,
                status="failed",
                code="SUBAGENT_PROMPT_NOT_FOUND",
                output="Required Subagent runtime prompt is missing: subagent.md",
                subagent_id=f"subagent-{task.task_id}",
                child_session_id=f"child-{task.task_id}",
                child_turn_id=f"turn-{task.task_id}",
                duration_ms=1,
                stage="context",
            )
            for task in request.tasks
        )


def _write_prompts(root):
    root.mkdir()
    for name in ("identity", "tool_use_policy", "skills_intro"):
        (root / f"{name}.md").write_text(name, encoding="utf-8")


def test_agent_loop_dispatches_delegate_tool_with_trusted_parent_context(tmp_path):
    prompts = tmp_path / "prompts"
    _write_prompts(prompts)
    coordinator = _Coordinator()
    provider = AugmentedToolProvider(
        _BaseProvider(),
        (DelegateTasksTool(coordinator, profile_summaries=(("explorer", "inspect"),)),),
    )
    sessions = JsonlSessionStore(tmp_path / "sessions")
    loop = AgentLoop(
        _SequenceLLM(),
        sessions,
        ContextBuilder(PromptLoader(prompts)),
        tmp_path,
        tools=provider,
    )
    actor = ActorContext(
        actor_type="local_operator",
        user_id=None,
        username="owner",
        display_name="Owner",
        role_keys=frozenset({"owner"}),
        permission_keys=frozenset(),
        channel="cli",
    )

    output = loop.run_turn("parent-session", "use a child", turn_id="parent-turn", actor=actor)

    assert output == "final synthesis"
    assert isinstance(coordinator.context, ToolExecutionContext)
    assert coordinator.context.root_session_id == "parent-session"
    assert coordinator.context.root_turn_id == "parent-turn"
    assert coordinator.context.tool_started_event_id.startswith("event-")


def test_delegate_tool_keeps_common_child_terminal_code_in_parent_metadata():
    tool = DelegateTasksTool(_FailingCoordinator())
    result = tool.execute_with_context(
        {
            "reason": "parallel_independent",
            "tasks": [
                {"id": "one", "task": "inspect one", "profile": "explorer"},
                {"id": "two", "task": "inspect two", "profile": "explorer"},
            ],
        },
        ToolExecutionContext(
            actor=ActorContext(
                actor_type="local_operator",
                user_id=None,
                username="owner",
                display_name="Owner",
                role_keys=frozenset({"owner"}),
                permission_keys=frozenset(),
                channel="cli",
            ),
            session_id="s",
            turn_id="t",
            turn_index=1,
            channel="cli",
        ),
    )

    assert result.is_error is True
    assert result.metadata["code"] == "SUBAGENT_PROMPT_NOT_FOUND"
    payload = json.loads(result.output)
    assert {item["stage"] for item in payload["results"]} == {"context"}


def test_unavailable_delegate_facade_returns_cause_without_creating_children():
    tool = UnavailableDelegateTasksTool(
        CapabilityStatus(
            name="subagent",
            state="unavailable",
            code="SUBAGENT_PROMPT_NOT_FOUND",
            message="Required Subagent runtime prompt is missing: subagent.md",
            hint="Run zcagent init, then restart the process.",
        )
    )

    result = tool.execute({"reason": "explicit_user_request", "tasks": []})

    assert result.is_error is True
    assert result.metadata["code"] == "SUBAGENT_PROMPT_NOT_FOUND"
    assert json.loads(result.output) == {
        "code": "SUBAGENT_RUNTIME_UNAVAILABLE",
        "cause_code": "SUBAGENT_PROMPT_NOT_FOUND",
        "message": "Required Subagent runtime prompt is missing: subagent.md",
        "hint": "Run zcagent init, then restart the process.",
    }


def test_unavailable_delegate_facade_hides_cause_from_ordinary_actor():
    tool = UnavailableDelegateTasksTool(
        CapabilityStatus(
            name="subagent",
            state="unavailable",
            code="SUBAGENT_PROMPT_NOT_FOUND",
            message="Required Subagent runtime prompt is missing: subagent.md",
            hint="Run zcagent init, then restart the process.",
        )
    )
    actor = ActorContext(
        actor_type="user",
        user_id="user-1",
        username="member",
        display_name="Member",
        role_keys=frozenset({"viewer"}),
        permission_keys=frozenset(),
        channel="web",
    )

    result = tool.execute_with_context(
        {"reason": "explicit_user_request", "tasks": []},
        ToolExecutionContext(
            actor=actor,
            session_id="session-1",
            turn_id="turn-1",
            turn_index=1,
            channel="web",
        ),
    )

    assert result.is_error is True
    assert result.metadata["code"] == "SUBAGENT_UNAVAILABLE"
    assert result.output == (
        "Subagent is temporarily unavailable. Please contact an administrator."
    )
    assert "subagent.md" not in result.output
