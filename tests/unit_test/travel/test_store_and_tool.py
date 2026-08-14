from __future__ import annotations

import json

import pytest

from agent.applications.travel.config import TravelConfig
from agent.applications.travel.schemas import TravelPlanV1
from agent.applications.travel.service import TravelApplicationError, TravelApplicationService
from agent.applications.travel.store import TravelPlanStore, TravelPlanStoreError
from agent.auth.user_context import FilesystemUserContextResolver
from agent.protocols.auth import ActorContext
from agent.protocols.tool import ToolExecutionContext, ToolResult
from agent.session import JsonlSessionStore
from tests.unit_test.travel.fixtures import plan_payload


class Events:
    def __init__(self):
        self.items = []

    def emit(self, event_type, **kwargs):
        self.items.append((event_type, kwargs))


def test_store_persists_session_turn_lists_deletes_and_isolates_users(tmp_path):
    store_a = TravelPlanStore(tmp_path / "user-a")
    store_b = TravelPlanStore(tmp_path / "user-b")
    plan = TravelPlanV1.from_dict(plan_payload()).with_identity(
        plan_id="travel-plan-one", owner_user_id="user-a"
    )

    store_a.save(
        plan,
        owner_user_id="user-a",
        source_session_id="session-a",
        source_turn_id="turn-a",
        title="重庆到大理",
    )

    summary = store_a.list("user-a")[0]
    assert (summary.source_session_id, summary.source_turn_id) == ("session-a", "turn-a")
    assert store_a.get("user-a", "travel-plan-one").data["owner_user_id"] == "user-a"
    with pytest.raises(TravelPlanStoreError) as captured:
        store_b.get("user-b", "travel-plan-one")
    assert captured.value.code == "TRAVEL_PLAN_NOT_FOUND"
    assert store_a.delete("user-a", "travel-plan-one") == "session-a"
    assert store_a.list("user-a") == []


def test_finalize_tool_overwrites_forged_owner_emits_ready_and_returns_view_url(tmp_path):
    resolver = FilesystemUserContextResolver(tmp_path / "contexts", workspace_dir=tmp_path)
    service = TravelApplicationService(TravelConfig(enabled=True), resolver)
    actor = _actor("user-a")
    tool = service.tools_for_actor(actor)[0]
    events = Events()
    context = ToolExecutionContext(
        actor=actor,
        session_id="session-a",
        turn_id="turn-a",
        turn_index=1,
        channel="web",
        tool_call_id="call-1",
        tool_call_record_id="record-1",
        runtime_events=events,
    )

    result = tool.execute_with_context({"plan": plan_payload()}, context)

    assert not result.is_error
    payload = json.loads(result.output)
    assert payload["plan_id"].startswith("travel-plan-")
    assert payload["view_url"] == f"/travel?plan={payload['plan_id']}"
    saved = service.get_plan(actor, payload["plan_id"])
    assert saved.data["owner_user_id"] == "user-a"
    assert events.items[0][0] == "travel.plan_ready"
    assert events.items[0][1]["metadata"]["plan_id"] == payload["plan_id"]


def test_travel_finalize_rejects_when_configured_sources_were_not_queried(tmp_path):
    resolver = FilesystemUserContextResolver(tmp_path / "contexts", workspace_dir=tmp_path)
    service = TravelApplicationService(TravelConfig(enabled=True), resolver)
    actor = _actor("user-a")
    tool = service.tools_for_actor(actor)[0]
    service.source_ledger.register_expected(
        "session-travel",
        ["mcp__amap__search", "mcp__open-meteo__get_forecast"],
    )

    result = tool.execute_with_context(
        {"plan": plan_payload()},
        ToolExecutionContext(
            actor=actor,
            session_id="session-travel",
            turn_id="turn-a",
            turn_index=1,
            channel="travel",
        ),
    )

    assert result.is_error
    assert result.metadata["code"] == "TRAVEL_RESEARCH_INCOMPLETE"
    assert service.list_plans(actor) == []


