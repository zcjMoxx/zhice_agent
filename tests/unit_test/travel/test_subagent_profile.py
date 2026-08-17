import json
from types import MappingProxyType

from agent.app.runtime import _travel_child_llm_factory
from agent.applications.travel.source_ledger import (
    TravelSourceLedger,
    require_travel_finalization_before_saving,
)
from agent.applications.travel.subagents import (
    TRAVEL_CANDIDATE_RESEARCH_PROFILES,
    TRAVEL_FINAL_ROUTE_PROFILE,
    TRAVEL_FINAL_STAY_PROFILE,
    TRAVEL_FINAL_WEATHER_PROFILE,
    TRAVEL_GUIDES_PROFILE,
    TRAVEL_STAY_POI_PROFILE,
    TRAVEL_TRANSPORT_WEATHER_PROFILE,
    compact_travel_final_route_results,
    require_exact_travel_delegation,
    travel_subagent_config_for_stage,
    with_travel_research_profile,
)
from agent.protocols.llm import LLMEndpoint
from agent.protocols.subagent import SubagentProfile
from agent.protocols.tool import ToolResult
from agent.subagents.config import SubagentConfig


class _ParentLlm:
    def __init__(self, endpoints: list[LLMEndpoint], current: str) -> None:
        self._endpoints = endpoints
        self._current = next(endpoint for endpoint in endpoints if endpoint.name == current)

    def endpoints(self) -> list[LLMEndpoint]:
        return list(self._endpoints)

    def current_endpoint(self) -> LLMEndpoint:
        return self._current


def _endpoint(
    name: str,
    *,
    role: str = "default",
    model: str = "model",
    priority: int = 1,
) -> LLMEndpoint:
    return LLMEndpoint(
        name=name,
        protocol="openai",
        base_url="https://example.invalid/v1",
        model=model,
        api_key="test-key",
        role=role,
        priority=priority,
    )


def test_travel_child_model_inherits_parent_until_fast_role_is_explicitly_configured():
    main = _endpoint("main", model="main-model")
    profile = SubagentProfile(
        name=TRAVEL_GUIDES_PROFILE,
        description="Collect guides.",
        tools=("mcp__tavily__tavily_search",),
        model_role="fast",
    )

    inherited = _travel_child_llm_factory(_ParentLlm([main], "main"))(profile)
    assert inherited.current_endpoint().name == "main"
    assert inherited.current_endpoint().model == "main-model"

    fast = _endpoint("fast", role="fast", model="fast-model")
    selected = _travel_child_llm_factory(_ParentLlm([main, fast], "main"))(profile)
    assert selected.current_endpoint().name == "fast"
    assert selected.current_endpoint().model == "fast-model"


def test_travel_child_model_uses_highest_priority_fast_endpoint():
    main = _endpoint("main", model="main-model")
    slow_fast = _endpoint("slow-fast", role="fast", priority=8)
    preferred_fast = _endpoint("preferred-fast", role="fast", priority=2)
    profile = SubagentProfile(
        name=TRAVEL_GUIDES_PROFILE,
        description="Collect guides.",
        tools=("mcp__tavily__tavily_search",),
        model_role="fast",
    )

    selected = _travel_child_llm_factory(
        _ParentLlm([main, slow_fast, preferred_fast], "main")
    )(profile)

    assert selected.current_endpoint().name == "preferred-fast"


