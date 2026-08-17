from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from agent.app.gateway import create_app
from agent.applications.travel.config import TravelConfigurationError, load_travel_config
from agent.applications.travel.schemas import TravelPlanV1
from agent.applications.travel.service import TravelApplicationError
from agent.config import AppConfig
from tests.unit_test.travel.fixtures import plan_payload


def test_travel_config_accepts_legacy_fields_without_exposing_fake_controls(tmp_path, caplog):
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    assert load_travel_config(config_dir).enabled is False
    config_dir.joinpath("config.yml").write_text(
        """
schema_version: 1
travel:
  enabled: true
  default_mode: deep
  max_search_results: 6
  max_evidence_items: 35
  deep_subagent_count: 3
  xhs_readonly_enabled: true
  max_plan_bytes: 524288
""",
        encoding="utf-8",
    )

    config = load_travel_config(config_dir)

    assert config.enabled is True
    assert config.max_evidence_items == 35
    assert config.max_plan_bytes == 524288
    assert not hasattr(config, "default_mode")
    assert "Ignoring deprecated travel configuration fields" in caplog.text


def test_travel_config_rejects_invalid_legacy_and_unknown_fields(tmp_path):
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    config_dir.joinpath("config.yml").write_text(
        "schema_version: 1\ntravel:\n  enabled: true\n  deep_subagent_count: 4\n",
        encoding="utf-8",
    )
    with pytest.raises(TravelConfigurationError):
        load_travel_config(config_dir)

    config_dir.joinpath("config.yml").write_text(
        "schema_version: 1\ntravel:\n  enabled: true\n  imaginary_switch: true\n",
        encoding="utf-8",
    )
    with pytest.raises(TravelConfigurationError, match="unknown fields"):
        load_travel_config(config_dir)


def test_travel_api_lists_reads_deletes_and_maps_safe_errors(tmp_path):
    service = FakeTravelService()
    client = _client(tmp_path, service)

    listed = client.get("/api/travel/plans")
    read = client.get("/api/travel/plans/travel-plan-one")
    deleted = client.delete("/api/travel/plans/travel-plan-one")

    assert listed.status_code == 200
    assert listed.json()["plans"][0]["plan_id"] == "travel-plan-one"
    assert read.status_code == 200
    assert read.json()["plan"]["request"]["origin"] == "重庆"
    assert deleted.json() == {"plan_id": "travel-plan-one", "status": "deleted"}
    assert service.deleted == ["travel-plan-one"]

    missing = client.get("/api/travel/plans/missing")
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "TRAVEL_PLAN_NOT_FOUND"


def test_travel_requirement_extraction_api_returns_review_draft_without_starting_plan(tmp_path):
    service = FakeTravelService()
    extractor = FakeRequirementExtractor()
    client = _client(tmp_path, service, extractor=extractor)

    response = client.post(
        "/api/travel/requirements/extract",
        json={"text": "两个大学生从重庆去大理"},
    )

    assert response.status_code == 200
    assert response.json()["draft"]["origin"] == "重庆"
    assert response.json()["draft"]["intent"] == "travel_requirement"
    assert response.json()["missing_fields"] == ["开始日期", "结束日期", "旅行基调"]
    assert extractor.calls == ["两个大学生从重庆去大理"]


def test_travel_generation_api_returns_actor_scoped_recovery_projection(tmp_path):
    service = FakeTravelService()
    client = _client(tmp_path, service, generation={
        "status": "running",
        "session_id": "session-travel",
        "turn_id": "turn-travel",
        "plan_id": "",
        "error_code": "",
    })

    response = client.get("/api/travel/generation?session_id=session-travel")

    assert response.status_code == 200
    assert response.json() == {
        "status": "running",
        "session_id": "session-travel",
        "turn_id": "turn-travel",
        "plan_id": "",
        "error_code": "",
    }


def test_travel_progress_api_returns_bounded_safe_history(tmp_path):
    client = _client(
        tmp_path,
        FakeTravelService(),
        progress_reader=lambda actor, session_id: {
            "session_id": session_id,
            "items": [{
                "id": "history-complete",
                "stage": "complete",
                "title": "旅行计划已完成",
                "detail": "完整行程已保存",
                "status": "done",
            }],
        },
    )

    response = client.get("/api/travel/sessions/session-travel/progress")

    assert response.status_code == 200
    assert response.json()["session_id"] == "session-travel"
    assert response.json()["items"][0]["id"] == "history-complete"


