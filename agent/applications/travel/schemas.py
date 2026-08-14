"""Strict, provider-neutral data contracts for the travel application."""

from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any, Iterable
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

TRAVEL_REQUEST_SCHEMA_VERSION = "1"
TRAVEL_PLAN_SCHEMA_VERSION = "1"

EVIDENCE_SOURCE_TYPES = frozenset(
    {
        "official_api",
        "live_query",
        "official_page",
        "web_article",
        "social_post",
        "model_estimate",
    }
)
FRESHNESS_TYPES = frozenset({"live", "snapshot", "historical", "estimate", "unknown"})
PLANNING_MODES = frozenset({"quick", "deep"})
PACE_TYPES = frozenset({"relaxed", "balanced", "intensive"})

MAX_PLAN_BYTES = 512 * 1024
MAX_EVIDENCE_ITEMS = 40
MAX_SOURCE_URLS = 80
MAX_EXCERPT_CHARS = 1200
MAX_FACT_CHARS = 500
MAX_FACTS_PER_EVIDENCE = 20
MAX_DAY_COUNT = 60
MAX_ACTIVITIES_PER_DAY = 24
MAX_ROUTE_SEGMENTS_PER_DAY = 32

_HASH_RE = re.compile(r"^[0-9a-f]{64}$")
_TIME_RE = re.compile(r"^(?:[01]\d|2[0-3]):[0-5]\d$")
_SENSITIVE_KEY_PARTS = (
    "api_key",
    "authorization",
    "cookie",
    "credential",
    "password",
    "secret",
    "token",
)


class TravelValidationError(ValueError):
    """One safe, stable validation failure at the travel domain boundary."""

    def __init__(self, code: str, message: str, *, field: str = ""):
        super().__init__(message)
        self.code = str(code)
        self.message = str(message)
        self.field = str(field)


@dataclass(frozen=True)
class TravellerV1:
    """One bounded traveller category and count."""

    type: str
    count: int

    @classmethod
    def from_dict(cls, value: object, *, field: str) -> TravellerV1:
        raw = _object(value, field)
        _reject_unknown(raw, {"type", "count"}, field)
        traveller_type = _text(raw.get("type"), f"{field}.type", max_chars=40)
        count = _integer(raw.get("count"), f"{field}.count", minimum=1, maximum=50)
        return cls(type=traveller_type, count=count)

    def to_dict(self) -> dict[str, Any]:
        return {"type": self.type, "count": self.count}