def test_travel_finalize_requires_external_evidence_after_successful_source_calls(tmp_path):
    resolver = FilesystemUserContextResolver(tmp_path / "contexts", workspace_dir=tmp_path)
    service = TravelApplicationService(TravelConfig(enabled=True), resolver)
    actor = _actor("user-a")
    tool = service.tools_for_actor(actor)[0]
    service.source_ledger.register_expected("session-travel", ["mcp__amap__search"])
    service.source_ledger.observe(
        "session-travel",
        "mcp__amap__search",
        ToolResult(output='{"pois":[{"name":"大理古城"}]}', metadata={"code": "MCP_OK"}),
    )
    plan = plan_payload()
    plan["evidence"] = [
        {**plan["evidence"][0], "source_type": "model_estimate", "source_url": "", "freshness": "estimate"}
    ]
    for day in plan["days"]:
        for activity in day["activities"]:
            activity["evidence_ids"] = []
        for segment in day["route_segments"]:
            segment["evidence_ids"] = []

    result = tool.execute_with_context(
        {"plan": plan},
        ToolExecutionContext(
            actor=actor,
            session_id="session-travel",
            turn_id="turn-a",
            turn_index=1,
            channel="travel",
        ),
    )

    assert result.is_error
    assert result.metadata["code"] == "TRAVEL_EVIDENCE_INSUFFICIENT"


def test_travel_finalize_succeeds_after_all_available_sources_are_attempted(tmp_path):
    resolver = FilesystemUserContextResolver(tmp_path / "contexts", workspace_dir=tmp_path)
    service = TravelApplicationService(TravelConfig(enabled=True), resolver)
    actor = _actor("user-a")
    tool = service.tools_for_actor(actor)[0]
    tools = [
        "mcp__amap__search",
        "mcp__open-meteo__get_forecast",
        "mcp__12306__get-tickets",
        "mcp__tavily__tavily_search",
        "mcp__xhs-readonly__search_notes",
    ]
    service.source_ledger.register_expected("session-travel", tools)
    for name in tools:
        output = '{"status":"success","code":"OK"}'
        if "tavily" in name:
            output = '{"status":"success","results":[{"title":"大理古城"}]}'
        elif "xhs" in name:
            output = '{"status":"success","data":{"text":"{\\"feeds\\":[{\\"id\\":\\"1\\"}]}"}}'
        service.source_ledger.observe(
            "session-travel",
            name,
            ToolResult(output=output, metadata={"code": "MCP_OK"}),
        )
    plan = plan_payload()
    candidate = _candidate_from_plan("balanced", plan, recommended=True)
    service.save_candidate_review(
        actor,
        session_id="session-travel",
        turn_id="turn-a",
        candidates=[candidate, _candidate_summary("alternative")],
        recommended_candidate_id="balanced",
    )
    service.select_candidate(actor, "session-travel", "balanced")

    result = tool.execute_with_context(
        {"plan": plan, "selected_candidate_id": "balanced"},
        ToolExecutionContext(
            actor=actor,
            session_id="session-travel",
            turn_id="turn-a",
            turn_index=1,
            channel="travel",
        ),
    )

    assert not result.is_error
    assert service.source_ledger.snapshot("session-travel").expected == frozenset()


def test_travel_finalize_requires_candidate_review_before_saving(tmp_path):
    resolver = FilesystemUserContextResolver(tmp_path / "contexts", workspace_dir=tmp_path)
    service = TravelApplicationService(TravelConfig(enabled=True), resolver)
    actor = _actor("user-a")
    tools = ["mcp__amap__search", "mcp__open-meteo__get_forecast"]
    service.source_ledger.register_expected("session-travel", tools)
    for name in tools:
        service.source_ledger.observe(
            "session-travel",
            name,
            ToolResult(output='{"status":"success","code":"OK"}', metadata={"code": "MCP_OK"}),
        )
    result = service.tools_for_actor(actor)[0].execute_with_context(
        {"plan": plan_payload()},
        ToolExecutionContext(
            actor=actor,
            session_id="session-travel",
            turn_id="turn-a",
            turn_index=1,
            channel="travel",
        ),
    )

    assert result.is_error
    assert result.metadata["code"] == "TRAVEL_CANDIDATE_SELECTION_REQUIRED"


