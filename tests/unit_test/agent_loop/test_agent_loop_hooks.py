"""AgentLoop integration tests for Part 12 Tool Hooks."""

from __future__ import annotations

import json

from agent.core.loop import AgentLoop
from agent.hooks.config import HookRegistry, HookSpec
from agent.hooks.runtime import ConfiguredHookRuntime
from agent.message import Message
from agent.protocols.auth import ActorContext
from agent.protocols.hook import PostToolHookResult, PreToolHookResult
from agent.protocols.llm import LLMResponse
from agent.protocols.session import SessionState
from agent.protocols.tool import ToolConfirmationResult, ToolExecutionDecision, ToolResult
from agent.tools.readonly import ReadFileTool
from agent.tools.registry import ToolRegistry


def test_pre_hook_modify_is_revalidated_and_confirmed_with_final_arguments(tmp_path):
    hooks = _Hooks(pre=PreToolHookResult(action="modify", arguments={"path": "after.txt"}))
    policy = _Policy("confirm")
    broker = _Broker()
    tools = _Tools()
    loop = _loop(tmp_path, hooks=hooks, tools=tools, policy=policy, broker=broker)
    events = []

    result = loop.run_turn("default", "read", actor=_actor(), on_event=events.append)

    assert result == "done"
    assert policy.arguments == [{"path": "after.txt"}]
    assert broker.arguments == [{"path": "after.txt"}]
    assert tools.calls == [("read_file", {"path": "after.txt"})]
    runtime_types = [event["type"] for event in events if event.get("protocol_version") == 1]
    assert "tool.waiting_confirmation" in runtime_types
    assert runtime_types.index("tool.waiting_confirmation") < runtime_types.index("tool.completed")


def test_pre_hook_invalid_modified_arguments_fail_before_policy_and_tool(tmp_path):
    hooks = _Hooks(pre=PreToolHookResult(action="modify", arguments={"path": 3}))
    policy = _Policy("allow")
    tools = _Tools()
    sessions = _Sessions()
    loop = _loop(tmp_path, hooks=hooks, tools=tools, policy=policy, sessions=sessions)

    result = loop.run_turn("default", "read", actor=_actor())

    assert result == "done"
    assert policy.arguments == []
    assert tools.calls == []
    tool_message = next(message for message in sessions.appended if message.role == "tool")
    assert json.loads(tool_message.content)["metadata"]["code"] == "INVALID_PARAM"
    assert tool_message.metadata["hook_modified"] is True


def test_pre_hook_block_adds_failure_without_running_tool(tmp_path):
    hooks = _Hooks(
        pre=PreToolHookResult(
            action="block",
            code="BUSINESS_BLOCKED",
            message="blocked by project policy",
        )
    )
    tools = _Tools()
    sessions = _Sessions()
    loop = _loop(tmp_path, hooks=hooks, tools=tools, sessions=sessions)

    loop.run_turn("default", "read", actor=_actor())

    assert tools.calls == []
    tool_message = next(message for message in sessions.appended if message.role == "tool")
    assert json.loads(tool_message.content)["metadata"]["code"] == "BUSINESS_BLOCKED"


def test_post_hook_enriches_event_without_changing_tool_result(tmp_path):
    hooks = _Hooks(
        post=PostToolHookResult(
            display={"title": "文件读取完成", "icon": "search"},
            ui_metadata={"detail_type": "summary", "detail_data": {"items": ["a.txt"]}},
        )
    )
    events = []
    loop = _loop(tmp_path, hooks=hooks, tools=_Tools())

    result = loop.run_turn("default", "read", actor=_actor(), on_event=events.append)

    completed = next(event for event in events if event.get("type") == "tool.completed")
    assert result == "done"
    assert completed["display"]["title"] == "文件读取完成"
    assert completed["ui_metadata"]["detail_type"] == "summary"
    assert completed["status"] == "completed"