@dataclass(frozen=True)
class TravelRequestV1:
    """Normalized travel requirements used by planning and persistence."""

    schema_version: str
    origin: str
    destinations: tuple[str, ...]
    start_date: str
    end_date: str
    date_flexibility: str
    duration_days: int
    travellers: tuple[TravellerV1, ...]
    budget_total_cny: float | None
    transport_preferences: tuple[str, ...]
    stay_preferences: tuple[str, ...]
    interest_tags: tuple[str, ...]
    pace: str
    hard_constraints: tuple[str, ...]
    soft_preferences: tuple[str, ...]
    planning_mode: str

    @classmethod
    def from_dict(cls, value: object) -> TravelRequestV1:
        raw = _object(value, "request")
        allowed = {
            "schema_version",
            "origin",
            "destinations",
            "start_date",
            "end_date",
            "date_flexibility",
            "duration_days",
            "travellers",
            "budget_total_cny",
            "transport_preferences",
            "stay_preferences",
            "interest_tags",
            "pace",
            "hard_constraints",
            "soft_preferences",
            "planning_mode",
        }
        _reject_unknown(raw, allowed, "request")
        schema_version = str(raw.get("schema_version") or TRAVEL_REQUEST_SCHEMA_VERSION)
        if schema_version != TRAVEL_REQUEST_SCHEMA_VERSION:
            _invalid("request.schema_version", "Unsupported TravelRequestV1 schema version.")
        origin = _text(raw.get("origin"), "request.origin", max_chars=120)
        destinations = _text_list(
            raw.get("destinations"),
            "request.destinations",
            minimum=1,
            maximum=8,
            item_chars=120,
        )
        start = _date(raw.get("start_date"), "request.start_date")
        end = _date(raw.get("end_date"), "request.end_date")
        if end < start:
            _invalid("request.end_date", "Travel end date must not be before start date.")
        duration_days = _integer(
            raw.get("duration_days"), "request.duration_days", minimum=1, maximum=MAX_DAY_COUNT
        )
        calendar_days = (end - start).days + 1
        if duration_days != calendar_days:
            _invalid(
                "request.duration_days",
                "Travel duration must match the inclusive start and end dates.",
            )
        raw_travellers = raw.get("travellers")
        if not isinstance(raw_travellers, list) or not 1 <= len(raw_travellers) <= 10:
            _invalid("request.travellers", "Travel request must include traveller counts.")
        travellers = tuple(
            TravellerV1.from_dict(item, field=f"request.travellers[{index}]")
            for index, item in enumerate(raw_travellers)
        )
        if sum(item.count for item in travellers) > 50:
            _invalid("request.travellers", "Traveller count exceeds the supported limit.")
        budget_raw = raw.get("budget_total_cny")
        budget = None if budget_raw is None else _number(
            budget_raw,
            "request.budget_total_cny",
            minimum=100,
            maximum=10_000_000,
        )
        pace = str(raw.get("pace") or "balanced").strip()
        if pace not in PACE_TYPES:
            _invalid("request.pace", "Travel pace is invalid.")
        planning_mode = str(raw.get("planning_mode") or "quick").strip()
        if planning_mode not in PLANNING_MODES:
            _invalid("request.planning_mode", "Planning mode must be quick or deep.")
        return cls(
            schema_version=schema_version,
            origin=origin,
            destinations=destinations,
            start_date=start.isoformat(),
            end_date=end.isoformat(),
            date_flexibility=_optional_text(
                raw.get("date_flexibility"), max_chars=300, field="request.date_flexibility"
            ),
            duration_days=duration_days,
            travellers=travellers,
            budget_total_cny=budget,
            transport_preferences=_text_list(
                raw.get("transport_preferences", []),
                "request.transport_preferences",
                minimum=0,
                maximum=12,
                item_chars=100,
            ),
            stay_preferences=_text_list(
                raw.get("stay_preferences", []),
                "request.stay_preferences",
                minimum=0,
                maximum=12,
                item_chars=160,
            ),
            interest_tags=_text_list(
                raw.get("interest_tags", []),
                "request.interest_tags",
                minimum=0,
                maximum=20,
                item_chars=80,
            ),
            pace=pace,
            hard_constraints=_text_list(
                raw.get("hard_constraints", []),
                "request.hard_constraints",
                minimum=0,
                maximum=20,
                item_chars=300,
            ),
            soft_preferences=_text_list(
                raw.get("soft_preferences", []),
                "request.soft_preferences",
                minimum=0,
                maximum=30,
                item_chars=300,
            ),
            planning_mode=planning_mode,
        )

    @property
    def traveller_count(self) -> int:
        return sum(item.count for item in self.travellers)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "origin": self.origin,
            "destinations": list(self.destinations),
            "start_date": self.start_date,
            "end_date": self.end_date,
            "date_flexibility": self.date_flexibility,
            "duration_days": self.duration_days,
            "travellers": [item.to_dict() for item in self.travellers],
            "budget_total_cny": self.budget_total_cny,
            "transport_preferences": list(self.transport_preferences),
            "stay_preferences": list(self.stay_preferences),
            "interest_tags": list(self.interest_tags),
            "pace": self.pace,
            "hard_constraints": list(self.hard_constraints),
            "soft_preferences": list(self.soft_preferences),
            "planning_mode": self.planning_mode,
        }


