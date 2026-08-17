from __future__ import annotations

import json
from copy import deepcopy
from datetime import date

import pytest

import agent.applications.travel.tools as travel_tools_module
from agent.applications.travel.config import TravelConfig
from agent.applications.travel.schemas import TravelPlanV1
from agent.applications.travel.service import TravelApplicationError, TravelApplicationService
from agent.applications.travel.store import TravelPlanStore, TravelPlanStoreError
from agent.auth.user_context import FilesystemUserContextResolver
from agent.message import Message
from agent.protocols.auth import ActorContext
from agent.protocols.tool import ToolExecutionContext, ToolResult
from agent.session import JsonlSessionStore
from tests.unit_test.travel.fixtures import plan_payload


class Events:
    def __init__(self):
        self.items = []

    def emit(self, event_type, **kwargs):
        self.items.append((event_type, kwargs))


def _intake_call(patch):
    return {
        "id": "call-intake",
        "type": "function",
        "function": {
            "name": "update_travel_draft",
            "arguments": json.dumps({"patch": patch, "clear_fields": []}, ensure_ascii=False),
        },
    }


def test_structured_ledger_results_replace_matching_hotel_price_estimate():
    plan = plan_payload()
    plan["stay_recommendations"][0]["hotel_name"] = "全季酒店(大理古城店)"
    merged = travel_tools_module._merge_ledger_structured_results(  # noqa: SLF001
        plan,
        {
            "map_pois": [{
                "name": "全季酒店(大理古城店)",
                "address": "大理古城苍山门北300米",
                "location": "100.154734,25.697299",
                "typecode": "100103",
            }],
            "transit_routes": [],
            "hotel_observations": [{
                "retrieved_at": "2026-08-16T11:36:04Z",
                "query": {"city": "大理", "checkin": "2026-08-18", "checkout": "2026-08-20"},
                "hotels": [{
                    "name": "全季酒店(大理古城店)",
                    "observed_price_per_night_cny": 796,
                    "source_url": "",
                }],
            }],
        },
    )

    stay = merged["stay_recommendations"][0]
    assert stay["observed_price_per_night_cny"] == 796
    assert stay["planning_estimate_per_night_cny"] is None
    assert stay["price_status"] == "live_observed"
    assert stay["address"] == "大理古城苍山门北300米"
    assert any(item["provider"] == "携程账号只读查询" for item in merged["evidence"])


def test_observed_stay_price_replaces_stale_estimate_in_budget_summary():
    plan = plan_payload()
    stay = plan["stay_recommendations"][0]
    stay["observed_price_per_night_cny"] = 169
    stay["planning_estimate_per_night_cny"] = 210
    stay["price_status"] = "live_observed"

    reconciled = travel_tools_module._reconcile_observed_stay_budget(plan)  # noqa: SLF001

    reconciled_stay = reconciled["stay_recommendations"][0]
    lodging = reconciled["budget"]["items"][1]
    assert reconciled_stay["planning_estimate_per_night_cny"] is None
    assert lodging["expected"] == 169
    assert reconciled["budget"]["expected"] == 2069


def test_structured_ledger_replaces_model_hotel_with_lowest_jointly_verified_observation():
    plan = plan_payload()
    plan["stay_recommendations"][0]["hotel_name"] = "模型自行编写酒店"
    merged = travel_tools_module._merge_ledger_structured_results(  # noqa: SLF001
        plan,
        {
            "map_pois": [
                {
                    "name": "重庆安心酒店(解放碑店)",
                    "address": "重庆市渝中区民权路1号",
                    "location": "106.576,29.557",
                    "typecode": "100105",
                },
                {
                    "name": "重庆舒适酒店(洪崖洞店)",
                    "address": "重庆市渝中区嘉滨路2号",
                    "location": "106.579,29.563",
                    "typecode": "100105",
                },
            ],
            "transit_routes": [],
            "hotel_observations": [{
                "retrieved_at": "2026-08-16T13:00:00Z",
                "query": {"city": "重庆", "checkin": "2026-08-25", "checkout": "2026-08-26"},
                "hotels": [
                    {"name": "重庆舒适酒店(洪崖洞店)", "observed_price_per_night_cny": 228},
                    {"name": "重庆安心酒店(解放碑店)", "observed_price_per_night_cny": 168},
                ],
            }],
        },
    )

    stay = merged["stay_recommendations"][0]
    assert stay["hotel_name"] == "重庆安心酒店(解放碑店)"
    assert stay["address"] == "重庆市渝中区民权路1号"
    assert stay["location"] == {"longitude": 106.576, "latitude": 29.557}
    assert stay["observed_price_per_night_cny"] == 168.0
    assert stay["price_status"] == "live_observed"
    evidence = {item["evidence_id"]: item for item in merged["evidence"]}
    assert evidence[stay["evidence_ids"][-1]]["provider"] == "高德地图"
    assert evidence[stay["price_source_evidence_ids"][-1]]["provider"] == "携程账号只读查询"