def test_clarification_tool_emits_bounded_questions_for_the_current_turn(tmp_path):
    resolver = FilesystemUserContextResolver(tmp_path / "contexts", workspace_dir=tmp_path)
    service = TravelApplicationService(TravelConfig(enabled=True), resolver)
    actor = _actor("user-a")
    tool = service.tools_for_actor(actor)[1]
    events = Events()
    context = ToolExecutionContext(
        actor=actor,
        session_id="session-a",
        turn_id="turn-a",
        turn_index=1,
        channel="web",
        tool_call_id="call-clarify",
        runtime_events=events,
    )

    result = tool.execute_with_context(
        {"questions": ["请确认预算档位？", "是否必须乘坐火车？"]}, context
    )

    assert not result.is_error
    assert result.metadata["code"] == "TRAVEL_CLARIFICATION_REQUIRED"
    assert events.items[0][0] == "travel.clarification_required"
    assert events.items[0][1]["ui_metadata"]["detail_data"]["questions"] == [
        "请确认预算档位？",
        "是否必须乘坐火车？",
    ]


def test_intake_tool_merges_validated_draft_and_emits_refresh_safe_state(tmp_path):
    resolver = FilesystemUserContextResolver(tmp_path / "contexts", workspace_dir=tmp_path)
    service = TravelApplicationService(TravelConfig(enabled=True), resolver)
    sessions = JsonlSessionStore(tmp_path / "sessions")
    sessions.update_metadata("travel-a", {"travel_phase": "intake"})
    update, _handoff = service.intake_tools_for_actor(_actor("user-a"), sessions)
    events = Events()

    first = update.execute_with_context(
        {"patch": {"origin": "重庆", "destinations": ["大理"]}},
        ToolExecutionContext(
            actor=_actor("user-a"),
            session_id="travel-a",
            turn_id="turn-a",
            turn_index=1,
            channel="travel",
            runtime_events=events,
        ),
    )
    second = update.execute_with_context(
        {
            "patch": {
                "start_date": "2026-10-01",
                "end_date": "2026-10-03",
                "traveller_count": 2,
            }
        },
        ToolExecutionContext(
            actor=_actor("user-a"),
            session_id="travel-a",
            turn_id="turn-b",
            turn_index=2,
            channel="travel",
            runtime_events=events,
        ),
    )

    assert json.loads(first.output)["missing_fields"] == ["开始日期", "结束日期", "人数"]
    assert json.loads(second.output)["ready"] is True
    state = sessions.load("travel-a")
    assert state.metadata["travel_draft"]["origin"] == "重庆"
    assert state.metadata["travel_draft"]["traveller_count"] == 2
    assert state.metadata["travel_intake_turn_ids"] == ["turn-a", "turn-b"]
    assert events.items[-1][0] == "travel.intake_draft_updated"


def test_intake_handoff_preserves_question_without_answering_or_opening_capabilities(tmp_path):
    resolver = FilesystemUserContextResolver(tmp_path / "contexts", workspace_dir=tmp_path)
    service = TravelApplicationService(TravelConfig(enabled=True), resolver)
    sessions = JsonlSessionStore(tmp_path / "sessions")
    sessions.update_metadata("travel-a", {"travel_phase": "intake"})
    _update, handoff = service.intake_tools_for_actor(_actor("user-a"), sessions)
    events = Events()

    result = handoff.execute_with_context(
        {"question": "帮我写一段 Python 代码", "topic": "编程"},
        ToolExecutionContext(
            actor=_actor("user-a"),
            session_id="travel-a",
            turn_id="turn-a",
            turn_index=1,
            channel="travel",
            runtime_events=events,
        ),
    )

    assert not result.is_error
    assert json.loads(result.output) == {
        "status": "handoff_offered",
        "code": "OK",
        "topic": "编程",
    }
    event_type, payload = events.items[0]
    assert event_type == "travel.main_chat_handoff"
    assert payload["ui_metadata"]["detail_data"]["question"] == "帮我写一段 Python 代码"