@dataclass(frozen=True)
class EvidenceItemV1:
    """One short, attributable evidence record, never a full republished page."""

    evidence_id: str
    source_type: str
    provider: str
    title: str
    source_url: str
    published_at: str
    retrieved_at: str
    data_as_of: str
    excerpt: str
    facts: tuple[str, ...]
    confidence: float
    freshness: str
    content_hash: str

    @classmethod
    def from_dict(cls, value: object, *, index: int = 0) -> EvidenceItemV1:
        field = f"evidence[{index}]"
        raw = _object(value, field)
        allowed = {
            "evidence_id",
            "source_type",
            "provider",
            "title",
            "source_url",
            "published_at",
            "retrieved_at",
            "data_as_of",
            "excerpt",
            "facts",
            "confidence",
            "freshness",
            "content_hash",
        }
        _reject_unknown(raw, allowed, field)
        evidence_id = _text(raw.get("evidence_id"), f"{field}.evidence_id", max_chars=100)
        source_type = str(raw.get("source_type") or "").strip()
        if source_type not in EVIDENCE_SOURCE_TYPES:
            _invalid(f"{field}.source_type", "Evidence source type is invalid.")
        freshness = str(raw.get("freshness") or "").strip()
        if freshness not in FRESHNESS_TYPES:
            _invalid(f"{field}.freshness", "Evidence freshness label is invalid.")
        _validate_source_freshness(source_type, freshness, f"{field}.freshness")
        source_url = _source_url(
            raw.get("source_url"),
            f"{field}.source_url",
            required=source_type != "model_estimate",
        )
        excerpt = _optional_text(
            raw.get("excerpt"), max_chars=MAX_EXCERPT_CHARS, field=f"{field}.excerpt", truncate=True
        )
        facts = _text_list(
            raw.get("facts", []),
            f"{field}.facts",
            minimum=0,
            maximum=MAX_FACTS_PER_EVIDENCE,
            item_chars=MAX_FACT_CHARS,
            truncate=True,
        )
        retrieved_at = _timestamp(raw.get("retrieved_at"), f"{field}.retrieved_at", required=True)
        published_at = _timestamp(raw.get("published_at"), f"{field}.published_at")
        data_as_of = _timestamp(raw.get("data_as_of"), f"{field}.data_as_of")
        confidence = _number(raw.get("confidence", 0.5), f"{field}.confidence", minimum=0, maximum=1)
        title = _text(raw.get("title"), f"{field}.title", max_chars=300)
        provider = _text(raw.get("provider"), f"{field}.provider", max_chars=100)
        calculated = evidence_content_hash(
            provider=provider,
            title=title,
            source_url=source_url,
            excerpt=excerpt,
            facts=facts,
        )
        supplied_hash = str(raw.get("content_hash") or "").strip().lower()
        if supplied_hash and not _HASH_RE.fullmatch(supplied_hash):
            _invalid(f"{field}.content_hash", "Evidence content hash must be SHA-256 hex.")
        return cls(
            evidence_id=evidence_id,
            source_type=source_type,
            provider=provider,
            title=title,
            source_url=source_url,
            published_at=published_at,
            retrieved_at=retrieved_at,
            data_as_of=data_as_of,
            excerpt=excerpt,
            facts=facts,
            confidence=confidence,
            freshness=freshness,
            content_hash=supplied_hash or calculated,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "evidence_id": self.evidence_id,
            "source_type": self.source_type,
            "provider": self.provider,
            "title": self.title,
            "source_url": self.source_url,
            "published_at": self.published_at,
            "retrieved_at": self.retrieved_at,
            "data_as_of": self.data_as_of,
            "excerpt": self.excerpt,
            "facts": list(self.facts),
            "confidence": self.confidence,
            "freshness": self.freshness,
            "content_hash": self.content_hash,
        }


