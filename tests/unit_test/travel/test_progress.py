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


def _request(
    tool_name: str,
    *,
    arguments: dict | None = None,
    output: str = "{}",
    is_error: bool = False,
    channel: str = "travel",
) -> PostToolHookRequest:
    return PostToolHookRequest(
        tool_name=tool_name,
        arguments=arguments or {},
        output=output,
        is_error=is_error,
        result_metadata={},
        session_id="session-travel",
        turn_id="turn-travel",
        channel=channel,
    )
