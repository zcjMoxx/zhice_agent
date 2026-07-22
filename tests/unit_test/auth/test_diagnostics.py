from __future__ import annotations

import json
from datetime import datetime

from agent.auth.activity import SqliteRuntimeActivitySink
from agent.auth.diagnostics import RecentActivityDiagnostics
from agent.auth.store import SQLiteAuthStore
from agent.protocols.activity import RuntimeActivityEvent
from agent.protocols.auth import ActorContext
from agent.protocols.diagnostics import DiagnosticContext
from agent.tools.diagnostics import DiagnoseRecentActivityTool


def test_diagnostics_tool_hides_subagent_cause_from_ordinary_user(tmp_path):
    actor = ActorContext(
        actor_type="user",
        user_id="user-1",
        username="member",
        display_name="Member",
        role_keys=frozenset({"viewer"}),
        permission_keys=frozenset(),
        channel="web",
    )
    tool = DiagnoseRecentActivityTool(
        tmp_path,
        actor=actor,
        diagnostics=_SubagentDiagnostics(),
        context=DiagnosticContext(session_id="session-a", current_turn_id="turn-2"),
    )

    result = tool.execute({"focus": "failure"})
    payload = json.loads(result.output)

    assert payload["cause_code"] == ""
    assert payload["evidence"] == []
    assert payload["summary"] == (
        "Subagent is temporarily unavailable. Please contact an administrator."
    )
    assert "SUBAGENT_PROMPT_NOT_FOUND" not in result.output


def test_diagnostics_tool_keeps_subagent_cause_for_owner(tmp_path):
    actor = ActorContext(
        actor_type="user",
        user_id="owner-1",
        username="owner",
        display_name="Owner",
        role_keys=frozenset({"owner"}),
        permission_keys=frozenset(),
        channel="web",
    )
    tool = DiagnoseRecentActivityTool(
        tmp_path,
        actor=actor,
        diagnostics=_SubagentDiagnostics(),
        context=DiagnosticContext(session_id="session-a", current_turn_id="turn-2"),
    )

    result = tool.execute({"focus": "failure"})

    assert json.loads(result.output)["cause_code"] == "SUBAGENT_PROMPT_NOT_FOUND"


class _SubagentDiagnostics:
    def diagnose(self, actor, context, options):
        del actor, context, options
        return {
            "status": "diagnosed",
            "focus": "failure",
            "summary": "Required Subagent runtime prompt is missing: subagent.md",
            "failure_stage": "subagent.startup",
            "cause_code": "SUBAGENT_PROMPT_NOT_FOUND",
            "confirmed_facts": ["missing_prompt=subagent.md"],
            "probable_cause": "Prompt missing",
            "confidence": "high",
            "evidence": [{"missing_prompt": "subagent.md"}],
            "next_actions": ["Run zcagent init"],
            "limitations": [],
        }


def test_diagnostics_auto_selects_previous_completed_turn_in_current_session(tmp_path):
    store, actor = _store_and_actor(tmp_path)
    activity = SqliteRuntimeActivitySink(store)
    _record_turn(activity, actor, "session-a", "turn-previous", duration_ms=12500)
    activity.record(
        RuntimeActivityEvent(
            action="chat.turn_started",
            actor=actor,
            session_id="session-a",
            turn_id="turn-diagnostic",
            request_id="req-diagnostic",
            channel="web",
            metadata={"turn_index": 2},
        )
    )
    _write_trace(
        tmp_path,
        [
            {
                "event": "llm.done",
                "actor_user_id": actor.user_id,
                "session_id": "session-a",
                "turn_id": "turn-previous",
                "request_id": "req-turn-previous",
                "duration_ms": 10000,
            },
            {
                "event": "llm.call",
                "actor_user_id": actor.user_id,
                "session_id": "session-a",
                "turn_id": "turn-diagnostic",
                "request_id": "req-diagnostic",
            },
        ],
    )

    report = RecentActivityDiagnostics(store, tmp_path / "logs").diagnose(
        actor,
        DiagnosticContext(
            session_id="session-a",
            current_turn_id="turn-diagnostic",
            current_request_id="req-diagnostic",
            channel="web",
        ),
        {"focus": "latency"},
    )

    assert report["status"] == "diagnosed"
    assert report["target"]["turn_id"] == "turn-previous"
    assert report["target"]["request_id"] == "req-turn-previous"
    assert report["cause_code"] == "LLM_PRIMARY_LATENCY"
    assert "llm_ms=10000" in report["confirmed_facts"]


