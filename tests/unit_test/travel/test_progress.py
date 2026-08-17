from __future__ import annotations

import json

from agent.applications.travel.progress import (
    TravelProgressHookRuntime,
    travel_tool_presentation,
)
from agent.protocols.hook import PostToolHookRequest, PostToolHookResult, PreToolHookRequest


def test_amap_search_projects_query_count_and_bounded_place_results():
    result = travel_tool_presentation(
        _request(
            "mcp__amap-maps__maps_text_search",
            arguments={"keywords": "大理古城周边景点", "city": "大理"},
            output=json.dumps(
                {
                    "pois": [
                        {"name": f"景点{index}", "address": f"地址{index}"}
                        for index in range(8)
                    ]
                },
                ensure_ascii=False,
            ),
        )
    )

    assert result.display["title"] == "高德地图查询完成"
    assert result.ui_metadata["detail_type"] == "search_results"
    detail = result.ui_metadata["detail_data"]
    assert detail["provider"] == "高德地图"
    assert detail["query"] == "大理古城周边景点"
    assert detail["result_count"] == 8
    assert [item["title"] for item in detail["items"]] == [f"景点{index}" for index in range(5)]


def test_amap_history_recovery_reads_the_persisted_tool_output_envelope():
    inner = json.dumps(
        {"pois": [{"name": "龙门石窟", "address": "龙门中街13号"}]},
        ensure_ascii=False,
    )
    result = travel_tool_presentation(
        _request(
            "mcp__amap-maps__maps_text_search",
            arguments={"keywords": "龙门石窟", "city": "洛阳"},
            output=json.dumps(
                {"status": "success", "output": inner, "metadata": {"code": "MCP_OK"}},
                ensure_ascii=False,
            ),
        )
    )

    detail = result.ui_metadata["detail_data"]
    assert detail["result_count"] == 1
    assert detail["items"] == [{"title": "龙门石窟", "detail": "龙门中街13号"}]


def test_amap_geocode_projects_resolved_city_and_coordinate_instead_of_empty_copy():
    result = travel_tool_presentation(
        _request(
            "mcp__amap-maps__maps_geo",
            arguments={"address": "北中街路118号", "city": "沈阳"},
            output=json.dumps(
                {
                    "return": [
                        {
                            "province": "辽宁省",
                            "city": "沈阳市",
                            "district": "沈河区",
                            "street": "北中街路",
                            "number": "118号",
                            "location": "123.454495,41.804019",
                        }
                    ]
                },
                ensure_ascii=False,
            ),
        )
    )

    detail = result.ui_metadata["detail_data"]
    assert detail["summary"] == "解析到 1 个地址候选，展示前 1 个"
    assert detail["items"] == [
        {
            "title": "辽宁省沈阳市沈河区北中街路118号",
            "detail": "沈河区 · 坐标 123.454495,41.804019",
        }
    ]


def test_amap_detail_projects_single_poi_object_instead_of_empty_result_copy():
    result = travel_tool_presentation(
        _request(
            "mcp__amap-maps__maps_search_detail",
            arguments={"id": "B017B00X4D"},
            output=json.dumps(
                {
                    "id": "B017B00X4D",
                    "name": "龙门石窟",
                    "address": "龙门中街13号",
                    "business_area": "龙门石窟街道",
                    "location": "112.477463,34.558782",
                },
                ensure_ascii=False,
            ),
        )
    )

    detail = result.ui_metadata["detail_data"]
    assert detail["query"] == "龙门石窟"
    assert detail["summary"] == "已核对 龙门石窟 的地址与地点信息"
    assert detail["items"] == [
        {"title": "龙门石窟", "detail": "龙门中街13号 · 龙门石窟街道"}
    ]


def test_weather_geocode_hides_unverified_same_name_place_details():
    result = travel_tool_presentation(
        _request(
            "mcp__open-meteo__geocode_place",
            arguments={"name": "洛阳", "count": 1, "language": "zh"},
            output=json.dumps(
                {
                    "results": [
                        {
                            "name": "洛阳",
                            "latitude": 24.95938,
                            "longitude": 118.683,
                            "country": "中国",
                            "admin1": "福建省",
                        }
                    ]
                },
                ensure_ascii=False,
            ),
        )
    )

    detail = result.ui_metadata["detail_data"]
    assert detail["items"] == []
    assert "行政区域核对通过后" in detail["summary"]
    assert "福建" not in json.dumps(detail, ensure_ascii=False)