@dataclass(frozen=True)
class TravelPlanV1:
    """Validated final travel plan and its normalized JSON projection."""

    data: dict[str, Any]

    @classmethod
    def from_dict(
        cls,
        value: object,
        *,
        max_evidence_items: int = MAX_EVIDENCE_ITEMS,
        max_source_urls: int = MAX_SOURCE_URLS,
        max_plan_bytes: int = MAX_PLAN_BYTES,
    ) -> TravelPlanV1:
        raw = _object(value, "plan")
        allowed = {
            "schema_version",
            "plan_id",
            "owner_user_id",
            "request",
            "assumptions",
            "freshness_summary",
            "transport_options",
            "stay_recommendations",
            "days",
            "budget",
            "weather_summary",
            "fallbacks",
            "avoidance_tips",
            "evidence",
            "unknowns",
            "generated_at",
        }
        _reject_unknown(raw, allowed, "plan")
        _reject_sensitive_keys(raw)
        schema_version = str(raw.get("schema_version") or TRAVEL_PLAN_SCHEMA_VERSION)
        if schema_version != TRAVEL_PLAN_SCHEMA_VERSION:
            _invalid("plan.schema_version", "Unsupported TravelPlanV1 schema version.")
        request = TravelRequestV1.from_dict(raw.get("request"))
        evidence_raw = raw.get("evidence")
        if not isinstance(evidence_raw, list) or len(evidence_raw) > max_evidence_items:
            _invalid("plan.evidence", "Travel plan evidence count exceeds the allowed limit.")
        parsed_evidence = [
            EvidenceItemV1.from_dict(item, index=index)
            for index, item in enumerate(evidence_raw)
        ]
        evidence, aliases = deduplicate_evidence(parsed_evidence)
        if len({item.source_url for item in evidence if item.source_url}) > max_source_urls:
            _invalid("plan.evidence", "Travel plan has too many source URLs.")
        evidence_ids = {item.evidence_id for item in evidence}
        days = _normalize_days(raw.get("days"), request, evidence_ids, aliases)
        budget = _normalize_budget(raw.get("budget"), request)
        generated_at = _timestamp(
            raw.get("generated_at") or _utc_now(), "plan.generated_at", required=True
        )
        normalized = {
            "schema_version": schema_version,
            "plan_id": _optional_text(
                raw.get("plan_id"), max_chars=100, field="plan.plan_id"
            ),
            "owner_user_id": _optional_text(
                raw.get("owner_user_id"), max_chars=100, field="plan.owner_user_id"
            ),
            "request": request.to_dict(),
            "assumptions": list(
                _text_list(
                    raw.get("assumptions", []),
                    "plan.assumptions",
                    minimum=0,
                    maximum=40,
                    item_chars=500,
                )
            ),
            "freshness_summary": _bounded_json_copy(
                raw.get("freshness_summary", {}), "plan.freshness_summary", max_bytes=16_000
            ),
            "transport_options": _object_list(
                raw.get("transport_options"), "plan.transport_options", maximum=20
            ),
            "stay_recommendations": _object_list(
                raw.get("stay_recommendations"), "plan.stay_recommendations", maximum=20
            ),
            "days": days,
            "budget": budget,
            "weather_summary": _object_list(
                raw.get("weather_summary"), "plan.weather_summary", maximum=MAX_DAY_COUNT
            ),
            "fallbacks": list(
                _text_list(
                    raw.get("fallbacks", []),
                    "plan.fallbacks",
                    minimum=0,
                    maximum=40,
                    item_chars=500,
                )
            ),
            "avoidance_tips": list(
                _text_list(
                    raw.get("avoidance_tips", []),
                    "plan.avoidance_tips",
                    minimum=0,
                    maximum=40,
                    item_chars=500,
                )
            ),
            "evidence": [item.to_dict() for item in evidence],
            "unknowns": list(
                _text_list(
                    raw.get("unknowns", []),
                    "plan.unknowns",
                    minimum=0,
                    maximum=60,
                    item_chars=500,
                )
            ),
            "generated_at": generated_at,
        }
        _ensure_json_size(normalized, max_plan_bytes)
        return cls(data=normalized)

    @property
    def request(self) -> TravelRequestV1:
        return TravelRequestV1.from_dict(self.data["request"])

    def with_identity(self, *, plan_id: str, owner_user_id: str) -> TravelPlanV1:
        value = dict(self.data)
        value["plan_id"] = plan_id
        value["owner_user_id"] = owner_user_id
        return TravelPlanV1(value)

    def to_dict(self) -> dict[str, Any]:
        return json.loads(json.dumps(self.data, ensure_ascii=False, allow_nan=False))


