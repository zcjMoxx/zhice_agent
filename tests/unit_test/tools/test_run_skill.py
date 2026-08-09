"""Tests for contextual ``run_skill`` dispatch and Skill RuntimeEvent linkage."""

from __future__ import annotations

import json

import pytest

from agent.core.event_emitter import RuntimeEventEmitter, callback_runtime_event_sink
from agent.core.loop import (
    CancellationToken,
    _safe_tool_args_preview,
    _safe_tool_output_preview,
)
from agent.protocols.auth import ActorContext
from agent.protocols.skill import SkillResult
from agent.protocols.tool import ToolExecutionContext, ToolResult
from agent.skills.executor import PythonSkillExecutor
from agent.skills.loader import SkillLoader, SkillRoot
from agent.subagents.context import FilteredSkillProvider
from agent.tools.discovery import DiscoverableToolProvider
from agent.tools.filtered import FilteredToolProvider
from agent.tools.registry import ToolRegistry
from agent.tools.scoped import UserScopedToolProvider
from agent.tools.skill import RunSkillTool
from agent.tools.subagent import AugmentedToolProvider


def test_run_skill_requires_context_and_emits_linked_lifecycle(tmp_path):
    loader = _loader(tmp_path)
    tool = RunSkillTool(tmp_path, loader, PythonSkillExecutor())
    direct = tool.execute({"skill": "official/demo", "params": {}})
    emitted = []
    emitter = RuntimeEventEmitter(
        session_id="session-1",
        turn_id="turn-1",
        sink=callback_runtime_event_sink(emitted.append),
    )
    context = ToolExecutionContext(
        actor=_actor(),
        session_id="session-1",
        turn_id="turn-1",
        turn_index=1,
        channel="web",
        tool_name="run_skill",
        tool_call_id="call-1",
        tool_call_record_id="record-1",
        tool_started_event_id="event-tool",
        cancellation_token=CancellationToken(),
        runtime_events=emitter,
    )

    result = ToolRegistry([tool]).execute_with_context(
        "run_skill",
        {"skill": "official/demo", "params": {"value": 7}},
        context,
    )

    assert direct.metadata["code"] == "SKILL_CONTEXT_REQUIRED"
    assert json.loads(result.output)["data"] == {"value": 7}
    assert [event["type"] for event in emitted] == [
        "skill.started",
        "skill.progress",
        "skill.completed",
    ]
    assert {event["parent_event_id"] for event in emitted} == {"event-tool"}
    assert len({event["skill_run_id"] for event in emitted}) == 1


def test_run_skill_rejects_instruction_only_skill(tmp_path):
    skills = tmp_path / "skills"
    root = skills / "demo"
    root.mkdir(parents=True)
    (root / "SKILL.md").write_text(
        "---\nname: demo\ndescription: Demo.\n---\n\nInstructions.\n",
        encoding="utf-8",
    )
    tool = RunSkillTool(tmp_path, SkillLoader([("official", skills)]), PythonSkillExecutor())
    context = ToolExecutionContext(
        actor=_actor(),
        session_id="session-1",
        turn_id="turn-1",
        turn_index=1,
        channel="web",
    )

    result = tool.execute_with_context(
        {"skill": "official/demo", "params": {}},
        context,
    )

    assert result.is_error is True
    assert result.metadata["code"] == "SKILL_NOT_EXECUTABLE"


def test_run_skill_rejects_extra_fields_without_starting_lifecycle(tmp_path):
    emitted = []
    tool = RunSkillTool(tmp_path, _loader(tmp_path), PythonSkillExecutor())
    context = _context(emitted)

    result = tool.execute_with_context(
        {"skill": "official/demo", "params": {}, "timeout_seconds": 900},
        context,
    )

    assert result.metadata["code"] == "INVALID_PARAM"
    assert emitted == []


def test_run_skill_preserves_context_through_scoped_filtered_discovery_and_augmented(
    tmp_path,
):
    actor = _actor()
    files = tmp_path / "files"
    shared = tmp_path / "shared"
    files.mkdir()
    shared.mkdir()
    scoped = UserScopedToolProvider(
        files_dir=files,
        shared_readonly_dir=shared,
        actor=actor,
        skills=_loader(tmp_path).for_actor(actor),
        skill_executor=PythonSkillExecutor(),
    )
    filtered = FilteredToolProvider(scoped, allowed_tools=("run_skill",))
    discovery = DiscoverableToolProvider(filtered)
    provider = AugmentedToolProvider(discovery, ())
    context = ToolExecutionContext(
        actor=actor,
        session_id="session-1",
        turn_id="turn-1",
        turn_index=1,
        channel="web",
        cancellation_token=CancellationToken(),
    )

    inactive = provider.execute_with_context(
        "run_skill", {"skill": "official/demo", "params": {}}, context
    )
    provider.execute("discover_tools", {"query": "", "names": ["run_skill"]})
    result = provider.execute_with_context(
        "run_skill", {"skill": "official/demo", "params": {"value": 9}}, context
    )

    assert inactive.metadata["code"] == "TOOL_NOT_ACTIVATED"
    assert json.loads(result.output)["data"] == {"value": 9}


