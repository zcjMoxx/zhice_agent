"""Pure-computation optimizer for already researched travel candidates."""

from __future__ import annotations

import argparse
import json
import math
import traceback
from copy import deepcopy
from datetime import date, timedelta
from typing import Any

_MAX_AUTOMATIC_ACTIVITY_TRIM_MINUTES = 150
_MIN_ACTIVITY_MINUTES = 45
_INTENSITY_HARD_LIMIT_MARGIN = 0.1


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--params", required=True)
    args = parser.parse_args()
    try:
        params = json.loads(args.params)
        _progress("正在校验旅行需求", 10)
        request, candidates, limits = _validate_params(params)
        _progress("正在校验候选行程", 35)
        evaluated = [_evaluate_candidate(request, candidate, limits) for candidate in candidates]
        accepted = [item for item in evaluated if not item["rejection_reasons"]]
        if not accepted:
            return _result(
                "error",
                "TRAVEL_OPTIMIZATION_FAILED",
                {
                    "selected_candidate": None,
                    "rejected_candidates": [_rejected(item) for item in evaluated],
                },
                "All travel candidates failed hard feasibility gates.",
            )
        _progress("正在比较预算、路线与每日强度", 70)
        accepted.sort(key=lambda item: (-item["score"], item["candidate_id"]))
        review_candidates, candidate_count_reason = _select_review_candidates(
            request, accepted
        )
        selected = review_candidates[0]
        _progress("候选方案校验完成", 100)
        return _result(
            "success",
            "OK",
            {
                "selected_candidate": selected["candidate"],
                "score": selected["score"],
                "budget": selected["budget"],
                "quality_gate": selected["quality_gate"],
                "feasible_candidates": [
                    _candidate_summary(
                        item,
                        recommended=item is selected,
                        cohort=review_candidates,
                    )
                    for item in review_candidates
                ],
                "candidate_count_reason": candidate_count_reason,
                "rejected_candidates": [_rejected(item) for item in evaluated if item["rejection_reasons"]],
            },
            "Travel candidate selected.",
        )
    except ValidationError as exc:
        return _result("error", exc.code, None, exc.message)
    except Exception:
        return _result(
            "error",
            "INTERNAL_ERROR",
            None,
            "Travel optimization failed safely.",
            traceback.format_exc()[:1500],
        )


class ValidationError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def _validate_params(params: Any) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, Any]]:
    if not isinstance(params, dict) or set(params) - {"request", "candidates", "limits"}:
        raise ValidationError("TRAVEL_PLAN_SCHEMA_INVALID", "Skill params must be a supported object.")
    request = params.get("request")
    candidates = params.get("candidates")
    limits = params.get("limits", {})
    if not isinstance(request, dict):
        raise ValidationError("TRAVEL_REQUEST_INCOMPLETE", "Travel request is required.")
    required = {"origin", "destinations", "start_date", "end_date", "duration_days", "travellers", "pace"}
    if any(key not in request for key in required):
        raise ValidationError("TRAVEL_REQUEST_INCOMPLETE", "Travel request is incomplete.")
    if not isinstance(request.get("destinations"), list) or not request["destinations"]:
        raise ValidationError("TRAVEL_REQUEST_INCOMPLETE", "Travel destinations are required.")
    if not isinstance(request.get("travellers"), list) or not request["travellers"]:
        raise ValidationError("TRAVEL_REQUEST_INCOMPLETE", "Traveller counts are required.")
    duration = _integer(request.get("duration_days"), 1, 60)
    start = _iso_date(request.get("start_date"))
    end = _iso_date(request.get("end_date"))
    if end < start or (end - start).days + 1 != duration:
        raise ValidationError("TRAVEL_REQUEST_INCOMPLETE", "Travel dates and duration are inconsistent.")
    budget = request.get("budget_total_cny")
    if budget is not None:
        _number(budget, 100, 10_000_000)
    if request.get("pace") not in {"relaxed", "balanced", "intensive"}:
        raise ValidationError("TRAVEL_PLAN_SCHEMA_INVALID", "Travel pace is invalid.")
    if not isinstance(candidates, list) or not 1 <= len(candidates) <= 20:
        raise ValidationError("TRAVEL_PLAN_SCHEMA_INVALID", "One to twenty candidates are required.")
    if not isinstance(limits, dict):
        raise ValidationError("TRAVEL_PLAN_SCHEMA_INVALID", "Optimization limits must be an object.")
    allowed_limits = {"max_daily_minutes", "max_daily_distance_km", "max_backtracks_per_day"}
    if set(limits) - allowed_limits:
        raise ValidationError("TRAVEL_PLAN_SCHEMA_INVALID", "Optimization limits contain unsupported fields.")
    normalized_limits = {
        "max_daily_minutes": _integer(
            limits.get("max_daily_minutes", _pace_minutes(request["pace"])), 120, 1200
        ),
        "max_daily_distance_km": _number(limits.get("max_daily_distance_km", 250), 1, 2000),
        "max_backtracks_per_day": _integer(limits.get("max_backtracks_per_day", 0), 0, 5),
    }
    return request, candidates, normalized_limits