def test_structured_ledger_selects_return_train_after_final_activity_and_station_buffer():
    plan = plan_payload()
    plan["request"].update(
        {
            "origin": "成都",
            "destinations": ["重庆"],
            "start_date": "2026-08-20",
            "end_date": "2026-08-22",
        }
    )
    plan["days"][-1]["date"] = "2026-08-22"
    plan["days"][-1]["activities"][-1]["end"] = "12:00"
    plan["transport_options"] = [
        {
            "name": "去程",
            "mode": "高铁",
            "from": "成都东",
            "to": "重庆北",
            "service_name": "C778",
            "departure": "2026-08-20T05:55:00+08:00",
            "arrival": "2026-08-20T08:28:00+08:00",
            "duration_minutes": 153,
            "seat": "二等座",
            "price_cny_per_person": 85,
            "price_cny_total": 170,
            "source": "铁路 12306",
            "summary": "早到",
            "evidence_ids": ["ev-train"],
        },
        {
            "name": "过早返程",
            "mode": "高铁",
            "from": "重庆北",
            "to": "成都东",
            "service_name": "C77",
            "departure": "2026-08-22T06:18:00+08:00",
            "arrival": "2026-08-22T09:14:00+08:00",
            "duration_minutes": 176,
            "seat": "二等座",
            "price_cny_per_person": 94,
            "price_cny_total": 188,
            "source": "铁路 12306",
            "summary": "过早",
            "evidence_ids": ["ev-train"],
        },
    ]
    plan["budget"] = {
        "lower": 621,
        "expected": 683,
        "upper": 876,
        "items": [
            {"name": "往返铁路交通", "lower": 358, "expected": 358, "upper": 436},
            {"name": "住宿", "lower": 99, "expected": 99, "upper": 130},
        ],
    }
    merged = travel_tools_module._merge_ledger_structured_results(  # noqa: SLF001
        plan,
        {
            "map_pois": [],
            "transit_routes": [],
            "hotel_observations": [],
            "rail_options": [
                {
                    "service_name": "C778",
                    "from": "成都东",
                    "to": "重庆北",
                    "departure_time": "05:55",
                    "arrival_time": "08:28",
                    "duration_minutes": 153,
                    "seat": "二等座",
                    "price_cny_per_person": 85.0,
                },
                {
                    "service_name": "C77",
                    "from": "重庆北",
                    "to": "成都东",
                    "departure_time": "06:18",
                    "arrival_time": "09:14",
                    "duration_minutes": 176,
                    "seat": "二等座",
                    "price_cny_per_person": 94.0,
                },
                {
                    "service_name": "G8771",
                    "from": "重庆西",
                    "to": "成都东",
                    "departure_time": "13:44",
                    "arrival_time": "16:36",
                    "duration_minutes": 172,
                    "seat": "二等座",
                    "price_cny_per_person": 218.0,
                },
                {
                    "service_name": "G2894",
                    "from": "重庆北",
                    "to": "成都东",
                    "departure_time": "13:24",
                    "arrival_time": "14:40",
                    "duration_minutes": 76,
                    "seat": "二等座",
                    "price_cny_per_person": 161.0,
                },
            ],
        },
    )

    selected = merged["transport_options"][1]
    assert selected["service_name"] == "G2894"
    assert selected["from"] == "重庆北"
    assert selected["departure"] == "2026-08-22T13:24:00+08:00"
    assert selected["price_cny_total"] == 322.0
    assert merged["budget"]["expected"] == 817.0
    assert merged["budget"]["items"][0]["expected"] == 492.0
    evidence = {item["evidence_id"]: item for item in merged["evidence"]}
    assert evidence[selected["evidence_ids"][0]]["provider"] == "铁路 12306"


def test_structured_ledger_moves_final_day_earlier_for_latest_real_return_train():
    plan = plan_payload()
    plan["request"].update(
        {
            "origin": "重庆",
            "destinations": ["成都"],
            "start_date": "2026-08-20",
            "end_date": "2026-08-22",
        }
    )
    plan["days"][-1]["date"] = "2026-08-22"
    plan["days"][-1]["activities"] = [
        {**deepcopy(plan["days"][-1]["activities"][0]), "start": "09:00", "end": "11:00"},
        {**deepcopy(plan["days"][-1]["activities"][0]), "start": "13:00", "end": "15:00"},
    ]
    plan["transport_options"] = [
        {
            **deepcopy(plan["transport_options"][0]),
            "from": "成都东",
            "to": "重庆北",
            "departure": "2026-08-22T10:00:00+08:00",
            "arrival": "2026-08-22T12:00:00+08:00",
        }
    ]

    merged = travel_tools_module._merge_ledger_structured_results(  # noqa: SLF001
        plan,
        {
            "map_pois": [],
            "transit_routes": [],
            "hotel_observations": [],
            "rail_options": [
                {
                    "service_name": "G999",
                    "from": "成都东",
                    "to": "重庆北",
                    "departure_time": "15:30",
                    "arrival_time": "17:00",
                    "duration_minutes": 90,
                    "seat": "二等座",
                    "price_cny_per_person": 150.0,
                }
            ],
        },
    )

    assert merged["transport_options"][0]["service_name"] == "G999"
    assert merged["transport_options"][0]["departure"] == "2026-08-22T15:30:00+08:00"
    assert merged["days"][-1]["activities"] == [
        {**plan["days"][-1]["activities"][0], "start": "10:15", "end": "12:15"},
        {**plan["days"][-1]["activities"][1], "start": "12:30", "end": "14:30"},
    ]
    assert "60分钟进站缓冲" in merged["days"][-1]["fallback_plan"]


def test_finalizer_reconciles_persisted_return_train_without_live_ledger():
    plan = plan_payload()
    plan["request"].update(
        {
            "origin": "重庆",
            "destinations": ["成都"],
            "start_date": "2026-08-20",
            "end_date": "2026-08-22",
        }
    )
    plan["days"][-1]["date"] = "2026-08-22"
    plan["days"][-1]["activities"][-1].update({"start": "14:00", "end": "16:00"})
    plan["transport_options"] = [
        {
            **deepcopy(plan["transport_options"][0]),
            "from": "成都东",
            "to": "重庆北",
            "service_name": "G999",
            "departure": "2026-08-22T15:30:00+08:00",
            "arrival": "2026-08-22T17:00:00+08:00",
            "source": "12306 实时余票查询",
        }
    ]

    reconciled = travel_tools_module._reconcile_persisted_transport_envelope(  # noqa: SLF001
        plan
    )

    assert reconciled["transport_options"][0]["departure"] == plan["transport_options"][0]["departure"]
    assert reconciled["days"][-1]["activities"][-1]["end"] == "14:30"
    assert "60分钟进站缓冲" in reconciled["days"][-1]["fallback_plan"]


def test_structured_ledger_adds_missing_return_direction_from_12306_rows():
    plan = plan_payload()
    plan["request"].update(
        {
            "origin": "重庆",
            "destinations": ["成都"],
            "start_date": "2026-08-20",
            "end_date": "2026-08-22",
        }
    )
    plan["days"][-1]["date"] = "2026-08-22"
    plan["days"][-1]["activities"][-1].update({"start": "10:00", "end": "12:00"})
    plan["transport_options"] = [
        {
            **deepcopy(plan["transport_options"][0]),
            "from": "重庆北",
            "to": "成都东",
            "departure": "2026-08-20T06:00:00+08:00",
            "arrival": "2026-08-20T08:00:00+08:00",
        }
    ]

    merged = travel_tools_module._merge_ledger_structured_results(  # noqa: SLF001
        plan,
        {
            "map_pois": [],
            "transit_routes": [],
            "hotel_observations": [],
            "rail_options": [
                {
                    "service_name": "G100",
                    "from": "重庆北",
                    "to": "成都东",
                    "departure_time": "06:00",
                    "arrival_time": "08:00",
                    "duration_minutes": 120,
                    "seat": "二等座",
                    "price_cny_per_person": 150.0,
                },
                {
                    "service_name": "G101",
                    "from": "成都东",
                    "to": "重庆北",
                    "departure_time": "15:30",
                    "arrival_time": "17:00",
                    "duration_minutes": 90,
                    "seat": "二等座",
                    "price_cny_per_person": 160.0,
                },
            ],
        },
    )

    assert len(merged["transport_options"]) == 2
    returning = merged["transport_options"][1]
    assert returning["service_name"] == "G101"
    assert returning["from"] == "成都东"
    assert returning["to"] == "重庆北"
    assert returning["evidence_ids"]