def test_amap_route_and_plain_text_12306_results_have_human_readable_units_and_rows():
    route = travel_tool_presentation(
        _request(
            "mcp__amap-maps__maps_direction_transit_integrated",
            arguments={"origin": "A", "destination": "B"},
            output=json.dumps(
                {
                    "route": {
                        "distance": "6141",
                        "transits": [
                            {
                                "duration": "1792",
                                "segments": [
                                    {
                                        "bus": {
                                            "buslines": [
                                                {
                                                    "name": "地铁1号线",
                                                    "departure_stop": {"name": "沈阳站"},
                                                    "arrival_stop": {"name": "怀远门"},
                                                }
                                            ]
                                        }
                                    }
                                ],
                            }
                        ],
                    }
                },
                ensure_ascii=False,
            ),
        )
    )
    rail = travel_tool_presentation(
        _request(
            "mcp__12306__get-tickets",
            arguments={"fromStation": "IFP", "toStation": "SYT"},
            output=(
                "车次 | 出发站 -> 到达站 | 出发时间 -> 到达时间 | 历时\n"
                "G3501(实际车次train_no: 24000G350110) 北京朝阳(telecode: IFP) -> "
                "沈阳北(telecode: SBT) 06:35 -> 09:11 02:36"
            ),
        )
    )

    assert route.ui_metadata["detail_data"]["items"][0]["detail"] == (
        "距离 6.1 公里 · 约 30 分钟 · 地铁1号线：沈阳站 → 怀远门"
    )
    assert rail.ui_metadata["detail_data"]["items"] == [
        {"title": "G3501", "detail": "北京朝阳 06:35 → 沈阳北 09:11 · 历时 02:36"}
    ]


def test_amap_transit_progress_does_not_present_a_nested_walk_as_total_distance():
    route = travel_tool_presentation(
        _request(
            "mcp__amap-maps__maps_direction_transit_integrated",
            arguments={"origin": "沈阳北站", "destination": "西塔"},
            output=json.dumps(
                {
                    "route": {
                        "transits": [
                            {
                                "duration": "2280",
                                "segments": [
                                    {"walking": {"distance": "231"}},
                                    {
                                        "bus": {
                                            "buslines": [
                                                {
                                                    "name": "地铁2号线",
                                                    "departure_stop": {"name": "沈阳北站"},
                                                    "arrival_stop": {"name": "青年大街"},
                                                }
                                            ]
                                        }
                                    },
                                ],
                            }
                        ]
                    }
                },
                ensure_ascii=False,
            ),
        )
    )

    detail = route.ui_metadata["detail_data"]["items"][0]["detail"]
    assert detail == "约 38 分钟 · 地铁2号线：沈阳北站 → 青年大街"
    assert "231" not in detail


def test_amap_transit_progress_does_not_present_metrics_from_truncated_fragment():
    route = travel_tool_presentation(
        _request(
            "mcp__amap-maps__maps_direction_transit_integrated",
            arguments={"origin": "酒店", "destination": "白马寺"},
            output=json.dumps(
                {
                    "distance": "275",
                    "duration": "120",
                    "bus": {
                        "buslines": [
                            {
                                "name": "58路",
                                "departure_stop": {"name": "龙门大道关林路口"},
                                "arrival_stop": {"name": "白马寺公交停车场"},
                            }
                        ]
                    },
                },
                ensure_ascii=False,
            ),
        )
    )

    detail = route.ui_metadata["detail_data"]["items"][0]["detail"]
    assert detail == "58路：龙门大道关林路口 → 白马寺公交停车场"
    assert "275" not in detail


def test_amap_geocode_return_wrapper_displays_city_and_coordinate():
    result = travel_tool_presentation(
        _request(
            "mcp__amap-maps__maps_geo",
            arguments={"address": "洛阳"},
            output=json.dumps(
                {
                    "return": [
                        {
                            "province": "河南省",
                            "city": "洛阳市",
                            "district": [],
                            "location": "112.453895,34.619702",
                        }
                    ]
                },
                ensure_ascii=False,
            ),
        )
    )

    detail = result.ui_metadata["detail_data"]
    assert detail["summary"] == "解析到 1 个地址候选，展示前 1 个"
    assert detail["items"] == [
        {"title": "河南省洛阳市", "detail": "坐标 112.453895,34.619702"}
    ]