def deduplicate_evidence(
    items: Iterable[EvidenceItemV1],
) -> tuple[tuple[EvidenceItemV1, ...], dict[str, str]]:
    """Deduplicate same URL/content while retaining an id remapping table."""

    kept: list[EvidenceItemV1] = []
    aliases: dict[str, str] = {}
    by_key: dict[tuple[str, str], EvidenceItemV1] = {}
    by_hash: dict[str, EvidenceItemV1] = {}
    for item in items:
        url_key = normalized_source_url(item.source_url)
        key = (item.provider.casefold(), url_key)
        existing = by_hash.get(item.content_hash) or (by_key.get(key) if url_key else None)
        if existing is not None:
            aliases[item.evidence_id] = existing.evidence_id
            continue
        kept.append(item)
        by_hash[item.content_hash] = item
        if url_key:
            by_key[key] = item
    return tuple(kept), aliases


def evidence_content_hash(
    *,
    provider: str,
    title: str,
    source_url: str,
    excerpt: str,
    facts: Iterable[str],
) -> str:
    """Return a stable hash used only for duplicate evidence detection."""

    payload = "\n".join(
        [
            provider.strip().casefold(),
            " ".join(title.split()).casefold(),
            normalized_source_url(source_url),
            " ".join(excerpt.split()).casefold(),
            *(" ".join(item.split()).casefold() for item in facts),
        ]
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def normalized_source_url(value: str) -> str:
    """Normalize one HTTP(S) source URL for duplicate detection."""

    if not value:
        return ""
    parsed = urlsplit(value)
    query = [
        (key, item)
        for key, item in parse_qsl(parsed.query, keep_blank_values=True)
        if key.casefold() not in {"utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content"}
    ]
    path = parsed.path.rstrip("/") or "/"
    host = (parsed.hostname or "").casefold()
    port = parsed.port
    netloc = host if port is None else f"{host}:{port}"
    return urlunsplit((parsed.scheme.casefold(), netloc, path, urlencode(query), ""))


def _normalize_days(
    value: object,
    request: TravelRequestV1,
    evidence_ids: set[str],
    aliases: dict[str, str],
) -> list[dict[str, Any]]:
    if not isinstance(value, list) or len(value) != request.duration_days:
        _invalid("plan.days", "Travel plan must include exactly one entry per travel day.")
    start_date = date.fromisoformat(request.start_date)
    expected_dates = [
        (start_date + timedelta(days=index)).isoformat()
        for index in range(request.duration_days)
    ]
    result = []
    previous_area = ""
    for index, item in enumerate(value):
        field = f"plan.days[{index}]"
        raw = _object(item, field)
        allowed = {
            "date",
            "city_or_area",
            "activities",
            "route_segments",
            "meal_suggestions",
            "daily_budget",
            "weather_adjustment",
            "fallback_plan",
            "intensity_score",
        }
        _reject_unknown(raw, allowed, field)
        day_date = _date(raw.get("date"), f"{field}.date").isoformat()
        if day_date != expected_dates[index]:
            _invalid(f"{field}.date", "Travel day dates must be complete and ordered.")
        area = _text(raw.get("city_or_area"), f"{field}.city_or_area", max_chars=120)
        activities_raw = raw.get("activities")
        if not isinstance(activities_raw, list) or not activities_raw:
            _invalid(f"{field}.activities", "Each travel day must include activities.")
        if len(activities_raw) > MAX_ACTIVITIES_PER_DAY:
            _invalid(f"{field}.activities", "Travel day has too many activities.")
        activities = []
        previous_end = "00:00"
        for activity_index, activity in enumerate(activities_raw):
            activity_field = f"{field}.activities[{activity_index}]"
            activity_raw = _object(activity, activity_field)
            _reject_unknown(
                activity_raw,
                {
                    "start",
                    "end",
                    "place",
                    "reason",
                    "evidence_ids",
                    "opening_hours",
                    "location",
                },
                activity_field,
            )
            start = _clock(activity_raw.get("start"), f"{activity_field}.start")
            end = _clock(activity_raw.get("end"), f"{activity_field}.end")
            if end <= start:
                _invalid(f"{activity_field}.end", "Activity end time must be after start time.")
            if start < previous_end:
                _invalid(activity_field, "Travel activities overlap in time.")
            previous_end = end
            references = _evidence_references(
                activity_raw.get("evidence_ids", []), activity_field, evidence_ids, aliases
            )
            opening_hours = _optional_text(
                activity_raw.get("opening_hours"),
                max_chars=120,
                field=f"{activity_field}.opening_hours",
            )
            location = _location(
                activity_raw.get("location"), f"{activity_field}.location"
            )
            if location is None:
                _invalid(
                    f"{activity_field}.location",
                    "Every travel activity requires drawable longitude and latitude.",
                )
            activities.append(
                {
                    "start": start,
                    "end": end,
                    "place": _text(
                        activity_raw.get("place"), f"{activity_field}.place", max_chars=200
                    ),
                    "reason": _text(
                        activity_raw.get("reason"), f"{activity_field}.reason", max_chars=500
                    ),
                    "evidence_ids": references,
                    "opening_hours": opening_hours,
                    "location": location,
                }
            )
        segments_raw = raw.get("route_segments", [])
        if not isinstance(segments_raw, list) or len(segments_raw) > MAX_ROUTE_SEGMENTS_PER_DAY:
            _invalid(f"{field}.route_segments", "Travel route segment count is invalid.")
        segments = []
        for segment_index, segment in enumerate(segments_raw):
            segment_field = f"{field}.route_segments[{segment_index}]"
            segment_raw = _object(segment, segment_field)
            _reject_unknown(
                segment_raw,
                {
                    "mode",
                    "from",
                    "to",
                    "duration",
                    "distance",
                    "source",
                    "evidence_ids",
                    "path",
                },
                segment_field,
            )
            duration = _number(
                segment_raw.get("duration"), f"{segment_field}.duration", minimum=0, maximum=1440
            )
            distance = _number(
                segment_raw.get("distance"), f"{segment_field}.distance", minimum=0, maximum=20_000
            )
            segments.append(
                {
                    "mode": _text(segment_raw.get("mode"), f"{segment_field}.mode", max_chars=50),
                    "from": _text(segment_raw.get("from"), f"{segment_field}.from", max_chars=200),
                    "to": _text(segment_raw.get("to"), f"{segment_field}.to", max_chars=200),
                    "duration": duration,
                    "distance": distance,
                    "source": _text(
                        segment_raw.get("source"), f"{segment_field}.source", max_chars=100
                    ),
                    "evidence_ids": _evidence_references(
                        segment_raw.get("evidence_ids", []), segment_field, evidence_ids, aliases
                    ),
                    "path": _coordinate_path(
                        segment_raw.get("path", []), f"{segment_field}.path"
                    ),
                }
            )
        if previous_area and area != previous_area and not segments:
            _invalid(field, "A cross-area travel day must include route evidence.")
        previous_area = area
        result.append(
            {
                "date": day_date,
                "city_or_area": area,
                "activities": activities,
                "route_segments": segments,
                "meal_suggestions": list(
                    _text_list(
                        raw.get("meal_suggestions", []),
                        f"{field}.meal_suggestions",
                        minimum=0,
                        maximum=12,
                        item_chars=240,
                    )
                ),
                "daily_budget": _number(
                    raw.get("daily_budget", 0), f"{field}.daily_budget", minimum=0, maximum=10_000_000
                ),
                "weather_adjustment": _optional_text(
                    raw.get("weather_adjustment"),
                    max_chars=500,
                    field=f"{field}.weather_adjustment",
                ),
                "fallback_plan": _optional_text(
                    raw.get("fallback_plan"), max_chars=500, field=f"{field}.fallback_plan"
                ),
                "intensity_score": _number(
                    raw.get("intensity_score", 0),
                    f"{field}.intensity_score",
                    minimum=0,
                    maximum=10,
                ),
            }
        )
    return result


def _normalize_budget(value: object, request: TravelRequestV1) -> dict[str, Any]:
    raw = _object(value, "plan.budget")
    _reject_unknown(raw, {"lower", "expected", "upper", "items"}, "plan.budget")
    lower = _number(raw.get("lower"), "plan.budget.lower", minimum=0, maximum=10_000_000)
    expected = _number(raw.get("expected"), "plan.budget.expected", minimum=0, maximum=10_000_000)
    upper = _number(raw.get("upper"), "plan.budget.upper", minimum=0, maximum=10_000_000)
    if not lower <= expected <= upper:
        _invalid("plan.budget", "Budget must satisfy lower <= expected <= upper.")
    items = _object_list(raw.get("items"), "plan.budget.items", maximum=50)
    if request.budget_total_cny is not None and lower > request.budget_total_cny:
        _invalid("plan.budget.lower", "Even the lower budget exceeds the user's hard budget.")
    return {"lower": lower, "expected": expected, "upper": upper, "items": items}


def _evidence_references(
    value: object,
    field: str,
    evidence_ids: set[str],
    aliases: dict[str, str],
) -> list[str]:
    references = _text_list(
        value, f"{field}.evidence_ids", minimum=0, maximum=20, item_chars=100
    )
    normalized = []
    for item in references:
        resolved = aliases.get(item, item)
        if resolved not in evidence_ids:
            _invalid(f"{field}.evidence_ids", "Travel plan references unknown evidence.")
        if resolved not in normalized:
            normalized.append(resolved)
    return normalized


def _validate_source_freshness(source_type: str, freshness: str, field: str) -> None:
    allowed = {
        "official_api": {"live", "historical", "unknown"},
        "live_query": {"live", "snapshot", "unknown"},
        "official_page": {"snapshot", "live", "unknown"},
        "web_article": {"snapshot", "unknown"},
        "social_post": {"snapshot", "unknown"},
        "model_estimate": {"estimate", "unknown"},
    }
    if freshness not in allowed[source_type]:
        _invalid(field, "Evidence source type and freshness label are inconsistent.")


def _source_url(value: object, field: str, *, required: bool) -> str:
    text = _optional_text(value, max_chars=2048, field=field)
    if not text:
        if required:
            _invalid(field, "Evidence source URL is required.")
        return ""
    try:
        parsed = urlsplit(text)
        port = parsed.port
    except ValueError:
        _invalid(field, "Evidence source URL is invalid.")
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        _invalid(field, "Evidence source URL must use HTTP or HTTPS.")
    if parsed.username is not None or parsed.password is not None:
        _invalid(field, "Evidence source URL must not contain credentials.")
    for key, _ in parse_qsl(parsed.query, keep_blank_values=True):
        normalized_key = key.casefold()
        if any(part in normalized_key for part in _SENSITIVE_KEY_PARTS):
            _invalid(field, "Evidence source URL must not contain credential parameters.")
    if port is not None and not 1 <= port <= 65535:
        _invalid(field, "Evidence source URL contains an invalid port.")
    return text


def _location(value: object, field: str) -> dict[str, float] | None:
    if value is None:
        return None
    raw = _object(value, field)
    _reject_unknown(raw, {"longitude", "latitude"}, field)
    return {
        "longitude": _number(raw.get("longitude"), f"{field}.longitude", minimum=-180, maximum=180),
        "latitude": _number(raw.get("latitude"), f"{field}.latitude", minimum=-90, maximum=90),
    }


def _coordinate_path(value: object, field: str) -> list[dict[str, float]]:
    if not isinstance(value, list) or len(value) > 500:
        _invalid(field, f"{field} must be a bounded coordinate array.")
    result = []
    for index, item in enumerate(value):
        location = _location(item, f"{field}[{index}]")
        if location is not None:
            result.append(location)
    return result


def _reject_sensitive_keys(value: object, *, path: str = "plan") -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            normalized = str(key).casefold()
            if any(part in normalized for part in _SENSITIVE_KEY_PARTS):
                _invalid(path, "Travel plan contains a credential-like field.")
            _reject_sensitive_keys(item, path=f"{path}.{key}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _reject_sensitive_keys(item, path=f"{path}[{index}]")


def _object(value: object, field: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        _invalid(field, f"{field} must be an object.")
    return dict(value)


def _object_list(value: object, field: str, *, maximum: int) -> list[dict[str, Any]]:
    if not isinstance(value, list) or len(value) > maximum:
        _invalid(field, f"{field} must be a bounded array of objects.")
    result = []
    for index, item in enumerate(value):
        raw = _object(item, f"{field}[{index}]")
        _reject_sensitive_keys(raw, path=f"{field}[{index}]")
        result.append(_bounded_json_copy(raw, f"{field}[{index}]", max_bytes=16_000))
    return result


def _bounded_json_copy(value: object, field: str, *, max_bytes: int) -> Any:
    try:
        encoded = json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError):
        _invalid(field, f"{field} must be valid JSON.")
    if len(encoded.encode("utf-8")) > max_bytes:
        _invalid(field, f"{field} exceeds the allowed size.")
    return json.loads(encoded)


def _ensure_json_size(value: object, maximum: int) -> None:
    try:
        encoded = json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError):
        raise TravelValidationError(
            "TRAVEL_PLAN_SCHEMA_INVALID", "Travel plan is not valid JSON."
        ) from None
    if len(encoded.encode("utf-8")) > maximum:
        raise TravelValidationError("TRAVEL_PLAN_TOO_LARGE", "Travel plan exceeds the size limit.")


def _reject_unknown(raw: dict[str, Any], allowed: set[str], field: str) -> None:
    unknown = sorted(set(raw) - allowed)
    if unknown:
        _invalid(field, f"{field} contains unsupported fields.")


def _text(value: object, field: str, *, max_chars: int) -> str:
    if not isinstance(value, str) or not value.strip():
        _invalid(field, f"{field} is required.")
    text = " ".join(value.split())
    if len(text) > max_chars:
        _invalid(field, f"{field} exceeds the length limit.")
    return text


def _optional_text(
    value: object,
    *,
    max_chars: int,
    field: str,
    truncate: bool = False,
) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        _invalid(field, f"{field} must be text.")
    text = " ".join(value.split())
    if len(text) > max_chars:
        if truncate:
            return text[:max_chars]
        _invalid(field, f"{field} exceeds the length limit.")
    return text


def _text_list(
    value: object,
    field: str,
    *,
    minimum: int,
    maximum: int,
    item_chars: int,
    truncate: bool = False,
) -> tuple[str, ...]:
    if not isinstance(value, list) or not minimum <= len(value) <= maximum:
        _invalid(field, f"{field} must contain between {minimum} and {maximum} items.")
    items = []
    for index, item in enumerate(value):
        if not isinstance(item, str) or not item.strip():
            _invalid(f"{field}[{index}]", f"{field} items must be non-empty text.")
        text = " ".join(item.split())
        if len(text) > item_chars:
            if truncate:
                text = text[:item_chars]
            else:
                _invalid(f"{field}[{index}]", f"{field} item exceeds the length limit.")
        if text not in items:
            items.append(text)
    return tuple(items)


def _date(value: object, field: str) -> date:
    if not isinstance(value, str):
        _invalid(field, f"{field} must be an ISO date.")
    try:
        return date.fromisoformat(value)
    except ValueError:
        _invalid(field, f"{field} must be an ISO date.")


def _timestamp(value: object, field: str, *, required: bool = False) -> str:
    if value in {None, ""}:
        if required:
            _invalid(field, f"{field} is required.")
        return ""
    if not isinstance(value, str):
        _invalid(field, f"{field} must be an ISO timestamp.")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        _invalid(field, f"{field} must be an ISO timestamp.")
    if parsed.tzinfo is None:
        _invalid(field, f"{field} must include a timezone.")
    return parsed.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _clock(value: object, field: str) -> str:
    if not isinstance(value, str) or not _TIME_RE.fullmatch(value):
        _invalid(field, f"{field} must use 24-hour HH:MM format.")
    return value


def _integer(value: object, field: str, *, minimum: int, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        _invalid(field, f"{field} must be an integer in the supported range.")
    return value


def _number(value: object, field: str, *, minimum: float, maximum: float) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        _invalid(field, f"{field} must be numeric.")
    result = float(value)
    if not math.isfinite(result) or not minimum <= result <= maximum:
        _invalid(field, f"{field} is outside the supported range.")
    return result


def _invalid(field: str, message: str) -> None:
    raise TravelValidationError("TRAVEL_PLAN_SCHEMA_INVALID", message, field=field)


def _utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")