def _evaluate_candidate(request: dict[str, Any], candidate: Any, limits: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(candidate, dict) or set(candidate) - {
        "candidate_id", "days", "budget_items", "evidence_coverage"
    }:
        raise ValidationError("TRAVEL_PLAN_SCHEMA_INVALID", "Candidate shape is invalid.")
    candidate = deepcopy(candidate)
    candidate_id = _required_text(candidate.get("candidate_id"), 100)
    days = candidate.get("days")
    if not isinstance(days, list) or len(days) != request["duration_days"]:
        raise ValidationError("TRAVEL_PLAN_SCHEMA_INVALID", "Candidate day count is invalid.")
    start_date = _iso_date(request["start_date"])
    rejections: list[str] = []
    warnings = _tighten_small_schedule_overruns(candidate, limits, request["pace"])
    total_route_minutes = 0.0
    total_route_distance = 0.0
    intensity_scores: list[float] = []
    previous_area = ""
    for index, day in enumerate(days):
        result = _evaluate_day(
            day,
            expected_date=(start_date + timedelta(days=index)).isoformat(),
            previous_area=previous_area,
            limits=limits,
            pace=request["pace"],
        )
        previous_area = result["area"]
        total_route_minutes += result["route_minutes"]
        total_route_distance += result["route_distance"]
        intensity_scores.append(result["intensity_score"])
        rejections.extend(result["rejections"])
        warnings.extend(result["warnings"])
    budget = _budget(candidate.get("budget_items"), days)
    hard_budget = request.get("budget_total_cny")
    if hard_budget is not None and budget["expected"] > float(hard_budget):
        if _tighten_flexible_budget(candidate, days, float(hard_budget)):
            budget = _budget(candidate.get("budget_items"), days)
            warnings.append("BUDGET_EXPECTED_TIGHTENED_TO_HARD_LIMIT")
    if hard_budget is not None and budget["expected"] > float(hard_budget):
        rejections.append("BUDGET_EXPECTED_EXCEEDS_HARD_LIMIT")
    elif hard_budget is not None and budget["upper"] > float(hard_budget):
        warnings.append("BUDGET_UPPER_EXCEEDS_HARD_LIMIT")
    evidence_coverage = _number(candidate.get("evidence_coverage", 0), 0, 1)
    if evidence_coverage < 0.5:
        warnings.append("EVIDENCE_COVERAGE_LOW")
    score = 100.0
    score -= total_route_minutes / 120
    score -= total_route_distance / 250
    score -= sum(max(0, item - _pace_intensity(request["pace"])) for item in intensity_scores) * 2
    score += evidence_coverage * 15
    if hard_budget:
        score -= min(20, budget["expected"] / float(hard_budget) * 10)
    score -= len(warnings) * 1.5
    return {
        "candidate_id": candidate_id,
        "candidate": candidate,
        "budget": budget,
        "score": round(score, 3),
        "rejection_reasons": sorted(set(rejections)),
        "quality_gate": {
            "passed": not rejections,
            "warnings": sorted(set(warnings)),
            "route_minutes": round(total_route_minutes, 1),
            "route_distance_km": round(total_route_distance, 2),
            "daily_intensity_scores": [round(min(10.0, max(0.0, item)), 2) for item in intensity_scores],
            "evidence_coverage": evidence_coverage,
            "checks": {
                "budget": "passed" if "BUDGET_EXPECTED_EXCEEDS_HARD_LIMIT" not in rejections else "failed",
                "time": "passed" if not any("TIME" in item or "OVERLAP" in item for item in rejections) else "failed",
                "route": "passed" if not any("ROUTE" in item or "CROSS_CITY" in item for item in rejections) else "failed",
                "opening_hours": "passed" if "OPENING_HOURS_CONFLICT" not in rejections else "failed",
                "backtrack": "passed" if "ROUTE_BACKTRACK" not in rejections else "failed",
                "intensity": "passed" if not any("INTENSITY" in item for item in rejections) else "failed",
            },
        },
    }


def _tighten_small_schedule_overruns(
    candidate: dict[str, Any],
    limits: dict[str, Any],
    pace: str,
) -> list[str]:
    """Trim small activity-window overruns, then leave all hard gates intact.

    Candidate activity durations are planning choices, unlike externally sourced route
    durations and distances. A bounded deterministic projection avoids a second full LLM
    rewrite when the skeleton only misses the time/intensity ceiling by a few minutes.
    Large or structurally impossible overruns continue to be rejected by ``_evaluate_day``.
    """

    adjusted = False
    days = candidate.get("days")
    if not isinstance(days, list):
        return []
    for day in days:
        if not isinstance(day, dict):
            continue
        activities = day.get("activities")
        segments = day.get("route_segments", [])
        if not isinstance(activities, list) or not activities or not isinstance(segments, list):
            continue

        windows: list[tuple[int, int, dict[str, Any]]] = []
        for activity in activities:
            if not isinstance(activity, dict):
                windows = []
                break
            start = _minutes(activity.get("start"))
            end = _minutes(activity.get("end"))
            windows.append((start, end, activity))
        if not windows:
            continue

        route_minutes = 0.0
        route_distance = 0.0
        valid_segments = True
        for segment in segments:
            if not isinstance(segment, dict):
                valid_segments = False
                break
            route_minutes += _number(segment.get("duration"), 0, 1440)
            route_distance += _number(segment.get("distance"), 0, 20_000)
        if not valid_segments:
            continue

        activity_minutes = sum(max(0, end - start) for start, end, _ in windows)
        time_allowance = math.floor(float(limits["max_daily_minutes"]) - route_minutes)
        intensity_allowance = math.floor(
            (
                _pace_intensity(pace)
                + 2
                - _INTENSITY_HARD_LIMIT_MARGIN
                - len(windows) * 0.35
                - route_distance / 80
            )
            * 60
            - route_minutes
        )
        allowed_activity_minutes = min(time_allowance, intensity_allowance)
        required_trim = max(0, activity_minutes - allowed_activity_minutes)
        if not required_trim:
            continue
        if required_trim > _MAX_AUTOMATIC_ACTIVITY_TRIM_MINUTES:
            continue

        capacities = [max(0, end - start - _MIN_ACTIVITY_MINUTES) for start, end, _ in windows]
        if sum(capacities) < required_trim:
            continue
        remaining = required_trim
        for index in sorted(range(len(windows)), key=lambda item: capacities[item], reverse=True):
            reduction = min(remaining, capacities[index])
            if reduction <= 0:
                continue
            start, end, activity = windows[index]
            activity["end"] = _clock(end - reduction)
            remaining -= reduction
            if remaining <= 0:
                break
        adjusted = adjusted or remaining <= 0
    return ["ACTIVITY_DURATION_TIGHTENED_FOR_FEASIBILITY"] if adjusted else []


def _evaluate_day(
    day: Any,
    *,
    expected_date: str,
    previous_area: str,
    limits: dict[str, Any],
    pace: str,
) -> dict[str, Any]:
    if not isinstance(day, dict):
        raise ValidationError("TRAVEL_PLAN_SCHEMA_INVALID", "Candidate day must be an object.")
    if day.get("date") != expected_date:
        raise ValidationError("TRAVEL_PLAN_SCHEMA_INVALID", "Candidate dates must be complete and ordered.")
    area = _required_text(day.get("city_or_area"), 120)
    activities = day.get("activities")
    segments = day.get("route_segments", [])
    if not isinstance(activities, list) or not activities or len(activities) > 24:
        raise ValidationError("TRAVEL_PLAN_SCHEMA_INVALID", "Candidate activities are invalid.")
    if not isinstance(segments, list) or len(segments) > 32:
        raise ValidationError("TRAVEL_PLAN_SCHEMA_INVALID", "Candidate route segments are invalid.")
    rejections: list[str] = []
    warnings: list[str] = []
    activity_minutes = 0
    previous_end = -1
    places: list[str] = []
    for activity in activities:
        if not isinstance(activity, dict):
            raise ValidationError("TRAVEL_PLAN_SCHEMA_INVALID", "Activity must be an object.")
        start = _minutes(activity.get("start"))
        end = _minutes(activity.get("end"))
        if end <= start:
            rejections.append("ACTIVITY_TIME_INVALID")
        if start < previous_end:
            rejections.append("ACTIVITY_OVERLAP")
        previous_end = max(previous_end, end)
        activity_minutes += max(0, end - start)
        place = _required_text(activity.get("place"), 200)
        places.append(_place_key(place))
        opening_windows = activity.get("opening_windows", [])
        if opening_windows and not _within_opening_window(start, end, opening_windows):
            rejections.append("OPENING_HOURS_CONFLICT")
    route_minutes = 0.0
    route_distance = 0.0
    for segment in segments:
        if not isinstance(segment, dict):
            raise ValidationError("TRAVEL_PLAN_SCHEMA_INVALID", "Route segment must be an object.")
        route_minutes += _number(segment.get("duration"), 0, 1440)
        route_distance += _number(segment.get("distance"), 0, 20_000)
        _required_text(segment.get("from"), 200)
        _required_text(segment.get("to"), 200)
    total_minutes = activity_minutes + route_minutes
    if total_minutes > limits["max_daily_minutes"]:
        rejections.append("DAILY_TIME_LIMIT_EXCEEDED")
    if route_distance > limits["max_daily_distance_km"]:
        warnings.append("DAILY_ROUTE_DISTANCE_HIGH")
    if previous_area and previous_area != area and not segments:
        rejections.append("CROSS_CITY_ROUTE_MISSING")
    if _backtracks(places) > limits["max_backtracks_per_day"]:
        rejections.append("ROUTE_BACKTRACK")
    intensity = total_minutes / 60 + len(activities) * 0.35 + route_distance / 80
    if intensity > _pace_intensity(pace) + 2:
        rejections.append("DAILY_INTENSITY_EXCEEDED")
    elif intensity > _pace_intensity(pace):
        warnings.append("DAILY_INTENSITY_HIGH")
    return {
        "area": area,
        "route_minutes": route_minutes,
        "route_distance": route_distance,
        "intensity_score": intensity,
        "rejections": rejections,
        "warnings": warnings,
    }


def _budget(items: Any, days: list[dict[str, Any]]) -> dict[str, float]:
    if not isinstance(items, list) or len(items) > 50:
        raise ValidationError("TRAVEL_PLAN_SCHEMA_INVALID", "Budget items are invalid.")
    lower = expected = upper = 0.0
    for item in items:
        if not isinstance(item, dict):
            raise ValidationError("TRAVEL_PLAN_SCHEMA_INVALID", "Budget item must be an object.")
        _required_text(item.get("name"), 100)
        item_lower = _number(item.get("lower"), 0, 10_000_000)
        item_expected = _number(item.get("expected"), 0, 10_000_000)
        item_upper = _number(item.get("upper"), 0, 10_000_000)
        if not item_lower <= item_expected <= item_upper:
            raise ValidationError("TRAVEL_PLAN_SCHEMA_INVALID", "Budget range is invalid.")
        lower += item_lower
        expected += item_expected
        upper += item_upper
    daily_total = sum(_number(day.get("daily_budget", 0), 0, 10_000_000) for day in days)
    if not items:
        lower = expected = upper = daily_total
    elif daily_total and expected < daily_total:
        expected = daily_total
        upper = max(upper, daily_total)
    return {"lower": round(lower, 2), "expected": round(expected, 2), "upper": round(upper, 2)}


def _tighten_flexible_budget(
    candidate: dict[str, Any],
    days: list[dict[str, Any]],
    hard_budget: float,
) -> bool:
    """Select lower in-range flexible estimates without rewriting fixed-price facts."""

    items = candidate.get("budget_items")
    if not isinstance(items, list) or sum(
        _number(day.get("daily_budget", 0), 0, 10_000_000) for day in days
    ) > hard_budget:
        return False
    current = _budget(items, days)
    excess = current["expected"] - hard_budget
    if excess <= 0:
        return True
    markers = (
        "餐饮",
        "meal",
        "food",
        "市内交通",
        "local transport",
        "misc",
        "其他",
    )
    flexible = [
        item
        for item in items
        if isinstance(item, dict)
        and any(marker in str(item.get("name") or "").casefold() for marker in markers)
    ]
    capacity = sum(
        max(0.0, float(item.get("expected", 0)) - float(item.get("lower", 0)))
        for item in flexible
    )
    if capacity + 1e-9 < excess:
        return False
    remaining = excess
    for item in flexible:
        expected = float(item["expected"])
        lower = float(item["lower"])
        reduction = min(remaining, max(0.0, expected - lower))
        item["expected"] = round(expected - reduction, 2)
        remaining -= reduction
        if remaining <= 1e-9:
            break
    return _budget(items, days)["expected"] <= hard_budget + 1e-9


def _within_opening_window(start: int, end: int, windows: Any) -> bool:
    if not isinstance(windows, list) or len(windows) > 8:
        raise ValidationError("TRAVEL_PLAN_SCHEMA_INVALID", "Opening windows are invalid.")
    for window in windows:
        if not isinstance(window, dict) or set(window) != {"start", "end"}:
            raise ValidationError("TRAVEL_PLAN_SCHEMA_INVALID", "Opening window is invalid.")
        if _minutes(window["start"]) <= start and end <= _minutes(window["end"]):
            return True
    return False


def _backtracks(places: list[str]) -> int:
    return sum(1 for index in range(2, len(places)) if places[index] == places[index - 2] != places[index - 1])


def _rejected(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "candidate_id": item["candidate_id"],
        "reasons": item["rejection_reasons"],
        "score": item["score"],
    }


def _select_review_candidates(
    request: dict[str, Any], accepted: list[dict[str, Any]]
) -> tuple[list[dict[str, Any]], str]:
    """Keep only genuinely different choices and shrink choice count as time expands."""

    distinct: list[dict[str, Any]] = []
    for item in accepted:
        if not distinct or all(_meaningfully_different(item, kept) for kept in distinct):
            distinct.append(item)
    duration = int(request["duration_days"])
    union = set().union(*(_candidate_places(item) for item in distinct)) if distinct else set()
    best_coverage = max(
        (len(_candidate_places(item)) / len(union) for item in distinct),
        default=1.0,
    )
    if duration <= 3:
        target, reason = 3, "short_trip_high_choice_pressure"
    elif duration <= 5:
        target, reason = 2, "medium_trip_has_meaningful_tradeoffs"
    elif best_coverage >= 0.85:
        target, reason = 1, "long_trip_covers_core_places"
    else:
        target, reason = 2, "long_trip_still_has_unresolved_tradeoffs"
    selected = distinct[:target]
    if len(selected) == 1 and len(accepted) > 1:
        reason = "candidate_differences_too_small" if duration <= 5 else reason
    return selected, reason


def _meaningfully_different(left: dict[str, Any], right: dict[str, Any]) -> bool:
    left_places = _candidate_places(left)
    right_places = _candidate_places(right)
    union = left_places | right_places
    overlap = len(left_places & right_places) / len(union) if union else 1.0
    left_gate, right_gate = left["quality_gate"], right["quality_gate"]
    route_delta = abs(left_gate["route_minutes"] - right_gate["route_minutes"])
    left_budget, right_budget = left["budget"]["expected"], right["budget"]["expected"]
    budget_delta = abs(left_budget - right_budget) / max(left_budget, right_budget, 1)
    left_intensity = sum(left_gate["daily_intensity_scores"]) / max(
        len(left_gate["daily_intensity_scores"]), 1
    )
    right_intensity = sum(right_gate["daily_intensity_scores"]) / max(
        len(right_gate["daily_intensity_scores"]), 1
    )
    return (
        overlap < 0.75
        or route_delta >= 60
        or budget_delta >= 0.1
        or abs(left_intensity - right_intensity) >= 1.0
    )


def _candidate_places(item: dict[str, Any]) -> set[str]:
    return {
        _place_key(activity["place"])
        for day in item["candidate"]["days"]
        for activity in day["activities"]
    }


def _candidate_summary(
    item: dict[str, Any],
    *,
    recommended: bool,
    cohort: list[dict[str, Any]],
) -> dict[str, Any]:
    candidate = item["candidate"]
    days = []
    for day in candidate["days"]:
        days.append(
            {
                "date": day["date"],
                "city_or_area": day["city_or_area"],
                "places": [activity["place"] for activity in day["activities"][:6]],
            }
        )
    own_places = _candidate_places(item)
    other_places = set().union(
        *(_candidate_places(other) for other in cohort if other is not item)
    ) if len(cohort) > 1 else set()
    unique = sorted(own_places - other_places)[:4]
    omitted = sorted(other_places - own_places)[:4]
    strategy_label = _strategy_label(item, unique)
    tradeoff_parts = []
    if unique:
        tradeoff_parts.append("重点保留" + "、".join(unique))
    if omitted:
        tradeoff_parts.append("减少" + "、".join(omitted))
    if not tradeoff_parts:
        tradeoff_parts.append(
            f"通勤约{round(item['quality_gate']['route_minutes'])}分钟，"
            f"预算约¥{round(item['budget']['expected'])}"
        )
    return {
        "candidate_id": item["candidate_id"],
        "recommended": recommended,
        "score": item["score"],
        "days": days,
        "budget": item["budget"],
        "route_minutes": item["quality_gate"]["route_minutes"],
        "route_distance_km": item["quality_gate"]["route_distance_km"],
        "daily_intensity_scores": item["quality_gate"]["daily_intensity_scores"],
        "evidence_coverage": item["quality_gate"]["evidence_coverage"],
        "warnings": item["quality_gate"]["warnings"],
        "strategy_label": strategy_label,
        "core_tradeoff": "；".join(tradeoff_parts),
        "unique_highlights": unique,
        "omitted_highlights": omitted,
        "itinerary": _candidate_itinerary(candidate),
        "budget_items": [
            {
                "name": budget_item["name"],
                "lower": budget_item["lower"],
                "expected": budget_item["expected"],
                "upper": budget_item["upper"],
            }
            for budget_item in candidate["budget_items"]
        ],
    }


def _strategy_label(item: dict[str, Any], unique: list[str]) -> str:
    identity = str(item["candidate_id"]).casefold()
    if any(marker in identity for marker in ("slow", "relaxed", "comfort")):
        return "舒适慢游"
    if any(marker in identity for marker in ("nature", "cangshan", "erhai", "scenic")):
        return "自然风光优先"
    if any(marker in identity for marker in ("economy", "value", "budget")):
        return "经济实惠"
    if any(marker in identity for marker in ("classic", "core", "coverage")):
        return "经典覆盖"
    if unique:
        return f"{unique[0]}重点"
    return "均衡精选"


def _candidate_itinerary(candidate: dict[str, Any]) -> dict[str, Any]:
    days = []
    for day in candidate["days"]:
        days.append(
            {
                "date": day["date"],
                "city_or_area": day["city_or_area"],
                "activities": [
                    {
                        "start": activity["start"],
                        "end": activity["end"],
                        "place": activity["place"],
                    }
                    for activity in day["activities"]
                ],
                "route_segments": [
                    {
                        "from": segment["from"],
                        "to": segment["to"],
                        "duration": segment["duration"],
                        "distance": segment["distance"],
                        "mode": segment.get("mode", ""),
                    }
                    for segment in day.get("route_segments", [])
                ],
                "daily_budget": day.get("daily_budget", 0),
            }
        )
    return {"days": days}


def _progress(message: str, percent: int) -> None:
    print(json.dumps({"type": "progress", "message": message, "percent": percent}, ensure_ascii=False), flush=True)


def _result(status: str, code: str, data: Any, message: str, error_stack: str = "") -> int:
    print(
        json.dumps(
            {
                "type": "result",
                "status": status,
                "code": code,
                "data": data,
                "message": message,
                "error_stack": error_stack,
            },
            ensure_ascii=False,
            separators=(",", ":"),
        ),
        flush=True,
    )
    return 0 if status == "success" else 1


def _pace_minutes(pace: str) -> int:
    return {"relaxed": 480, "balanced": 600, "intensive": 720}[pace]


def _pace_intensity(pace: str) -> float:
    return {"relaxed": 7.0, "balanced": 9.0, "intensive": 11.0}[pace]


def _minutes(value: Any) -> int:
    if not isinstance(value, str) or len(value) != 5 or value[2] != ":":
        raise ValidationError("TRAVEL_PLAN_SCHEMA_INVALID", "Time must use HH:MM.")
    try:
        hour, minute = (int(part) for part in value.split(":"))
    except ValueError as exc:
        raise ValidationError("TRAVEL_PLAN_SCHEMA_INVALID", "Time must use HH:MM.") from exc
    if not 0 <= hour <= 23 or not 0 <= minute <= 59:
        raise ValidationError("TRAVEL_PLAN_SCHEMA_INVALID", "Time must use HH:MM.")
    return hour * 60 + minute


def _clock(value: int) -> str:
    """Format validated same-day minutes as HH:MM."""

    return f"{value // 60:02d}:{value % 60:02d}"


def _iso_date(value: Any) -> date:
    if not isinstance(value, str):
        raise ValidationError("TRAVEL_REQUEST_INCOMPLETE", "Travel date is required.")
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise ValidationError("TRAVEL_REQUEST_INCOMPLETE", "Travel date is invalid.") from exc


def _required_text(value: Any, maximum: int) -> str:
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > maximum:
        raise ValidationError("TRAVEL_PLAN_SCHEMA_INVALID", "Required text is invalid.")
    return " ".join(value.split())


def _integer(value: Any, minimum: int, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise ValidationError("TRAVEL_PLAN_SCHEMA_INVALID", "Integer is outside the supported range.")
    return value


def _number(value: Any, minimum: float, maximum: float) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ValidationError("TRAVEL_PLAN_SCHEMA_INVALID", "Numeric value is invalid.")
    result = float(value)
    if not math.isfinite(result) or not minimum <= result <= maximum:
        raise ValidationError("TRAVEL_PLAN_SCHEMA_INVALID", "Numeric value is outside the supported range.")
    return result


def _place_key(value: str) -> str:
    return "".join(value.casefold().split())


if __name__ == "__main__":
    raise SystemExit(main())