def test_amap_truncated_route_uses_retained_route_total_not_nested_step():
    output = """
    {"route":{"origin":"112.605311,34.721828","destination":"112.471152,34.676032",
    "distance":"17697","transits":[{"duration":"4005","segments":[{"walking":
    {"distance":"155"},"bus":{"buslines":[{"name":"56路","departure_stop":
    {"name":"白马寺公交停车场"},"arrival_stop":{"name":"中州东路安居路口"}}]}}
    [truncated middle]
    {"walking":{"distance":"252"}}]}}]}}
    """
    result = travel_tool_presentation(
        _request(
            "mcp__amap-maps__maps_direction_transit_integrated",
            arguments={
                "origin": "112.605311,34.721828",
                "destination": "112.471152,34.676032",
            },
            output=output,
        )
    )

    detail = result.ui_metadata["detail_data"]["items"][0]["detail"]
    assert detail == (
        "距离 17.7 公里 · 约 67 分钟 · "
        "56路：白马寺公交停车场 → 中州东路安居路口"
    )
    assert "155 米" not in detail


def test_amap_persisted_tool_wrapper_unwraps_truncated_route_total():
    raw = (
        '{"route":{"distance":"17697","transits":[{"duration":"3442",'
        '"segments":[{"walking":{"distance":"155"},"bus":{"buslines":'
        '[{"name":"58路","departure_stop":{"name":"白马寺公交停车场"},'
        '"arrival_stop":{"name":"承福门大街盐店口街口"}}]}}'
        '[truncated middle] {"walking":{"distance":"252"}}]}}]}}'
    )
    persisted = json.dumps(
        {"status": "success", "output": raw, "metadata": {"code": "MCP_OK"}},
        ensure_ascii=False,
    )
    result = travel_tool_presentation(
        _request(
            "mcp__amap-maps__maps_direction_transit_integrated",
            arguments={"origin": "白马寺", "destination": "酒店"},
            output=persisted,
        )
    )

    detail = result.ui_metadata["detail_data"]["items"][0]["detail"]
    assert detail == (
        "距离 17.7 公里 · 约 57 分钟 · "
        "58路：白马寺公交停车场 → 承福门大街盐店口街口"
    )


def test_optimizer_projects_selected_candidate_budget_and_places():
    output = {
        "status": "success",
        "code": "OK",
        "data": {
            "selected_candidate": {
                "candidate_id": "rail-first",
                "days": [
                    {"activities": [{"place": "大理古城"}, {"place": "洱海生态廊道"}]}
                ],
            },
            "budget": {"lower": 3600, "expected": 4200, "upper": 4800},
            "quality_gate": {
                "route_minutes": 260,
                "route_distance_km": 68,
                "evidence_coverage": 0.85,
            },
        },
    }
    result = travel_tool_presentation(
        _request(
            "run_skill",
            arguments={
                "skill": "zhice-official/travel-planner",
                "params": {"candidates": [{"candidate_id": "rail-first"}, {"candidate_id": "slow"}]},
            },
            output=json.dumps(output, ensure_ascii=False),
        )
    )

    detail = result.ui_metadata["detail_data"]
    assert detail["provider"] == "行程可行性筛选"
    assert detail["result_count"] == 2
    assert detail["summary"] == "比较 2 个候选后，已采用可行方案"
    assert detail["items"][0] == {
        "title": "已采用候选方案",
        "detail": "大理古城、洱海生态廊道",
    }


def test_internal_travel_tools_are_hidden_and_source_errors_are_user_facing():
    hidden = travel_tool_presentation(_request("load_skills"))
    failed = travel_tool_presentation(
        _request(
            "mcp__xhs-readonly__search_notes",
            arguments={"keyword": "大理避坑"},
            output="remote failure",
            is_error=True,
        )
    )

    assert hidden.display == {"visibility": "internal"}
    assert failed.display["title"] == "小红书只读暂未取得结果"
    assert "安全降级" in failed.display["detail"]
    assert "mcp__" not in json.dumps(failed.display, ensure_ascii=False)


