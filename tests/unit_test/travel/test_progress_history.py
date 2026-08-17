from __future__ import annotations

import json

from agent.applications.travel.history import project_travel_progress
from agent.message import Message


def test_history_rebuilds_safe_tool_progress_and_completion():
    messages = [
        _assistant_call(
            "map-call",
            "mcp__amap-maps__maps_text_search",
            {"keywords": "大理古城周边景点", "city": "大理"},
        ),
        Message(
            role="tool",
            name="mcp__amap-maps__maps_text_search",
            tool_call_id="map-call",
            content=json.dumps(
                {"pois": [{"name": "大理古城", "address": "大理镇"}]},
                ensure_ascii=False,
            ),
            metadata={"is_error": False},
        ),
        _assistant_call("internal", "load_skills", {}),
        Message(
            role="tool",
            name="load_skills",
            tool_call_id="internal",
            content="hidden",
            metadata={"is_error": False},
        ),
        _assistant_call("final", "finalize_travel_plan", {"plan": {}}),
        Message(
            role="tool",
            name="finalize_travel_plan",
            tool_call_id="final",
            content='{"status":"success"}',
            metadata={"is_error": False, "plan_id": "travel-plan-one"},
        ),
    ]

    items = project_travel_progress(messages)

    assert [item["id"] for item in items] == [
        "history-requirements",
        "map-call",
        "final",
        "history-complete",
    ]
    assert items[1]["title"] == "高德地图查询完成"
    assert items[1]["result"]["items"][0]["title"] == "大理古城"
    assert items[-1]["stage"] == "complete"
    assert "internal" not in {item["id"] for item in items}


def test_history_keeps_safe_failure_without_raw_error_text():
    messages = [
        _assistant_call(
            "xhs-failed",
            "mcp__xhs-readonly__search_notes",
            {"keyword": "大理避坑"},
        ),
        Message(
            role="tool",
            name="mcp__xhs-readonly__search_notes",
            tool_call_id="xhs-failed",
            content="secret upstream traceback",
            metadata={"is_error": True, "code": "UPSTREAM_TIMEOUT"},
        ),
    ]

    items = project_travel_progress(messages)

    assert items[-1]["status"] == "error"
    assert items[-1]["stage"] == "guides"
    assert "secret upstream traceback" not in json.dumps(items, ensure_ascii=False)


def test_history_returns_empty_for_requirement_only_session():
    assert project_travel_progress([Message(role="user", content="重庆去大理")]) == []


def test_history_restores_selected_candidate_finalization_lanes():
    messages = [
        _assistant_call(
            "final-batch",
            "delegate_tasks",
            {
                "tasks": [
                    {"id": "stay", "task": "stay", "profile": "travel-final-stay"},
                    {"id": "route", "task": "route", "profile": "travel-final-route"},
                ]
            },
        ),
        Message(
            role="tool",
            name="delegate_tasks",
            tool_call_id="final-batch",
            content='{"status":"completed"}',
            metadata={"is_error": False},
        ),
    ]

    items = project_travel_progress(messages)

    assert [item.get("lane") for item in items if item.get("lane")] == [
        "lodging",
        "transport",
    ]
    assert all(item["stage"] == "validate" for item in items if item.get("lane"))
    assert all(item["status"] == "done" for item in items if item.get("lane"))


def _assistant_call(call_id: str, name: str, arguments: dict) -> Message:
    return Message(
        role="assistant",
        content="",
        turn_id="turn-a",
        tool_calls=[
            {
                "id": call_id,
                "type": "function",
                "function": {
                    "name": name,
                    "arguments": json.dumps(arguments, ensure_ascii=False),
                },
            }
        ],
    )