def test_diagnostics_reports_latest_tool_failure_without_internal_ids(tmp_path):
    store, actor = _store_and_actor(tmp_path)
    activity = SqliteRuntimeActivitySink(store)
    activity.record(
        RuntimeActivityEvent(
            action="chat.turn_started",
            actor=actor,
            session_id="session-a",
            turn_id="turn-failed",
            request_id="req-failed",
            channel="web",
            metadata={"turn_index": 1},
        )
    )
    activity.record(
        RuntimeActivityEvent(
            action="tool.call_requested",
            actor=actor,
            resource_id="call-1",
            session_id="session-a",
            turn_id="turn-failed",
            request_id="req-failed",
            channel="web",
            tool_call_record_id="tool-record-1",
            metadata={"tool_name": "exec", "timeout_seconds": 30},
        )
    )
    activity.record(
        RuntimeActivityEvent(
            action="tool.call_error",
            actor=actor,
            resource_id="call-1",
            session_id="session-a",
            turn_id="turn-failed",
            request_id="req-failed",
            channel="web",
            tool_call_record_id="tool-record-1",
            decision="error",
            reason_code="COMMAND_TIMEOUT",
            metadata={
                "tool_name": "exec",
                "duration_ms": 30000,
                "timeout_seconds": 30,
                "stderr_tail": "test_stop did not finish",
            },
        )
    )
    activity.record(
        RuntimeActivityEvent(
            action="chat.turn_done",
            actor=actor,
            session_id="session-a",
            turn_id="turn-failed",
            request_id="req-failed",
            channel="web",
            decision="done",
            metadata={"duration_ms": 31000},
        )
    )

    report = RecentActivityDiagnostics(store, tmp_path / "logs").diagnose(
        actor,
        DiagnosticContext(session_id="session-a", current_turn_id="turn-diagnostic"),
        {"focus": "failure"},
    )

    assert report["failure_stage"] == "tool.exec"
    assert report["cause_code"] == "COMMAND_TIMEOUT"
    assert report["confidence"] == "high"
    assert report["evidence"][0]["stderr_tail"] == "test_stop did not finish"


def test_diagnostics_never_crosses_actor_or_session_scope(tmp_path):
    store, actor = _store_and_actor(tmp_path)
    other = store.create_user("other", "Other", "other-password", role_keys=["viewer"])
    other_actor = store.actor_for_user(other.id, channel="web")
    store.session_index_create(session_id="other-session", owner_user_id=other.id, channel="web")
    activity = SqliteRuntimeActivitySink(store)
    _record_turn(activity, other_actor, "other-session", "other-turn", duration_ms=5000)

    report = RecentActivityDiagnostics(store, tmp_path / "logs").diagnose(
        actor,
        DiagnosticContext(session_id="session-a", current_turn_id="turn-diagnostic"),
        {},
    )

    assert report["status"] == "insufficient_evidence"
    assert "other-turn" not in json.dumps(report)


def test_diagnostics_follows_parent_turn_to_child_terminal_failure(tmp_path):
    store, actor = _store_and_actor(tmp_path)
    activity = SqliteRuntimeActivitySink(store)
    _record_delegate_failure(activity, actor, "turn-parent")
    _write_trace(
        tmp_path,
        [
            {
                "event": "subagent.task_failed",
                "actor_user_id": actor.user_id,
                "session_id": "child-session",
                "turn_id": "child-turn",
                "root_session_id": "session-a",
                "root_turn_id": "turn-parent",
                "parent_session_id": "session-a",
                "parent_turn_id": "turn-parent",
                "batch_id": "batch-1",
                "task_id": "task-1",
                "subagent_id": "subagent-1",
                "profile": "developer",
                "workspace_mode": "worktree",
                "status": "failed",
                "stage": "workspace",
                "code": "SUBAGENT_WORKTREE_FAILED",
                "error_type": "TypeError",
                "error_message": (
                    "SubagentContextBuilder.build() got an unexpected keyword argument "
                    "'context_budget'; Authorization: Bearer private-token"
                ),
                "secret_prompt": "must not be exposed",
            }
        ],
    )

    report = RecentActivityDiagnostics(store, tmp_path / "logs").diagnose(
        actor,
        DiagnosticContext(session_id="session-a", current_turn_id="turn-diagnostic"),
        {"focus": "failure"},
    )

    assert report["cause_code"] == "SUBAGENT_WORKTREE_FAILED"
    assert report["failure_stage"] == "subagent.workspace"
    assert report["confidence"] == "high"
    assert "child_failure_count=1" in report["confirmed_facts"]
    assert "common_child_failure_count=1" in report["confirmed_facts"]
    assert "child_stage=workspace" in report["confirmed_facts"]
    assert report["evidence"][0]["root_turn_id"] == "turn-parent"
    assert report["evidence"][0]["task_id"] == "task-1"
    assert "secret_prompt" not in report["evidence"][0]
    assert report["trace_events"][0]["error_type"] == "TypeError"
    assert "unexpected keyword argument 'context_budget'" in report["trace_events"][0][
        "error_message"
    ]
    assert "private-token" not in json.dumps(report)
    assert "Analyze the chronological trace_events" in report["diagnostic_instruction"]


