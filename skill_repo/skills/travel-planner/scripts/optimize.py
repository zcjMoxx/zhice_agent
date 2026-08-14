"""Pure-computation optimizer for already researched travel candidates."""

from __future__ import annotations

import argparse
import json
import math
import traceback
from datetime import date, timedelta
from typing import Any


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
        selected = accepted[0]
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
                    _candidate_summary(item, recommended=item is selected) for item in accepted
                ],
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
    candidate_id = _required_text(candidate.get("candidate_id"), 100)
    days = candidate.get("days")
    if not isinstance(days, list) or len(days) != request["duration_days"]:
        raise ValidationError("TRAVEL_PLAN_SCHEMA_INVALID", "Candidate day count is invalid.")
    start_date = _iso_date(request["start_date"])
    rejections: list[str] = []
    warnings: list[str] = []
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
    if hard_budget is not None and budget["lower"] > float(hard_budget):
        rejections.append("BUDGET_LOWER_EXCEEDS_HARD_LIMIT")
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
            "daily_intensity_scores": [round(item, 2) for item in intensity_scores],
            "evidence_coverage": evidence_coverage,
            "checks": {
                "budget": "passed" if "BUDGET_LOWER_EXCEEDS_HARD_LIMIT" not in rejections else "failed",
                "time": "passed" if not any("TIME" in item or "OVERLAP" in item for item in rejections) else "failed",
                "route": "passed" if not any("ROUTE" in item or "CROSS_CITY" in item for item in rejections) else "failed",
                "opening_hours": "passed" if "OPENING_HOURS_CONFLICT" not in rejections else "failed",
                "backtrack": "passed" if "ROUTE_BACKTRACK" not in rejections else "failed",
                "intensity": "passed" if not any("INTENSITY" in item for item in rejections) else "failed",
            },
        },
    }


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


def _candidate_summary(item: dict[str, Any], *, recommended: bool) -> dict[str, Any]:
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
    }


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