def test_run_skill_enforces_source_and_subagent_profile_intersection(tmp_path):
    loader = _loader(tmp_path)
    source_restricted = SkillLoader(
        [
            SkillRoot(
                source="official",
                root=tmp_path / "skills",
                allowed_roles=("owner",),
            )
        ]
    ).for_actor(_actor("viewer"))
    source_result = RunSkillTool(
        tmp_path,
        source_restricted,
        PythonSkillExecutor(),
    ).execute_with_context(
        {"skill": "official/demo", "params": {}},
        _context(actor=_actor("viewer")),
    )
    profile_result = RunSkillTool(
        tmp_path,
        FilteredSkillProvider(loader, ()),
        PythonSkillExecutor(),
    ).execute_with_context(
        {"skill": "official/demo", "params": {}},
        _context(),
    )

    assert source_result.metadata["code"] == "UNKNOWN_SKILL"
    assert profile_result.metadata["code"] == "SUBAGENT_SKILL_NOT_ALLOWED"


@pytest.mark.parametrize(
    ("status", "code"),
    [("cancelled", "SKILL_CANCELLED"), ("error", "SKILL_TIMEOUT")],
)
def test_run_skill_emits_failed_for_cancellation_and_timeout(tmp_path, status, code):
    emitted = []
    executor = _StaticExecutor(
        SkillResult(status=status, code=code, data=None, message="stopped")
    )
    tool = RunSkillTool(tmp_path, _loader(tmp_path), executor)

    result = tool.execute_with_context(
        {"skill": "official/demo", "params": {}},
        _context(emitted),
    )

    assert result.metadata["code"] == code
    assert [event["type"] for event in emitted] == ["skill.started", "skill.failed"]


def test_run_skill_executor_exception_closes_failed_lifecycle(tmp_path):
    emitted = []
    tool = RunSkillTool(tmp_path, _loader(tmp_path), _RaisingExecutor())

    result = tool.execute_with_context(
        {"skill": "official/demo", "params": {}},
        _context(emitted),
    )

    assert result.metadata["code"] == "SKILL_EXECUTOR_FAILED"
    assert [event["type"] for event in emitted] == ["skill.started", "skill.failed"]


def test_run_skill_activity_previews_never_include_params_or_result_data():
    secret = "part18-super-secret-value"
    args_preview = _safe_tool_args_preview(
        "run_skill",
        {"skill": "official/demo", "params": {"query": secret}},
    )
    output_preview = _safe_tool_output_preview(
        "run_skill",
        ToolResult(
            output=json.dumps({"data": {"answer": secret}}),
            metadata={"code": "OK", "skill": "official/demo", "duration_ms": 1},
        ),
        limit=160,
    )

    assert secret not in args_preview
    assert secret not in output_preview
    assert "query" in args_preview
    assert "official/demo" in output_preview


def _loader(tmp_path):
    skills = tmp_path / "skills"
    root = skills / "demo"
    (root / "scripts").mkdir(parents=True)
    (root / "scripts" / "main.py").write_text(
        """import argparse, json
p=argparse.ArgumentParser(); p.add_argument('--params'); a=p.parse_args()
print(json.dumps({'type':'progress','message':'working','percent':50}))
print(json.dumps({'type':'result','status':'success','code':'OK','data':json.loads(a.params),'message':'done','error_stack':''}))
""",
        encoding="utf-8",
    )
    (root / "SKILL.md").write_text(
        """---
name: demo
description: Demo.
runtime:
  type: python
  entrypoint: scripts/main.py
  protocol: ndjson-v1
  timeout_seconds: 5
---

Demo.
""",
        encoding="utf-8",
    )
    return SkillLoader([("official", skills)])


def _actor(role="owner"):
    return ActorContext(
        actor_type="user",
        user_id=role,
        username=role,
        display_name=role.title(),
        role_keys=frozenset({role}),
        permission_keys=frozenset(),
        channel="web",
    )


def _context(emitted=None, *, actor=None):
    emitter = None
    if emitted is not None:
        emitter = RuntimeEventEmitter(
            session_id="session-1",
            turn_id="turn-1",
            sink=callback_runtime_event_sink(emitted.append),
        )
    return ToolExecutionContext(
        actor=actor or _actor(),
        session_id="session-1",
        turn_id="turn-1",
        turn_index=1,
        channel="web",
        tool_name="run_skill",
        tool_call_id="call-1",
        tool_call_record_id="record-1",
        tool_started_event_id="event-tool",
        cancellation_token=CancellationToken(),
        runtime_events=emitter,
    )


class _StaticExecutor:
    def __init__(self, result):
        self.result = result

    def run(self, request, skill, *, progress_sink=None):
        del request, skill, progress_sink
        return self.result


class _RaisingExecutor:
    def run(self, request, skill, *, progress_sink=None):
        del request, skill, progress_sink
        raise RuntimeError("private executor detail")