def test_travel_profile_is_added_with_bounded_read_only_sources():
    config = SubagentConfig(
        enabled=True,
        max_parallel=8,
        max_tasks_per_call=8,
        max_subagents_per_parent_turn=8,
        max_batches_per_parent_turn=2,
        profiles=MappingProxyType(
            {
                "explorer": SubagentProfile(
                    name="explorer",
                    description="Inspect files.",
                    tools=("read_file",),
                )
            }
        ),
    )

    effective = with_travel_research_profile(config)

    transport = effective.get_profile(TRAVEL_TRANSPORT_WEATHER_PROFILE)
    stay = effective.get_profile(TRAVEL_STAY_POI_PROFILE)
    guides = effective.get_profile(TRAVEL_GUIDES_PROFILE)
    final_stay = effective.get_profile(TRAVEL_FINAL_STAY_PROFILE)
    final_route = effective.get_profile(TRAVEL_FINAL_ROUTE_PROFILE)
    assert all(
        profile is not None
        for profile in (transport, stay, guides, final_stay, final_route)
    )
    assert transport is not None and stay is not None and guides is not None
    assert transport.tools == (
        "mcp__12306__get-station-code-of-citys",
        "mcp__12306__get-tickets",
        "mcp__amap-maps__maps_geo",
        "mcp__open-meteo__geocode_place",
        "mcp__open-meteo__get_forecast",
        "mcp__open-meteo__get_historical_weather",
    )
    assert transport.initial_tools == (
        "mcp__12306__get-station-code-of-citys",
        "mcp__12306__get-tickets",
        "mcp__amap-maps__maps_geo",
        "mcp__open-meteo__geocode_place",
        "mcp__open-meteo__get_forecast",
        "mcp__open-meteo__get_historical_weather",
    )
    assert "city-scoped AMap geocoder" in transport.description
    assert "search_travel_hotels" in stay.tools
    assert "mcp__xhs-readonly__*" not in stay.tools
    assert stay.max_tool_iterations == 6
    assert stay.timeout_seconds == 150
    assert guides.tools == (
        "mcp__tavily__tavily_search",
        "mcp__xhs-readonly__search_notes",
        "mcp__xhs-readonly__get_note_detail",
    )
    assert guides.timeout_seconds == 180
    assert final_stay is not None and final_route is not None
    assert "search_travel_hotels" in final_stay.tools
    assert "mcp__amap-maps__maps_text_search" in final_stay.initial_tools
    assert "mcp__amap-maps__maps_direction_transit_integrated" in final_route.tools
    assert "mcp__amap-maps__maps_direction_driving" in final_route.tools
    assert "游客中心" in final_route.description
    assert "transits=[]" in final_route.description
    assert final_route.initial_tools == final_route.tools
    assert final_stay.max_tool_iterations == 10
    assert "every distinct overnight area" in final_stay.description
    assert final_route.max_tool_iterations == 12
    assert final_route.timeout_seconds == 150
    assert final_route.max_result_chars == 8_000
    assert all(
        profile.workspace_mode == "shared_readonly" and profile.model_role == "fast"
        for profile in (transport, stay, guides, final_stay, final_route)
    )
    assert effective.max_parallel == 3
    assert effective.max_tasks_per_call == 3
    assert effective.max_subagents_per_parent_turn == 3
    assert effective.max_batches_per_parent_turn == 1
    assert effective.get_profile("explorer") is config.get_profile("explorer")


def test_travel_profile_does_not_enable_subagents_or_override_operator_profile():
    disabled = SubagentConfig()
    assert with_travel_research_profile(disabled) is disabled

    custom = SubagentProfile(
        name=TRAVEL_STAY_POI_PROFILE,
        description="Operator-defined travel profile.",
        tools=("mcp__approved__*",),
    )
    configured = SubagentConfig(
        enabled=True,
        profiles=MappingProxyType({TRAVEL_STAY_POI_PROFILE: custom}),
    )

    effective = with_travel_research_profile(configured)
    assert effective.get_profile(TRAVEL_STAY_POI_PROFILE) is custom
    assert effective.get_profile(TRAVEL_TRANSPORT_WEATHER_PROFILE) is not None
    assert effective.get_profile(TRAVEL_GUIDES_PROFILE) is not None


