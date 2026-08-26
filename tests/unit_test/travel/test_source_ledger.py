from __future__ import annotations

import json
from datetime import date

import agent.applications.travel.source_ledger as source_ledger_module
from agent.applications.travel.source_ledger import (
    TravelSourceLedger,
    guard_travel_tools,
    preferred_travel_tool_names,
    require_travel_finalization_before_saving,
    require_travel_research_before_solving,
    source_category,
    source_operation,
)
from agent.applications.travel.tools import (
    _merge_previous_live_weather,
    _merge_previous_verified_transit,
)
from agent.protocols.auth import ActorContext
from agent.protocols.tool import ToolExecutionContext, ToolResult


class _CountingTool:
    description = "test"
    parameters = {"type": "object"}

    def __init__(self, name: str) -> None:
        self.name = name
        self.calls: list[dict[str, object]] = []

    def execute(self, args: dict[str, object]) -> ToolResult:
        self.calls.append(args)
        return ToolResult(output='{"status":"success"}', metadata={"code": "MCP_OK"})


class _StaticTool(_CountingTool):
    def __init__(self, name: str, output: str) -> None:
        super().__init__(name)
        self.output = output

    def execute(self, args: dict[str, object]) -> ToolResult:
        self.calls.append(args)
        return ToolResult(output=self.output, metadata={"code": "MCP_OK"})


class _Provider:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, object]]] = []

    def definitions(self):
        return [
            {"type": "function", "function": {"name": name}}
            for name in (
                "delegate_tasks",
                "run_skill",
                "request_travel_candidate_review",
                "finalize_travel_plan",
            )
        ]

    def execute(self, name: str, args: dict[str, object]) -> ToolResult:
        self.calls.append((name, args))
        return ToolResult(output='{"status":"success"}', metadata={"code": "OK"})


class _CandidateReviewTool:
    name = "request_travel_candidate_review"

    def __init__(self) -> None:
        self.calls: list[tuple[dict[str, object], ToolExecutionContext]] = []

    def execute_with_context(
        self,
        args: dict[str, object],
        context: ToolExecutionContext,
    ) -> ToolResult:
        self.calls.append((args, context))
        return ToolResult(
            output='{"status":"waiting_for_user"}',
            metadata={"code": "TRAVEL_CANDIDATE_REVIEW_REQUIRED"},
        )


def test_finalizer_retry_preserves_previous_verified_transit_fields():
    previous = {
        "days": [{
            "date": "2026-09-15",
            "route_segments": [{
                "mode": "公交/地铁",
                "from": "西安站",
                "to": "酒店",
                "duration": 24,
                "distance": 5.2,
                "source": "amap_transit",
                "transit_legs": [{
                    "mode": "地铁",
                    "line_name": "地铁4号线",
                    "departure_stop": "西安站",
                    "arrival_stop": "北大街",
                    "via_stops": [],
                }],
            }],
        }],
    }
    current = {
        "days": [{
            "date": "2026-09-15",
            "route_segments": [{
                "mode": "公交/地铁",
                "from": "西安站",
                "to": "酒店",
                "duration": 25,
                "distance": 5.2,
                "source": "planning_estimate",
            }],
        }],
    }

    merged = _merge_previous_verified_transit(current, previous)

    segment = merged["days"][0]["route_segments"][0]
    assert segment["source"] == "amap_transit"
    assert segment["transit_legs"][0]["line_name"] == "地铁4号线"
    assert current["days"][0]["route_segments"][0]["source"] == "planning_estimate"


def test_finalizer_retry_preserves_previous_live_weather_and_evidence():
    previous = {
        "weather_summary": [{
            "date": "2026-09-15",
            "summary": "阵雨",
            "provider": "Open-Meteo",
            "freshness": "live",
        }],
        "days": [{"date": "2026-09-15", "weather_adjustment": "携带雨具"}],
        "evidence": [{
            "evidence_id": "weather-live",
            "source_type": "official_api",
            "provider": "Open-Meteo",
            "title": "实时天气预报",
            "freshness": "live",
        }],
    }
    current = {
        "weather_summary": [{
            "date": "2026-09-15",
            "summary": "历史同期",
            "provider": "Open-Meteo",
            "freshness": "historical",
        }],
        "days": [{"date": "2026-09-15", "weather_adjustment": "参考历史气候"}],
        "evidence": [],
    }

    merged = _merge_previous_live_weather(current, previous)

    assert merged["weather_summary"][0]["freshness"] == "live"
    assert merged["days"][0]["weather_adjustment"] == "携带雨具"
    assert merged["evidence"][0]["evidence_id"] == "weather-live"
    assert current["weather_summary"][0]["freshness"] == "historical"


def test_source_ledger_tracks_expected_attempted_and_successful_categories():
    ledger = TravelSourceLedger()
    ledger.register_expected(
        "session-travel",
        [
            "mcp__amap-maps__maps_text_search",
            "mcp__open-meteo__get_forecast",
            "mcp__12306__get-tickets",
            "mcp__tavily__tavily_search",
            "mcp__xhs-readonly__search_notes",
        ],
    )
    ledger.observe(
        "session-travel",
        "mcp__amap-maps__maps_text_search",
        ToolResult(output='{"pois":[{"name":"大理古城"}]}', metadata={"code": "MCP_OK"}),
    )
    ledger.observe(
        "session-travel",
        "mcp__open-meteo__get_forecast",
        ToolResult(
            output='{"status":"error","code":"TRAVEL_WEATHER_OUT_OF_RANGE"}',
            metadata={"code": "MCP_OK"},
        ),
    )

    snapshot = ledger.snapshot("session-travel")
    assert snapshot.expected == frozenset({"maps", "weather", "transport", "web", "social"})
    assert snapshot.attempted == frozenset({"maps", "weather"})
    assert snapshot.successful == frozenset({"maps"})
    assert snapshot.missing_attempts == ("social", "transport", "web")
    assert snapshot.evidence_coverage == 0.2


def test_parent_cannot_run_optimizer_before_configured_research_attempts():
    ledger = TravelSourceLedger()
    ledger.register_expected(
        "session-order",
        ["mcp__amap__search", "mcp__open-meteo__get_forecast"],
    )
    delegate = _Provider()
    provider = require_travel_research_before_solving(
        delegate,
        ledger,
        "session-order",
    )

    assert [item["function"]["name"] for item in provider.definitions()] == [
        "delegate_tasks"
    ]

    blocked = provider.execute("run_skill", {"skill": "travel-planner"})

    assert blocked.is_error is True
    assert blocked.metadata["code"] == "TRAVEL_PARALLEL_RESEARCH_REQUIRED"
    assert delegate.calls == []

    ledger.observe(
        "session-order",
        "mcp__amap__search",
        ToolResult(output='{"pois":[]}', metadata={"code": "MCP_OK"}),
    )
    ledger.observe(
        "session-order",
        "mcp__open-meteo__get_forecast",
        ToolResult(output='{"daily":{}}', metadata={"code": "MCP_OK"}),
    )

    allowed = provider.execute("run_skill", {"skill": "travel-planner"})

    assert allowed.is_error is False
    assert delegate.calls == [("run_skill", {"skill": "travel-planner"})]
    assert [item["function"]["name"] for item in provider.definitions()] == [
        "run_skill",
        "finalize_travel_plan",
    ]


def test_station_lookup_does_not_satisfy_two_dated_ticket_attempts():
    ledger = TravelSourceLedger()
    ledger.register_expected("session-rail", ["mcp__12306__get-tickets"])
    ledger.observe(
        "session-rail",
        "mcp__12306__get-station-code-of-citys",
        ToolResult(
            output='[{"station_name":"重庆","station_code":"CQW"}]',
            metadata={"code": "MCP_OK"},
        ),
    )

    assert ledger.snapshot("session-rail").candidate_missing_attempts == ("transport",)

    for travel_date in ("2026-09-15", "2026-09-17"):
        ledger.observe(
            "session-rail",
            "mcp__12306__get-tickets",
            ToolResult(
                output=json.dumps(
                    {
                        "status": "not_on_sale",
                        "code": "OK",
                        "date": travel_date,
                        "sale_open_date": "2026-09-01",
                        "trains": [],
                    }
                ),
                metadata={"code": "MCP_OK", "travel_source_status": "not_on_sale"},
            ),
        )

    snapshot = ledger.snapshot("session-rail")
    assert snapshot.candidate_missing_attempts == ()
    assert snapshot.transport_ticket_attempt_count == 2
    assert snapshot.transport_ticket_success_count == 2
    assert snapshot.transport_ticket_not_on_sale is True