def test_empty_intake_patch_keeps_handoff_until_travel_fields_actually_change(tmp_path):
    resolver = FilesystemUserContextResolver(tmp_path / "contexts", workspace_dir=tmp_path)
    service = TravelApplicationService(TravelConfig(enabled=True), resolver)
    sessions = JsonlSessionStore(tmp_path / "sessions")
    sessions.update_metadata(
        "travel-a",
        {
            "travel_phase": "intake",
            "travel_handoff_question": "帮我写 Python",
            "travel_handoff_topic": "编程",
        },
    )
    update, _handoff = service.intake_tools_for_actor(_actor("user-a"), sessions)
    events = Events()
    context = ToolExecutionContext(
        actor=_actor("user-a"),
        session_id="travel-a",
        turn_id="turn-follow-up",
        turn_index=2,
        channel="travel",
        runtime_events=events,
    )

    update.execute_with_context({"patch": {}}, context)

    assert sessions.load("travel-a").metadata["travel_handoff_question"] == "帮我写 Python"
    assert events.items[-1][1]["ui_metadata"]["detail_data"]["changed_fields"] == []

    update.execute_with_context(
        {"patch": {"origin": "重庆"}},
        context,
    )

    assert sessions.load("travel-a").metadata["travel_handoff_question"] == ""
    assert events.items[-1][1]["ui_metadata"]["detail_data"]["changed_fields"] == ["origin"]


def test_intake_confirmation_reuses_server_confirmation_and_emits_planning_event(tmp_path):
    resolver = FilesystemUserContextResolver(tmp_path / "contexts", workspace_dir=tmp_path)
    service = TravelApplicationService(TravelConfig(enabled=True), resolver)
    sessions = JsonlSessionStore(tmp_path / "sessions")
    draft = {
        "intent": "travel_requirement",
        "intent_topic": "",
        "origin": "重庆南山",
        "destinations": ["重庆綦江"],
        "start_date": "2026-08-15",
        "end_date": "2026-08-15",
        "traveller_type": "",
        "traveller_count": 1,
        "budget_total_cny": None,
        "budget_level": "",
        "transport_preferences": [],
        "stay_preferences": [],
        "interest_tags": [],
        "pace": "",
        "planning_mode": "",
        "hard_constraints": [],
    }
    sessions.update_metadata(
        "travel-a",
        {"travel_phase": "intake", "travel_draft": draft},
    )
    calls = []

    def confirm(actor, session_id, confirmed_draft):
        calls.append((actor.user_id, session_id, confirmed_draft))
        sessions.update_metadata(session_id, {"travel_phase": "planning"})
        return {"session_id": session_id, "phase": "planning", "status": "confirmed"}

    _update, _handoff, start = service.intake_tools_for_actor(
        _actor("user-a"),
        sessions,
        confirm_planning=confirm,
    )
    events = Events()

    result = start.execute_with_context(
        {},
        ToolExecutionContext(
            actor=_actor("user-a"),
            session_id="travel-a",
            turn_id="turn-confirm",
            turn_index=3,
            channel="travel",
            runtime_events=events,
        ),
    )

    assert not result.is_error
    assert json.loads(result.output)["status"] == "confirmed"
    assert calls == [("user-a", "travel-a", draft)]
    assert sessions.load("travel-a").metadata["travel_phase"] == "planning"
    assert events.items[-1][0] == "travel.planning_confirmed"


def test_intake_tools_reject_calls_after_planning_confirmation(tmp_path):
    resolver = FilesystemUserContextResolver(tmp_path / "contexts", workspace_dir=tmp_path)
    service = TravelApplicationService(TravelConfig(enabled=True), resolver)
    sessions = JsonlSessionStore(tmp_path / "sessions")
    sessions.update_metadata("travel-a", {"travel_phase": "planning"})
    update, _handoff = service.intake_tools_for_actor(_actor("user-a"), sessions)

    result = update.execute_with_context(
        {"patch": {}},
        ToolExecutionContext(
            actor=_actor("user-a"),
            session_id="travel-a",
            turn_id="turn-a",
            turn_index=1,
            channel="travel",
        ),
    )

    assert result.is_error
    assert result.metadata["code"] == "TRAVEL_INTAKE_PHASE_CLOSED"


@pytest.mark.parametrize("questions", [[], [""], ["问题"] * 7])
def test_clarification_tool_rejects_empty_or_oversized_question_sets(tmp_path, questions):
    resolver = FilesystemUserContextResolver(tmp_path / "contexts", workspace_dir=tmp_path)
    service = TravelApplicationService(TravelConfig(enabled=True), resolver)
    tool = service.tools_for_actor(_actor("user-a"))[1]
    result = tool.execute_with_context(
        {"questions": questions},
        ToolExecutionContext(
            actor=_actor("user-a"),
            session_id="session-a",
            turn_id="turn-a",
            turn_index=1,
            channel="web",
        ),
    )

    assert result.is_error
    assert result.metadata["code"] == "TRAVEL_CLARIFICATION_INVALID"