def test_generic_subagent_failure_without_child_terminal_is_not_high_confidence(tmp_path):
    store, actor = _store_and_actor(tmp_path)
    activity = SqliteRuntimeActivitySink(store)
    _record_delegate_failure(activity, actor, "turn-parent")

    report = RecentActivityDiagnostics(store, tmp_path / "logs").diagnose(
        actor,
        DiagnosticContext(session_id="session-a", current_turn_id="turn-diagnostic"),
        {"focus": "failure"},
    )

    assert report["cause_code"] == "SUBAGENT_FAILED"
    assert report["failure_stage"] == "tool.delegate_tasks"
    assert report["confidence"] == "medium"
    assert "no correlated child terminal failure" in report["limitations"][0]


def test_child_trace_correlation_keeps_actor_boundary(tmp_path):
    store, actor = _store_and_actor(tmp_path)
    other = store.create_user("other", "Other", "other-password", role_keys=["viewer"])
    activity = SqliteRuntimeActivitySink(store)
    _record_delegate_failure(activity, actor, "turn-parent")
    _write_trace(
        tmp_path,
        [
            {
                "event": "subagent.task_failed",
                "actor_user_id": other.id,
                "session_id": "other-child-session",
                "turn_id": "other-child-turn",
                "root_session_id": "session-a",
                "root_turn_id": "turn-parent",
                "status": "failed",
                "stage": "llm",
                "code": "OTHER_ACTOR_SECRET_FAILURE",
            }
        ],
    )

    report = RecentActivityDiagnostics(store, tmp_path / "logs").diagnose(
        actor,
        DiagnosticContext(session_id="session-a", current_turn_id="turn-diagnostic"),
        {"focus": "failure"},
    )

    assert report["cause_code"] == "SUBAGENT_FAILED"
    assert "OTHER_ACTOR_SECRET_FAILURE" not in json.dumps(report)


def _store_and_actor(tmp_path):
    store = SQLiteAuthStore(tmp_path / "state" / "auth.sqlite3")
    user = store.initialize_owner("owner", "Owner", "password-123")
    store.session_index_create(session_id="session-a", owner_user_id=user.id, channel="web")
    return store, store.actor_for_user(user.id, channel="web")


def _record_turn(activity, actor, session_id, turn_id, *, duration_ms):
    activity.record(
        RuntimeActivityEvent(
            action="chat.turn_started",
            actor=actor,
            session_id=session_id,
            turn_id=turn_id,
            request_id=f"req-{turn_id}",
            channel="web",
            metadata={"turn_index": 1},
        )
    )
    activity.record(
        RuntimeActivityEvent(
            action="chat.turn_done",
            actor=actor,
            session_id=session_id,
            turn_id=turn_id,
            request_id=f"req-{turn_id}",
            channel="web",
            decision="done",
            metadata={"duration_ms": duration_ms},
        )
    )


def _record_delegate_failure(activity, actor, turn_id):
    request_id = f"req-{turn_id}"
    activity.record(
        RuntimeActivityEvent(
            action="chat.turn_started",
            actor=actor,
            session_id="session-a",
            turn_id=turn_id,
            request_id=request_id,
            channel="web",
            metadata={"turn_index": 1},
        )
    )
    activity.record(
        RuntimeActivityEvent(
            action="tool.call_requested",
            actor=actor,
            resource_id="delegate-call",
            session_id="session-a",
            turn_id=turn_id,
            request_id=request_id,
            channel="web",
            tool_call_record_id=f"tool-{turn_id}",
            metadata={"tool_name": "delegate_tasks"},
        )
    )
    activity.record(
        RuntimeActivityEvent(
            action="tool.call_error",
            actor=actor,
            resource_id="delegate-call",
            session_id="session-a",
            turn_id=turn_id,
            request_id=request_id,
            channel="web",
            tool_call_record_id=f"tool-{turn_id}",
            decision="error",
            reason_code="SUBAGENT_FAILED",
            metadata={"tool_name": "delegate_tasks", "duration_ms": 250},
        )
    )
    activity.record(
        RuntimeActivityEvent(
            action="chat.turn_done",
            actor=actor,
            session_id="session-a",
            turn_id=turn_id,
            request_id=request_id,
            channel="web",
            decision="done",
            metadata={"duration_ms": 300},
        )
    )
def _write_trace(tmp_path, events):
    path = tmp_path / "logs" / datetime.now().strftime("%Y-%m-%d") / "trace.log"
    path.parent.mkdir(parents=True, exist_ok=True)
    normalized = []
    for event in events:
        normalized.append(
            {
                "ts": datetime.now().astimezone().isoformat(),
                "level": "INFO",
                "component": "agent",
                **event,
            }
        )
    path.write_text(
        "\n".join(json.dumps(event) for event in normalized) + "\n",
        encoding="utf-8",
    )