def test_final_route_provider_compacts_amap_to_one_walkable_transit():
    raw = {
        "route": {
            "origin": "108.1,34.1",
            "destination": "109.1,34.2",
            "distance": "38000",
            "transits": [
                {
                    "duration": "2000",
                    "walking_distance": "2500",
                    "segments": [_route_segment("快线", "甲站", "乙站", 12)],
                },
                {
                    "duration": "2600",
                    "walking_distance": "900",
                    "segments": [_route_segment("地铁9号线", "华清池", "纺织城", 12)],
                },
            ],
        }
    }
    provider = compact_travel_final_route_results(_RouteProvider(raw))

    result = provider.execute(
        "mcp__amap-maps__maps_direction_transit_integrated",
        {"origin": "108.1,34.1", "destination": "109.1,34.2"},
    )

    payload = json.loads(result.output)
    transits = payload["route"]["transits"]
    lines = transits[0]["segments"][0]["bus"]["buslines"]
    assert len(transits) == 1
    assert transits[0]["walking_distance"] == "900"
    assert lines[0]["name"] == "地铁9号线"
    assert lines[0]["departure_stop"]["name"] == "华清池"
    assert len(lines[0]["via_stops"]) == 8
    assert "steps" not in result.output
    assert result.metadata["travel_route_compacted"] is True
    assert result.metadata["original_output_chars"] > len(result.output)


def test_final_route_provider_recovers_complete_transit_from_truncated_json():
    raw = json.dumps(
        {
            "route": {
                "origin": "108.1,34.1",
                "destination": "109.1,34.2",
                "distance": "38000",
                "transits": [
                    {
                        "duration": "2600",
                        "walking_distance": "900",
                        "segments": [_route_segment("地铁9号线", "华清池", "纺织城", 12)],
                    },
                    {
                        "duration": "3000",
                        "walking_distance": "1100",
                        "segments": [_route_segment("备选线路", "甲站", "乙站", 12)],
                    },
                ],
            }
        },
        ensure_ascii=False,
    )
    truncated = raw[: raw.index("备选线路") + 3]
    provider = compact_travel_final_route_results(_RouteProvider(truncated))

    result = provider.execute(
        "mcp__amap-maps__maps_direction_transit_integrated",
        {},
    )

    payload = json.loads(result.output)
    transit = payload["route"]["transits"][0]
    assert transit["walking_distance"] == "900"
    assert transit["segments"][0]["bus"]["buslines"][0]["name"] == "地铁9号线"
    assert result.metadata["travel_route_compacted"] is True


def test_final_route_provider_compacts_amap_driving_fallback():
    raw = {
        "route": {
            "origin": "111.620940,33.780469",
            "destination": "111.657836,33.739153",
            "taxi_cost": "21",
            "paths": [
                {
                    "distance": "8071",
                    "duration": "1100",
                    "strategy": "速度优先",
                    "tolls": "0",
                    "steps": [{"instruction": "不应进入模型上下文"}],
                },
                {"distance": "9200", "duration": "1500", "steps": []},
            ],
        }
    }
    provider = compact_travel_final_route_results(_RouteProvider(raw))

    result = provider.execute(
        "mcp__amap-maps__maps_direction_driving",
        {"origin": "111.620940,33.780469", "destination": "111.657836,33.739153"},
    )

    payload = json.loads(result.output)
    assert payload == {
        "route": {
            "origin": "111.620940,33.780469",
            "destination": "111.657836,33.739153",
            "taxi_cost": "21",
            "paths": [
                {
                    "distance": "8071",
                    "duration": "1100",
                    "strategy": "速度优先",
                    "tolls": "0",
                }
            ],
        }
    }
    assert result.metadata["travel_route_compacted"] is True
    assert "steps" not in result.output