def test_candidate_review_persists_selection_and_emits_waiting_event(tmp_path):
    resolver = FilesystemUserContextResolver(tmp_path / "contexts", workspace_dir=tmp_path)
    service = TravelApplicationService(TravelConfig(enabled=True), resolver)
    tool = service.tools_for_actor(_actor("user-a"))[2]
    events = Events()
    context = ToolExecutionContext(
        actor=_actor("user-a"),
        session_id="travel-a",
        turn_id="turn-a",
        turn_index=1,
        channel="travel",
        tool_call_id="call-review",
        runtime_events=events,
    )
    candidates = [_candidate_summary("slow", recommended=True), _candidate_summary("compact")]

    result = tool.execute_with_context(
        {"recommended_candidate_id": "slow", "candidates": candidates}, context
    )
    selected = service.select_candidate(_actor("user-a"), "travel-a", "compact")

    assert not result.is_error
    assert result.metadata["code"] == "TRAVEL_CANDIDATE_REVIEW_REQUIRED"
    assert events.items[0][0] == "travel.candidate_review_required"
    assert selected.selected_candidate_id == "compact"
    assert selected.status == "selected"


def test_candidate_review_rejects_unknown_selection(tmp_path):
    resolver = FilesystemUserContextResolver(tmp_path / "contexts", workspace_dir=tmp_path)
    service = TravelApplicationService(TravelConfig(enabled=True), resolver)
    service.save_candidate_review(
        _actor("user-a"),
        session_id="travel-a",
        turn_id="turn-a",
        candidates=[_candidate_summary("slow", recommended=True), _candidate_summary("compact")],
        recommended_candidate_id="slow",
    )

    with pytest.raises(TravelApplicationError) as exc_info:
        service.select_candidate(_actor("user-a"), "travel-a", "unknown")

    assert exc_info.value.code == "TRAVEL_CANDIDATE_SELECTION_INVALID"


def _candidate_summary(candidate_id, recommended=False):
    return {
        "candidate_id": candidate_id,
        "recommended": recommended,
        "score": 100,
        "days": [{"date": "2026-10-01", "city_or_area": "郑州", "places": ["河南博物院"]}],
        "budget": {"lower": 1000, "expected": 1500, "upper": 2000},
        "route_minutes": 90,
        "route_distance_km": 20,
        "daily_intensity_scores": [6.5],
        "evidence_coverage": 0.8,
        "warnings": [],
    }


def _candidate_from_plan(candidate_id, plan, recommended=False):
    return {
        "candidate_id": candidate_id,
        "recommended": recommended,
        "score": 100,
        "days": [
            {
                "date": day["date"],
                "city_or_area": day["city_or_area"],
                "places": [activity["place"] for activity in day["activities"]],
            }
            for day in plan["days"]
        ],
        "budget": {key: plan["budget"][key] for key in ("lower", "expected", "upper")},
        "route_minutes": sum(segment["duration"] for day in plan["days"] for segment in day["route_segments"]),
        "route_distance_km": sum(segment["distance"] for day in plan["days"] for segment in day["route_segments"]),
        "daily_intensity_scores": [day["intensity_score"] for day in plan["days"]],
        "evidence_coverage": 0.9,
        "warnings": [],
    }