def test_structured_stay_merge_does_not_replace_hotel_with_similar_landmark_hotel():
    plan = plan_payload()
    stay = plan["stay_recommendations"][0]
    stay["hotel_name"] = "重庆嘉玺江景酒店(解放碑洪崖洞店)"
    merged = travel_tools_module._merge_ledger_structured_results(  # noqa: SLF001
        plan,
        {
            "map_pois": [{
                "name": "重庆解放碑嘉遇酒店",
                "address": "民生路283号",
                "location": "106.57,29.56",
                "typecode": "100105",
            }],
            "transit_routes": [],
            "rail_options": [],
            "hotel_observations": [{
                "retrieved_at": "2026-08-16T13:00:00Z",
                "query": {"city": "重庆"},
                "hotels": [{
                    "name": "重庆嘉玺江景酒店(解放碑洪崖洞店)",
                    "observed_price_per_night_cny": 135,
                }],
            }],
        },
    )

    selected = merged["stay_recommendations"][0]
    assert selected["hotel_name"] == "重庆嘉玺江景酒店(解放碑洪崖洞店)"
    assert selected["address"] != "民生路283号"
    assert selected["observed_price_per_night_cny"] == 135.0


def test_search_evidence_removes_unknown_that_contradicts_xhs_results():
    plan = plan_payload()
    plan["evidence"] = []
    plan["unknowns"] = ["社区经验暂未补充，本次小红书未得到可展示笔记。", "门票需复核"]
    social = {
        "evidence_id": "xhs-one",
        "source_type": "social_post",
        "provider": "小红书只读",
        "title": "重庆三天两晚攻略",
        "source_url": "https://www.xiaohongshu.com/explore/note-one",
        "retrieved_at": "2026-08-16T13:00:00Z",
        "freshness": "snapshot",
        "excerpt": "洪崖洞和李子坝路线",
    }

    merged = travel_tools_module._merge_ledger_search_evidence(  # noqa: SLF001
        plan, web=[], social=[social]
    )

    assert merged["unknowns"] == ["门票需复核"]
    assert any(item.get("evidence_id") == "xhs-one" for item in merged["evidence"])


def test_structured_ledger_results_replace_matching_route_estimate():
    plan = plan_payload()
    segment = plan["days"][0]["route_segments"][0]
    segment.update({"source": "planning_estimate", "transit_legs": [], "walking_distance": 0})
    merged = travel_tools_module._merge_ledger_structured_results(  # noqa: SLF001
        plan,
        {
            "map_pois": [
                {"name": "大理站", "location": "100.25000,25.59000"},
                {"name": "大理古城", "location": "100.16500,25.69400"},
            ],
            "hotel_observations": [],
            "transit_routes": [{
                "arguments": {"origin": "100.25000,25.59000", "destination": "100.16500,25.69400"},
                "route": {"distance": "18000", "transits": [{
                    "duration": "3000",
                    "walking_distance": "600",
                    "cost": "5",
                    "segments": [{"bus": {"buslines": [{
                        "name": "4路",
                        "departure_stop": {"name": "大理站"},
                        "arrival_stop": {"name": "大理古城南门"},
                        "via_stops": [{"name": "市医院"}],
                    }]}}],
                }]},
            }],
        },
    )

    actual = merged["days"][0]["route_segments"][0]
    assert actual["source"] == "高德地图实时公交路线"
    assert actual["duration"] == 50
    assert actual["distance"] == 18
    assert actual["transit_legs"][0]["line_name"] == "4路"


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


def test_finalize_tool_removes_credential_query_fields_from_evidence_urls(tmp_path):
    resolver = FilesystemUserContextResolver(tmp_path / "contexts", workspace_dir=tmp_path)
    service = TravelApplicationService(TravelConfig(enabled=True), resolver)
    actor = _actor("user-a")
    tool = service.tools_for_actor(actor)[0]
    plan = plan_payload()
    plan["evidence"][0]["source_url"] = (
        "https://restapi.amap.com/v3/direction/transit/integrated"
        "?origin=106.5,29.5&key=secret-value&city=郑州&token=secret-token"
    )

    result = tool.execute_with_context(
        {"plan": plan},
        ToolExecutionContext(
            actor=actor,
            session_id="session-a",
            turn_id="turn-a",
            turn_index=1,
            channel="web",
        ),
    )

    assert not result.is_error
    saved = service.get_plan(actor, json.loads(result.output)["plan_id"])
    source_url = saved.data["evidence"][0]["source_url"]
    assert source_url == (
        "https://restapi.amap.com/v3/direction/transit/integrated"
        "?origin=106.5%2C29.5&city=%E9%83%91%E5%B7%9E"
    )
    assert "secret-value" not in json.dumps(saved.data, ensure_ascii=False)
    assert "secret-token" not in json.dumps(saved.data, ensure_ascii=False)


def test_finalize_tool_converts_numeric_walking_distance_string(tmp_path):
    resolver = FilesystemUserContextResolver(tmp_path / "contexts", workspace_dir=tmp_path)
    service = TravelApplicationService(TravelConfig(enabled=True), resolver)
    actor = _actor("user-a")
    tool = service.tools_for_actor(actor)[0]
    plan = plan_payload()
    plan["days"][0]["route_segments"][0]["walking_distance"] = "600"

    result = tool.execute_with_context(
        {"plan": plan},
        ToolExecutionContext(
            actor=actor,
            session_id="session-a",
            turn_id="turn-a",
            turn_index=1,
            channel="web",
        ),
    )

    assert not result.is_error
    saved = service.get_plan(actor, json.loads(result.output)["plan_id"])
    assert saved.data["days"][0]["route_segments"][0]["walking_distance"] == 600.0


def test_finalize_tool_accepts_verified_amap_walking_distance_above_two_km(tmp_path):
    resolver = FilesystemUserContextResolver(tmp_path / "contexts", workspace_dir=tmp_path)
    service = TravelApplicationService(TravelConfig(enabled=True), resolver)
    actor = _actor("user-a")
    tool = service.tools_for_actor(actor)[0]
    plan = plan_payload()
    plan["days"][0]["route_segments"][0]["walking_distance"] = "2338"

    result = tool.execute_with_context(
        {"plan": plan},
        ToolExecutionContext(
            actor=actor,
            session_id="session-a",
            turn_id="turn-a",
            turn_index=1,
            channel="web",
        ),
    )

    assert not result.is_error
    saved = service.get_plan(actor, json.loads(result.output)["plan_id"])
    assert saved.data["days"][0]["route_segments"][0]["walking_distance"] == 2338.0


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
    assert "maps" in result.output
    assert "weather" in result.output
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


