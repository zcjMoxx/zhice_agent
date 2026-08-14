from __future__ import annotations

from types import SimpleNamespace

import pytest

from agent.app.runtime import WebRuntime
from agent.applications.travel.service import TravelApplicationError
from agent.config import AppConfig
from agent.protocols.auth import ActorContext
from agent.protocols.session import SessionState


def test_runtime_persists_requirement_conversation_and_retries_idempotently(tmp_path):
    access = _SessionAccess(channel="travel")
    runtime = _runtime(tmp_path, access)
    messages = [
        {"role": "user", "content": "重庆出发去大理"},
        {"role": "assistant", "content": "哪天出发？"},
    ]

    saved = runtime.persist_travel_conversation(_actor(), "travel-a", messages)
    unchanged = runtime.persist_travel_conversation(_actor(), "travel-a", messages)

    assert saved == {"session_id": "travel-a", "message_count": 2, "status": "saved"}
    assert unchanged["status"] == "unchanged"
    assert [message.role for message in access.store.messages] == ["user", "assistant"]
    assert all(message.metadata["travel_visibility"] == "conversation" for message in access.store.messages)
    assert access.refreshes == 1


def test_runtime_updates_collecting_draft_and_restores_structured_state(tmp_path):
    access = _SessionAccess(channel="travel")
    runtime = _runtime(tmp_path, access)
    first = [{"role": "user", "content": "重庆出发去大理"}]
    updated = [*first, {"role": "assistant", "content": "几号出发？"}]
    draft = _draft()

    runtime.persist_travel_conversation(_actor(), "travel-a", first)
    result = runtime.persist_travel_conversation(
        _actor(), "travel-a", updated, draft=draft
    )
    restored = runtime.travel_draft(_actor(), "travel-a")

    assert result["status"] == "updated"
    assert restored["messages"] == updated
    assert restored["draft"]["origin"] == "重庆"
    assert restored["phase"] == "intake"
    assert access.store.metadata["title"] == "重庆 → 大理"


def test_runtime_confirms_complete_draft_and_rejects_incomplete_state(tmp_path):
    access = _SessionAccess(channel="travel")
    runtime = _runtime(tmp_path, access)

    confirmed = runtime.confirm_travel_planning(_actor(), "travel-a", _draft())

    assert confirmed == {
        "session_id": "travel-a",
        "phase": "planning",
        "status": "confirmed",
    }
    assert access.store.metadata["travel_phase"] == "planning"
    assert access.store.metadata["travel_draft"]["destinations"] == ["大理"]

    incomplete = {**_draft(), "traveller_count": None}
    with pytest.raises(TravelApplicationError) as captured:
        runtime.confirm_travel_planning(_actor(), "travel-b", incomplete)
    assert captured.value.code == "TRAVEL_REQUIREMENTS_INCOMPLETE"


def test_runtime_lists_collecting_and_failed_travel_work_items(tmp_path):
    access = _SessionAccess(channel="travel")
    runtime = _runtime(tmp_path, access)
    runtime.persist_travel_conversation(
        _actor(), "travel-a", [{"role": "user", "content": "重庆去大理"}], draft=_draft()
    )

    collecting = runtime.list_travel_work_items(_actor())
    access.store.update_metadata("travel-a", {"travel_phase": "planning"})
    access.turns = [{"session_id": "travel-a", "turn_id": "turn-a", "status": "error", "error_code": "LLM_TIMEOUT"}]
    failed = runtime.list_travel_work_items(_actor())

    assert collecting[0]["status"] == "collecting"
    assert collecting[0]["title"] == "重庆 → 大理"
    assert failed[0]["status"] == "failed"
    assert failed[0]["error_code"] == "LLM_TIMEOUT"


def test_completed_intake_agent_turn_stays_collecting_in_the_work_list(tmp_path):
    access = _SessionAccess(channel="travel")
    runtime = _runtime(tmp_path, access)
    runtime.persist_travel_conversation(
        _actor(), "travel-a", [{"role": "user", "content": "你是谁"}]
    )
    access.store.update_metadata("travel-a", {"travel_phase": "intake"})
    access.turns = [{"session_id": "travel-a", "turn_id": "turn-a", "status": "success"}]

    item = runtime.list_travel_work_items(_actor())[0]

    assert item["status"] == "collecting"
    assert item["error_code"] == ""


