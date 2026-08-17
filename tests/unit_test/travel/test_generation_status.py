from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from agent.app.runtime import ActiveTurn, WebRuntime
from agent.applications.travel.service import TravelApplicationError
from agent.config import AppConfig
from agent.core.loop import CancellationToken
from agent.message import Message
from agent.protocols.auth import ActorContext


def test_generation_status_reports_owned_active_travel_turn(tmp_path):
    store = _Store()
    runtime = _runtime(tmp_path, store)
    actor = _actor()
    runtime._register_turn(("user-a", "travel-a"), ActiveTurn("turn-a", CancellationToken()))

    status = runtime.travel_generation_status(actor, session_id="travel-a")

    assert status == {
        "status": "running",
        "session_id": "travel-a",
        "turn_id": "turn-a",
        "plan_id": "",
        "error_code": "",
    }


def test_generation_status_exposes_persisted_route_error_while_repair_is_running(tmp_path):
    runtime = _runtime(tmp_path, _Store())
    state = SimpleNamespace(
        messages=[
            Message(
                role="tool",
                content="route required",
                name="finalize_travel_plan",
                metadata={"is_error": True, "code": "TRAVEL_ROUTE_EVIDENCE_MISSING"},
            )
        ]
    )
    runtime.session_access = SimpleNamespace(
        resolve_session=lambda actor, session_id, write=True: SimpleNamespace(
            owner_user_id=actor.user_id,
            channel="travel",
            store=SimpleNamespace(load=lambda target: state),
        )
    )
    runtime.travel_service = SimpleNamespace()
    runtime._register_turn(
        ("user-a", "travel-a"), ActiveTurn("turn-route-repair", CancellationToken())
    )

    status = runtime.travel_generation_status(_actor(), session_id="travel-a")

    assert status == {
        "status": "running",
        "session_id": "travel-a",
        "turn_id": "turn-route-repair",
        "plan_id": "",
        "error_code": "TRAVEL_ROUTE_EVIDENCE_MISSING",
    }


def test_generation_status_recovers_completed_plan_and_terminal_failure(tmp_path):
    store = _Store(turns={"travel-failed": [{"turn_id": "turn-f", "status": "error", "error_code": "LLM_TIMEOUT"}]})
    runtime = _runtime(tmp_path, store)
    runtime.travel_service = SimpleNamespace(
        list_plans=lambda actor, limit=50: [
            SimpleNamespace(plan_id="plan-a", source_session_id="travel-a", source_turn_id="turn-a")
        ]
    )

    completed = runtime.travel_generation_status(_actor(), session_id="travel-a")
    failed = runtime.travel_generation_status(_actor(), session_id="travel-failed")

    assert completed["status"] == "completed"
    assert completed["plan_id"] == "plan-a"
    assert failed["status"] == "failed"
    assert failed["error_code"] == "LLM_TIMEOUT"


def test_generation_status_treats_a_done_turn_without_a_plan_as_failed(tmp_path):
    store = _Store(turns={"travel-a": [{"turn_id": "turn-a", "status": "success", "error_code": ""}]})
    runtime = _runtime(tmp_path, store)

    status = runtime.travel_generation_status(_actor(), session_id="travel-a")

    assert status["status"] == "failed"
    assert status["error_code"] == "TRAVEL_PLAN_NOT_FINALIZED"


def test_generation_status_recovers_persisted_finalizer_error_for_selected_review(tmp_path):
    runtime = _runtime(tmp_path, _Store())
    state = SimpleNamespace(
        messages=[
            Message(
                role="tool",
                content="forecast required",
                name="finalize_travel_plan",
                metadata={"is_error": True, "code": "TRAVEL_WEATHER_FORECAST_REQUIRED"},
            )
        ]
    )
    runtime.session_access = SimpleNamespace(
        resolve_session=lambda actor, session_id, write=True: SimpleNamespace(
            owner_user_id=actor.user_id,
            channel="travel",
            store=SimpleNamespace(load=lambda target: state),
        )
    )
    runtime.travel_service = SimpleNamespace(
        list_plans=lambda actor, limit=50: [],
        get_candidate_review=lambda actor, session_id: SimpleNamespace(
            status="selected",
            turn_id="turn-candidates",
        ),
    )

    status = runtime.travel_generation_status(_actor(), session_id="travel-a")

    assert status["status"] == "failed"
    assert status["error_code"] == "TRAVEL_WEATHER_FORECAST_REQUIRED"


def test_generation_status_rejects_other_user_and_non_travel_sessions(tmp_path):
    runtime = _runtime(tmp_path, _Store())

    with pytest.raises(TravelApplicationError) as exc_info:
        runtime.travel_generation_status(_actor(), session_id="web-a")

    assert exc_info.value.code == "TRAVEL_GENERATION_NOT_FOUND"
    assert exc_info.value.status_code == 404


def test_generation_status_does_not_treat_a_recent_collecting_draft_as_running(tmp_path):
    store = _Store()
    store.rows[0]["updated_at"] = datetime.now(UTC).isoformat()
    runtime = _runtime(tmp_path, store)

    status = runtime.travel_generation_status(_actor())

    assert status["status"] == "idle"
    assert status["session_id"] == ""


def _runtime(tmp_path, store):
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
        auth=SimpleNamespace(store=store),
        session_access=SimpleNamespace(),
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


class _Store:
    def __init__(self, *, turns=None):
        self.turns = turns or {}
        self.rows = [
            {"session_id": "travel-a", "owner_user_id": "user-a", "channel": "travel", "updated_at": "2026-01-01T00:00:00+00:00"},
            {"session_id": "travel-failed", "owner_user_id": "user-a", "channel": "travel", "updated_at": "2026-01-01T00:00:00+00:00"},
            {"session_id": "web-a", "owner_user_id": "user-a", "channel": "web", "updated_at": "2026-01-01T00:00:00+00:00"},
        ]

    def session_index_list(self, owner_user_id):
        return [row for row in self.rows if row["owner_user_id"] == owner_user_id]

    def list_turn_runs(self, *, actor_user_id, session_id="", limit=100):
        del actor_user_id, limit
        return self.turns.get(session_id, [])
