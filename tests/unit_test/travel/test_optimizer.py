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
    assert result.data["quality_gate"]["passed"] is True
    assert result.data["budget"]["lower"] <= result.data["budget"]["expected"] <= result.data["budget"]["upper"]


@pytest.mark.parametrize(
    ("mutate", "reason"),
    [
        (lambda value: value["candidates"][0]["budget_items"].__setitem__(0, {"name": "交通", "lower": 6000, "expected": 6200, "upper": 6500}), "BUDGET_LOWER_EXCEEDS_HARD_LIMIT"),
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