def test_finalize_tool_schema_publishes_strict_request_and_evidence_fields(tmp_path):
    resolver = FilesystemUserContextResolver(tmp_path / "contexts", workspace_dir=tmp_path)
    service = TravelApplicationService(TravelConfig(enabled=True), resolver)
    tool = service.tools_for_actor(_actor("user-a"))[0]
    plan_schema = tool.parameters["properties"]["plan"]
    request_schema = plan_schema["properties"]["request"]
    evidence_schema = plan_schema["properties"]["evidence"]["items"]
    day_schema = plan_schema["properties"]["days"]["items"]

    assert request_schema["additionalProperties"] is False
    assert "planning_mode" in request_schema["properties"]
    assert "mode" not in request_schema["properties"]
    assert evidence_schema["additionalProperties"] is False
    assert set(evidence_schema["properties"]) == {
        "evidence_id", "source_type", "provider", "title", "source_url",
        "published_at", "retrieved_at", "data_as_of", "excerpt", "facts",
        "confidence", "freshness", "content_hash",
    }
    assert "metadata" not in evidence_schema["properties"]
    assert "source_url" in evidence_schema["required"]
    assert day_schema["additionalProperties"] is False
    assert "total_minutes" not in day_schema["properties"]
    assert set(day_schema["properties"]) == {
        "date", "city_or_area", "activities", "route_segments", "meal_suggestions",
        "daily_budget", "weather_adjustment", "fallback_plan", "intensity_score",
    }
    assert day_schema["properties"]["activities"]["items"]["additionalProperties"] is False
    assert day_schema["properties"]["route_segments"]["items"]["additionalProperties"] is False
    activity_schema = day_schema["properties"]["activities"]["items"]
    route_schema = day_schema["properties"]["route_segments"]["items"]
    assert "location" in activity_schema["required"]
    assert activity_schema["properties"]["location"]["type"] == "object"
    assert route_schema["properties"]["path"]["items"]["type"] == "object"


def test_finalize_requires_a_narrower_retry_after_first_empty_search(tmp_path):
    resolver = FilesystemUserContextResolver(tmp_path / "contexts", workspace_dir=tmp_path)
    service = TravelApplicationService(TravelConfig(enabled=True), resolver)
    actor = _actor("user-a")
    service.source_ledger.register_expected(
        "session-travel", ["mcp__xhs-readonly__search_notes"]
    )
    service.source_ledger.observe(
        "session-travel",
        "mcp__xhs-readonly__search_notes",
        ToolResult(output='{"status":"success","data":{"text":"{\\"feeds\\":[]}"}}'),
    )

    result = service.tools_for_actor(actor)[0].execute_with_context(
        {"plan": plan_payload()},
        ToolExecutionContext(
            actor=actor,
            session_id="session-travel",
            turn_id="turn-a",
            turn_index=1,
            channel="travel",
        ),
    )

    assert result.is_error
    assert result.metadata["code"] == "TRAVEL_RESEARCH_INCOMPLETE"


def test_finalize_rejects_schema_size_disabled_and_actorless_access(tmp_path):
    resolver = FilesystemUserContextResolver(tmp_path / "contexts", workspace_dir=tmp_path)
    service = TravelApplicationService(TravelConfig(enabled=True, max_plan_bytes=100), resolver)
    with pytest.raises(TravelApplicationError) as captured:
        service.finalize(_actor("user-a"), plan_payload(), source_session_id="s", source_turn_id="t")
    assert captured.value.code == "TRAVEL_PLAN_TOO_LARGE"

    disabled = TravelApplicationService(TravelConfig(enabled=False), resolver)
    with pytest.raises(TravelApplicationError) as captured:
        disabled.list_plans(_actor("user-a"))
    assert captured.value.code == "TRAVEL_DISABLED"
    with pytest.raises(TravelApplicationError) as captured:
        TravelApplicationService(TravelConfig(enabled=True), resolver).list_plans(_actor(None))
    assert captured.value.code == "TRAVEL_PLAN_ACCESS_DENIED"


def test_finalize_tool_error_includes_safe_field_path(tmp_path):
    resolver = FilesystemUserContextResolver(tmp_path / "contexts", workspace_dir=tmp_path)
    service = TravelApplicationService(TravelConfig(enabled=True), resolver)
    actor = _actor("user-a")
    tool = service.tools_for_actor(actor)[0]
    invalid = plan_payload()
    invalid["evidence"][0]["source_url"] = ""
    context = ToolExecutionContext(
        actor=actor, session_id="s", turn_id="t", turn_index=1, channel="web"
    )

    result = tool.execute_with_context({"plan": invalid}, context)

    assert result.is_error
    assert result.metadata["field"] == "evidence[0].source_url"
    assert result.output.startswith("evidence[0].source_url:")


def _actor(user_id: str | None) -> ActorContext:
    return ActorContext(
        actor_type="user",
        user_id=user_id,
        username="traveller",
        display_name="Traveller",
        role_keys=frozenset({"viewer"}),
        permission_keys=frozenset(),
        channel="web",
    )