def test_travel_finalize_preserves_successful_ctrip_prices_in_stay_cards(tmp_path):
    resolver = FilesystemUserContextResolver(tmp_path / "contexts", workspace_dir=tmp_path)
    service = TravelApplicationService(TravelConfig(enabled=True), resolver)
    service.source_ledger.register_expected("session-travel", ["search_travel_hotels"])
    service.source_ledger.observe(
        "session-travel",
        "search_travel_hotels",
        ToolResult(
            output=(
                '{"status":"success","code":"OK","hotels":['
                '{"name":"贵阳舒适酒店","observed_price_per_night_cny":288}]}'
            ),
            metadata={"code": "OK"},
        ),
    )
    plan = plan_payload()

    missing = travel_tools_module._research_completion_error(
        service.source_ledger.snapshot("session-travel"),
        plan,
    )

    assert missing is not None
    assert missing.metadata["code"] == "TRAVEL_HOTEL_PRICE_EVIDENCE_MISSING"

    plan["evidence"].append(
        {
            "evidence_id": "ev-ctrip-price",
            "source_type": "live_query",
            "provider": "ctrip-account-observation",
            "title": "贵阳舒适酒店日期房价",
            "source_url": "https://hotels.ctrip.com/hotels/detail/1",
            "published_at": "",
            "retrieved_at": "2026-08-16T08:00:00Z",
            "data_as_of": "2026-08-16T08:00:00Z",
            "excerpt": "指定入住日期观察价为每晚 288 元",
            "facts": ["每晚 288 元"],
            "confidence": 0.9,
            "freshness": "live",
            "content_hash": "",
        }
    )
    stay = plan["stay_recommendations"][0]
    stay.update(
        {
            "observed_price_per_night_cny": 288,
            "planning_estimate_per_night_cny": None,
            "price_status": "live_observed",
            "price_source_evidence_ids": ["ev-ctrip-price"],
        }
    )

    assert (
        travel_tools_module._research_completion_error(
            service.source_ledger.snapshot("session-travel"),
            plan,
        )
        is None
    )


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
            output = (
                '{"status":"success","results":[{"title":"大理古城避坑攻略",'
                '"url":"https://example.com/dali-guide",'
                '"content":"大理古城游览应保留机动时间"}]}'
            )
        elif "xhs" in name:
            output = '{"status":"success","data":{"text":"{\\"feeds\\":[{\\"id\\":\\"1\\"}]}"}}'
        service.source_ledger.observe(
            "session-travel",
            name,
            ToolResult(output=output, metadata={"code": "MCP_OK"}),
        )
    plan = plan_payload()
    rail_evidence = deepcopy(plan["evidence"][0])
    rail_evidence.update(
        {
            "evidence_id": "ev-rail",
            "provider": "铁路 12306",
            "title": "G123 重庆至大理",
            "source_url": "https://www.12306.cn/index/",
            "excerpt": "G123 二等座查询结果",
            "facts": ["G123 08:00 出发 13:00 到达，二等座 300 元"],
        }
    )
    plan["evidence"].append(rail_evidence)
    plan["transport_options"][0].update(
        {
            "service_name": "G123",
            "departure": "2026-10-01T08:00:00+08:00",
            "arrival": "2026-10-01T13:00:00+08:00",
            "duration_minutes": 300,
            "seat": "二等座",
            "price_cny_per_person": 300,
            "price_cny_total": 600,
            "source": "铁路 12306",
            "summary": "真实查询结果",
            "evidence_ids": ["ev-rail"],
        }
    )
    candidate = _candidate_from_plan("balanced", plan, recommended=True)
    service.save_candidate_review(
        actor,
        session_id="session-travel",
        turn_id="turn-a",
        candidates=[candidate, _candidate_summary("alternative")],
        recommended_candidate_id="balanced",
    )
    service.select_candidate(actor, "session-travel", "balanced")
    service.source_ledger.observe(
        "session-travel",
        "mcp__amap-maps__maps_direction_transit_integrated",
        ToolResult(
            output=(
                '{"route":{"transits":[{"segments":[{"bus":{"buslines":['
                '{"name":"轨道交通2号线","departure_stop":{"name":"起点站"},'
                '"arrival_stop":{"name":"终点站"}}]}}]}]}}'
            ),
            metadata={"code": "MCP_OK"},
        ),
    )

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
    saved = service.get_plan(actor, json.loads(result.output)["plan_id"])
    assert any(
        item["source_type"] == "web_article"
        and item["source_url"] == "https://example.com/dali-guide"
        for item in saved.data["evidence"]
    )
    assert service.source_ledger.snapshot("session-travel").expected == frozenset()
    persisted_review = service.get_candidate_review(actor, "session-travel")
    assert persisted_review is not None
    assert persisted_review.selected_candidate_id == "balanced"


def test_travel_finalize_inherits_selected_activity_identity_without_forcing_route_totals(tmp_path):
    resolver = FilesystemUserContextResolver(tmp_path / "contexts", workspace_dir=tmp_path)
    service = TravelApplicationService(TravelConfig(enabled=True), resolver)
    actor = _actor("user-a")
    service.source_ledger.register_expected("session-travel", ["mcp__amap__search"])
    service.source_ledger.observe(
        "session-travel",
        "mcp__amap__search",
        ToolResult(output='{"pois":[{"name":"大理古城"}]}', metadata={"code": "MCP_OK"}),
    )
    selected_plan = plan_payload()
    candidate = _candidate_from_plan("balanced", selected_plan, recommended=True)
    service.save_candidate_review(
        actor,
        session_id="session-travel",
        turn_id="turn-a",
        candidates=[candidate, _candidate_summary("alternative")],
        recommended_candidate_id="balanced",
    )
    service.select_candidate(actor, "session-travel", "balanced")
    service.source_ledger.observe(
        "session-travel",
        "mcp__amap-maps__maps_direction_transit_integrated",
        ToolResult(
            output=(
                '{"route":{"transits":[{"segments":[{"bus":{"buslines":['
                '{"name":"轨道交通2号线","departure_stop":{"name":"起点站"},'
                '"arrival_stop":{"name":"终点站"}}]}}]}]}}'
            ),
            metadata={"code": "MCP_OK"},
        ),
    )
    final_plan = deepcopy(selected_plan)
    final_plan["days"][0]["activities"][0]["place"] = "模型改写的地点名"
    final_plan["days"][0]["route_segments"][0]["duration"] += 7
    final_plan["days"][0]["route_segments"][0]["distance"] += 1.5
    final_plan["budget"]["expected"] += 69
    final_plan["budget"]["upper"] += 69
    final_plan["budget"]["items"][0]["expected"] += 69
    final_plan["budget"]["items"][0]["upper"] += 69

    result = service.tools_for_actor(actor)[0].execute_with_context(
        {"plan": final_plan, "selected_candidate_id": "balanced"},
        ToolExecutionContext(
            actor=actor,
            session_id="session-travel",
            turn_id="turn-a",
            turn_index=1,
            channel="travel",
        ),
    )

    assert not result.is_error
    saved = service.get_plan(actor, result.metadata["plan_id"])
    assert saved.data["days"][0]["activities"][0]["place"] == selected_plan["days"][0]["activities"][0]["place"]
    assert saved.data["days"][0]["route_segments"][0]["duration"] == final_plan["days"][0]["route_segments"][0]["duration"]
    assert saved.data["days"][0]["route_segments"][0]["distance"] == final_plan["days"][0]["route_segments"][0]["distance"]
    assert saved.data["budget"]["expected"] == final_plan["budget"]["expected"]
    assert saved.data["budget"]["items"][0]["expected"] == final_plan["budget"]["items"][0]["expected"]