def test_travel_stage_profiles_are_mutually_exclusive_and_bounded():
    config = SubagentConfig(
        enabled=True,
        max_parallel=8,
        max_tasks_per_call=8,
        max_subagents_per_parent_turn=8,
        profiles=MappingProxyType(
            {
                "explorer": SubagentProfile(
                    name="explorer",
                    description="Inspect files.",
                    tools=("read_file",),
                )
            }
        ),
    )

    candidate = travel_subagent_config_for_stage(config, finalization=False)
    finalization = travel_subagent_config_for_stage(config, finalization=True)

    assert set(candidate.profiles) == set(TRAVEL_CANDIDATE_RESEARCH_PROFILES)
    assert set(finalization.profiles) == {
        TRAVEL_FINAL_STAY_PROFILE,
        TRAVEL_FINAL_ROUTE_PROFILE,
    }
    assert candidate.max_parallel == candidate.max_tasks_per_call == 3
    assert finalization.max_parallel == finalization.max_tasks_per_call == 2
    assert "explorer" not in candidate.profiles
    assert "explorer" not in finalization.profiles

    repair = travel_subagent_config_for_stage(
        config,
        finalization=True,
        repair_profiles=frozenset({TRAVEL_FINAL_WEATHER_PROFILE}),
    )
    assert set(repair.profiles) == {TRAVEL_FINAL_WEATHER_PROFILE}
    assert repair.max_parallel == repair.max_tasks_per_call == 1


def test_travel_delegation_requires_every_stage_profile_in_one_batch():
    class _Provider:
        def __init__(self):
            self.calls = []

        def definitions(self):
            return []

        def execute(self, name, args):
            self.calls.append((name, args))
            return ToolResult(output="delegated")

    delegate = _Provider()
    candidate = require_exact_travel_delegation(delegate, finalization=False)
    partial = candidate.execute(
        "delegate_tasks",
        {
            "tasks": [
                {
                    "id": "transport",
                    "task": "query transport",
                    "profile": TRAVEL_TRANSPORT_WEATHER_PROFILE,
                }
            ]
        },
    )
    complete_tasks = [
        {"id": profile, "task": f"run {profile}", "profile": profile}
        for profile in sorted(TRAVEL_CANDIDATE_RESEARCH_PROFILES)
    ]
    complete = candidate.execute("delegate_tasks", {"tasks": complete_tasks})

    assert partial.is_error is True
    assert partial.metadata["code"] == "TRAVEL_SUBAGENT_BATCH_INVALID"
    assert complete.is_error is False
    assert len(delegate.calls) == 1

    finalization = require_exact_travel_delegation(
        delegate,
        finalization=True,
        final_stay_context='{"candidate_hotel_observations":[{"hotels":[{"name":"重庆测试酒店","observed_price_per_night_cny":168}]}]}',
    )
    duplicate = finalization.execute(
        "delegate_tasks",
        {
            "tasks": [
                {
                    "id": "stay-a",
                    "task": "query stay",
                    "profile": TRAVEL_FINAL_STAY_PROFILE,
                },
                {
                    "id": "stay-b",
                    "task": "query stay again",
                    "profile": TRAVEL_FINAL_STAY_PROFILE,
                },
            ]
        },
    )
    exact = finalization.execute(
        "delegate_tasks",
        {
            "tasks": [
                {
                    "id": "stay",
                    "task": "query stay",
                    "profile": TRAVEL_FINAL_STAY_PROFILE,
                },
                {
                    "id": "route",
                    "task": "query route",
                    "profile": TRAVEL_FINAL_ROUTE_PROFILE,
                },
            ]
        },
    )

    assert duplicate.is_error is True
    assert exact.is_error is False
    assert len(delegate.calls) == 2
    delegated_stay = next(
        task
        for task in delegate.calls[-1][1]["tasks"]
        if task["profile"] == TRAVEL_FINAL_STAY_PROFILE
    )
    assert "重庆测试酒店" in delegated_stay["task"]
    assert "本轮 dated 酒店查询失败" in delegated_stay["task"]
    assert "最多再尝试五个" in delegated_stay["task"]

    weather_repair = require_exact_travel_delegation(
        delegate,
        finalization=True,
        expected_profiles=frozenset({TRAVEL_FINAL_WEATHER_PROFILE}),
        final_weather_context='{"start_date":"2026-08-19","end_date":"2026-08-24","inside_forecast_window":true}',
    )
    repaired = weather_repair.execute(
        "delegate_tasks",
        {
            "tasks": [{
                "id": "weather",
                "task": "repair weather",
                "profile": TRAVEL_FINAL_WEATHER_PROFILE,
            }]
        },
    )
    assert repaired.is_error is False
    assert "inside_forecast_window" in delegate.calls[-1][1]["tasks"][0]["task"]
    assert "禁止改查 historical weather" in delegate.calls[-1][1]["tasks"][0]["task"]