def test_completed_candidate_fan_in_allows_county_without_rail_ticket_calls():
    ledger = TravelSourceLedger()
    ledger.register_expected(
        "session-county",
        [
            "mcp__12306__get-tickets",
            "mcp__open-meteo__get_forecast",
            "mcp__hotel-browser__search_travel_hotels",
            "mcp__tavily__tavily_search",
            "mcp__xhs-readonly__search_notes",
        ],
    )
    ledger.observe(
        "session-county",
        "mcp__12306__get-station-code-of-citys",
        ToolResult(
            output='[{"station_name":"重庆","station_code":"CQW"}]',
            metadata={"code": "MCP_OK"},
        ),
    )
    assert ledger.snapshot("session-county").candidate_missing_attempts == (
        "lodging",
        "social",
        "transport",
        "weather",
        "web",
    )

    ledger.mark_candidate_profiles_completed(
        "session-county",
        {
            "travel-transport-weather",
            "travel-stay-poi",
            "travel-guides",
        },
    )
    snapshot = ledger.snapshot("session-county")
    assert snapshot.candidate_research_complete is True
    assert snapshot.transport_ticket_attempt_count == 0
    assert snapshot.candidate_missing_attempts == ()

    provider = require_travel_research_before_solving(
        _Provider(), ledger, "session-county"
    )
    assert [item["function"]["name"] for item in provider.definitions()] == [
        "run_skill",
        "finalize_travel_plan",
    ]


def test_partial_candidate_profiles_do_not_bypass_rail_research_gate():
    ledger = TravelSourceLedger()
    ledger.register_expected("session-partial", ["mcp__12306__get-tickets"])
    ledger.mark_candidate_profiles_completed(
        "session-partial", {"travel-transport-weather", "travel-stay-poi"}
    )

    snapshot = ledger.snapshot("session-partial")
    assert snapshot.candidate_research_complete is False
    assert snapshot.candidate_missing_attempts == ("transport",)


def test_partial_candidate_profiles_keep_only_the_failed_lane_resumable():
    ledger = TravelSourceLedger()
    ledger.mark_candidate_profiles_completed(
        "session-partial-lane", {"travel-transport-weather", "travel-stay-poi"}
    )

    snapshot = ledger.snapshot("session-partial-lane")

    assert snapshot.candidate_research_complete is False
    assert snapshot.candidate_missing_attempts == ("travel-guides",)


def test_weather_geocode_does_not_satisfy_weather_data_attempt():
    ledger = TravelSourceLedger()
    ledger.register_expected(
        "session-weather",
        [
            "mcp__open-meteo__geocode_place",
            "mcp__open-meteo__get_historical_weather",
        ],
    )
    ledger.observe(
        "session-weather",
        "mcp__open-meteo__geocode_place",
        ToolResult(
            output='{"results":[{"name":"西安","latitude":34.26,"longitude":108.94}]}',
            metadata={"code": "MCP_OK"},
        ),
    )

    geocoded = ledger.snapshot("session-weather")
    assert geocoded.candidate_missing_attempts == ("weather",)
    assert geocoded.weather_data_attempted is False
    assert "weather" not in geocoded.successful

    ledger.observe(
        "session-weather",
        "mcp__open-meteo__get_historical_weather",
        ToolResult(
            output='{"daily":{"time":["2025-09-15"]}}',
            metadata={"code": "MCP_OK"},
        ),
    )

    weather = ledger.snapshot("session-weather")
    assert weather.candidate_missing_attempts == ()
    assert weather.weather_data_attempted is True
    assert weather.weather_data_successful is True
    assert "weather" in weather.successful


def test_future_historical_weather_query_uses_latest_past_same_date_window(monkeypatch):
    monkeypatch.setattr(source_ledger_module, "_china_today", lambda: date(2026, 8, 16))
    ledger = TravelSourceLedger()
    delegate = _CountingTool("mcp__open-meteo__get_historical_weather")
    guarded = guard_travel_tools([delegate], ledger, "session-weather-history")[0]

    result = guarded.execute(
        {
            "latitude": 34.26,
            "longitude": 108.94,
            "start_date": "2026-09-15",
            "end_date": "2026-09-17",
            "timezone": "Asia/Shanghai",
        }
    )

    assert result.is_error is False
    assert delegate.calls == [
        {
            "latitude": 34.26,
            "longitude": 108.94,
            "start_date": "2025-09-15",
            "end_date": "2025-09-17",
            "timezone": "Asia/Shanghai",
        }
    ]
    ledger.observe(
        "session-weather-history",
        "mcp__open-meteo__get_historical_weather",
        result,
    )
    snapshot = ledger.snapshot("session-weather-history")
    assert snapshot.weather_data_attempted is True
    assert snapshot.weather_data_successful is True


def test_forecast_window_rejects_historical_fallback_without_consuming_weather_attempt(
    monkeypatch,
):
    monkeypatch.setattr(source_ledger_module, "_china_today", lambda: date(2026, 8, 17))
    ledger = TravelSourceLedger()
    delegate = _CountingTool("mcp__open-meteo__get_historical_weather")
    guarded = guard_travel_tools([delegate], ledger, "session-live-weather")[0]

    result = guarded.execute(
        {
            "latitude": 34.62,
            "longitude": 112.45,
            "start_date": "2026-08-24",
            "end_date": "2026-08-26",
        }
    )

    assert result.is_error is True
    assert result.metadata["code"] == "TRAVEL_LIVE_FORECAST_REQUIRED"
    assert delegate.calls == []
    snapshot = ledger.snapshot("session-live-weather")
    assert snapshot.weather_data_attempted is False
    assert "weather" not in snapshot.successful


def test_candidate_optimizer_does_not_wait_for_maps_reserved_for_selected_route():
    ledger = TravelSourceLedger()
    ledger.register_expected(
        "session-candidate-map",
        ["mcp__amap__search", "mcp__open-meteo__get_forecast"],
    )
    ledger.observe(
        "session-candidate-map",
        "mcp__open-meteo__get_forecast",
        ToolResult(output='{"daily":{}}', metadata={"code": "MCP_OK"}),
    )
    provider = require_travel_research_before_solving(
        _Provider(),
        ledger,
        "session-candidate-map",
    )

    assert ledger.snapshot("session-candidate-map").missing_attempts == ("maps",)
    assert ledger.snapshot("session-candidate-map").candidate_missing_attempts == ()
    assert [item["function"]["name"] for item in provider.definitions()] == [
        "run_skill",
        "finalize_travel_plan",
    ]


def test_selected_candidate_must_run_final_detail_batch_before_saving():
    ledger = TravelSourceLedger()
    ledger.register_expected(
        "session-final",
        ["mcp__amap__search", "search_travel_hotels"],
    )
    provider = require_travel_finalization_before_saving(
        _Provider(),
        ledger,
        "session-final",
    )

    assert [item["function"]["name"] for item in provider.definitions()] == [
        "delegate_tasks"
    ]
    blocked = provider.execute("finalize_travel_plan", {"plan": {}})
    assert blocked.metadata["code"] == "TRAVEL_FINALIZATION_RESEARCH_REQUIRED"

    ledger.observe(
        "session-final",
        "mcp__amap__maps_direction_transit_integrated",
        ToolResult(output='{"route":{"transits":[]}}', metadata={"code": "MCP_OK"}),
    )
    ledger.observe(
        "session-final",
        "search_travel_hotels",
        ToolResult(
            output='{"status":"error"}',
            is_error=True,
            metadata={"code": "HOTEL_MANUAL_VERIFICATION_REQUIRED"},
        ),
    )

    assert [item["function"]["name"] for item in provider.definitions()] == [
        "finalize_travel_plan"
    ]


def test_forecast_repair_reopens_weather_once_after_historical_success():
    ledger = TravelSourceLedger()
    session_id = "session-forecast-repair"
    geocode = "mcp__open-meteo__geocode_place"
    historical = "mcp__open-meteo__get_historical_weather"
    forecast = "mcp__open-meteo__get_forecast"
    ledger.register_expected(session_id, [geocode, historical, forecast])

    assert ledger.admit_call(session_id, geocode, {"name": "郑州"}) is None
    ledger.observe(
        session_id,
        geocode,
        ToolResult(output='{"results":[{"latitude":34.7,"longitude":113.6}]}', metadata={"code": "MCP_OK"}),
    )
    assert ledger.admit_call(
        session_id,
        historical,
        {"latitude": 34.7, "longitude": 113.6, "start_date": "2025-08-19", "end_date": "2025-08-24"},
    ) is None
    ledger.observe(
        session_id,
        historical,
        ToolResult(output='{"daily":{"time":["2025-08-19"]}}', metadata={"code": "MCP_OK"}),
    )

    blocked = ledger.admit_call(
        session_id,
        forecast,
        {"latitude": 34.7, "longitude": 113.6, "start_date": "2026-08-19", "end_date": "2026-08-24"},
    )
    assert blocked is not None
    assert blocked.metadata["code"] == "TRAVEL_SOURCE_ALREADY_SATISFIED"

    ledger.begin_forecast_repair(session_id)
    assert ledger.admit_call(session_id, geocode, {"name": "郑州"}) is None
    ledger.observe(
        session_id,
        geocode,
        ToolResult(output='{"results":[{"latitude":34.7,"longitude":113.6}]}', metadata={"code": "MCP_OK"}),
    )
    assert ledger.admit_call(
        session_id,
        forecast,
        {"latitude": 34.7, "longitude": 113.6, "start_date": "2026-08-19", "end_date": "2026-08-24"},
    ) is None
    ledger.observe(
        session_id,
        forecast,
        ToolResult(output='{"daily":{"time":["2026-08-19"]}}', metadata={"code": "MCP_OK"}),
    )

    assert ledger.snapshot(session_id).forecast_successful is True
    repeated = ledger.admit_call(
        session_id,
        forecast,
        {"latitude": 34.7, "longitude": 113.6, "start_date": "2026-08-20", "end_date": "2026-08-24"},
    )
    assert repeated is not None
    assert repeated.metadata["code"] == "TRAVEL_SOURCE_ALREADY_SATISFIED"