def test_travel_finalize_requires_amap_transit_lines_when_source_returned_them(tmp_path):
    resolver = FilesystemUserContextResolver(tmp_path / "contexts", workspace_dir=tmp_path)
    service = TravelApplicationService(TravelConfig(enabled=True), resolver)
    actor = _actor("user-a")
    tool_name = "mcp__amap-maps__maps_direction_transit_integrated"
    service.source_ledger.register_expected("session-travel", [tool_name])
    service.source_ledger.observe(
        "session-travel",
        tool_name,
        ToolResult(
            output=(
                '{"route":{"transits":[{"segments":[{"bus":{"buslines":['
                '{"departure_stop":{"name":"沈阳站"},'
                '"arrival_stop":{"name":"怀远门"}}]}}]}]}}'
            ),
            metadata={"code": "MCP_OK"},
        ),
    )
    plan = plan_payload()
    for day in plan["days"]:
        for segment in day["route_segments"]:
            segment["source"] = "planning_estimate"
            segment["transit_legs"] = []

    result = service.tools_for_actor(actor)[0].execute_with_context(
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
    assert result.metadata["code"] == "TRAVEL_TRANSIT_EVIDENCE_MISSING"


def test_travel_finalize_preserves_two_12306_not_on_sale_results(tmp_path):
    service = TravelApplicationService(
        TravelConfig(enabled=True),
        FilesystemUserContextResolver(tmp_path / "contexts", workspace_dir=tmp_path),
    )
    service.source_ledger.register_expected(
        "session-rail",
        ["mcp__12306__get-tickets"],
    )
    for travel_date in ("2026-10-01", "2026-10-02"):
        service.source_ledger.observe(
            "session-rail",
            "mcp__12306__get-tickets",
            ToolResult(
                output=(
                    '{"status":"not_on_sale","code":"OK","date":"'
                    + travel_date
                    + '","sale_open_date":"2026-09-17","trains":[]}'
                ),
                metadata={"code": "MCP_OK", "travel_source_status": "not_on_sale"},
            ),
        )
    plan = plan_payload()

    missing = travel_tools_module._research_completion_error(
        service.source_ledger.snapshot("session-rail"),
        plan,
    )

    assert missing is not None
    assert missing.metadata["code"] == "TRAVEL_RAIL_EVIDENCE_MISSING"

    plan["evidence"].append(
        {
            **deepcopy(plan["evidence"][0]),
            "evidence_id": "ev-rail-not-on-sale",
            "provider": "铁路 12306",
            "title": "往返车票 not_on_sale",
            "source_url": "https://www.12306.cn/index/",
            "excerpt": "两日均未开售",
            "facts": ["not_on_sale", "sale_open_date=2026-09-17"],
        }
    )
    outbound = plan["transport_options"][0]
    outbound.update(
        {
            "source": "12306 not_on_sale planning_estimate",
            "summary": "未开售，起售后复核",
            "evidence_ids": ["ev-rail-not-on-sale"],
        }
    )
    plan["transport_options"].append(
        {
            **deepcopy(outbound),
            "name": "大理至重庆铁路方案",
            "from": "大理",
            "to": "重庆",
        }
    )

    assert (
        travel_tools_module._research_completion_error(
            service.source_ledger.snapshot("session-rail"),
            plan,
        )
        is None
    )


def test_travel_finalize_rejects_empty_overnight_stays_before_persistence(tmp_path):
    resolver = FilesystemUserContextResolver(tmp_path / "contexts", workspace_dir=tmp_path)
    service = TravelApplicationService(TravelConfig(enabled=True), resolver)
    actor = _actor("user-a")
    service.source_ledger.register_expected("session-travel", ["mcp__amap__search"])
    service.source_ledger.observe(
        "session-travel",
        "mcp__amap__search",
        ToolResult(output='{"pois":[{"name":"沈阳故宫"}]}', metadata={"code": "MCP_OK"}),
    )
    plan = plan_payload()
    plan["stay_recommendations"] = []

    result = service.tools_for_actor(actor)[0].execute_with_context(
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
    assert result.metadata["code"] == "TRAVEL_STAY_REQUIRED"


def test_travel_finalize_requires_weather_provider_and_freshness_after_success(tmp_path):
    resolver = FilesystemUserContextResolver(tmp_path / "contexts", workspace_dir=tmp_path)
    service = TravelApplicationService(TravelConfig(enabled=True), resolver)
    actor = _actor("user-a")
    tool_name = "mcp__open-meteo__get_forecast"
    service.source_ledger.register_expected("session-travel", [tool_name])
    service.source_ledger.observe(
        "session-travel",
        tool_name,
        ToolResult(output='{"daily":{"time":["2026-10-01"]}}', metadata={"code": "MCP_OK"}),
    )
    plan = plan_payload()
    for item in plan["weather_summary"]:
        item.pop("provider", None)
        item["freshness"] = "unknown"

    result = service.tools_for_actor(actor)[0].execute_with_context(
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
    assert result.metadata["code"] == "TRAVEL_WEATHER_EVIDENCE_MISSING"


def test_travel_finalize_requires_forecast_inside_available_window(tmp_path, monkeypatch):
    monkeypatch.setattr(travel_tools_module, "_travel_today", lambda: date(2026, 9, 28))
    resolver = FilesystemUserContextResolver(tmp_path / "contexts", workspace_dir=tmp_path)
    service = TravelApplicationService(TravelConfig(enabled=True), resolver)
    actor = _actor("user-a")
    service.source_ledger.register_expected(
        "session-travel",
        [
            "mcp__open-meteo__get_forecast",
            "mcp__open-meteo__get_historical_weather",
        ],
    )
    service.source_ledger.observe(
        "session-travel",
        "mcp__open-meteo__get_historical_weather",
        ToolResult(output='{"daily":{"time":["2025-10-01"]}}', metadata={"code": "MCP_OK"}),
    )
    plan = plan_payload()

    missing_forecast = service.tools_for_actor(actor)[0].execute_with_context(
        {"plan": plan},
        ToolExecutionContext(
            actor=actor,
            session_id="session-travel",
            turn_id="turn-a",
            turn_index=1,
            channel="travel",
        ),
    )
    assert missing_forecast.metadata["code"] == "TRAVEL_WEATHER_FORECAST_REQUIRED"

    service.source_ledger.observe(
        "session-travel",
        "mcp__open-meteo__get_forecast",
        ToolResult(output='{"daily":{"time":["2026-10-01"]}}', metadata={"code": "MCP_OK"}),
    )
    for item in plan["weather_summary"]:
        item["freshness"] = "historical"
    discarded_forecast = service.tools_for_actor(actor)[0].execute_with_context(
        {"plan": plan},
        ToolExecutionContext(
            actor=actor,
            session_id="session-travel",
            turn_id="turn-a",
            turn_index=1,
            channel="travel",
        ),
    )
    assert discarded_forecast.metadata["code"] == "TRAVEL_WEATHER_FORECAST_EVIDENCE_MISSING"


@pytest.mark.parametrize(
    ("tool_name", "output", "removed_type", "expected_code"),
    [
        (
            "mcp__tavily__tavily_search",
            '{"results":[{"title":"沈阳攻略"}]}',
            "web_article",
            "TRAVEL_WEB_EVIDENCE_MISSING",
        ),
        (
            "mcp__xhs-readonly__search_notes",
            '{"data":{"text":"{\\"feeds\\":[{\\"id\\":\\"note-1\\"}]}"}}',
            "social_post",
            "TRAVEL_SOCIAL_EVIDENCE_MISSING",
        ),
    ],
)
def test_travel_finalize_rejects_search_results_without_safe_citations(
    tmp_path,
    tool_name,
    output,
    removed_type,
    expected_code,
):
    resolver = FilesystemUserContextResolver(tmp_path / "contexts", workspace_dir=tmp_path)
    service = TravelApplicationService(TravelConfig(enabled=True), resolver)
    actor = _actor("user-a")
    service.source_ledger.register_expected("session-travel", [tool_name])
    service.source_ledger.observe(
        "session-travel",
        tool_name,
        ToolResult(output=output, metadata={"code": "MCP_OK"}),
    )
    plan = plan_payload()
    plan["evidence"] = [
        item for item in plan["evidence"] if item["source_type"] != removed_type
    ]

    result = service.tools_for_actor(actor)[0].execute_with_context(
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
    assert result.metadata["code"] == expected_code


def test_travel_source_ledger_retains_only_bounded_safe_search_citations(tmp_path):
    resolver = FilesystemUserContextResolver(tmp_path / "contexts", workspace_dir=tmp_path)
    service = TravelApplicationService(TravelConfig(enabled=True), resolver)
    tavily_rows = [
        {
            "title": f"大理攻略 {index}",
            "url": f"https://example.com/dali-{index}",
            "content": "大理短途建议 " + ("正文" * 400),
        }
        for index in range(8)
    ]
    xhs_payload = {
        "data": {
            "text": json.dumps(
                {
                    "feeds": [{
                        "id": "note-1",
                        "xsecToken": "token-value",
                        "noteCard": {
                            "displayTitle": "大理环海避坑",
                            "user": {"nickname": "旅行者"},
                        },
                    }]
                },
                ensure_ascii=False,
            )
        }
    }
    service.source_ledger.observe(
        "session-travel",
        "mcp__tavily__tavily_search",
        ToolResult(output=json.dumps({"results": tavily_rows}), metadata={"code": "MCP_OK"}),
    )
    service.source_ledger.observe(
        "session-travel",
        "mcp__xhs-readonly__search_notes",
        ToolResult(output=json.dumps(xhs_payload, ensure_ascii=False), metadata={"code": "MCP_OK"}),
    )

    web = service.source_ledger.search_evidence("session-travel", "web")
    social = service.source_ledger.search_evidence("session-travel", "social")

    assert len(web) == 5
    assert all(len(item["excerpt"]) <= 500 for item in web)
    assert social[0]["title"] == "大理环海避坑"
    assert social[0]["source_url"] == "https://www.xiaohongshu.com/explore/note-1"
    assert "token-value" not in social[0]["source_url"]


def test_travel_finalize_rejects_long_unresolved_local_transit_segments(tmp_path):
    resolver = FilesystemUserContextResolver(tmp_path / "contexts", workspace_dir=tmp_path)
    service = TravelApplicationService(TravelConfig(enabled=True), resolver)
    actor = _actor("user-a")
    service.source_ledger.register_expected("session-travel", ["mcp__amap__search"])
    service.source_ledger.observe(
        "session-travel",
        "mcp__amap__search",
        ToolResult(output='{"pois":[{"name":"大理古城"}]}', metadata={"code": "MCP_OK"}),
    )
    plan = plan_payload()
    segment = plan["days"][0]["route_segments"][0]
    segment.update(
        {
            "mode": "规划估算",
            "distance": 4.8,
            "source": "planning_estimate",
            "transit_legs": [],
        }
    )

    result = service.tools_for_actor(actor)[0].execute_with_context(
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
    assert result.metadata["code"] == "TRAVEL_ROUTE_EVIDENCE_MISSING"


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
                "budget_level": "balanced",
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

    assert json.loads(first.output)["missing_fields"] == ["开始日期", "结束日期", "人数", "旅行基调"]
    assert json.loads(second.output)["ready"] is True
    state = sessions.load("travel-a")
    assert state.metadata["travel_draft"]["origin"] == "重庆"
    assert state.metadata["travel_draft"]["traveller_count"] == 2
    assert state.metadata["travel_intake_turn_ids"] == ["turn-a", "turn-b"]
    assert events.items[-1][0] == "travel.intake_draft_updated"


def test_intake_tool_rejects_llm_defaults_not_grounded_in_current_user_turn(tmp_path):
    resolver = FilesystemUserContextResolver(tmp_path / "contexts", workspace_dir=tmp_path)
    service = TravelApplicationService(TravelConfig(enabled=True), resolver)
    sessions = JsonlSessionStore(tmp_path / "sessions")
    sessions.update_metadata("travel-a", {"travel_phase": "intake"})
    sessions.append(
        "travel-a",
        [Message(role="user", content="我想去洛阳玩", turn_id="turn-a")],
    )
    update, _handoff = service.intake_tools_for_actor(_actor("user-a"), sessions)

    result = update.execute_with_context(
        {
            "patch": {
                "origin": "洛阳",
                "destinations": ["洛阳"],
                "start_date": "2026-08-17",
                "end_date": "2026-08-17",
                "traveller_type": "solo",
                "traveller_count": 1,
                "budget_total_cny": 100,
                "transport_preferences": ["string"],
                "stay_preferences": ["string"],
                "interest_tags": ["string"],
                "hard_constraints": ["string"],
            }
        },
        ToolExecutionContext(
            actor=_actor("user-a"),
            session_id="travel-a",
            turn_id="turn-a",
            turn_index=1,
            channel="travel",
        ),
    )

    payload = json.loads(result.output)
    assert payload["draft"]["destinations"] == ["洛阳"]
    assert payload["draft"]["origin"] == ""
    assert payload["draft"]["start_date"] == ""
    assert payload["draft"]["end_date"] == ""
    assert payload["draft"]["traveller_count"] is None
    assert payload["draft"]["budget_total_cny"] is None
    assert payload["draft"]["transport_preferences"] == []
    assert payload["missing_fields"] == [
        "出发地",
        "开始日期",
        "结束日期",
        "人数",
        "旅行基调",
    ]


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


def test_intake_patch_empty_placeholders_do_not_erase_saved_conditions(tmp_path):
    resolver = FilesystemUserContextResolver(tmp_path / "contexts", workspace_dir=tmp_path)
    service = TravelApplicationService(TravelConfig(enabled=True), resolver)
    sessions = JsonlSessionStore(tmp_path / "sessions")
    sessions.update_metadata(
        "travel-a",
        {
            "travel_phase": "intake",
            "travel_draft": {
                "intent": "travel_requirement",
                "intent_topic": "",
                "origin": "重庆南山",
                "destinations": ["重庆主城"],
                "start_date": "2026-08-15",
                "end_date": "2026-08-15",
                "traveller_type": "",
                "traveller_count": 1,
                "budget_total_cny": None,
                "budget_level": "balanced",
                "transport_preferences": [],
                "stay_preferences": [],
                "interest_tags": [],
                "pace": "",
                "planning_mode": "",
                "hard_constraints": [],
            },
        },
    )
    update, _handoff = service.intake_tools_for_actor(_actor("user-a"), sessions)
    result = update.execute_with_context(
        {
            "patch": {
                "origin": "",
                "destinations": [],
                "start_date": "",
                "end_date": "",
                "traveller_count": None,
                "budget_total_cny": None,
                "transport_preferences": ["地铁", "公交"],
                "interest_tags": ["美食", "夜景"],
            },
            "clear_fields": [],
        },
        ToolExecutionContext(
            actor=_actor("user-a"),
            session_id="travel-a",
            turn_id="turn-follow-up",
            turn_index=2,
            channel="travel",
        ),
    )

    payload = json.loads(result.output)
    assert payload["ready"] is True
    assert payload["missing_fields"] == []
    assert payload["changed_fields"] == ["interest_tags", "transport_preferences"]
    assert payload["draft"]["origin"] == "重庆南山"
    assert payload["draft"]["destinations"] == ["重庆主城"]
    assert payload["draft"]["start_date"] == "2026-08-15"
    assert payload["draft"]["end_date"] == "2026-08-15"
    assert payload["draft"]["traveller_count"] == 1
    assert payload["draft"]["transport_preferences"] == ["地铁", "公交"]
    assert payload["draft"]["interest_tags"] == ["美食", "夜景"]


def test_intake_v1_session_replays_historical_patches_after_old_empty_overwrite(tmp_path):
    resolver = FilesystemUserContextResolver(tmp_path / "contexts", workspace_dir=tmp_path)
    service = TravelApplicationService(TravelConfig(enabled=True), resolver)
    sessions = JsonlSessionStore(tmp_path / "sessions")
    sessions.append(
        "travel-a",
        [
            Message(
                role="assistant",
                content="",
                tool_calls=[
                    _intake_call(
                        {
                            "origin": "重庆南山",
                            "destinations": ["重庆主城"],
                            "start_date": "2026-08-15",
                            "end_date": "2026-08-15",
                            "traveller_count": 1,
                            "budget_level": "balanced",
                        }
                    )
                ],
            ),
            Message(
                role="assistant",
                content="",
                tool_calls=[
                    _intake_call(
                        {
                            "origin": "",
                            "destinations": [],
                            "start_date": "",
                            "end_date": "",
                            "traveller_count": None,
                            "transport_preferences": ["地铁", "公交"],
                            "interest_tags": ["美食", "夜景"],
                        }
                    )
                ],
            ),
        ],
    )
    sessions.update_metadata(
        "travel-a",
        {
            "travel_phase": "intake",
            "travel_draft_version": 1,
            "travel_draft": {
                "transport_preferences": ["地铁", "公交"],
                "interest_tags": ["美食", "夜景"],
            },
        },
    )
    update, _handoff = service.intake_tools_for_actor(_actor("user-a"), sessions)

    result = update.execute_with_context(
        {"patch": {}},
        ToolExecutionContext(
            actor=_actor("user-a"),
            session_id="travel-a",
            turn_id="turn-recovery",
            turn_index=3,
            channel="travel",
        ),
    )

    payload = json.loads(result.output)
    assert payload["ready"] is True
    assert payload["draft"]["origin"] == "重庆南山"
    assert payload["draft"]["destinations"] == ["重庆主城"]
    assert payload["draft"]["traveller_count"] == 1
    assert payload["draft"]["transport_preferences"] == ["地铁", "公交"]
    assert payload["draft"]["interest_tags"] == ["美食", "夜景"]
    assert sessions.load("travel-a").metadata["travel_draft_version"] == 3


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
        "budget_level": "balanced",
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
    public_candidates = events.items[0][1]["ui_metadata"]["detail_data"]["candidates"]
    assert all("itinerary" not in candidate for candidate in public_candidates)
    assert all("budget_items" not in candidate for candidate in public_candidates)
    assert selected.selected_candidate_id == "compact"
    assert selected.status == "selected"


def test_single_candidate_is_auto_selected_and_emits_decision_record(tmp_path):
    resolver = FilesystemUserContextResolver(tmp_path / "contexts", workspace_dir=tmp_path)
    service = TravelApplicationService(TravelConfig(enabled=True), resolver)
    tool = service.tools_for_actor(_actor("user-a"))[2]
    events = Events()
    candidate = _candidate_summary("complete-coverage", recommended=True)

    result = tool.execute_with_context(
        {"recommended_candidate_id": "complete-coverage", "candidates": [candidate]},
        ToolExecutionContext(
            actor=_actor("user-a"),
            session_id="travel-auto",
            turn_id="turn-auto",
            turn_index=1,
            channel="travel",
            runtime_events=events,
        ),
    )

    review = service.get_candidate_review(_actor("user-a"), "travel-auto")
    assert not result.is_error
    assert result.metadata["code"] == "TRAVEL_CANDIDATE_AUTO_SELECTED"
    assert review is not None and review.status == "selected"
    assert review.selected_candidate_id == "complete-coverage"
    assert events.items[0][0] == "travel.candidate_review_auto_selected"


def test_candidate_selection_starts_finalization_source_budget_once(tmp_path, monkeypatch):
    resolver = FilesystemUserContextResolver(tmp_path / "contexts", workspace_dir=tmp_path)
    service = TravelApplicationService(TravelConfig(enabled=True), resolver)
    service.save_candidate_review(
        _actor("user-a"),
        session_id="travel-a",
        turn_id="turn-a",
        candidates=[_candidate_summary("slow", recommended=True), _candidate_summary("compact")],
        recommended_candidate_id="slow",
    )
    calls: list[str] = []
    monkeypatch.setattr(
        service.source_ledger,
        "begin_finalization_budget",
        lambda session_id: calls.append(session_id),
    )

    service.select_candidate(_actor("user-a"), "travel-a", "compact")

    assert calls == ["travel-a"]


def test_candidate_review_accepts_optimizer_summary_when_internal_skeleton_is_omitted(tmp_path):
    resolver = FilesystemUserContextResolver(tmp_path / "contexts", workspace_dir=tmp_path)
    service = TravelApplicationService(TravelConfig(enabled=True), resolver)
    tool = service.tools_for_actor(_actor("user-a"))[2]
    candidates = [_candidate_summary("slow", recommended=True), _candidate_summary("compact")]
    for candidate in candidates:
        candidate.pop("itinerary")
        candidate.pop("budget_items")

    result = tool.execute_with_context(
        {"recommended_candidate_id": "slow", "candidates": candidates},
        ToolExecutionContext(
            actor=_actor("user-a"),
            session_id="travel-a",
            turn_id="turn-a",
            turn_index=1,
            channel="travel",
        ),
    )

    assert not result.is_error
    assert result.metadata["code"] == "TRAVEL_CANDIDATE_REVIEW_REQUIRED"


def test_candidate_review_uses_verified_source_coverage_instead_of_model_claim(tmp_path):
    resolver = FilesystemUserContextResolver(tmp_path / "contexts", workspace_dir=tmp_path)
    service = TravelApplicationService(TravelConfig(enabled=True), resolver)
    service.source_ledger.register_expected(
        "travel-a",
        ["mcp__amap__maps_text_search", "mcp__open-meteo__get_forecast"],
    )
    service.source_ledger.observe(
        "travel-a",
        "mcp__amap__maps_text_search",
        ToolResult(output='{"pois":[{"name":"沈阳故宫"}]}', metadata={"code": "MCP_OK"}),
    )
    tool = service.tools_for_actor(_actor("user-a"))[2]
    events = Events()
    candidates = [_candidate_summary("slow", recommended=True), _candidate_summary("compact")]
    for candidate in candidates:
        candidate["evidence_coverage"] = 0

    result = tool.execute_with_context(
        {"recommended_candidate_id": "slow", "candidates": candidates},
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
    review = service.get_candidate_review(_actor("user-a"), "travel-a")
    assert review is not None
    assert [item["evidence_coverage"] for item in review.candidates] == [0.5, 0.5]
    public = events.items[0][1]["ui_metadata"]["detail_data"]["candidates"]
    assert [item["evidence_coverage"] for item in public] == [0.5, 0.5]


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
        "itinerary": {
            "days": [{
                "date": "2026-10-01",
                "city_or_area": "郑州",
                "activities": [{"start": "09:00", "end": "12:00", "place": "河南博物院"}],
                "route_segments": [],
                "daily_budget": 500,
            }],
        },
        "budget_items": [{"name": "总预算", "lower": 1000, "expected": 1500, "upper": 2000}],
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
        "itinerary": {
            "days": [
                {
                    "date": day["date"],
                    "city_or_area": day["city_or_area"],
                    "activities": [
                        {key: activity[key] for key in ("start", "end", "place")}
                        for activity in day["activities"]
                    ],
                    "route_segments": [
                        {key: segment.get(key, "") for key in ("from", "to", "duration", "distance", "mode")}
                        for segment in day["route_segments"]
                    ],
                    "daily_budget": day["daily_budget"],
                }
                for day in plan["days"]
            ]
        },
        "budget_items": plan["budget"]["items"],
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
    walking_schema = (
        day_schema["properties"]["route_segments"]["items"]["properties"]
        ["walking_distance"]
    )
    assert walking_schema["anyOf"][0]["maximum"] == 50000
    assert walking_schema["anyOf"][1]["pattern"] == r"^\d+(?:\.\d+)?$"
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
    transport_schema = plan_schema["properties"]["transport_options"]["items"]
    stay_schema = plan_schema["properties"]["stay_recommendations"]["items"]
    assert "location" in activity_schema["required"]
    assert activity_schema["properties"]["location"]["type"] == "object"
    assert route_schema["properties"]["path"]["items"]["type"] == "object"
    assert route_schema["properties"]["transit_legs"]["items"]["additionalProperties"] is False
    assert {"line_name", "departure_stop", "arrival_stop"}.issubset(
        route_schema["properties"]["transit_legs"]["items"]["required"]
    )
    assert transport_schema["additionalProperties"] is False
    assert {"service_name", "departure", "arrival", "price_cny_per_person"}.issubset(
        transport_schema["properties"]
    )
    assert stay_schema["additionalProperties"] is False
    assert {
        "hotel_name",
        "check_in",
        "check_out",
        "price_status",
        "evidence_ids",
        "price_source_evidence_ids",
    }.issubset(
        stay_schema["required"]
    )


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
    assert "social" in result.output


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