def test_wrapped_xhs_source_error_is_not_presented_as_empty_results():
    upstream_error = {
        "status": "error",
        "code": "TRAVEL_SOURCE_UNAVAILABLE",
        "message": "Xiaohongshu read-only query failed.",
    }
    wrapped = {
        "status": "success",
        "output": json.dumps(upstream_error, ensure_ascii=False),
        "metadata": {"code": "MCP_OK"},
    }

    failed = travel_tool_presentation(
        _request(
            "mcp__xhs-readonly__search_notes",
            arguments={"keyword": "洛阳旅游攻略"},
            output=json.dumps(wrapped, ensure_ascii=False),
        )
    )

    assert failed.display["title"] == "小红书只读暂未取得结果"
    assert "本次查询未成功" in failed.display["detail"]
    assert "空结果" not in json.dumps(
        {"display": failed.display, "ui_metadata": failed.ui_metadata},
        ensure_ascii=False,
    )


def test_non_json_and_oversized_external_output_fail_open_without_leaking_content():
    runtime = TravelProgressHookRuntime()
    result = runtime.run_post_tooluse(
        _request(
            "mcp__amap__maps_text_search",
            arguments={"keywords": "景点"},
            output="API_KEY=should-not-appear" * 2000,
        )
    )

    assert result.display["title"] == "高德地图查询完成"
    assert "API_KEY" not in json.dumps(
        {"display": result.display, "ui_metadata": result.ui_metadata},
        ensure_ascii=False,
    )
    assert result.ui_metadata["detail_data"]["items"] == []


def test_travel_runtime_does_not_enrich_ordinary_web_channel():
    runtime = TravelProgressHookRuntime()
    result = runtime.run_post_tooluse(
        _request(
            "mcp__amap__maps_text_search",
            arguments={"keywords": "景点"},
            output='{"pois":[{"name":"大理古城"}]}',
            channel="web",
        )
    )

    assert result == PostToolHookResult()


def test_travel_tavily_query_is_bounded_and_avoids_fast_country_conflict():
    runtime = TravelProgressHookRuntime()
    result = runtime.run_pre_tooluse(
        PreToolHookRequest(
            tool_name="mcp__tavily__tavily_search",
            arguments={
                "query": "河南攻略",
                "country": "china",
                "search_depth": "fast",
                "max_results": 20,
                "include_raw_content": True,
            },
            session_id="session-travel",
            turn_id="turn-travel",
            channel="travel",
        )
    )

    assert result.action == "modify"
    assert result.arguments["include_raw_content"] is False
    assert result.arguments["max_results"] == 5
    assert result.arguments["search_depth"] == "basic"


def test_travel_tavily_extract_is_not_modified_like_search():
    runtime = TravelProgressHookRuntime()
    request = PreToolHookRequest(
        tool_name="mcp__tavily__tavily_extract",
        arguments={"urls": ["https://example.com"]},
        session_id="session-travel",
        turn_id="turn-travel",
        channel="travel",
    )

    assert runtime.run_pre_tooluse(request).action == "continue"


def test_tavily_reads_multiple_mcp_json_documents_and_keeps_filtered_excerpts():
    structured = {"query": "郑州博物馆", "results": [{"title": "河南博物院", "content": "开放时间与预约说明"}]}
    output = json.dumps(structured, ensure_ascii=False) + "\n\n" + json.dumps(structured, ensure_ascii=False)

    result = travel_tool_presentation(
        _request("mcp__tavily__tavily_search", arguments={"query": "郑州博物馆"}, output=output)
    )

    detail = result.ui_metadata["detail_data"]
    assert detail["result_count"] == 1
    assert detail["items"] == [{"title": "河南博物院", "detail": "开放时间与预约说明"}]


def test_tavily_progress_uses_effective_query_from_compacted_result():
    result = travel_tool_presentation(
        _request(
            "mcp__tavily__tavily_search",
            arguments={
                "query": "洛阳 旅游攻略 公共交通 经济 实惠 龙门石窟 白马寺 洛阳博物馆"
            },
            output=json.dumps(
                {
                    "query": "洛阳旅游攻略 公共交通",
                    "results": [
                        {
                            "title": "洛阳公共交通攻略",
                            "content": "洛阳城区可优先乘坐地铁。",
                        }
                    ],
                },
                ensure_ascii=False,
            ),
        )
    )

    detail = result.ui_metadata["detail_data"]
    assert detail["query"] == "洛阳旅游攻略 公共交通"
    assert "经济 实惠" not in detail["query"]