def test_finalization_weather_repair_hides_finalizer_until_forecast_succeeds():
    ledger = TravelSourceLedger()
    session_id = "session-final-weather-repair"
    provider = require_travel_finalization_before_saving(
        _Provider(),
        ledger,
        session_id,
        repair_categories=frozenset({"weather"}),
    )

    assert [item["function"]["name"] for item in provider.definitions()] == [
        "delegate_tasks"
    ]
    ledger.observe(
        session_id,
        "mcp__open-meteo__get_forecast",
        ToolResult(output='{"daily":{"time":["2026-08-19"]}}', metadata={"code": "MCP_OK"}),
    )
    assert [item["function"]["name"] for item in provider.definitions()] == [
        "finalize_travel_plan"
    ]


def test_finalizer_plan_attempts_are_bounded_isolated_and_cleared():
    ledger = TravelSourceLedger()
    session_id = "session-plan-attempts"
    plans = [
        {
            "plan": {"marker": index, "nested": {"value": index}},
            "live_weather_verified": index >= 5,
            "transit_verified": index >= 7,
        }
        for index in range(10)
    ]

    ledger.restore_plan_attempts(session_id, plans)
    restored = ledger.plan_attempts(session_id)

    assert [item["plan"]["marker"] for item in restored] == list(reversed(range(2, 10)))
    assert all(item["draft_revision"].startswith("sha256:") for item in restored)
    restored[0]["plan"]["nested"]["value"] = -1
    assert ledger.plan_attempts(session_id)[0]["plan"]["nested"]["value"] == 9

    remembered = ledger.remember_plan_attempt(
        session_id,
        {"marker": 10},
        selected_candidate_id="candidate-a",
    )
    assert remembered["selected_candidate_id"] == "candidate-a"
    assert [item["plan"]["marker"] for item in ledger.plan_attempts(session_id)] == list(
        reversed(range(3, 11))
    )
    ledger.clear(session_id)
    assert ledger.plan_attempts(session_id) == []


def test_finalization_route_repair_reopens_map_budget_and_then_finalizer():
    ledger = TravelSourceLedger()
    session_id = "session-final-route-repair"
    route = "mcp__amap-maps__maps_direction_transit_integrated"
    arguments = {"origin": "113.6,34.7", "destination": "113.7,34.8", "city": "郑州"}
    ledger.register_expected(session_id, [route])
    assert ledger.admit_call(session_id, route, arguments) is None
    ledger.observe(
        session_id,
        route,
        ToolResult(output='{"route":{"transits":[]}}', metadata={"code": "MCP_OK"}),
    )
    ledger.begin_route_repair(session_id)
    provider = require_travel_finalization_before_saving(
        _Provider(),
        ledger,
        session_id,
        repair_categories=frozenset({"maps"}),
    )

    assert [item["function"]["name"] for item in provider.definitions()] == [
        "delegate_tasks"
    ]
    assert ledger.admit_call(session_id, route, arguments) is None
    ledger.observe(
        session_id,
        route,
        ToolResult(output='{"route":{"transits":[{"duration":"1200"}]}}', metadata={"code": "MCP_OK"}),
    )
    assert ledger.snapshot(session_id).route_repair_attempted is True
    assert [item["function"]["name"] for item in provider.definitions()] == [
        "finalize_travel_plan"
    ]


def test_route_repair_budget_covers_twenty_missing_local_segments():
    ledger = TravelSourceLedger()
    delegate = _CountingTool("mcp__amap-maps__maps_direction_transit_integrated")
    guarded = guard_travel_tools([delegate], ledger, "session-route-repair-budget")[0]
    ledger.begin_route_repair("session-route-repair-budget")

    for index in range(20):
        assert guarded.execute(
            {"origin": f"repair-{index}", "destination": "hotel", "city": "郑州"}
        ).is_error is False
    exhausted = guarded.execute(
        {"origin": "repair-20", "destination": "hotel", "city": "郑州"}
    )

    assert exhausted.metadata["code"] == "TRAVEL_SOURCE_BUDGET_EXHAUSTED"
    assert len(delegate.calls) == 20


def test_xhs_combination_query_is_rewritten_to_destination_then_single_attraction():
    ledger = TravelSourceLedger()
    first = ledger.normalize_arguments(
        "session-xhs-query",
        "mcp__xhs-readonly__search_notes",
        {"keyword": "河南 旅行 攻略 避坑 公共交通 8月", "max_results": 6},
    )
    assert first["keyword"] == "河南旅游攻略"
    assert ledger.admit_call(
        "session-xhs-query", "mcp__xhs-readonly__search_notes", first
    ) is None

    second = ledger.normalize_arguments(
        "session-xhs-query",
        "mcp__xhs-readonly__search_notes",
        {"keyword": "河南 老君山 攻略", "max_results": 6},
    )

    assert second["keyword"] == "老君山攻略"


def test_tavily_combination_query_is_rewritten_to_one_city_intent():
    ledger = TravelSourceLedger()
    first = ledger.normalize_arguments(
        "session-tavily-query",
        "mcp__tavily__tavily_search",
        {
            "query": "洛阳 旅游攻略 公共交通 经济 实惠 龙门石窟 白马寺 洛阳博物馆",
            "max_results": 5,
        },
    )

    assert first["query"] == "洛阳旅游攻略 公共交通"
    assert first["max_results"] == 5


def test_tavily_compaction_removes_english_platform_boilerplate():
    ledger = TravelSourceLedger()
    guarded = guard_travel_tools(
        [
            _StaticTool(
                "mcp__tavily__tavily_search",
                json.dumps(
                    {
                        "results": [
                            {
                                "title": "洛阳游玩攻略",
                                "url": "https://example.com/luoyang",
                                "content": (
                                    "All reactions: 15 Like Comment Subscribe. "
                                    "洛阳城区可优先乘坐地铁，龙门石窟建议预留半天。"
                                ),
                            }
                        ]
                    },
                    ensure_ascii=False,
                ),
            )
        ],
        ledger,
        "session-clean-web",
    )[0]

    result = guarded.execute({"query": "洛阳 旅游攻略 公共交通 龙门石窟"})

    payload = json.loads(result.output)
    assert payload["results"][0]["content"] == (
        "洛阳城区可优先乘坐地铁，龙门石窟建议预留半天。"
    )
    assert "All reactions" not in result.output


def test_xhs_retry_prefers_verified_map_attraction_over_city_keyword():
    ledger = TravelSourceLedger()
    session_id = "session-xhs-map-seed"
    tool_name = "mcp__xhs-readonly__search_notes"
    first = ledger.normalize_arguments(
        session_id, tool_name, {"keyword": "河南旅游攻略", "max_results": 6}
    )
    assert ledger.admit_call(session_id, tool_name, first) is None
    ledger.observe(
        session_id,
        tool_name,
        ToolResult(output='{"feeds":[],"count":0}', metadata={"code": "MCP_OK"}),
    )
    ledger.observe(
        session_id,
        "mcp__amap-maps__maps_text_search",
        ToolResult(
            output='{"pois":[{"name":"龙门石窟","type":"风景名胜"}]}',
            metadata={"code": "MCP_OK"},
        ),
    )

    second = ledger.normalize_arguments(
        session_id, tool_name, {"keyword": "洛阳攻略", "max_results": 6}
    )

    assert second["keyword"] == "龙门石窟攻略"
    assert ledger.admit_call(session_id, tool_name, second) is None


