from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest

from agent.core.loop import CancellationToken
from agent.protocols.auth import ActorContext
from agent.protocols.skill import SkillRunRequest
from agent.skills.executor import PythonSkillExecutor
from agent.skills.loader import SkillLoader
from tests.unit_test.travel.fixtures import plan_payload, request_payload


def test_optimizer_selects_feasible_candidate_and_returns_quality_gate():
    result = _run(_params())

    assert result.status == "success"
    assert result.code == "OK"
    assert result.data["selected_candidate"]["candidate_id"] == "candidate-a"
    assert result.data["feasible_candidates"][0]["candidate_id"] == "candidate-a"
    assert result.data["feasible_candidates"][0]["recommended"] is True
    assert result.data["feasible_candidates"][0]["itinerary"]["days"][0]["activities"][0] == {
        "start": "14:00",
        "end": "17:00",
        "place": "大理古城",
    }
    assert result.data["feasible_candidates"][0]["budget_items"] == result.data["selected_candidate"]["budget_items"]
    assert result.data["quality_gate"]["passed"] is True
    assert result.data["budget"]["lower"] <= result.data["budget"]["expected"] <= result.data["budget"]["upper"]


@pytest.mark.parametrize(
    ("mutate", "reason"),
    [
        (lambda value: value["candidates"][0]["budget_items"].__setitem__(0, {"name": "交通", "lower": 6000, "expected": 6200, "upper": 6500}), "BUDGET_EXPECTED_EXCEEDS_HARD_LIMIT"),
        (lambda value: value["candidates"][0]["days"][1].update({"city_or_area": "丽江", "route_segments": []}), "CROSS_CITY_ROUTE_MISSING"),
        (lambda value: value["candidates"][0]["days"][0]["activities"][0].update({"opening_windows": [{"start": "09:00", "end": "12:00"}]}), "OPENING_HOURS_CONFLICT"),
        (lambda value: value.update({"limits": {"max_daily_minutes": 120}}), "DAILY_TIME_LIMIT_EXCEEDED"),
    ],
)
def test_optimizer_rejects_budget_cross_city_opening_and_time_conflicts(mutate, reason):
    params = _params()
    mutate(params)

    result = _run(params)

    assert result.status == "error"
    assert result.code == "TRAVEL_OPTIMIZATION_FAILED"
    assert reason in result.data["rejected_candidates"][0]["reasons"]


def test_optimizer_rejects_overlap_and_route_backtrack():
    params = _params()
    activities = params["candidates"][0]["days"][1]["activities"]
    activities[1]["start"] = "11:00"
    result = _run(params)
    assert result.code == "TRAVEL_OPTIMIZATION_FAILED"
    assert "ACTIVITY_OVERLAP" in result.data["rejected_candidates"][0]["reasons"]

    params = _params()
    params["candidates"][0]["days"][1]["activities"] = [
        {"start": "08:00", "end": "09:00", "place": "A"},
        {"start": "10:00", "end": "11:00", "place": "B"},
        {"start": "12:00", "end": "13:00", "place": "A"},
    ]
    result = _run(params)
    assert "ROUTE_BACKTRACK" in result.data["rejected_candidates"][0]["reasons"]


def test_optimizer_rejects_expected_total_above_hard_budget():
    params = _params()
    params["request"]["budget_total_cny"] = 3000

    result = _run(params)

    assert result.code == "TRAVEL_OPTIMIZATION_FAILED"
    assert "BUDGET_EXPECTED_EXCEEDS_HARD_LIMIT" in result.data["rejected_candidates"][0][
        "reasons"
    ]


def test_optimizer_tightens_flexible_expected_budget_to_hard_limit():
    params = _params()
    params["request"]["budget_total_cny"] = 3500
    params["candidates"][0]["budget_items"].append(
        {"name": "餐饮", "lower": 300, "expected": 450, "upper": 600}
    )

    result = _run(params)

    assert result.status == "success"
    candidate = result.data["feasible_candidates"][0]
    assert candidate["budget"]["expected"] == 3500
    assert "BUDGET_EXPECTED_TIGHTENED_TO_HARD_LIMIT" in candidate["warnings"]
    meal = next(item for item in candidate["budget_items"] if item["name"] == "餐饮")
    assert meal["expected"] == 400


def test_optimizer_keeps_raw_intensity_gate_but_caps_public_scores_at_ten():
    params = _params()
    params["request"]["pace"] = "intensive"
    params["candidates"][0]["days"][0]["activities"][0].update(
        {"start": "08:00", "end": "19:00"}
    )

    result = _run(params)

    assert result.status == "success"
    candidate = result.data["feasible_candidates"][0]
    assert candidate["daily_intensity_scores"][0] == 10.0
    assert "DAILY_INTENSITY_HIGH" in candidate["warnings"]


def test_optimizer_tightens_small_activity_overrun_without_changing_route_facts():
    params = _params()
    day = params["candidates"][0]["days"][1]
    day["activities"] = [
        {"start": "08:30", "end": "14:30", "place": "兵马俑"},
        {"start": "16:30", "end": "18:00", "place": "钟楼周边"},
    ]
    day["route_segments"] = [
        {"from": "酒店", "to": "兵马俑", "duration": 70, "distance": 36, "mode": "公交"},
        {"from": "兵马俑", "to": "酒店", "duration": 70, "distance": 36, "mode": "公交"},
    ]

    result = _run(params)

    assert result.status == "success"
    candidate = result.data["feasible_candidates"][0]
    assert "ACTIVITY_DURATION_TIGHTENED_FOR_FEASIBILITY" in candidate["warnings"]
    adjusted_routes = candidate["itinerary"]["days"][1]["route_segments"]
    assert [item["duration"] for item in adjusted_routes] == [70, 70]
    assert [item["distance"] for item in adjusted_routes] == [36, 36]
    assert candidate["daily_intensity_scores"][1] <= 11
    adjusted = candidate["itinerary"]["days"][1]["activities"]
    assert adjusted[0]["start"] == "08:30"
    assert adjusted[0]["end"] < "14:30"