def test_xhs_reads_nested_text_json_and_note_card_summary():
    nested = {
        "feeds": [{
            "note_card": {
                "display_title": "黔灵山避坑指南",
                "desc": "早上人少，雨天石阶湿滑",
                "user": {"nickname": "旅行者甲"},
            }
        }]
    }
    output = json.dumps({"status": "success", "data": {"text": json.dumps(nested, ensure_ascii=False)}}, ensure_ascii=False)

    result = travel_tool_presentation(
        _request("mcp__xhs-readonly__search_notes", arguments={"keyword": "黔灵山 避坑"}, output=output)
    )

    detail = result.ui_metadata["detail_data"]
    assert detail["result_count"] == 1
    assert detail["items"] == [{"title": "黔灵山避坑指南", "detail": "早上人少，雨天石阶湿滑 · 旅行者甲"}]


def test_xhs_reads_rednote_camel_case_feed_cards():
    output = json.dumps(
        {
            "feeds": [
                {
                    "noteCard": {
                        "displayTitle": "涪陵一日游不绕路路线",
                        "desc": "白鹤梁水下博物馆和美心红酒小镇顺路安排",
                        "user": {"nickName": "山城周末"},
                    }
                },
                {
                    "noteCard": {
                        "displayTitle": "816 工程参观提醒",
                        "user": {"nickName": "旅行研究所"},
                    }
                },
            ]
        },
        ensure_ascii=False,
    )

    result = travel_tool_presentation(
        _request("mcp__xhs-readonly__search_notes", arguments={"keyword": "涪陵 一日游 白鹤梁 816工程"}, output=output)
    )

    detail = result.ui_metadata["detail_data"]
    assert detail["result_count"] == 2
    assert detail["summary"] == "读取 2 条公开经验，展示前 2 条筛选摘要"
    assert detail["items"] == [
        {"title": "涪陵一日游不绕路路线", "detail": "白鹤梁水下博物馆和美心红酒小镇顺路安排 · 山城周末"},
        {"title": "816 工程参观提醒", "detail": "旅行研究所"},
    ]


def test_travel_guard_result_is_hidden_from_user_progress():
    result = travel_tool_presentation(
        _request(
            "mcp__12306__get-tickets",
            is_error=True,
            result_metadata={"travel_progress_visibility": "internal"},
        )
    )

    assert result.display == {"visibility": "internal"}


def test_12306_station_lookup_is_hidden_and_not_on_sale_shows_sale_date():
    lookup = travel_tool_presentation(
        _request("mcp__12306__get-stations-code-in-city", output='{"stations":[]}')
    )
    ticket = travel_tool_presentation(
        _request(
            "mcp__12306__get-tickets",
            arguments={"date": "2026-09-20", "from": "BJP", "to": "SYT"},
            output=(
                '{"status":"not_on_sale","date":"2026-09-20",'
                '"sale_open_date":"2026-09-06","trains":[]}'
            ),
        )
    )

    assert lookup.display == {"visibility": "internal"}
    assert ticket.display["title"] == "铁路 12306查询完成"
    assert "2026-09-06" in ticket.display["detail"]


def test_progress_parser_keeps_large_valid_tavily_results():
    output = json.dumps(
        {"results": [{"title": "沈阳酒店", "content": "x" * 25_000}]},
        ensure_ascii=False,
    )

    result = travel_tool_presentation(
        _request(
            "mcp__tavily__tavily_search",
            arguments={"query": "沈阳酒店"},
            output=output,
        )
    )

    assert result.ui_metadata["detail_data"]["result_count"] == 1
    assert result.ui_metadata["detail_data"]["items"][0]["title"] == "沈阳酒店"


def _request(
    tool_name: str,
    *,
    arguments: dict | None = None,
    output: str = "{}",
    is_error: bool = False,
    channel: str = "travel",
    result_metadata: dict | None = None,
) -> PostToolHookRequest:
    return PostToolHookRequest(
        tool_name=tool_name,
        arguments=arguments or {},
        output=output,
        is_error=is_error,
        result_metadata=result_metadata or {},
        session_id="session-travel",
        turn_id="turn-travel",
        channel=channel,
    )
