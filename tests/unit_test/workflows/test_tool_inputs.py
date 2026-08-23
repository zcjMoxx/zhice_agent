from __future__ import annotations

import pytest

from agent.workflows.tool_inputs import prepare_tool_arguments, with_required_query_helpers


def test_workflow_allowlist_automatically_includes_hidden_task_helpers():
    assert with_required_query_helpers({"mcp__open-meteo__get_forecast"}) == {
        "mcp__open-meteo__get_forecast",
        "mcp__open-meteo__geocode_place",
        "mcp__amap-maps__maps_text_search",
        "mcp__amap-maps__maps_search_detail",
    }
    assert with_required_query_helpers({"mcp__12306__get-tickets"}) == {
        "mcp__12306__get-tickets",
        "mcp__12306__get-station-code-of-citys",
    }
    assert with_required_query_helpers({"mcp__xhs-readonly__search_notes"}) == {
        "mcp__xhs-readonly__search_notes"
    }


def test_weather_place_name_is_resolved_to_coordinates():
    calls = []

    def invoke(name, arguments):
        calls.append((name, arguments))
        return {"results": [{"name": "上海", "latitude": 31.23, "longitude": 121.47}]}

    result = prepare_tool_arguments(
        "mcp__open-meteo__get_forecast",
        {"place_name": "上海", "start_date": "2026-08-22", "end_date": "2026-08-23"},
        invoke,
    )

    assert calls == [("mcp__open-meteo__geocode_place", {"name": "上海", "count": 1, "language": "zh"})]
    assert result == {
        "latitude": 31.23,
        "longitude": 121.47,
        "start_date": "2026-08-22",
        "end_date": "2026-08-23",
    }


def test_weather_place_name_requires_a_real_geocoding_result():
    with pytest.raises(ValueError, match="WORKFLOW_LOCATION_NOT_FOUND"):
        prepare_tool_arguments(
            "mcp__open-meteo__get_forecast",
            {"place_name": "不存在的地方"},
            lambda *_args: {"results": []},
        )


def test_detailed_chinese_place_falls_back_to_amap_poi_detail():
    calls = []

    def invoke(name, arguments):
        calls.append((name, arguments))
        if name.endswith("geocode_place"):
            return {"results": []}
        if name.endswith("maps_text_search"):
            return {"pois": [{"id": "B0FFF9ID6L", "name": "南岸区"}]}
        return {"id": "B0FFF9ID6L", "location": "106.644254,29.501090"}

    result = prepare_tool_arguments(
        "mcp__open-meteo__get_forecast",
        {"place_name": "重庆南岸区", "forecast_days": 1},
        invoke,
    )

    assert [name for name, _arguments in calls] == [
        "mcp__open-meteo__geocode_place",
        "mcp__amap-maps__maps_text_search",
        "mcp__amap-maps__maps_search_detail",
    ]
    assert result["latitude"] == 29.50109
    assert result["longitude"] == 106.644254


def test_ticket_places_are_resolved_to_station_codes():
    result = prepare_tool_arguments(
        "mcp__12306__get-tickets",
        {"departure_name": "北京", "arrival_name": "上海虹桥", "date": "2026-08-22"},
        lambda name, arguments: {
            "北京": {"station_code": "BJP"},
            "上海虹桥": {"station_code": "AOH"},
        },
    )

    assert result == {
        "fromStation": "BJP",
        "toStation": "AOH",
        "date": "2026-08-22",
    }


def test_ticket_station_parser_accepts_text_wrapped_mcp_results():
    result = prepare_tool_arguments(
        "mcp__12306__get-tickets",
        {"departure_name": "北京", "arrival_name": "上海虹桥", "date": "2026-08-22"},
        lambda _name, _arguments: {
            "content": "北京对应站码 BJP；上海虹桥对应站码 AOH"
        },
    )

    assert result["fromStation"] == "BJP"
    assert result["toStation"] == "AOH"




def test_weather_forecast_defaults_to_a_dynamic_two_day_window():
    result = prepare_tool_arguments(
        "mcp__open-meteo__get_forecast",
        {"place_name": "上海"},
        lambda *_args: {"results": [{"latitude": 31.23, "longitude": 121.47}]},
    )

    assert result["start_date"] <= result["end_date"]
    assert "forecast_days" not in result


def test_xhs_note_url_is_converted_to_detail_arguments():
    result = prepare_tool_arguments(
        "mcp__xhs-readonly__get_note_detail",
        {
            "note_url": "https://www.xiaohongshu.com/explore/note-123?xsec_token=token-456",
            "include_comments": True,
        },
        lambda *_args: None,
    )

    assert result == {
        "feed_id": "note-123",
        "xsec_token": "token-456",
        "include_comments": True,
    }


@pytest.mark.parametrize(
    "url",
    [
        "https://example.com/explore/note?xsec_token=token",
        "http://www.xiaohongshu.com/explore/note?xsec_token=token",
        "https://www.xiaohongshu.com/explore/note",
    ],
)
def test_xhs_note_url_rejects_unsafe_or_incomplete_links(url):
    with pytest.raises(ValueError, match="WORKFLOW_XHS_LINK_INVALID"):
        prepare_tool_arguments(
            "mcp__xhs-readonly__get_note_detail",
            {"note_url": url},
            lambda *_args: None,
        )