def test_xhs_retry_is_rejected_after_timeout_or_with_only_a_city_keyword():
    ledger = TravelSourceLedger()
    tool_name = "mcp__xhs-readonly__search_notes"
    timeout_session = "session-xhs-timeout"
    first = ledger.normalize_arguments(
        timeout_session, tool_name, {"keyword": "河南旅游攻略"}
    )
    assert ledger.admit_call(timeout_session, tool_name, first) is None
    ledger.observe(
        timeout_session,
        tool_name,
        ToolResult(
            output='{"status":"error","code":"TRAVEL_SOURCE_TIMEOUT"}',
            is_error=True,
            metadata={"code": "TRAVEL_SOURCE_TIMEOUT"},
        ),
    )
    retry = ledger.normalize_arguments(
        timeout_session, tool_name, {"keyword": "老君山攻略"}
    )
    denied = ledger.admit_call(timeout_session, tool_name, retry)
    assert denied is not None
    assert denied.metadata["code"] == "TRAVEL_SOCIAL_RETRY_NOT_ALLOWED"

    city_session = "session-xhs-city"
    city_first = ledger.normalize_arguments(
        city_session, tool_name, {"keyword": "河南旅游攻略"}
    )
    assert ledger.admit_call(city_session, tool_name, city_first) is None
    ledger.observe(
        city_session,
        tool_name,
        ToolResult(output='{"feeds":[]}', metadata={"code": "MCP_OK"}),
    )
    city_retry = ledger.normalize_arguments(
        city_session, tool_name, {"keyword": "洛阳攻略"}
    )
    denied = ledger.admit_call(city_session, tool_name, city_retry)
    assert denied is not None
    assert denied.metadata["code"] == "TRAVEL_SOCIAL_ATTRACTION_REQUIRED"


def test_xhs_one_note_detail_is_allowed_after_successful_search_without_becoming_retry():
    ledger = TravelSourceLedger()
    session_id = "session-xhs-detail"
    search_name = "mcp__xhs-readonly__search_notes"
    detail_name = "mcp__xhs-readonly__get_note_detail"
    search_args = ledger.normalize_arguments(
        session_id, search_name, {"keyword": "洛阳 攻略", "max_results": 6}
    )
    assert ledger.admit_call(session_id, search_name, search_args) is None
    ledger.observe(
        session_id,
        search_name,
        ToolResult(
            output='{"feeds":[{"id":"note-1","title":"洛阳三日攻略"}]}',
            metadata={"code": "MCP_OK"},
        ),
    )

    assert ledger.admit_call(
        session_id, detail_name, {"feed_id": "note-1", "xsec_token": "token-1"}
    ) is None
    denied = ledger.admit_call(
        session_id, detail_name, {"feed_id": "note-2", "xsec_token": "token-2"}
    )

    assert denied is not None
    assert denied.metadata["code"] == "TRAVEL_SOURCE_BUDGET_EXHAUSTED"


def test_guard_persists_normalized_public_query_without_sensitive_detail_token():
    ledger = TravelSourceLedger()
    guarded = guard_travel_tools(
        [_StaticTool("mcp__xhs-readonly__search_notes", '{"feeds":[]}')],
        ledger,
        "session-effective-query",
    )[0]

    result = guarded.execute(
        {
            "keyword": "河南 旅行 攻略 避坑 8月",
            "max_results": 6,
            "xsec_token": "must-not-persist",
        }
    )

    assert result.metadata["travel_effective_arguments"] == {
        "keyword": "河南旅游攻略",
        "max_results": 6,
    }
    assert "must-not-persist" not in json.dumps(result.metadata, ensure_ascii=False)


def test_distinct_dated_hotel_queries_are_allowed_for_multi_area_itinerary():
    ledger = TravelSourceLedger()
    session_id = "session-multi-area-hotels"
    tool_name = "search_travel_hotels"
    first = {
        "city": "洛阳",
        "checkin": "2026-09-20",
        "checkout": "2026-09-21",
        "keyword": "洛邑古城",
    }
    second = {
        "city": "栾川",
        "checkin": "2026-09-21",
        "checkout": "2026-09-22",
        "keyword": "老君山",
    }
    assert ledger.admit_call(session_id, tool_name, first) is None
    ledger.observe(
        session_id,
        tool_name,
        ToolResult(
            output='{"hotels":[{"name":"洛阳测试酒店","observed_price_per_night_cny":178}]}',
            metadata={"code": "OK"},
        ),
        first,
    )

    assert ledger.admit_call(session_id, tool_name, second) is None


def test_finalization_requires_fresh_map_and_stay_attempts_after_candidate_research():
    ledger = TravelSourceLedger()
    ledger.register_expected(
        "session-final-fresh",
        ["mcp__amap__search", "search_travel_hotels"],
    )
    ledger.observe(
        "session-final-fresh",
        "mcp__amap__search",
        ToolResult(output='{"pois":[{"name":"候选景点"}]}', metadata={"code": "MCP_OK"}),
    )
    ledger.observe(
        "session-final-fresh",
        "search_travel_hotels",
        ToolResult(output='{"hotels":[{"name":"候选酒店"}]}', metadata={"code": "OK"}),
    )
    assert ledger.snapshot("session-final-fresh").missing_attempts == ()

    ledger.begin_finalization_budget("session-final-fresh")
    provider = require_travel_finalization_before_saving(
        _Provider(), ledger, "session-final-fresh"
    )

    assert ledger.snapshot("session-final-fresh").missing_attempts == (
        "lodging",
        "maps",
    )
    assert [item["function"]["name"] for item in provider.definitions()] == [
        "delegate_tasks"
    ]

    ledger.observe(
        "session-final-fresh",
        "mcp__amap__maps_direction_transit_integrated",
        ToolResult(output='{"route":{"transits":[]}}', metadata={"code": "MCP_OK"}),
    )
    ledger.observe(
        "session-final-fresh",
        "search_travel_hotels",
        ToolResult(
            output='{"status":"error"}',
            is_error=True,
            metadata={"code": "HOTEL_MANUAL_VERIFICATION_REQUIRED"},
        ),
    )

    assert ledger.snapshot("session-final-fresh").missing_attempts == ()
    assert [item["function"]["name"] for item in provider.definitions()] == [
        "finalize_travel_plan"
    ]


def test_candidate_replay_does_not_satisfy_selected_itinerary_detail_batch():
    ledger = TravelSourceLedger()
    ledger.register_expected(
        "session-final-replay",
        ["mcp__amap__maps_text_search", "search_travel_hotels"],
    )
    ledger.begin_finalization_budget("session-final-replay")

    ledger.observe(
        "session-final-replay",
        "mcp__amap__maps_text_search",
        ToolResult(output='{"pois":[{"name":"候选景点"}]}', metadata={"code": "MCP_OK"}),
        record_finalization=False,
    )
    ledger.observe(
        "session-final-replay",
        "search_travel_hotels",
        ToolResult(output='{"hotels":[{"name":"候选酒店"}]}', metadata={"code": "OK"}),
        record_finalization=False,
    )

    assert ledger.snapshot("session-final-replay").missing_attempts == (
        "lodging",
        "maps",
    )


def test_completed_finalization_lanes_can_satisfy_batch_without_repeat_source_call():
    ledger = TravelSourceLedger()
    ledger.register_expected(
        "session-final-child",
        ["mcp__amap__maps_direction_transit_integrated", "search_travel_hotels"],
    )
    ledger.begin_finalization_budget("session-final-child")
    provider = require_travel_finalization_before_saving(
        _Provider(), ledger, "session-final-child"
    )

    ledger.mark_finalization_attempted(
        "session-final-child",
        {"lodging", "maps", "weather", "unknown"},
    )

    snapshot = ledger.snapshot("session-final-child")
    assert snapshot.finalization_attempted == frozenset({"lodging", "maps"})
    assert snapshot.missing_attempts == ()
    assert [item["function"]["name"] for item in provider.definitions()] == [
        "finalize_travel_plan"
    ]


def test_finalization_lane_marker_is_ignored_before_budget_starts():
    ledger = TravelSourceLedger()
    ledger.register_expected("session-final-early", ["search_travel_hotels"])

    ledger.mark_finalization_attempted("session-final-early", {"lodging"})

    assert ledger.snapshot("session-final-early").finalization_attempted == frozenset()


def test_finalization_map_lane_requires_an_actual_route_attempt():
    ledger = TravelSourceLedger()
    ledger.register_expected(
        "session-final-route",
        ["mcp__amap__maps_text_search", "search_travel_hotels"],
    )
    ledger.begin_finalization_budget("session-final-route")

    ledger.observe(
        "session-final-route",
        "mcp__amap__maps_text_search",
        ToolResult(output='{"pois":[{"name":"洪崖洞"}]}', metadata={"code": "MCP_OK"}),
    )
    assert ledger.snapshot("session-final-route").missing_attempts == (
        "lodging",
        "maps",
    )

    ledger.observe(
        "session-final-route",
        "mcp__amap__maps_direction_transit_integrated",
        ToolResult(output='{"status":"error"}', is_error=True, metadata={"code": "MCP_REMOTE_ERROR"}),
    )
    assert ledger.snapshot("session-final-route").missing_attempts == ("lodging",)


def test_iteration_limit_placeholder_does_not_count_as_route_source_attempt():
    ledger = TravelSourceLedger()
    ledger.register_expected(
        "session-route-limit",
        ["mcp__amap__maps_direction_transit_integrated"],
    )
    ledger.begin_finalization_budget("session-route-limit")

    ledger.observe(
        "session-route-limit",
        "mcp__amap__maps_direction_transit_integrated",
        ToolResult(
            output="Tool call limit reached.",
            is_error=True,
            metadata={"code": "TOOL_ITERATION_LIMIT"},
        ),
    )

    assert ledger.snapshot("session-route-limit").missing_attempts == ("maps",)


