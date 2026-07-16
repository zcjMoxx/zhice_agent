from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pytest

from agent.auth.activity import SqliteRuntimeActivitySink
from agent.auth.confirmation import SQLiteToolConfirmationBroker
from agent.auth.store import AuthStoreError, SQLiteAuthStore
from agent.protocols.activity import RuntimeActivityEvent
from agent.protocols.tool import ToolExecutionContext, ToolExecutionDecision


def test_web_confirmation_blocks_until_same_actor_approves(tmp_path):
    store = SQLiteAuthStore(tmp_path / "auth.sqlite3")
    admin = store.initialize_owner("admin", "Admin", "password-123")
    actor = store.actor_for_user(admin.id, channel="web")
    context = _context(actor)
    SqliteRuntimeActivitySink(store).record(
        RuntimeActivityEvent(
            action="tool.call_requested",
            actor=actor,
            resource_id=context.tool_call_id,
            session_id=context.session_id,
            turn_id=context.turn_id,
            tool_call_record_id=context.tool_call_record_id,
            metadata={"tool_name": "exec", "command_preview": "pip install demo"},
        )
    )
    broker = SQLiteToolConfirmationBroker(store, timeout_seconds=5)
    requested = []
    requested_event = Event()

    def on_requested(payload):
        requested.append(payload)
        requested_event.set()

    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(
            broker.request,
            ToolExecutionDecision(
                action="confirm",
                code="NETWORK_CONFIRMATION_REQUIRED",
                message="confirm",
                permission_key="tool.exec.dangerous",
                risk_level="high",
                risk_category="network",
            ),
            context,
            {"command": "pip install demo"},
            on_requested=on_requested,
        )
        assert requested_event.wait(timeout=2)
        status = broker.decide(actor, requested[0]["confirmation_id"], True)
        result = future.result(timeout=2)

    assert status == "approved"
    assert result.status == "approved"
    assert store.get_tool_confirmation(result.confirmation_id)["status"] == "approved"


def test_other_user_cannot_decide_confirmation(tmp_path):
    store = SQLiteAuthStore(tmp_path / "auth.sqlite3")
    store.initialize_owner("admin", "Admin", "password-123")
    first = store.create_user("first", "First", "first-password", role_keys=["developer"])
    second = store.create_user("second", "Second", "second-password", role_keys=["developer"])
    first_actor = store.actor_for_user(first.id, channel="web")
    second_actor = store.actor_for_user(second.id, channel="web")
    context = _context(first_actor)
    SqliteRuntimeActivitySink(store).record(
        RuntimeActivityEvent(
            action="tool.call_requested",
            actor=first_actor,
            resource_id=context.tool_call_id,
            session_id=context.session_id,
            turn_id=context.turn_id,
            tool_call_record_id=context.tool_call_record_id,
            metadata={"tool_name": "exec"},
        )
    )
    store.create_tool_confirmation(
        confirmation_id="conf-1",
        tool_call_record_id=context.tool_call_record_id,
        actor_user_id=first.id,
        session_id=context.session_id,
        turn_id=context.turn_id,
        tool_name="exec",
        risk_level="high",
        command_preview="pip install demo",
        args_hash="hash",
        expires_at="2999-01-01T00:00:00+00:00",
    )

    with pytest.raises(AuthStoreError, match="not found"):
        store.decide_tool_confirmation(
            "conf-1",
            decision_actor_user_id=second_actor.user_id,
            approved=True,
        )


def _context(actor):
    return ToolExecutionContext(
        actor=actor,
        session_id="session-a",
        turn_id="turn-a",
        turn_index=1,
        channel="web",
        tool_name="exec",
        tool_call_id="call-a",
        tool_call_record_id="tool-record-a",
    )