def test_runtime_rejects_non_travel_conflict_and_invalid_messages(tmp_path):
    non_travel = _runtime(tmp_path, _SessionAccess(channel="web"))
    with pytest.raises(TravelApplicationError) as missing:
        non_travel.persist_travel_conversation(
            _actor(), "web-a", [{"role": "user", "content": "hello"}]
        )
    assert missing.value.code == "TRAVEL_GENERATION_NOT_FOUND"

    access = _SessionAccess(channel="travel")
    runtime = _runtime(tmp_path, access)
    with pytest.raises(TravelApplicationError) as invalid:
        runtime.persist_travel_conversation(
            _actor(), "travel-a", [{"role": "tool", "content": "hidden"}]
        )
    assert invalid.value.code == "TRAVEL_CONVERSATION_INVALID"

    runtime.persist_travel_conversation(
        _actor(), "travel-a", [{"role": "user", "content": "first"}]
    )
    access.turns = [{"turn_id": "turn-a", "status": "error"}]
    with pytest.raises(TravelApplicationError) as conflict:
        runtime.persist_travel_conversation(
            _actor(), "travel-a", [{"role": "user", "content": "replacement"}]
        )
    assert conflict.value.code == "TRAVEL_CONVERSATION_CONFLICT"


def test_runtime_deletes_plan_and_associated_travel_session(tmp_path):
    access = _SessionAccess(channel="travel")
    runtime = _runtime(tmp_path, access)
    runtime.travel_service = SimpleNamespace(
        delete_plan=lambda actor, plan_id: "travel-a"
    )

    runtime.delete_travel_plan(_actor(), "plan-a")

    assert access.deleted == ["travel-a"]


def test_runtime_does_not_delete_a_non_travel_session_returned_by_a_bad_plan_link(tmp_path):
    access = _SessionAccess(channel="web")
    runtime = _runtime(tmp_path, access)
    runtime.travel_service = SimpleNamespace(
        delete_plan=lambda actor, plan_id: "web-a"
    )

    runtime.delete_travel_plan(_actor(), "plan-a")

    assert access.deleted == []


def _runtime(tmp_path, session_access):
    return WebRuntime(
        config=AppConfig(
            workspace=tmp_path,
            config_dir=tmp_path / "config",
            prompts_dir=tmp_path / "prompts",
            contexts_dir=tmp_path / "contexts",
            sessions_dir=tmp_path / "contexts" / "sessions",
            extends_dir=tmp_path / "extends",
            logs_dir=tmp_path / "logs",
        ),
        sessions=SimpleNamespace(),
        agent_loop=SimpleNamespace(),
        llm=SimpleNamespace(),
        auth=SimpleNamespace(store=session_access),
        session_access=session_access,
    )


def _actor():
    return ActorContext(
        actor_type="user",
        user_id="user-a",
        username="user-a",
        display_name="User A",
        role_keys=frozenset({"viewer"}),
        permission_keys=frozenset(),
        channel="rest",
    )


class _MessageStore:
    def __init__(self):
        self.messages = []
        self.metadata = {}

    def load(self, session_id):
        return SessionState(
            session_id=session_id,
            messages=list(self.messages),
            metadata=dict(self.metadata),
        )

    def append(self, session_id, messages):
        del session_id
        self.messages.extend(messages)

    def replace(self, session_id, messages):
        del session_id
        self.messages = list(messages)

    def update_metadata(self, session_id, values):
        del session_id
        self.metadata.update(values)


class _SessionAccess:
    def __init__(self, *, channel):
        self.channel = channel
        self.store = _MessageStore()
        self.refreshes = 0
        self.deleted = []
        self.turns = []
        self.owner_user_id = "user-a"

    def resolve_session(self, actor, session_id, *, write=False, delete=False):
        del actor, write, delete
        return SimpleNamespace(
            session_id=session_id,
            owner_user_id=self.owner_user_id,
            channel=self.channel,
            store=self.store,
        )

    def refresh_index(self, actor, session_id):
        del actor, session_id
        self.refreshes += 1

    def delete_session(self, actor, session_id):
        del actor
        self.deleted.append(session_id)

    def list_turn_runs(self, *, actor_user_id, session_id="", limit=1):
        del actor_user_id, session_id, limit
        return list(self.turns)

    def session_index_list(self, owner_user_id):
        if owner_user_id != "user-a":
            return []
        return [{
            "session_id": "travel-a",
            "owner_user_id": "user-a",
            "channel": self.channel,
            "title": self.store.metadata.get("title", ""),
            "preview": "重庆去大理",
            "updated_at": "2026-08-13T00:00:00Z",
        }]


def _draft():
    return {
        "intent": "travel_requirement",
        "intent_topic": "",
        "origin": "重庆",
        "destinations": ["大理"],
        "start_date": "2026-10-01",
        "end_date": "2026-10-03",
        "traveller_type": "",
        "traveller_count": 2,
        "budget_total_cny": None,
        "budget_level": "",
        "transport_preferences": [],
        "stay_preferences": [],
        "interest_tags": [],
        "pace": "",
        "planning_mode": "",
        "hard_constraints": [],
    }