def test_optimizer_result_is_persisted_as_candidate_review_without_model_copy():
    ledger = TravelSourceLedger()
    delegate = _OptimizerProvider()
    provider = require_travel_research_before_solving(
        delegate,
        ledger,
        "session-candidates",
    )
    context = ToolExecutionContext(
        actor=ActorContext(
            actor_type="user",
            user_id="user-a",
            username="traveller",
            display_name="Traveller",
            role_keys=frozenset({"viewer"}),
            permission_keys=frozenset(),
            channel="web",
        ),
        session_id="session-candidates",
        turn_id="turn-a",
        turn_index=1,
        channel="travel",
    )

    result = provider.execute_with_context(
        "run_skill",
        {"skill": "zhice-official/travel-planner", "params": {}},
        context,
    )

    assert result.metadata["code"] == "TRAVEL_CANDIDATE_REVIEW_REQUIRED"
    assert [name for name, _ in delegate.calls] == [
        "run_skill",
        "request_travel_candidate_review",
    ]
    review_args = delegate.calls[-1][1]
    assert review_args["recommended_candidate_id"] == "candidate-a"
    assert [item["candidate_id"] for item in review_args["candidates"]] == [
        "candidate-a",
        "candidate-b",
    ]


def test_optimizer_bridge_uses_trusted_candidate_tool_not_filtered_redispatch():
    ledger = TravelSourceLedger()
    delegate = _OptimizerProvider()
    candidate_review = _CandidateReviewTool()
    provider = require_travel_research_before_solving(
        delegate,
        ledger,
        "session-candidates-direct",
        candidate_review_tool=candidate_review,
    )
    context = ToolExecutionContext(
        actor=ActorContext(
            actor_type="user",
            user_id="user-a",
            username="traveller",
            display_name="Traveller",
            role_keys=frozenset({"viewer"}),
            permission_keys=frozenset(),
            channel="web",
        ),
        session_id="session-candidates-direct",
        turn_id="turn-a",
        turn_index=1,
        channel="travel",
    )

    result = provider.execute_with_context(
        "run_skill",
        {"skill": "zhice-official/travel-planner", "params": {}},
        context,
    )

    assert result.metadata["code"] == "TRAVEL_CANDIDATE_REVIEW_REQUIRED"
    assert [name for name, _ in delegate.calls] == ["run_skill"]
    assert len(candidate_review.calls) == 1
    assert candidate_review.calls[0][1] is context


class _OptimizerProvider(_Provider):
    def execute_with_context(
        self,
        name: str,
        args: dict[str, object],
        context: ToolExecutionContext,
    ) -> ToolResult:
        del context
        self.calls.append((name, args))
        if name == "run_skill":
            return ToolResult(
                output=json.dumps(
                    {
                        "status": "success",
                        "code": "OK",
                        "data": {
                            "selected_candidate": {"candidate_id": "candidate-a"},
                            "feasible_candidates": [
                                {"candidate_id": "candidate-a", "recommended": True},
                                {"candidate_id": "candidate-b", "recommended": False},
                            ],
                        },
                    }
                ),
                metadata={"code": "OK"},
            )
        return ToolResult(
            output='{"status":"waiting_for_user"}',
            metadata={"code": "TRAVEL_CANDIDATE_REVIEW_REQUIRED"},
        )


def test_source_evidence_coverage_is_unknown_before_expected_sources_are_registered():
    assert TravelSourceLedger().snapshot("session-travel").evidence_coverage is None


def test_source_ledger_distinguishes_forecast_from_historical_weather():
    ledger = TravelSourceLedger()
    ledger.register_expected(
        "session-travel",
        [
            "mcp__open-meteo__get_forecast",
            "mcp__open-meteo__get_historical_weather",
        ],
    )
    ledger.observe(
        "session-travel",
        "mcp__open-meteo__get_historical_weather",
        ToolResult(output='{"daily":{"time":["2025-08-20"]}}', metadata={"code": "MCP_OK"}),
    )

    historical = ledger.snapshot("session-travel")
    assert historical.forecast_expected is True
    assert historical.forecast_attempted is False
    assert historical.forecast_successful is False

    ledger.observe(
        "session-travel",
        "mcp__open-meteo__get_forecast",
        ToolResult(output='{"daily":{"time":["2026-08-20"]}}', metadata={"code": "MCP_OK"}),
    )
    forecast = ledger.snapshot("session-travel")
    assert forecast.forecast_attempted is True
    assert forecast.forecast_successful is True


def test_source_ledger_does_not_record_failed_or_unrelated_tools_as_success():
    ledger = TravelSourceLedger()
    ledger.observe(
        "session-travel",
        "mcp__tavily__tavily_search",
        ToolResult(output="timeout", is_error=True, metadata={"code": "MCP_TOOL_TIMEOUT"}),
    )
    ledger.observe(
        "session-travel",
        "mcp__other__calendar",
        ToolResult(output="{}", metadata={"code": "MCP_OK"}),
    )

    snapshot = ledger.snapshot("session-travel")
    assert snapshot.attempted == frozenset({"web"})
    assert snapshot.successful == frozenset()
    assert snapshot.retry_required == ("web",)
    assert source_category("mcp__other__calendar") == ""


def test_search_sources_require_one_narrower_retry_after_an_empty_result():
    ledger = TravelSourceLedger()

    ledger.observe(
        "session-travel",
        "mcp__xhs-readonly__search_notes",
        ToolResult(output='{"status":"success","data":{"text":"{\\"feeds\\":[]}"}}'),
    )

    assert ledger.snapshot("session-travel").retry_required == ("social",)

    ledger.observe(
        "session-travel",
        "mcp__xhs-readonly__search_notes",
        ToolResult(output='{"status":"success","data":{"text":"{\\"feeds\\":[]}"}}'),
    )

    assert ledger.snapshot("session-travel").retry_required == ()


def test_wrapped_mcp_social_rows_are_recognized_as_success():
    ledger = TravelSourceLedger()
    nested = json.dumps(
        {
            "status": "success",
            "data": {
                "text": json.dumps(
                    {"feeds": [{"noteCard": {"displayTitle": "西安公交攻略"}}]},
                    ensure_ascii=False,
                )
            },
        },
        ensure_ascii=False,
    )
    wrapped = json.dumps(
        {"status": "success", "output": nested, "metadata": {"code": "MCP_OK"}},
        ensure_ascii=False,
    )

    ledger.observe(
        "session-wrapped-social",
        "mcp__xhs-readonly__search_notes",
        ToolResult(output=wrapped, metadata={"code": "MCP_OK"}),
    )

    snapshot = ledger.snapshot("session-wrapped-social")
    assert snapshot.successful == frozenset({"social"})
    assert snapshot.retry_required == ()


def test_auth_failure_is_not_retried_and_prior_web_success_survives_timeout():
    ledger = TravelSourceLedger()
    ledger.observe(
        "session-travel",
        "mcp__xhs-readonly__search_notes",
        ToolResult(
            output='{"status":"error","code":"TRAVEL_SOURCE_AUTH_REQUIRED"}',
            is_error=True,
            metadata={"code": "TRAVEL_SOURCE_AUTH_REQUIRED"},
        ),
    )
    ledger.observe(
        "session-travel",
        "mcp__tavily__tavily_search",
        ToolResult(output='{"results":[{"title":"大理古城"}]}', metadata={"code": "MCP_OK"}),
    )
    ledger.observe(
        "session-travel",
        "mcp__tavily__tavily_search",
        ToolResult(output="timeout", is_error=True, metadata={"code": "MCP_TOOL_TIMEOUT"}),
    )

    snapshot = ledger.snapshot("session-travel")
    assert snapshot.retry_required == ()
    assert snapshot.successful == frozenset({"web"})
    stable_auth = ledger.admit_call(
        "session-travel",
        "mcp__xhs-readonly__search_notes",
        {"keywords": "另一组关键词"},
    )
    assert stable_auth is not None
    assert stable_auth.metadata["code"] == "TRAVEL_SOURCE_STABLY_UNAVAILABLE"
    satisfied = ledger.admit_call(
        "session-travel",
        "mcp__tavily__tavily_search",
        {"query": "另一组关键词"},
    )
    assert satisfied is not None
    assert satisfied.metadata["code"] == "TRAVEL_SOURCE_ALREADY_SATISFIED"