def test_optimizer_does_not_hide_large_schedule_overrun():
    params = _params()
    params["candidates"][0]["days"][0]["activities"][0].update(
        {"start": "07:00", "end": "22:00"}
    )

    result = _run(params)

    assert result.code == "TRAVEL_OPTIMIZATION_FAILED"
    assert "DAILY_TIME_LIMIT_EXCEEDED" in result.data["rejected_candidates"][0]["reasons"]


def test_optimizer_can_keep_two_candidates_after_bounded_projection():
    params = _params()
    second = deepcopy(params["candidates"][0])
    second["candidate_id"] = "candidate-b"
    day = second["days"][1]
    day["activities"] = [
        {"start": "08:00", "end": "13:00", "place": "兵马俑"},
        {"start": "13:30", "end": "14:30", "place": "临潼午餐"},
        {"start": "16:30", "end": "18:00", "place": "市区自由活动"},
    ]
    day["route_segments"] = [
        {"from": "酒店", "to": "兵马俑", "duration": 90, "distance": 40, "mode": "公交"},
        {"from": "兵马俑", "to": "市区", "duration": 90, "distance": 40, "mode": "公交"},
    ]
    params["candidates"].append(second)

    result = _run(params)

    assert result.status == "success"
    assert len(result.data["feasible_candidates"]) == 2
    projected = next(
        item for item in result.data["feasible_candidates"] if item["candidate_id"] == "candidate-b"
    )
    assert "ACTIVITY_DURATION_TIGHTENED_FOR_FEASIBILITY" in projected["warnings"]
    assert projected["daily_intensity_scores"][1] <= 11


def test_optimizer_keeps_three_genuinely_different_short_trip_choices():
    params = _params()
    for candidate_id, replacements in (
        ("nature-route", ("苍山", "洱海", "喜洲古镇")),
        ("slow-route", ("大理古城慢游", "咖啡馆", "洱海日落")),
    ):
        candidate = deepcopy(params["candidates"][0])
        candidate["candidate_id"] = candidate_id
        activities = [
            activity
            for day in candidate["days"]
            for activity in day["activities"]
        ]
        for activity, place in zip(activities, replacements, strict=False):
            activity["place"] = place
        params["candidates"].append(candidate)

    result = _run(params)

    assert result.status == "success"
    assert len(result.data["feasible_candidates"]) == 3
    assert result.data["candidate_count_reason"] == "short_trip_high_choice_pressure"
    assert all(item["core_tradeoff"] for item in result.data["feasible_candidates"])


def test_optimizer_collapses_reordered_or_duplicate_choices_to_one():
    params = _params()
    duplicate = deepcopy(params["candidates"][0])
    duplicate["candidate_id"] = "same-places-different-id"
    params["candidates"].append(duplicate)

    result = _run(params)

    assert result.status == "success"
    assert len(result.data["feasible_candidates"]) == 1
    assert result.data["candidate_count_reason"] == "candidate_differences_too_small"


def test_travel_skill_error_cancel_and_output_limit_are_structured():
    malformed = _run({"request": {}, "candidates": []})
    assert malformed.code == "INVALID_SKILL_PARAMS"

    token = CancellationToken()
    token.cancel()
    cancelled = _run(_params(), token=token)
    assert cancelled.status == "cancelled"
    assert cancelled.code == "SKILL_CANCELLED"

    overflow = _run(_params(), executor=PythonSkillExecutor(max_stdout_bytes=32))
    assert overflow.code == "SKILL_STDOUT_LIMIT"


def _params() -> dict:
    plan = plan_payload()
    days = deepcopy(plan["days"])
    for day in days:
        for activity in day["activities"]:
            activity.pop("reason", None)
            activity.pop("evidence_ids", None)
            activity.pop("opening_hours", None)
            activity.pop("location", None)
        for segment in day["route_segments"]:
            segment.pop("source", None)
            segment.pop("evidence_ids", None)
            segment.pop("path", None)
            segment.pop("transit_legs", None)
            segment.pop("walking_distance", None)
            segment.pop("fare_cny", None)
    return {
        "request": request_payload(),
        "candidates": [
            {
                "candidate_id": "candidate-a",
                "days": days,
                "budget_items": deepcopy(plan["budget"]["items"]),
                "evidence_coverage": 0.9,
            }
        ],
    }


def _run(params: dict, *, token=None, executor=None):
    skill = SkillLoader(
        [("official", Path("skill_repo/skills").resolve())]
    ).get_skill("official/travel-planner")
    return (executor or PythonSkillExecutor()).run(
        SkillRunRequest(
            run_id="travel-run-1",
            qualified_name=skill.qualified_name,
            params=params,
            actor_context=ActorContext(
                actor_type="user",
                user_id="user-1",
                username="traveller",
                display_name="Traveller",
                role_keys=frozenset({"viewer"}),
                permission_keys=frozenset(),
                channel="web",
            ),
            session_id="session-1",
            turn_id="turn-1",
            cancellation_token=token,
        ),
        skill,
    )
