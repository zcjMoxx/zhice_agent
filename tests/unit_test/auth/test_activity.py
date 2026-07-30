from __future__ import annotations

import sqlite3

from agent.auth.activity import SqliteRuntimeActivitySink
from agent.auth.audit import SqliteAuditSink
from agent.auth.store import SQLiteAuthStore
from agent.protocols.activity import RuntimeActivityEvent
from agent.protocols.auth import AuditEvent


def test_runtime_activity_updates_turn_and_tool_indexes_without_audit_rows(tmp_path):
    store = SQLiteAuthStore(tmp_path / "auth.sqlite3")
    user = store.initialize_owner("owner", "Owner", "password-123")
    actor = store.actor_for_user(user.id, channel="web")
    activity = SqliteRuntimeActivitySink(store)

    activity.record(
        RuntimeActivityEvent(
            action="chat.turn_started",
            actor=actor,
            session_id="session-a",
            turn_id="turn-a",
            request_id="req-a",
            channel="web",
            metadata={"turn_index": 1},
        )
    )
    activity.record(
        RuntimeActivityEvent(
            action="tool.call_requested",
            actor=actor,
            resource_id="call-a",
            session_id="session-a",
            turn_id="turn-a",
            request_id="req-a",
            channel="web",
            tool_call_record_id="tool-record-a",
            metadata={
                "tool_name": "read_file",
                "args_preview": "notes.txt",
                "command_preview": "OPENAI_API_KEY=sk-testsecret123456",
            },
        )
    )
    activity.record(
        RuntimeActivityEvent(
            action="tool.call_done",
            actor=actor,
            resource_id="call-a",
            session_id="session-a",
            turn_id="turn-a",
            request_id="req-a",
            channel="web",
            tool_call_record_id="tool-record-a",
            decision="done",
            metadata={"tool_name": "read_file", "duration_ms": 25},
        )
    )
    activity.record(
        RuntimeActivityEvent(
            action="chat.turn_done",
            actor=actor,
            session_id="session-a",
            turn_id="turn-a",
            request_id="req-a",
            channel="web",
            decision="done",
            metadata={"duration_ms": 120},
        )
    )

    turn = store.list_turn_runs(actor_user_id=user.id, session_id="session-a")[0]
    tool = store.list_tool_call_records(actor_user_id=user.id, turn_id="turn-a")[0]
    assert turn["status"] == "done"
    assert turn["duration_ms"] == 120
    assert tool["tool_name"] == "read_file"
    assert tool["duration_seconds"] == 0.025
    assert "testsecret" not in tool["command_preview"]
    assert store.list_audit_events(limit=20) == []

    with sqlite3.connect(tmp_path / "auth.sqlite3") as connection:
        columns = connection.execute("PRAGMA table_info(turn_runs)").fetchall()
    column_names = [column[1] for column in columns]
    primary_keys = [column[1] for column in columns if column[5] == 1]
    assert "id" not in column_names
    assert primary_keys == ["turn_id"]


def test_security_audit_no_longer_mutates_runtime_indexes(tmp_path):
    store = SQLiteAuthStore(tmp_path / "auth.sqlite3")
    user = store.initialize_owner("owner", "Owner", "password-123")
    actor = store.actor_for_user(user.id, channel="web")

    SqliteAuditSink(store).record(
        AuditEvent(
            action="chat.turn_started",
            resource_type="turn",
            actor=actor,
            session_id="session-a",
            turn_id="turn-a",
        )
    )

    assert store.list_turn_runs(actor_user_id=user.id, session_id="session-a") == []
    assert store.list_audit_events(limit=20)[0]["action"] == "chat.turn_started"


def test_startup_recovery_finishes_only_interrupted_turns(tmp_path):
    store = SQLiteAuthStore(tmp_path / "auth.sqlite3")
    user = store.initialize_owner("owner", "Owner", "password-123")
    actor = store.actor_for_user(user.id, channel="web")
    activity = SqliteRuntimeActivitySink(store)
    for turn_id in ("interrupted", "completed"):
        activity.record(
            RuntimeActivityEvent(
                action="chat.turn_started",
                actor=actor,
                session_id="session-a",
                turn_id=turn_id,
                channel="web",
            )
        )
    activity.record(
        RuntimeActivityEvent(
            action="chat.turn_done",
            actor=actor,
            session_id="session-a",
            turn_id="completed",
            channel="web",
        )
    )

    assert store.recover_interrupted_turn_runs() == 1
    turns = {
        item["turn_id"]: item
        for item in store.list_turn_runs(actor_user_id=user.id, session_id="session-a")
    }
    assert turns["interrupted"]["status"] == "error"
    assert turns["interrupted"]["error_code"] == "GATEWAY_RESTART_INTERRUPTED"
    assert turns["interrupted"]["finished_at"]
    assert turns["completed"]["status"] == "done"
    assert store.recover_interrupted_turn_runs() == 0