def test_hotel_city_resolution_failure_allows_one_narrower_retry():
    ledger = TravelSourceLedger()
    ledger.register_expected(
        "session-travel",
        ["mcp__amap-maps__maps_text_search", "search_travel_hotels"],
    )
    ledger.begin_finalization_budget("session-travel")
    ledger.observe(
        "session-travel",
        "mcp__amap-maps__maps_text_search",
        ToolResult(
            output='{"pois":[{"name":"全季酒店(西安钟楼北大街地铁站店)"}]}',
            metadata={"code": "MCP_OK"},
        ),
    )
    ledger.observe(
        "session-travel",
        "search_travel_hotels",
        ToolResult(
            output='{"status":"error","code":"HOTEL_CITY_RESOLUTION_FAILED"}',
            is_error=True,
            metadata={"code": "HOTEL_CITY_RESOLUTION_FAILED"},
        ),
    )

    snapshot = ledger.snapshot("session-travel")
    assert snapshot.missing_attempts == ("maps",)
    assert snapshot.retry_required == ("lodging",)
    retry = ledger.admit_call(
        "session-travel",
        "search_travel_hotels",
        {"city": "西安市", "checkin": "2026-09-15", "checkout": "2026-09-17"},
    )
    assert retry is None


def test_large_valid_search_result_is_not_misclassified_as_empty():
    ledger = TravelSourceLedger()
    output = json.dumps(
        {"results": [{"title": "沈阳酒店", "content": "x" * 25_000}]},
        ensure_ascii=False,
    )

    ledger.observe(
        "session-travel",
        "mcp__tavily__tavily_search",
        ToolResult(output=output, metadata={"code": "MCP_OK"}),
    )

    snapshot = ledger.snapshot("session-travel")
    assert snapshot.successful == frozenset({"web"})
    assert snapshot.retry_required == ()


def test_truncated_tavily_json_is_compacted_to_complete_leading_results():
    output = (
        '{"query":"重庆攻略","results":['
        '{"title":"洪崖洞错峰攻略","url":"https://example.com/one",'
        '"content":"傍晚提前到达，优先乘坐地铁。"},'
        '{"title":"未完整的第二条","url":"https://example.com/two","content":"tail'
    )
    ledger = TravelSourceLedger()
    guarded = guard_travel_tools(
        [_StaticTool("mcp__tavily__tavily_search", output)],
        ledger,
        "session-truncated-web",
    )[0]

    result = guarded.execute({"query": "重庆攻略"})

    payload = json.loads(result.output)
    assert payload["results"] == [
        {
            "title": "洪崖洞错峰攻略",
            "url": "https://example.com/one",
            "content": "傍晚提前到达，优先乘坐地铁。",
        }
    ]
    assert result.metadata["travel_result_compacted"] is True
    assert ledger.search_evidence("session-truncated-web", "web")[0]["title"] == "洪崖洞错峰攻略"


def test_source_ledger_clear_removes_terminal_session_state():
    ledger = TravelSourceLedger()
    ledger.register_expected("session-travel", ["mcp__amap__search"])
    ledger.clear("session-travel")

    assert ledger.snapshot("session-travel").expected == frozenset()


def test_preferred_travel_tools_select_one_query_schema_per_source_category():
    names = [
        "mcp__amap__maps_search_detail",
        "mcp__amap__maps_text_search",
        "mcp__amap__maps_geo",
        "mcp__amap__maps_direction_walking",
        "mcp__open-meteo__get_historical_weather",
        "mcp__12306__get-tickets",
        "mcp__tavily__tavily_extract",
        "mcp__tavily__tavily_search",
        "mcp__xhs-readonly__get_note_detail",
        "mcp__xhs-readonly__search_notes",
    ]

    assert preferred_travel_tool_names(names) == (
        "mcp__amap__maps_text_search",
        "mcp__amap__maps_geo",
        "mcp__amap__maps_direction_walking",
        "mcp__open-meteo__get_historical_weather",
        "mcp__12306__get-tickets",
        "mcp__tavily__tavily_search",
        "mcp__xhs-readonly__search_notes",
    )


def test_hotel_browser_is_a_distinct_lodging_source_and_preferred_search():
    name = "mcp__hotel-browser__search_hotels"

    assert source_category(name) == "lodging"
    assert source_category("search_travel_hotels") == "lodging"
    assert preferred_travel_tool_names([name]) == (name,)


def test_travel_guard_blocks_exact_duplicate_without_recalling_remote_tool(monkeypatch):
    monkeypatch.setattr(source_ledger_module, "_china_today", lambda: date(2026, 9, 1))
    ledger = TravelSourceLedger()
    delegate = _CountingTool("mcp__12306__get-tickets")
    guarded = guard_travel_tools([delegate], ledger, "session-a")[0]

    first = guarded.execute({"date": "2026-09-10", "from": "北京", "to": "沈阳"})
    duplicate = guarded.execute(
        {"to": "沈阳", "from": "北京", "date": "2026-09-10"}
    )

    assert first.is_error is False
    assert duplicate.is_error is True
    assert duplicate.metadata["code"] == "TRAVEL_SOURCE_ALREADY_QUERIED"
    assert duplicate.metadata["travel_progress_visibility"] == "internal"
    assert len(delegate.calls) == 1


def test_travel_guard_budget_is_session_scoped(monkeypatch):
    monkeypatch.setattr(source_ledger_module, "_china_today", lambda: date(2026, 9, 1))
    ledger = TravelSourceLedger()
    delegate = _CountingTool("mcp__12306__get-tickets")
    guarded_a = guard_travel_tools([delegate], ledger, "session-a")[0]
    guarded_b = guard_travel_tools([delegate], ledger, "session-b")[0]

    assert guarded_a.execute({"date": "2026-09-10"}).is_error is False
    assert guarded_a.execute({"date": "2026-09-12"}).is_error is False
    exhausted = guarded_a.execute({"date": "2026-09-13"})
    independent = guarded_b.execute({"date": "2026-09-13"})

    assert exhausted.metadata["code"] == "TRAVEL_SOURCE_BUDGET_EXHAUSTED"
    assert independent.is_error is False
    assert len(delegate.calls) == 3


def test_transport_lookup_does_not_consume_the_two_ticket_queries(monkeypatch):
    monkeypatch.setattr(source_ledger_module, "_china_today", lambda: date(2026, 9, 1))
    ledger = TravelSourceLedger()
    lookup = _CountingTool("mcp__12306__get-station-code-of-citys")
    tickets = _CountingTool("mcp__12306__get-tickets")
    guarded_lookup, guarded_tickets = guard_travel_tools(
        [lookup, tickets], ledger, "session-a"
    )

    assert guarded_lookup.execute({"citys": "北京|沈阳"}).is_error is False
    assert guarded_tickets.execute({"date": "2026-09-10", "direction": "outbound"}).is_error is False
    assert guarded_tickets.execute({"date": "2026-09-12", "direction": "return"}).is_error is False
    exhausted = guarded_tickets.execute({"date": "2026-09-13", "direction": "extra"})

    assert exhausted.metadata["code"] == "TRAVEL_SOURCE_BUDGET_EXHAUSTED"
    assert len(lookup.calls) == 1
    assert len(tickets.calls) == 2


def test_ticket_station_codes_must_come_from_current_session_lookup(monkeypatch):
    monkeypatch.setattr(source_ledger_module, "_china_today", lambda: date(2026, 9, 1))
    ledger = TravelSourceLedger()
    delegate = _CountingTool("mcp__12306__get-tickets")
    guarded = guard_travel_tools([delegate], ledger, "session-a")[0]

    rejected = guarded.execute(
        {"date": "2026-09-10", "fromStation": "IFP", "toStation": "SYT"}
    )
    ledger.observe(
        "session-a",
        "mcp__12306__get-station-code-of-citys",
        ToolResult(
            output='[{"station_name":"北京朝阳","station_code":"IFP"},{"station_name":"沈阳","station_code":"SYT"}]',
            metadata={"code": "MCP_OK"},
        ),
    )
    allowed = guarded.execute(
        {"date": "2026-09-10", "fromStation": "IFP", "toStation": "SYT"}
    )

    assert rejected.is_error is True
    assert rejected.metadata["code"] == "TRAVEL_STATION_CODE_UNVERIFIED"
    assert rejected.metadata["travel_progress_visibility"] == "internal"
    assert allowed.is_error is False
    assert len(delegate.calls) == 1


def test_ticket_query_outside_sale_window_returns_not_on_sale_without_remote_call(monkeypatch):
    monkeypatch.setattr(source_ledger_module, "_china_today", lambda: date(2026, 9, 1))
    ledger = TravelSourceLedger()
    delegate = _CountingTool("mcp__12306__get-tickets")
    guarded = guard_travel_tools([delegate], ledger, "session-a")[0]

    result = guarded.execute({"date": "2026-09-20", "from": "BJP", "to": "SYT"})
    payload = json.loads(result.output)

    assert result.is_error is False
    assert result.metadata["travel_source_status"] == "not_on_sale"
    assert payload["status"] == "not_on_sale"
    assert payload["sale_open_date"] == "2026-09-06"
    assert delegate.calls == []
    snapshot = ledger.snapshot("session-a")
    assert snapshot.successful == frozenset({"transport"})
    assert snapshot.transport_ticket_attempt_count == 1
    assert snapshot.transport_ticket_success_count == 1
    assert snapshot.transport_ticket_not_on_sale is True


