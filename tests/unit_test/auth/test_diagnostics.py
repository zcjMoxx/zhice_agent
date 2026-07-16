from __future__ import annotations

import json
from datetime import datetime

from agent.auth.activity import SqliteRuntimeActivitySink
from agent.auth.diagnostics import RecentActivityDiagnostics
from agent.auth.store import SQLiteAuthStore
from agent.protocols.activity import RuntimeActivityEvent
from agent.protocols.diagnostics import DiagnosticContext


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