def test_travel_conversation_api_persists_bounded_messages_in_the_travel_session(tmp_path):
    calls = []
    client = _client(
        tmp_path,
        FakeTravelService(),
        conversation_writer=lambda actor, session_id, messages: calls.append(
            (actor.user_id, session_id, messages)
        ) or {"session_id": session_id, "message_count": len(messages), "status": "saved"},
    )

    response = client.post(
        "/api/travel/sessions/session-travel/conversation",
        json={"messages": [
            {"role": "user", "content": "重庆出发去大理"},
            {"role": "assistant", "content": "准备几号出发？"},
        ]},
    )

    assert response.status_code == 200
    assert response.json() == {
        "session_id": "session-travel",
        "message_count": 2,
        "status": "saved",
    }
    assert calls[0][1:] == (
        "session-travel",
        [
            {"role": "user", "content": "重庆出发去大理"},
            {"role": "assistant", "content": "准备几号出发？"},
        ],
    )


def test_travel_conversation_api_rejects_empty_and_oversized_collections(tmp_path):
    client = _client(tmp_path, FakeTravelService(), conversation_writer=lambda *_: None)

    empty = client.post(
        "/api/travel/sessions/session-travel/conversation",
        json={"messages": []},
    )
    oversized = client.post(
        "/api/travel/sessions/session-travel/conversation",
        json={"messages": [{"role": "user", "content": "x"}] * 21},
    )

    assert empty.status_code == 400
    assert oversized.status_code == 400


def test_travel_planning_confirmation_api_uses_actor_owned_runtime_transition(tmp_path):
    calls = []
    client = _client(
        tmp_path,
        FakeTravelService(),
        planning_confirmer=lambda actor, session_id, draft: calls.append(
            (actor.user_id, session_id, draft)
        )
        or {"session_id": session_id, "phase": "planning", "status": "confirmed"},
    )

    response = client.post(
        "/api/travel/sessions/session-travel/confirm-planning",
        json={"draft": FakeRequirementExtractor().extract("").to_dict()},
    )

    assert response.status_code == 200
    assert response.json() == {
        "session_id": "session-travel",
        "phase": "planning",
        "status": "confirmed",
    }
    assert calls[0][1] == "session-travel"


class FakeRequirementExtractor:
    def __init__(self):
        self.calls = []

    def extract(self, text):
        self.calls.append(text)
        return SimpleNamespace(
            to_dict=lambda: {
                "intent": "travel_requirement",
                "intent_topic": "",
                "origin": "重庆",
                "destinations": ["大理"],
                "start_date": "",
                "end_date": "",
                "traveller_type": "大学生",
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
        )


class FakeTravelService:
    def __init__(self):
        self.deleted = []
        self.plan = TravelPlanV1.from_dict(plan_payload()).with_identity(
            plan_id="travel-plan-one", owner_user_id="user-a"
        )

    def list_plans(self, actor, *, limit=50):
        del actor, limit
        return [
            SimpleNamespace(
                to_dict=lambda: {
                    "plan_id": "travel-plan-one",
                    "owner_user_id": "user-a",
                    "source_session_id": "session-a",
                    "source_turn_id": "turn-a",
                    "schema_version": "1",
                    "title": "重庆到大理",
                    "destination_summary": "大理",
                    "created_at": "2026-09-28T08:00:00Z",
                    "updated_at": "2026-09-28T08:00:00Z",
                }
            )
        ]

    def get_plan(self, actor, plan_id):
        del actor
        if plan_id != "travel-plan-one":
            raise TravelApplicationError(
                "TRAVEL_PLAN_NOT_FOUND", "Travel plan was not found.", status_code=404
            )
        return self.plan

    def delete_plan(self, actor, plan_id):
        del actor
        self.deleted.append(plan_id)


def _client(
    tmp_path,
    service,
    *,
    extractor=None,
    generation=None,
    conversation_writer=None,
    planning_confirmer=None,
    progress_reader=None,
):
    static = tmp_path / "static"
    static.mkdir()
    static.joinpath("index.html").write_text("travel", encoding="utf-8")
    runtime = SimpleNamespace(
        auth=None,
        travel_service=service,
        travel_requirement_extractor=extractor,
        travel_generation_status=lambda actor, session_id="": generation
        or {"status": "idle", "session_id": "", "turn_id": "", "plan_id": "", "error_code": ""},
        persist_travel_conversation=conversation_writer,
        confirm_travel_planning=planning_confirmer,
        travel_progress_history=progress_reader,
        startup=lambda: None,
        shutdown=lambda: None,
        capability_statuses=lambda: {},
        current_model_label=lambda: "fake/model",
    )
    return TestClient(create_app(config=_config(tmp_path), runtime=runtime, static_dir=static))


def _config(tmp_path):
    return AppConfig(
        workspace=tmp_path,
        config_dir=tmp_path / "config",
        prompts_dir=tmp_path / "prompts",
        contexts_dir=tmp_path / "contexts",
        sessions_dir=tmp_path / "contexts" / "sessions",
        extends_dir=tmp_path / "extends",
        logs_dir=tmp_path / "logs",
    )