def test_source_ledger_records_verified_amap_transit_availability():
    ledger = TravelSourceLedger()
    ledger.observe(
        "session-a",
        "mcp__amap-maps__maps_direction_transit_integrated",
        ToolResult(
            output=(
                '{"route":{"transits":[{"segments":[{"bus":{"buslines":['
                '{"departure_stop":{"name":"沈阳站"},'
                '"arrival_stop":{"name":"怀远门"}}]}}]}]}}'
            ),
            metadata={"code": "MCP_OK"},
        ),
    )

    assert ledger.snapshot("session-a").verified_transit_available is True


def test_source_ledger_keeps_late_12306_rows_from_returned_day_fragment():
    raw = """C77(实际车次train_no: 770000C77000) 重庆北(telecode: CUW) -> 成都东(telecode: ICW) 06:18 -> 09:14 历时：02:56
- 二等座: 有剩余 94元
G8771(实际车次train_no: 77000G877105) 重庆西(telecode: CXW) -> 成都东(telecode: ICW) 13:44 -> 16:36 历时：02:52
- 二等座: 有剩余 218元
    """
    ledger = TravelSourceLedger()
    ledger.observe(
        "session-rail",
        "mcp__12306__get-tickets",
        ToolResult(output=raw, metadata={"code": "MCP_OK"}),
        {"date": "2026-08-22", "fromStation": "CQW", "toStation": "CDW"},
    )

    rows = ledger.structured_results("session-rail")["rail_options"]
    assert [row["service_name"] for row in rows] == ["C77", "G8771"]
    assert rows[-1]["departure_time"] == "13:44"
    assert rows[-1]["price_cny_per_person"] == 218.0


def test_source_operation_separates_amap_search_geocode_and_route_budgets():
    assert source_operation("mcp__12306__get-station-code-of-citys") == "transport_lookup"
    assert source_operation("mcp__12306__get-stations-code-in-city") == "transport_lookup"
    assert source_operation("mcp__12306__get-tickets") == "transport_ticket"
    assert source_operation("mcp__amap__maps_text_search") == "maps_search"
    assert source_operation("mcp__amap__maps_geo") == "maps_geocode"
    assert source_operation("mcp__amap__maps_direction_transit_integrated") == "maps_route"


def test_amap_geocode_budget_covers_sixteen_unique_trip_locations():
    ledger = TravelSourceLedger()
    delegate = _CountingTool("mcp__amap-maps__maps_geo")
    guarded = guard_travel_tools([delegate], ledger, "session-a")[0]

    for index in range(16):
        assert guarded.execute({"address": f"place-{index}", "city": "沈阳"}).is_error is False

    exhausted = guarded.execute({"address": "place-16", "city": "沈阳"})

    assert exhausted.metadata["code"] == "TRAVEL_SOURCE_BUDGET_EXHAUSTED"
    assert len(delegate.calls) == 16


def test_amap_lodging_search_has_reserved_budget_after_generic_searches_are_exhausted():
    ledger = TravelSourceLedger()
    delegate = _CountingTool("mcp__amap-maps__maps_text_search")
    guarded = guard_travel_tools([delegate], ledger, "session-a")[0]

    for index in range(18):
        assert guarded.execute({"keywords": f"景点-{index}", "city": "沈阳"}).is_error is False
    generic_exhausted = guarded.execute({"keywords": "额外景点", "city": "沈阳"})
    hotel = guarded.execute({"keywords": "沈阳西塔酒店", "city": "沈阳"})

    assert generic_exhausted.metadata["code"] == "TRAVEL_SOURCE_BUDGET_EXHAUSTED"
    assert hotel.is_error is False
    assert len(delegate.calls) == 19


def test_finalization_allows_six_exact_lodging_searches_without_changing_other_budgets():
    ledger = TravelSourceLedger()
    map_delegate = _CountingTool("mcp__amap-maps__maps_text_search")
    hotel_delegate = _StaticTool(
        "search_travel_hotels",
        '{"status":"ok","hotels":[{"hotel_name":"重庆测试酒店","price_per_night_cny":218}]}',
    )
    guarded_map, guarded_hotel = guard_travel_tools(
        [map_delegate, hotel_delegate], ledger, "session-hotel-map-final"
    )

    ledger.begin_finalization_budget("session-hotel-map-final")

    for index in range(6):
        result = guarded_map.execute(
            {"keywords": f"重庆准确酒店{index}", "city": "重庆"}
        )
        assert result.is_error is False

    exhausted = guarded_map.execute({"keywords": "重庆准确酒店6", "city": "重庆"})
    assert exhausted.metadata["code"] == "TRAVEL_SOURCE_BUDGET_EXHAUSTED"

    for index in range(18):
        result = guarded_map.execute({"keywords": f"景点-{index}", "city": "重庆"})
        assert result.is_error is False
    generic_exhausted = guarded_map.execute({"keywords": "额外景点", "city": "重庆"})
    assert generic_exhausted.metadata["code"] == "TRAVEL_SOURCE_BUDGET_EXHAUSTED"

    lodging_arguments = {
        "city": "重庆",
        "checkin": "2026-09-01",
        "checkout": "2026-09-02",
        "max_price_per_night": 250,
    }
    assert guarded_hotel.execute(lodging_arguments).is_error is False
    lodging_repeated = guarded_hotel.execute(lodging_arguments)
    assert lodging_repeated.metadata["code"] == "TRAVEL_SOURCE_ALREADY_QUERIED"
    assert len(map_delegate.calls) == 24
    assert len(hotel_delegate.calls) == 1


def test_candidate_finalization_gets_one_fresh_detail_budget_without_repeating_queries():
    ledger = TravelSourceLedger()
    delegate = _CountingTool("mcp__amap-maps__maps_direction_transit_integrated")
    guarded = guard_travel_tools([delegate], ledger, "session-a")[0]

    for index in range(16):
        assert guarded.execute({"origin": f"candidate-{index}", "destination": "hotel"}).is_error is False
    assert guarded.execute({"origin": "final-0", "destination": "station"}).metadata["code"] == (
        "TRAVEL_SOURCE_BUDGET_EXHAUSTED"
    )

    ledger.begin_finalization_budget("session-a")

    duplicate = guarded.execute({"origin": "candidate-0", "destination": "hotel"})
    assert duplicate.metadata["code"] == "TRAVEL_SOURCE_ALREADY_QUERIED"
    for index in range(16):
        assert guarded.execute({"origin": f"final-{index}", "destination": "station"}).is_error is False
    assert guarded.execute({"origin": "final-16", "destination": "station"}).metadata["code"] == (
        "TRAVEL_SOURCE_BUDGET_EXHAUSTED"
    )

    ledger.begin_finalization_budget("session-a")
    still_exhausted = guarded.execute({"origin": "final-17", "destination": "station"})
    assert still_exhausted.metadata["code"] == "TRAVEL_SOURCE_BUDGET_EXHAUSTED"


def test_candidate_and_finalization_each_allow_one_lodging_query():
    ledger = TravelSourceLedger()
    delegate = _StaticTool(
        "search_travel_hotels",
        '{"status":"ok","hotels":[{"hotel_name":"重庆测试酒店","price_per_night_cny":218}]}',
    )
    guarded = guard_travel_tools([delegate], ledger, "session-hotel-final")[0]
    arguments = {
        "city": "重庆",
        "checkin": "2026-08-20",
        "checkout": "2026-08-22",
        "max_price_per_night": 250,
    }

    assert guarded.execute(arguments).is_error is False
    ledger.begin_finalization_budget("session-hotel-final")
    assert guarded.execute(arguments).is_error is False

    repeated = guarded.execute(arguments)
    assert repeated.metadata["code"] == "TRAVEL_SOURCE_ALREADY_QUERIED"
    assert len(delegate.calls) == 2


def test_candidate_finalization_resets_generic_map_search_budget():
    ledger = TravelSourceLedger()
    delegate = _CountingTool("mcp__amap-maps__maps_text_search")
    guarded = guard_travel_tools([delegate], ledger, "session-map-final")[0]

    for index in range(18):
        assert guarded.execute({"keywords": f"景点-{index}", "city": "西安"}).is_error is False
    assert guarded.execute({"keywords": "额外景点", "city": "西安"}).metadata["code"] == (
        "TRAVEL_SOURCE_BUDGET_EXHAUSTED"
    )

    ledger.begin_finalization_budget("session-map-final")

    assert guarded.execute({"keywords": "永宁门", "city": "西安"}).is_error is False