def test_finalization_delegation_reports_only_completed_ok_profiles_immediately():
    class _Provider:
        def definitions(self):
            return []

        def execute(self, name, args):
            del name, args
            return ToolResult(
                output=json.dumps(
                    {
                        "status": "partial",
                        "results": [
                            {"id": "stay", "status": "completed", "code": "OK"},
                            {"id": "route", "status": "failed", "code": "TIMEOUT"},
                        ],
                    }
                )
            )

    completed = []
    provider = require_exact_travel_delegation(
        _Provider(),
        finalization=True,
        on_profiles_completed=completed.append,
    )

    result = provider.execute(
        "delegate_tasks",
        {
            "tasks": [
                {"id": "stay", "task": "query stay", "profile": TRAVEL_FINAL_STAY_PROFILE},
                {"id": "route", "task": "query route", "profile": TRAVEL_FINAL_ROUTE_PROFILE},
            ]
        },
    )

    assert result.is_error is False
    assert completed == [frozenset({TRAVEL_FINAL_STAY_PROFILE})]


def test_completed_finalization_batch_switches_to_finalizer_in_same_turn():
    class _Provider:
        def definitions(self):
            return [
                {"type": "function", "function": {"name": "delegate_tasks"}},
                {"type": "function", "function": {"name": "finalize_travel_plan"}},
            ]

        def execute(self, name, args):
            del name
            return ToolResult(
                output=json.dumps(
                    {
                        "status": "completed",
                        "results": [
                            {
                                "id": task["id"],
                                "status": "completed",
                                "code": "OK",
                            }
                            for task in args["tasks"]
                        ],
                    }
                )
            )

    session_id = "session-same-turn"
    ledger = TravelSourceLedger()
    ledger.register_expected(
        session_id,
        ["search_travel_hotels", "mcp__amap__maps_direction_transit_integrated"],
    )
    ledger.begin_finalization_budget(session_id)
    exact = require_exact_travel_delegation(
        _Provider(),
        finalization=True,
        on_profiles_completed=lambda profiles: ledger.mark_finalization_attempted(
            session_id,
            {
                {
                    TRAVEL_FINAL_STAY_PROFILE: "lodging",
                    TRAVEL_FINAL_ROUTE_PROFILE: "maps",
                }[profile]
                for profile in profiles
            },
        ),
    )
    provider = require_travel_finalization_before_saving(exact, ledger, session_id)
    assert [item["function"]["name"] for item in provider.definitions()] == [
        "delegate_tasks"
    ]

    provider.execute(
        "delegate_tasks",
        {
            "tasks": [
                {"id": "stay", "task": "query stay", "profile": TRAVEL_FINAL_STAY_PROFILE},
                {"id": "route", "task": "query route", "profile": TRAVEL_FINAL_ROUTE_PROFILE},
            ]
        },
    )

    assert [item["function"]["name"] for item in provider.definitions()] == [
        "finalize_travel_plan"
    ]


class _RouteProvider:
    def __init__(self, payload):
        self.payload = payload

    def definitions(self):
        return []

    def execute(self, name, args):
        del name, args
        output = self.payload if isinstance(self.payload, str) else json.dumps(self.payload, ensure_ascii=False)
        return ToolResult(output=output, metadata={"code": "MCP_OK"})


def _route_segment(line, departure, arrival, via_count):
    return {
        "walking": {"steps": [{"instruction": "不应进入 Child 上下文"}]},
        "bus": {
            "buslines": [
                {
                    "name": line,
                    "departure_stop": {"name": departure},
                    "arrival_stop": {"name": arrival},
                    "distance": "12000",
                    "duration": "1800",
                    "via_stops": [{"name": f"途经站 {index}"} for index in range(via_count)],
                }
            ]
        },
    }