def test_pre_hook_modify_cannot_bypass_concrete_tool_workspace_guard(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("private", encoding="utf-8")
    sessions = _Sessions()
    loop = AgentLoop(
        llm=_ToolThenTextLLM(),
        sessions=sessions,
        context_builder=_ContextBuilder(),
        workspace=workspace,
        tools=ToolRegistry([ReadFileTool(workspace)]),
        hook_runtime=_Hooks(
            pre=PreToolHookResult(action="modify", arguments={"path": "../outside.txt"})
        ),
    )

    result = loop.run_turn("default", "read")

    tool_message = next(message for message in sessions.appended if message.role == "tool")
    payload = json.loads(tool_message.content)
    assert result == "done"
    assert payload["metadata"]["code"] == "PATH_OUTSIDE_WORKSPACE"
    assert "private" not in payload["output"]


def test_exempt_admin_permission_still_passes_core_policy_and_confirmation(tmp_path):
    runner = _NeverRunner()
    hook_runtime = ConfiguredHookRuntime(
        HookRegistry(
            (
                HookSpec(
                    name="admin-permission-exempt",
                    stage="pre_tooluse",
                    script=tmp_path / "never-run.py",
                    tools=("read_file",),
                    exempt_permissions=("auth.users.manage",),
                ),
                HookSpec(
                    name="admin-post-permission-exempt",
                    stage="post_tooluse",
                    script=tmp_path / "never-run.py",
                    tools=("read_file",),
                    exempt_permissions=("auth.users.manage",),
                ),
            )
        ),
        runner,
    )
    policy = _Policy("confirm")
    broker = _Broker()
    tools = _Tools()
    loop = _loop(tmp_path, hooks=hook_runtime, tools=tools, policy=policy, broker=broker)

    result = loop.run_turn(
        "default",
        "read",
        actor=_actor(
            role_keys={"admin"},
            permission_keys={"auth.users.manage"},
        ),
    )

    assert result == "done"
    assert policy.arguments == [{"path": "before.txt"}]
    assert broker.arguments == [{"path": "before.txt"}]
    assert tools.calls == [("read_file", {"path": "before.txt"})]
    assert runner.calls == []


def _loop(tmp_path, *, hooks, tools, policy=None, broker=None, sessions=None):
    return AgentLoop(
        llm=_ToolThenTextLLM(),
        sessions=sessions or _Sessions(),
        context_builder=_ContextBuilder(),
        workspace=tmp_path,
        tools=tools,
        tool_policy=policy,
        confirmation_broker=broker,
        hook_runtime=hooks,
    )


class _Hooks:
    def __init__(self, pre=None, post=None):
        self.pre = pre or PreToolHookResult(action="continue")
        self.post = post or PostToolHookResult()

    def run_pre_tooluse(self, _request):
        return self.pre

    def run_post_tooluse(self, _request):
        return self.post


class _NeverRunner:
    def __init__(self):
        self.calls = []

    def run(self, _spec, _payload):
        self.calls.append(_spec.name)
        raise AssertionError("role-exempt Hook must not start its Runner")


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
                        "function": {"name": "read_file", "arguments": '{"path":"before.txt"}'},
                    }
                ],
            )
        return LLMResponse(content="done")


class _ContextBuilder:
    def build(self, history, user_message, workspace, session_id):
        return [{"role": user_message.role, "content": user_message.content}]


class _Sessions:
    def __init__(self):
        self.appended: list[Message] = []

    def load(self, session_id):
        return SessionState(session_id=session_id, messages=[])

    def append(self, session_id, messages):
        self.appended.extend(messages)


class _Tools:
    def __init__(self):
        self.calls = []

    def definitions(self):
        return [
            {
                "type": "function",
                "function": {
                    "name": "read_file",
                    "description": "read",
                    "parameters": {
                        "type": "object",
                        "properties": {"path": {"type": "string"}},
                        "required": ["path"],
                        "additionalProperties": False,
                    },
                },
            }
        ]

    def execute(self, name, arguments):
        self.calls.append((name, dict(arguments)))
        return ToolResult(output="file content", metadata={"path": arguments["path"]})


class _Policy:
    def __init__(self, action):
        self.action = action
        self.arguments = []

    def decide(self, tool_name, arguments, context):
        self.arguments.append(dict(arguments))
        return ToolExecutionDecision(
            action=self.action,
            code="CONFIRM" if self.action == "confirm" else "ALLOWED",
            message="confirm" if self.action == "confirm" else "allowed",
            permission_key="tools.read_file",
            risk_level="high" if self.action == "confirm" else "low",
        )


class _Broker:
    def __init__(self):
        self.arguments = []

    def request(self, decision, context, args, *, on_requested=None, is_cancelled=None):
        self.arguments.append(dict(args))
        if on_requested is not None:
            on_requested({"confirmation_id": "confirmation-1"})
        return ToolConfirmationResult(status="approved", confirmation_id="confirmation-1")


def _actor(
    *,
    role_keys: set[str] | None = None,
    permission_keys: set[str] | None = None,
) -> ActorContext:
    return ActorContext(
        actor_type="user",
        user_id="user-1",
        username="user",
        display_name="User",
        role_keys=frozenset(role_keys or {"user"}),
        permission_keys=frozenset(
            {"tools.read_file"} if permission_keys is None else permission_keys
        ),
        channel="web",
    )