def test_travel_guard_compacts_amap_transit_without_losing_lines_and_stops():
    raw = json.dumps(
        {
            "route": {
                "origin": "123.1,41.1",
                "destination": "123.2,41.2",
                "distance": "4200",
                "transits": [{
                    "duration": "1800",
                    "walking_distance": "420",
                    "segments": [{
                        "walking": {
                            "origin": "123.1,41.1",
                            "destination": "123.11,41.11",
                            "distance": "200",
                            "duration": "180",
                            "steps": [{"instruction": "冗长步行说明" * 100}],
                        },
                        "bus": {"buslines": [{
                            "name": "地铁1号线",
                            "departure_stop": {"name": "中街"},
                            "arrival_stop": {"name": "青年大街"},
                            "via_stops": [{"name": "怀远门"}],
                            "polyline": "123.1,41.1;" * 500,
                        }]},
                    }],
                }],
            }
        },
        ensure_ascii=False,
    )
    delegate = _StaticTool("mcp__amap-maps__maps_direction_transit_integrated", raw)
    result = guard_travel_tools([delegate], TravelSourceLedger(), "session-a")[0].execute({})
    compact = json.loads(result.output)

    line = compact["route"]["transits"][0]["segments"][0]["bus"]["buslines"][0]
    assert line["name"] == "地铁1号线"
    assert line["departure_stop"]["name"] == "中街"
    assert line["arrival_stop"]["name"] == "青年大街"
    assert line["via_stops"] == [{"name": "怀远门"}]
    assert "steps" not in result.output
    assert "polyline" not in result.output
    assert len(result.output) < len(raw) / 4


def test_travel_guard_compacts_amap_poi_and_geocode_candidates():
    poi_raw = json.dumps(
        {"pois": [{"id": "p1", "name": "酒店", "address": "中街1号", "photos": [{"url": "x" * 3000}]}]},
        ensure_ascii=False,
    )
    geo_raw = json.dumps(
        {"return": [{"province": "辽宁省", "city": "沈阳市", "location": f"123.{index},41.8", "extra": "x" * 500} for index in range(12)]},
        ensure_ascii=False,
    )
    poi = guard_travel_tools(
        [_StaticTool("mcp__amap-maps__maps_text_search", poi_raw)],
        TravelSourceLedger(),
        "session-a",
    )[0].execute({})
    geo = guard_travel_tools(
        [_StaticTool("mcp__amap-maps__maps_geo", geo_raw)],
        TravelSourceLedger(),
        "session-b",
    )[0].execute({})

    assert json.loads(poi.output) == {
        "pois": [{"id": "p1", "name": "酒店", "address": "中街1号"}]
    }
    assert len(json.loads(geo.output)["return"]) == 8
    assert "extra" not in geo.output


def test_travel_guard_rejects_non_hotel_poi_from_lodging_search():
    raw = json.dumps(
        {
            "pois": [
                {
                    "id": "bus-stop",
                    "name": "钟楼饭店(公交站)",
                    "address": "观光巴士钟楼饭店线",
                    "typecode": "150700",
                    "cityname": "西安市",
                }
            ]
        },
        ensure_ascii=False,
    )
    guarded = guard_travel_tools(
        [_StaticTool("mcp__amap-maps__maps_text_search", raw)],
        TravelSourceLedger(),
        "session-hotel-filter",
    )[0]

    result = guarded.execute({"keywords": "钟楼饭店", "city": "西安", "types": "100000"})

    assert result.is_error is True
    assert result.metadata["code"] == "TRAVEL_HOTEL_POI_NOT_FOUND"


def test_travel_guard_keeps_actual_hotel_from_lodging_search():
    raw = json.dumps(
        {
            "pois": [
                {
                    "id": "hotel-1",
                    "name": "全季酒店(西安钟楼店)",
                    "address": "南大街1号",
                    "location": "108.95,34.26",
                    "typecode": "100103",
                    "cityname": "西安市",
                }
            ]
        },
        ensure_ascii=False,
    )
    guarded = guard_travel_tools(
        [_StaticTool("mcp__amap-maps__maps_text_search", raw)],
        TravelSourceLedger(),
        "session-hotel-keep",
    )[0]

    result = guarded.execute({"keywords": "全季酒店 西安钟楼", "city": "西安", "types": "100000"})

    assert result.is_error is False
    assert json.loads(result.output)["pois"][0]["name"] == "全季酒店(西安钟楼店)"


def test_travel_guard_rejects_amap_same_name_candidates_outside_requested_city():
    raw = json.dumps(
        {
            "return": [
                {
                    "province": "天津市",
                    "city": "天津市",
                    "district": "河东区",
                    "location": "117.217022,39.128774",
                }
            ]
        },
        ensure_ascii=False,
    )
    guarded = guard_travel_tools(
        [_StaticTool("mcp__amap-maps__maps_geo", raw)],
        TravelSourceLedger(),
        "session-city",
    )[0]

    result = guarded.execute({"address": "全季酒店(成都宽窄巷子店)", "city": "成都"})

    assert result.is_error is True
    assert result.metadata["code"] == "TRAVEL_MAP_CITY_MISMATCH"
    assert "117.217022" not in result.output


def test_travel_guard_keeps_only_amap_candidates_in_requested_city():
    raw = json.dumps(
        {
            "pois": [
                {"id": "wrong", "name": "宽窄巷子", "cityname": "博乐市"},
                {
                    "id": "right",
                    "name": "宽窄巷子",
                    "pname": "四川省",
                    "cityname": "成都市",
                    "adname": "青羊区",
                    "location": "104.055180,30.663213",
                },
            ]
        },
        ensure_ascii=False,
    )
    guarded = guard_travel_tools(
        [_StaticTool("mcp__amap-maps__maps_text_search", raw)],
        TravelSourceLedger(),
        "session-city-poi",
    )[0]

    result = guarded.execute({"keywords": "宽窄巷子", "city": "成都市"})

    assert result.is_error is False
    assert [item["id"] for item in json.loads(result.output)["pois"]] == ["right"]


def test_travel_guard_keeps_citylimited_amap_row_without_admin_fields():
    raw = json.dumps(
        {
            "pois": [
                {
                    "id": "white-horse-temple",
                    "name": "白马寺",
                    "address": "白马寺镇洛白路6号",
                    "typecode": "110205",
                }
            ]
        },
        ensure_ascii=False,
    )
    guarded = guard_travel_tools(
        [_StaticTool("mcp__amap-maps__maps_text_search", raw)],
        TravelSourceLedger(),
        "session-citylimit-no-admin",
    )[0]

    result = guarded.execute({"keywords": "白马寺", "city": "洛阳"})

    assert result.is_error is False
    assert json.loads(result.output)["pois"][0]["name"] == "白马寺"


def test_travel_guard_enables_amap_city_strict_search_for_explicit_city():
    delegate = _StaticTool(
        "mcp__amap-maps__maps_text_search",
        '{"pois":[{"id":"landmark","name":"解放碑步行街","address":"重庆市渝中区"}]}',
    )
    guarded = guard_travel_tools(
        [delegate], TravelSourceLedger(), "session-city-limit"
    )[0]

    result = guarded.execute({"keywords": "解放碑", "city": "重庆"})

    assert result.is_error is False
    assert delegate.calls == [
        {"keywords": "解放碑", "city": "重庆", "citylimit": "true"}
    ]


def test_travel_guard_rejects_hotel_food_and_parking_for_landmark_search():
    raw = json.dumps(
        {
            "pois": [
                {"id": "hotel", "name": "重庆解放碑嘉遇酒店", "typecode": "100105"},
                {"id": "food", "name": "解放碑美食城", "typecode": "050000"},
                {"id": "parking", "name": "解放碑停车场", "typecode": "150900"},
            ]
        },
        ensure_ascii=False,
    )
    guarded = guard_travel_tools(
        [_StaticTool("mcp__amap-maps__maps_text_search", raw)],
        TravelSourceLedger(),
        "session-landmark-category",
    )[0]

    result = guarded.execute({"keywords": "解放碑", "city": "重庆"})

    assert result.is_error is True
    assert result.metadata["code"] == "TRAVEL_MAP_POI_MISMATCH"
    assert "嘉遇酒店" not in result.output


def test_travel_guard_accepts_dali_prefecture_county_alias_but_keeps_cross_city_guard():
    raw = json.dumps(
        {
            "pois": [
                {"id": "wrong", "name": "双廊古镇", "cityname": "博乐市"},
                {
                    "id": "right",
                    "name": "双廊古镇",
                    "pname": "云南省",
                    "cityname": "大理白族自治州",
                    "adname": "洱源县",
                    "address": "双廊镇",
                    "location": "100.190,25.910",
                },
            ]
        },
        ensure_ascii=False,
    )
    guarded = guard_travel_tools(
        [_StaticTool("mcp__amap-maps__maps_text_search", raw)],
        TravelSourceLedger(),
        "session-dali-poi",
    )[0]

    result = guarded.execute({"keywords": "双廊古镇", "city": "大理"})

    assert result.is_error is False
    assert [item["id"] for item in json.loads(result.output)["pois"]] == ["right"]
