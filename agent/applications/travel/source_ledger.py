"""Bounded process-lifetime evidence that travel Sessions actually queried sources."""

from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import time
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from zoneinfo import ZoneInfo

from agent.protocols.tool import Tool, ToolExecutionContext, ToolProvider, ToolResult

TRAVEL_SOURCE_CATEGORIES = frozenset(
    {"maps", "weather", "transport", "lodging", "web", "social"}
)
_CANDIDATE_REQUIRED_CATEGORIES = TRAVEL_SOURCE_CATEGORIES - {"maps"}
_FINALIZATION_REQUIRED_CATEGORIES = frozenset({"maps", "lodging"})
_CANDIDATE_RESEARCH_PROFILES = frozenset(
    {"travel-transport-weather", "travel-stay-poi", "travel-guides"}
)
_MAX_SESSIONS = 128
_MAX_PLAN_ATTEMPTS = 8
_MAX_RESULT_PARSE_CHARS = 128_000
_CALL_BUDGETS = {
    "transport_lookup": 4,
    "transport_ticket": 2,
    "weather": 2,
    "web": 2,
    "social": 2,
    "social_detail": 1,
    "lodging": 3,
    "maps_search": 18,
    "maps_detail": 12,
    "maps_lodging_search": 6,
    "maps_geocode": 16,
    "maps_route": 16,
}
_FINALIZATION_BUDGET_OPERATIONS = frozenset(
    {
        "lodging",
        "maps_search",
        "maps_detail",
        "maps_lodging_search",
        "maps_geocode",
        "maps_route",
    }
)
_ROUTE_REPAIR_CALL_BUDGET = 20
_STABLE_SOURCE_ERROR_CODES = frozenset(
    {
        "TRAVEL_SOURCE_AUTH_REQUIRED",
        "HOTEL_AUTH_REQUIRED",
        "HOTEL_CREDENTIALS_NOT_CONFIGURED",
        "HOTEL_MANUAL_VERIFICATION_REQUIRED",
        "HOTEL_LOGIN_VERIFICATION_TIMEOUT",
    }
)
_SINGLE_SUCCESS_CATEGORIES = frozenset({"weather", "web", "social"})
_NON_SOURCE_ATTEMPT_CODES = frozenset(
    {
        "TOOL_ITERATION_LIMIT",
        "TRAVEL_SOURCE_ALREADY_QUERIED",
        "TRAVEL_SOURCE_ALREADY_SATISFIED",
        "TRAVEL_SOURCE_BUDGET_EXHAUSTED",
    }
)
_SENSITIVE_URL_KEY_PARTS = (
    "api_key",
    "authorization",
    "cookie",
    "credential",
    "password",
    "secret",
    "token",
)


@dataclass
class _SessionSources:
    expected: set[str] = field(default_factory=set)
    attempted: set[str] = field(default_factory=set)
    successful: set[str] = field(default_factory=set)
    retryable: set[str] = field(default_factory=set)
    attempt_counts: dict[str, int] = field(default_factory=dict)
    call_fingerprints: set[str] = field(default_factory=set)
    operation_counts: dict[str, int] = field(default_factory=dict)
    candidate_completed_profiles: set[str] = field(default_factory=set)
    verified_transit_available: bool = False
    transport_ticket_attempted: bool = False
    transport_ticket_attempt_count: int = 0
    transport_ticket_successful: bool = False
    transport_ticket_success_count: int = 0
    transport_ticket_not_on_sale: bool = False
    weather_data_attempted: bool = False
    weather_data_successful: bool = False
    station_codes: set[str] = field(default_factory=set)
    finalization_budget_started: bool = False
    finalization_attempted: set[str] = field(default_factory=set)
    forecast_expected: bool = False
    forecast_attempted: bool = False
    forecast_successful: bool = False
    forecast_repair_started: bool = False
    route_repair_started: bool = False
    route_repair_attempted: bool = False
    stable_unavailable: set[str] = field(default_factory=set)
    search_evidence: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    map_pois: list[dict[str, Any]] = field(default_factory=list)
    transit_routes: list[dict[str, Any]] = field(default_factory=list)
    hotel_observations: list[dict[str, Any]] = field(default_factory=list)
    rail_options: list[dict[str, Any]] = field(default_factory=list)
    social_destination: str = ""
    social_empty_search: bool = False
    web_destination: str = ""
    plan_attempts: list[dict[str, Any]] = field(default_factory=list)


class _AmapRequestGate:
    """Serialize AMap calls so parallel travel lanes respect the account QPS."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._last_call = 0.0

    def execute(self, call):
        with self._lock:
            interval = _amap_min_interval_seconds()
            wait_for = interval - (time.monotonic() - self._last_call)
            if wait_for > 0:
                time.sleep(wait_for)
            result = call()
            self._last_call = time.monotonic()
            if not _amap_qps_exceeded(result):
                return result
            time.sleep(max(1.0, interval * 2))
            result = call()
            self._last_call = time.monotonic()
            return result


_AMAP_REQUEST_GATE = _AmapRequestGate()


@dataclass(frozen=True)
class TravelSourceSnapshot:
    expected: frozenset[str]
    attempted: frozenset[str]
    successful: frozenset[str]
    retryable: frozenset[str]
    attempt_counts: tuple[tuple[str, int], ...]
    verified_transit_available: bool = False
    transport_ticket_attempted: bool = False
    transport_ticket_attempt_count: int = 0
    transport_ticket_successful: bool = False
    transport_ticket_success_count: int = 0
    transport_ticket_not_on_sale: bool = False
    weather_data_attempted: bool = False
    weather_data_successful: bool = False
    forecast_expected: bool = False
    forecast_attempted: bool = False
    forecast_successful: bool = False
    finalization_budget_started: bool = False
    finalization_attempted: frozenset[str] = frozenset()
    route_repair_attempted: bool = False
    candidate_completed_profiles: frozenset[str] = frozenset()

    @property
    def candidate_research_complete(self) -> bool:
        return _CANDIDATE_RESEARCH_PROFILES.issubset(
            self.candidate_completed_profiles
        )

    @property
    def missing_attempts(self) -> tuple[str, ...]:
        if self.finalization_budget_started:
            return tuple(
                sorted(
                    (self.expected & _FINALIZATION_REQUIRED_CATEGORIES)
                    - self.finalization_attempted
                )
            )
        return tuple(sorted(self.expected - self.attempted))

    @property
    def candidate_missing_attempts(self) -> tuple[str, ...]:
        # A completed fixed three-lane fan-in is the durable stage boundary. A lane
        # may legitimately finish without dated railway calls when one endpoint has
        # no station code, or with a stable source failure. Never demand the same
        # delegation batch again after that bounded outcome.
        if self.candidate_research_complete:
            return ()
        missing = set(
            (self.expected & _CANDIDATE_REQUIRED_CATEGORIES) - self.attempted
        )
        if "transport" in self.expected and self.transport_ticket_attempt_count < 2:
            missing.add("transport")
        if "weather" in self.expected and not self.weather_data_attempted:
            missing.add("weather")
        if not missing and self.candidate_completed_profiles:
            # Source attempts can all be terminal while one delegated lane itself
            # failed (for example its LLM timed out). Keep delegation available for
            # exactly that persisted lane instead of treating the stage as complete.
            missing.update(
                _CANDIDATE_RESEARCH_PROFILES - self.candidate_completed_profiles
            )
        return tuple(sorted(missing))

    @property
    def retry_required(self) -> tuple[str, ...]:
        counts = dict(self.attempt_counts)
        return tuple(
            sorted(
                category
                for category in self.retryable - self.successful
                if not self.expected or category in self.expected
                if counts.get(category, 0) < 2
            )
        )

    @property
    def evidence_coverage(self) -> float | None:
        """Return verified source-category coverage, or None before registration."""

        if not self.expected:
            return None
        return len(self.successful & self.expected) / len(self.expected)


class TravelSourceLedger:
    """Track only source categories and outcomes, never queries or result bodies."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._sessions: dict[str, _SessionSources] = {}

    def register_expected(self, session_id: str, tool_names: list[str]) -> None:
        categories = {category for name in tool_names if (category := source_category(name))}
        if not session_id or not categories:
            return
        with self._lock:
            if session_id not in self._sessions and len(self._sessions) >= _MAX_SESSIONS:
                self._sessions.pop(next(iter(self._sessions)))
            state = self._sessions.setdefault(session_id, _SessionSources())
            state.expected.update(categories)
            if any(_is_forecast_tool(name) for name in tool_names):
                state.forecast_expected = True

    def admit_call(
        self,
        session_id: str,
        tool_name: str,
        arguments: dict[str, Any],
    ) -> ToolResult | None:
        """Atomically reject duplicate or excessive travel source calls."""

        operation = _admission_operation(tool_name, arguments)
        if not session_id or not operation:
            return None
        with self._lock:
            state = self._sessions.setdefault(session_id, _SessionSources())
            category = source_category(tool_name)
            if operation == "social" and state.operation_counts.get("social", 0) > 0:
                if not state.social_empty_search:
                    return _guard_result(
                        "TRAVEL_SOCIAL_RETRY_NOT_ALLOWED",
                        "Xiaohongshu may be retried only after the first search returned a "
                        "real empty result set. Do not retry after usable rows, timeout, "
                        "authentication failure, or rate limiting.",
                    )
                keyword = str(arguments.get("keyword") or "").strip()
                anchor = _xhs_query_anchors(keyword)
                if not anchor or not _is_specific_attraction_anchor(
                    anchor[0], state.social_destination, state.map_pois
                ):
                    return _guard_result(
                        "TRAVEL_SOCIAL_ATTRACTION_REQUIRED",
                        "The one allowed Xiaohongshu retry requires a concrete attraction "
                        "obtained from the user request or verified map POIs. A province or "
                        "city keyword is not an attraction; keep the empty result instead.",
                    )
            fresh_finalization_lodging = (
                state.finalization_budget_started
                and category == "lodging"
                and category not in state.finalization_attempted
            )
            fresh_forecast_repair = (
                state.forecast_repair_started
                and (category == "weather" or _is_weather_geocode_tool(tool_name))
                and not state.forecast_successful
            )
            fresh_route_repair = state.route_repair_started and category == "maps"
            if category in state.stable_unavailable:
                return _guard_result(
                    "TRAVEL_SOURCE_STABLY_UNAVAILABLE",
                    "This source already returned a stable authentication or verification "
                    "failure in the current travel Session. Do not retry until the Owner "
                    "repairs the source outside this plan.",
                )
            if (
                category in _SINGLE_SUCCESS_CATEGORIES
                and category in state.successful
                and operation != "social_detail"
                and not fresh_finalization_lodging
                and not fresh_forecast_repair
            ):
                return _guard_result(
                    "TRAVEL_SOURCE_ALREADY_SATISFIED",
                    "This source category already returned usable data in the current travel "
                    "Session. Reuse the earlier ToolResult instead of running another query.",
                )
            if operation == "transport_ticket":
                station_codes = _ticket_station_codes(arguments)
                if station_codes and (
                    len(station_codes) != 2
                    or any(code not in state.station_codes for code in station_codes)
                ):
                    return _guard_result(
                        "TRAVEL_STATION_CODE_UNVERIFIED",
                        "Ticket station codes must come from a successful station-code ToolResult "
                        "in the current travel Session. Query the station-code tool first and reuse "
                        "its exact station_code values.",
                    )
            fingerprint = _call_fingerprint(tool_name, arguments)
            if state.finalization_budget_started and category == "lodging":
                fingerprint = f"finalization:{fingerprint}"
            if fresh_forecast_repair:
                fingerprint = f"forecast-repair:{fingerprint}"
            if fresh_route_repair:
                fingerprint = f"route-repair:{fingerprint}"
            if fingerprint in state.call_fingerprints:
                return _guard_result(
                    "TRAVEL_SOURCE_ALREADY_QUERIED",
                    "This exact source query already exists in the current travel Session. "
                    "Reuse the earlier ToolResult and do not retry it.",
                )
            limit = (
                _ROUTE_REPAIR_CALL_BUDGET
                if operation == "maps_route" and state.route_repair_started
                else _CALL_BUDGETS[operation]
            )
            count = state.operation_counts.get(operation, 0)
            if count >= limit:
                return _guard_result(
                    "TRAVEL_SOURCE_BUDGET_EXHAUSTED",
                    f"The {operation} source-call budget is exhausted for this travel Session. "
                    "Use existing evidence or record the remaining gap as unknown.",
                )
            state.call_fingerprints.add(fingerprint)
            state.operation_counts[operation] = count + 1
        return None

    def normalize_arguments(
        self,
        session_id: str,
        tool_name: str,
        arguments: dict[str, Any],
    ) -> dict[str, Any]:
        """Apply deterministic source-specific query rules before admission."""

        name = str(tool_name or "").casefold()
        category = source_category(name)
        if category == "web" and "search" in name:
            query = str(arguments.get("query") or arguments.get("search_query") or "").strip()
            anchors = _xhs_query_anchors(query)
            if not anchors:
                return arguments
            with self._lock:
                state = self._sessions.setdefault(session_id, _SessionSources())
                first_search = state.operation_counts.get("web", 0) == 0
                if first_search:
                    state.web_destination = anchors[0]
                    normalized = f"{anchors[0]}旅游攻略 公共交通"
                else:
                    destination = state.web_destination or anchors[0]
                    detail_anchor = next(
                        (item for item in anchors if item != destination),
                        "",
                    )
                    normalized = (
                        f"{destination} {detail_anchor} 攻略"
                        if detail_anchor
                        else f"{destination}旅游攻略"
                    )
            key = "query" if "query" in arguments or "search_query" not in arguments else "search_query"
            return {**arguments, key: normalized[:100]}
        if category != "social" or not any(
            marker in name for marker in ("search_notes", "search_feeds", "search")
        ):
            return arguments
        keyword = str(arguments.get("keyword") or "").strip()
        anchors = _xhs_query_anchors(keyword)
        if not anchors:
            return arguments
        with self._lock:
            state = self._sessions.setdefault(session_id, _SessionSources())
            first_search = state.operation_counts.get("social", 0) == 0
            if first_search:
                anchor = anchors[0]
                state.social_destination = anchor
                normalized = f"{anchor}旅游攻略"
            else:
                map_anchor = _first_specific_map_attraction(
                    state.map_pois, state.social_destination
                )
                anchor = map_anchor or next(
                    (
                        item
                        for item in anchors
                        if _is_specific_attraction_anchor(
                            item, state.social_destination, state.map_pois
                        )
                    ),
                    anchors[0],
                )
                normalized = f"{anchor}攻略"
        return {**arguments, "keyword": normalized[:80]}

    def observe(
        self,
        session_id: str,
        tool_name: str,
        result: ToolResult,
        arguments: dict[str, Any] | None = None,
        *,
        record_finalization: bool | None = None,
    ) -> None:
        category = source_category(tool_name)
        if not session_id or not category:
            return
        with self._lock:
            state = self._sessions.setdefault(session_id, _SessionSources())
            operation = source_operation(tool_name)
            state.attempted.add(category)
            if (
                state.finalization_budget_started
                and category in _FINALIZATION_REQUIRED_CATEGORIES
                and record_finalization is not False
                and (category != "maps" or operation == "maps_route")
                and str(result.metadata.get("code") or "").upper()
                not in _NON_SOURCE_ATTEMPT_CODES
            ):
                state.finalization_attempted.add(category)
            state.attempt_counts[category] = state.attempt_counts.get(category, 0) + 1
            if operation == "transport_ticket":
                state.transport_ticket_attempted = True
                state.transport_ticket_attempt_count += 1
            if category == "weather" and _is_weather_data_tool(tool_name):
                state.weather_data_attempted = True
            if _is_forecast_tool(tool_name):
                state.forecast_attempted = True
            if state.route_repair_started and operation == "maps_route":
                state.route_repair_attempted = True
            succeeded = _result_succeeded(result, category)
            if category == "social" and operation == "social":
                state.social_empty_search = _social_result_is_real_empty(result)
            if succeeded:
                _capture_structured_result(
                    state,
                    tool_name,
                    result,
                    arguments=arguments,
                )
                if category in {"web", "social"}:
                    evidence = _safe_search_evidence(category, result)
                    if evidence:
                        state.search_evidence[category] = evidence
                if operation == "transport_lookup":
                    state.station_codes.update(_station_codes_from_output(result.output))
                if operation == "transport_ticket":
                    state.transport_ticket_successful = True
                    state.transport_ticket_success_count += 1
                    state.transport_ticket_not_on_sale = _transport_not_on_sale(result)
                if category == "weather" and _is_weather_data_tool(tool_name):
                    state.weather_data_successful = True
                if _is_forecast_tool(tool_name):
                    state.forecast_successful = True
                category_fact_succeeded = not (
                    operation == "transport_lookup"
                    or (category == "weather" and not _is_weather_data_tool(tool_name))
                )
                if category_fact_succeeded:
                    state.successful.add(category)
                    state.retryable.discard(category)
                if _result_has_transit_details(tool_name, result):
                    state.verified_transit_available = True
            elif category not in state.successful and _result_retryable(result, category):
                state.retryable.add(category)
            elif _stable_source_failure(result):
                state.stable_unavailable.add(category)

    def begin_finalization_budget(self, session_id: str) -> None:
        """Open one bounded detail-query budget after candidate selection.

        Candidate research and final plan enrichment are separate product phases. Keep
        evidence and duplicate fingerprints across both phases, while allowing the
        selected itinerary to query its concrete hotel, coordinates, and local routes.
        """

        if not session_id:
            return
        with self._lock:
            state = self._sessions.setdefault(session_id, _SessionSources())
            if state.finalization_budget_started:
                return
            for operation in _FINALIZATION_BUDGET_OPERATIONS:
                state.operation_counts.pop(operation, None)
            state.expected.intersection_update(_FINALIZATION_REQUIRED_CATEGORIES)
            state.finalization_attempted.update(
                state.stable_unavailable & _FINALIZATION_REQUIRED_CATEGORIES
            )
            state.finalization_budget_started = True

    def mark_candidate_profiles_completed(
        self,
        session_id: str,
        profiles: set[str] | frozenset[str],
    ) -> None:
        """Persist the fixed candidate fan-in as the shared stage completion fact."""

        if not session_id or not profiles:
            return
        with self._lock:
            state = self._sessions.setdefault(session_id, _SessionSources())
            state.candidate_completed_profiles.update(
                set(profiles) & _CANDIDATE_RESEARCH_PROFILES
            )

    def mark_finalization_attempted(
        self,
        session_id: str,
        categories: set[str] | frozenset[str],
    ) -> None:
        """Record successfully completed finalization lanes without inventing facts.

        A finalization Child may legitimately reuse candidate evidence instead of
        calling the source Tool again.  In that case ``observe`` has no new lodging
        result to see, but the completed Child lane still satisfies the bounded
        orchestration requirement.  Only finalization-owned categories are accepted.
        """

        if not session_id or not categories:
            return
        with self._lock:
            state = self._sessions.setdefault(session_id, _SessionSources())
            if not state.finalization_budget_started:
                return
            state.finalization_attempted.update(
                set(categories) & _FINALIZATION_REQUIRED_CATEGORIES
            )

    def begin_forecast_repair(self, session_id: str) -> None:
        """Open one bounded weather-only repair after a forecast-required finalizer error."""

        if not session_id:
            return
        with self._lock:
            state = self._sessions.setdefault(session_id, _SessionSources())
            if state.forecast_successful or state.forecast_repair_started:
                return
            state.operation_counts.pop("weather", None)
            state.operation_counts.pop("maps_geocode", None)
            state.forecast_repair_started = True

    def begin_route_repair(self, session_id: str) -> None:
        """Open one bounded route-only repair after a route-evidence finalizer error."""

        if not session_id:
            return
        with self._lock:
            state = self._sessions.setdefault(session_id, _SessionSources())
            if state.route_repair_started:
                return
            for operation in ("maps_search", "maps_geocode", "maps_route"):
                state.operation_counts.pop(operation, None)
            state.route_repair_started = True

    def snapshot(self, session_id: str) -> TravelSourceSnapshot:
        with self._lock:
            state = self._sessions.get(session_id, _SessionSources())
            return TravelSourceSnapshot(
                expected=frozenset(state.expected),
                attempted=frozenset(state.attempted),
                successful=frozenset(state.successful),
                retryable=frozenset(state.retryable),
                attempt_counts=tuple(sorted(state.attempt_counts.items())),
                verified_transit_available=state.verified_transit_available,
                transport_ticket_attempted=state.transport_ticket_attempted,
                transport_ticket_attempt_count=state.transport_ticket_attempt_count,
                transport_ticket_successful=state.transport_ticket_successful,
                transport_ticket_success_count=state.transport_ticket_success_count,
                transport_ticket_not_on_sale=state.transport_ticket_not_on_sale,
                weather_data_attempted=state.weather_data_attempted,
                weather_data_successful=state.weather_data_successful,
                forecast_expected=state.forecast_expected,
                forecast_attempted=state.forecast_attempted,
                forecast_successful=state.forecast_successful,
                finalization_budget_started=state.finalization_budget_started,
                finalization_attempted=frozenset(state.finalization_attempted),
                route_repair_attempted=state.route_repair_attempted,
                candidate_completed_profiles=frozenset(
                    state.candidate_completed_profiles
                ),
            )

    def search_evidence(
        self, session_id: str, category: str
    ) -> list[dict[str, Any]]:
        """Return bounded safe search citations without exposing raw result bodies."""

        if category not in {"web", "social"}:
            return []
        with self._lock:
            state = self._sessions.get(session_id)
            return deepcopy(state.search_evidence.get(category, [])) if state else []

    def structured_results(self, session_id: str) -> dict[str, list[dict[str, Any]]]:
        """Return bounded verified facts used for deterministic final-plan merging."""

        with self._lock:
            state = self._sessions.get(session_id)
            if state is None:
                return {
                    "map_pois": [],
                    "transit_routes": [],
                    "hotel_observations": [],
                    "rail_options": [],
                }
            return {
                "map_pois": deepcopy(state.map_pois),
                "transit_routes": deepcopy(state.transit_routes),
                "hotel_observations": deepcopy(state.hotel_observations),
                "rail_options": deepcopy(state.rail_options),
            }

    def restore_plan_attempts(
        self, session_id: str, attempts: list[dict[str, Any]]
    ) -> None:
        """Restore bounded Finalizer drafts already persisted in parent Session history."""

        if not session_id:
            return
        safe = [
            {
                "plan": deepcopy(attempt["plan"]),
                "live_weather_verified": bool(attempt.get("live_weather_verified")),
                "transit_verified": bool(attempt.get("transit_verified")),
            }
            for attempt in attempts
            if isinstance(attempt, dict) and isinstance(attempt.get("plan"), dict)
        ][-_MAX_PLAN_ATTEMPTS:]
        if not safe:
            return
        with self._lock:
            state = self._sessions.setdefault(session_id, _SessionSources())
            state.plan_attempts = safe

    def remember_plan_attempt(self, session_id: str, plan: dict[str, Any]) -> None:
        """Keep one failed Finalizer draft for deterministic cross-Turn fact merging."""

        if not session_id or not isinstance(plan, dict):
            return
        with self._lock:
            state = self._sessions.setdefault(session_id, _SessionSources())
            state.plan_attempts.append(
                {
                    "plan": deepcopy(plan),
                    "live_weather_verified": state.forecast_successful,
                    "transit_verified": state.verified_transit_available,
                }
            )
            del state.plan_attempts[:-_MAX_PLAN_ATTEMPTS]

    def plan_attempts(self, session_id: str) -> list[dict[str, Any]]:
        """Return isolated newest-first failed Finalizer drafts for one Session."""

        with self._lock:
            state = self._sessions.get(session_id)
            if state is None:
                return []
            return deepcopy(list(reversed(state.plan_attempts)))

    def clear(self, session_id: str) -> None:
        with self._lock:
            self._sessions.pop(session_id, None)


def source_category(tool_name: str) -> str:
    name = str(tool_name or "").casefold()
    if "amap" in name or any(part in name for part in ("maps_", "geocode", "route")):
        return "maps"
    if "open-meteo" in name or "open_meteo" in name or "weather" in name or "forecast" in name:
        return "weather"
    if "12306" in name or any(part in name for part in ("train", "rail")):
        return "transport"
    if (
        "hotel-browser" in name
        or "ctrip" in name
        or "search_hotels" in name
        or "search_travel_hotels" in name
    ):
        return "lodging"
    if "tavily" in name:
        return "web"
    if "xhs" in name or "xiaohongshu" in name:
        return "social"
    return ""


def source_operation(tool_name: str) -> str:
    """Return the bounded call bucket for one travel MCP Tool."""

    category = source_category(tool_name)
    name = str(tool_name or "").casefold()
    if category == "transport":
        if any(
            marker in name
            for marker in (
                "station-code",
                "stations-code",
                "station_code",
                "stations_code",
                "current-date",
                "current_date",
            )
        ):
            return "transport_lookup"
        return "transport_ticket"
    if category != "maps":
        return category
    if any(part in name for part in ("direction", "distance", "route", "transit")):
        return "maps_route"
    if any(part in name for part in ("maps_geo", "geocode")):
        return "maps_geocode"
    if any(part in name for part in ("search_detail", "maps_detail")):
        return "maps_detail"
    return "maps_search"


def _is_forecast_tool(tool_name: str) -> bool:
    name = str(tool_name or "").casefold()
    return source_category(name) == "weather" and "forecast" in name and "historical" not in name


def _is_weather_geocode_tool(tool_name: str) -> bool:
    name = str(tool_name or "").casefold()
    return "open-meteo" in name and "geocode_place" in name


def _is_weather_data_tool(tool_name: str) -> bool:
    name = str(tool_name or "").casefold()
    return source_category(name) == "weather" and any(
        marker in name for marker in ("forecast", "historical", "weather")
    ) and "geocode" not in name


def _admission_operation(tool_name: str, arguments: dict[str, Any]) -> str:
    operation = source_operation(tool_name)
    name = str(tool_name or "").casefold()
    if operation == "social" and any(
        marker in name for marker in ("get_note_detail", "note_detail", "feed_detail")
    ):
        # A single selected-note detail read enriches an already successful search;
        # it is not a keyword retry and therefore has its own strict budget.
        return "social_detail"
    if operation != "maps_search":
        return operation
    requested_types = str(arguments.get("types") or "").strip().casefold()
    if requested_types.startswith("10") or any(
        marker in requested_types
        for marker in ("住宿", "宾馆", "酒店", "旅馆", "hotel", "hostel", "lodging")
    ):
        return "maps_lodging_search"
    text = json.dumps(arguments, ensure_ascii=False, default=str).casefold()
    lodging_markers = (
        "酒店",
        "宾馆",
        "旅馆",
        "客栈",
        "民宿",
        "住宿",
        "hotel",
        "hostel",
        "lodging",
        " inn",
    )
    return "maps_lodging_search" if any(marker in text for marker in lodging_markers) else operation


class TravelGuardedTool:
    """Travel-only MCP decorator that keeps AgentLoop business-agnostic."""

    def __init__(self, delegate: Tool, ledger: TravelSourceLedger, session_id: str) -> None:
        self.name = delegate.name
        self.description = delegate.description
        self.parameters = delegate.parameters
        self._delegate = delegate
        self._ledger = ledger
        self._session_id = session_id

    def execute(self, args: dict[str, Any]) -> ToolResult:
        live_forecast_required = _live_forecast_required_result(self.name, args)
        if live_forecast_required is not None:
            return live_forecast_required
        effective_args = _normalize_historical_weather_arguments(self.name, args)
        effective_args = _normalize_amap_text_search_arguments(
            self.name, effective_args
        )
        effective_args = self._ledger.normalize_arguments(
            self._session_id, self.name, effective_args
        )
        guarded = self._ledger.admit_call(self._session_id, self.name, effective_args)
        if guarded is not None:
            return guarded
        not_on_sale = _not_on_sale_result(self.name, effective_args)
        if not_on_sale is not None:
            self._ledger.observe(
                self._session_id, self.name, not_on_sale, effective_args
            )
            return not_on_sale
        def execute() -> ToolResult:
            return self._delegate.execute(effective_args)

        raw_result = (
            _AMAP_REQUEST_GATE.execute(execute)
            if source_category(self.name) == "maps"
            else execute()
        )
        result = _compact_travel_source_result(
            self.name, raw_result, arguments=effective_args
        )
        result.metadata["travel_effective_arguments"] = _safe_effective_arguments(
            effective_args
        )
        self._ledger.observe(self._session_id, self.name, result, effective_args)
        return result


def _safe_effective_arguments(arguments: dict[str, Any]) -> dict[str, Any]:
    """Persist only public query coordinates/labels needed for progress recovery."""

    allowed = {
        "address",
        "checkin",
        "checkout",
        "city",
        "cityd",
        "date",
        "destination",
        "end_date",
        "fromStation",
        "keyword",
        "keywords",
        "latitude",
        "longitude",
        "max_results",
        "name",
        "origin",
        "query",
        "search_query",
        "start_date",
        "toStation",
        "types",
    }
    safe: dict[str, Any] = {}
    for key in allowed:
        value = arguments.get(key)
        if isinstance(value, str):
            safe[key] = value[:200]
        elif isinstance(value, bool | int | float):
            safe[key] = value
    return safe


class TravelResearchRequiredToolProvider:
    """Require configured travel source attempts before candidate solving.

    This application-boundary decorator keeps the generic AgentLoop free of travel
    policy while preventing the parent model from skipping its required child fan-out
    and constructing evidence-free candidates.
    """

    def __init__(
        self,
        delegate: ToolProvider,
        ledger: TravelSourceLedger,
        session_id: str,
    ) -> None:
        self._delegate = delegate
        self._ledger = ledger
        self._session_id = session_id

    def definitions(self) -> list[dict[str, Any]]:
        definitions = self._delegate.definitions()
        if not self._ledger.snapshot(self._session_id).candidate_missing_attempts:
            return [
                definition
                for definition in definitions
                if _definition_name(definition)
                not in {"delegate_tasks", "request_travel_candidate_review"}
            ]
        return [
            definition
            for definition in definitions
            if _definition_name(definition) == "delegate_tasks"
        ]

    def execute(self, name: str, args: dict[str, Any]) -> ToolResult:
        guarded = self._guard(name)
        if guarded is not None:
            return guarded
        return self._delegate.execute(name, args)

    def execute_with_context(
        self,
        name: str,
        args: dict[str, Any],
        context: ToolExecutionContext,
    ) -> ToolResult:
        guarded = self._guard(name)
        if guarded is not None:
            return guarded
        contextual = getattr(self._delegate, "execute_with_context", None)
        result = (
            contextual(name, args, context)
            if callable(contextual)
            else self._delegate.execute(name, args)
        )
        review_args = _candidate_review_args(name, args, result)
        if review_args is None:
            return result
        if callable(contextual):
            return contextual("request_travel_candidate_review", review_args, context)
        return result

    def _guard(self, name: str) -> ToolResult | None:
        if str(name).casefold() != "run_skill":
            return None
        missing = self._ledger.snapshot(self._session_id).candidate_missing_attempts
        if not missing:
            return None
        return _guard_result(
            "TRAVEL_PARALLEL_RESEARCH_REQUIRED",
            "Candidate solving requires the configured travel research lanes first. "
            f"Delegate the missing source categories: {', '.join(missing)}.",
        )


class TravelFinalizationRequiredToolProvider:
    """Require selected-itinerary map/stay attempts before final plan saving."""

    def __init__(
        self,
        delegate: ToolProvider,
        ledger: TravelSourceLedger,
        session_id: str,
        repair_categories: frozenset[str] = frozenset(),
    ) -> None:
        self._delegate = delegate
        self._ledger = ledger
        self._session_id = session_id
        self._repair_categories = repair_categories

    def definitions(self) -> list[dict[str, Any]]:
        definitions = self._delegate.definitions()
        snapshot = self._ledger.snapshot(self._session_id)
        missing = (*snapshot.missing_attempts, *self._repair_missing(snapshot))
        expected = {"delegate_tasks"} if missing else {"finalize_travel_plan"}
        return [
            definition
            for definition in definitions
            if _definition_name(definition) in expected
        ]

    def execute(self, name: str, args: dict[str, Any]) -> ToolResult:
        guarded = self._guard(name)
        return guarded if guarded is not None else self._delegate.execute(name, args)

    def execute_with_context(
        self,
        name: str,
        args: dict[str, Any],
        context: ToolExecutionContext,
    ) -> ToolResult:
        guarded = self._guard(name)
        if guarded is not None:
            return guarded
        contextual = getattr(self._delegate, "execute_with_context", None)
        if callable(contextual):
            return contextual(name, args, context)
        return self._delegate.execute(name, args)

    def _guard(self, name: str) -> ToolResult | None:
        if str(name).casefold() != "finalize_travel_plan":
            return None
        snapshot = self._ledger.snapshot(self._session_id)
        missing = (*snapshot.missing_attempts, *self._repair_missing(snapshot))
        if not missing:
            return None
        return _guard_result(
            "TRAVEL_FINALIZATION_RESEARCH_REQUIRED",
            "Final plan saving requires the selected-itinerary map and stay lanes first. "
            f"Delegate the missing source categories: {', '.join(missing)}.",
        )

    def _repair_missing(self, snapshot: TravelSourceSnapshot) -> tuple[str, ...]:
        missing: list[str] = []
        if "weather" in self._repair_categories and not snapshot.forecast_successful:
            missing.append("weather")
        if "maps" in self._repair_categories and not snapshot.route_repair_attempted:
            missing.append("maps")
        return tuple(missing)


def guard_travel_tools(
    tools: list[Tool],
    ledger: TravelSourceLedger,
    session_id: str,
) -> list[Tool]:
    """Wrap only recognized travel source Tools for one Session."""

    return [
        TravelGuardedTool(tool, ledger, session_id)
        if source_operation(tool.name)
        else tool
        for tool in tools
    ]


def require_travel_research_before_solving(
    provider: ToolProvider,
    ledger: TravelSourceLedger,
    session_id: str,
) -> ToolProvider:
    """Return a parent-provider decorator enforcing research before optimizer use."""

    return TravelResearchRequiredToolProvider(provider, ledger, session_id)


def require_travel_finalization_before_saving(
    provider: ToolProvider,
    ledger: TravelSourceLedger,
    session_id: str,
    repair_categories: frozenset[str] = frozenset(),
) -> ToolProvider:
    """Return a stage decorator enforcing the selected-itinerary detail batch."""

    return TravelFinalizationRequiredToolProvider(
        provider,
        ledger,
        session_id,
        repair_categories=repair_categories,
    )


def _candidate_review_args(
    name: str,
    args: dict[str, Any],
    result: ToolResult,
) -> dict[str, Any] | None:
    """Bridge a trusted optimizer result directly into candidate review persistence."""

    if (
        name.casefold() != "run_skill"
        or result.is_error
        or not str(args.get("skill") or "").casefold().endswith("/travel-planner")
    ):
        return None
    try:
        payload = json.loads(result.output)
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
    data = payload.get("data") if isinstance(payload, dict) else None
    candidates = data.get("feasible_candidates") if isinstance(data, dict) else None
    if not isinstance(candidates, list) or not candidates:
        return None
    recommended = next(
        (
            str(candidate.get("candidate_id") or "").strip()
            for candidate in candidates
            if isinstance(candidate, dict) and candidate.get("recommended") is True
        ),
        "",
    )
    if not recommended:
        selected = data.get("selected_candidate")
        recommended = (
            str(selected.get("candidate_id") or "").strip()
            if isinstance(selected, dict)
            else ""
        )
    if not recommended:
        return None
    return {
        "recommended_candidate_id": recommended,
        "candidates": candidates,
    }


def preferred_travel_tool_names(tool_names: list[str]) -> tuple[str, ...]:
    """Select useful read-only Tools, including map search, geocoding, and routing."""

    preferences = {
        "maps": ("maps_text_search", "text_search", "search"),
        "weather": ("get_forecast", "historical_weather", "forecast", "weather"),
        "transport": ("get-tickets", "query", "train", "rail"),
        "lodging": ("search_hotels", "hotel", "search"),
        "web": ("tavily_search", "search"),
        "social": ("search_notes", "search"),
    }
    selected: list[str] = []
    for category in ("maps", "weather", "transport", "lodging", "web", "social"):
        candidates = [name for name in tool_names if source_category(name) == category]
        if not candidates:
            continue
        if category == "maps":
            for markers in (
                ("maps_text_search", "text_search"),
                ("maps_geo", "geocode"),
                (
                    "maps_direction_transit_integrated",
                    "maps_direction_walking",
                    "maps_direction_driving",
                    "maps_distance",
                ),
            ):
                chosen = next(
                    (
                        name
                        for marker in markers
                        for name in candidates
                        if marker in name.casefold()
                    ),
                    "",
                )
                if chosen and chosen not in selected:
                    selected.append(chosen)
            if not any(name in selected for name in candidates):
                selected.append(candidates[0])
            continue
        chosen = next(
            (
                name
                for marker in preferences[category]
                for name in candidates
                if marker in name.casefold()
            ),
            candidates[0],
        )
        selected.append(chosen)
    return tuple(selected)


def _result_succeeded(result: ToolResult, category: str) -> bool:
    if result.is_error:
        return False
    code = str(result.metadata.get("code") or "").upper()
    if code and code not in {"OK", "MCP_OK"}:
        return False
    payloads = _json_objects(result.output)
    payload = payloads[0] if payloads else {}
    status = str(payload.get("status") or "").casefold()
    payload_code = str(payload.get("code") or "").upper()
    if status in {"error", "failed", "failure"}:
        return False
    if payload_code and payload_code not in {"OK", "MCP_OK"}:
        return False
    if category in {"web", "social"}:
        return _has_search_rows(payloads, category)
    if category == "lodging":
        return any(
            isinstance(payload.get("hotels"), list) and bool(payload["hotels"])
            for payload in payloads
        )
    return True


def _capture_structured_result(
    state: _SessionSources,
    tool_name: str,
    result: ToolResult,
    *,
    arguments: dict[str, Any] | None,
) -> None:
    """Keep only bounded, non-secret facts needed to prevent model evidence loss."""

    payloads = _json_objects(result.output)
    operation = source_operation(tool_name)
    if operation == "transport_ticket":
        for row in _parse_12306_ticket_rows(result.output):
            if row not in state.rail_options:
                state.rail_options.append(row)
        state.rail_options[:] = state.rail_options[-80:]
        return
    if not payloads:
        return
    payload = payloads[0]
    if operation in {"maps_search", "maps_lodging_search"}:
        rows = payload.get("pois")
        if isinstance(rows, list):
            for row in rows[:10]:
                if not isinstance(row, dict):
                    continue
                safe = {
                    key: deepcopy(row[key])
                    for key in (
                        "id",
                        "name",
                        "address",
                        "location",
                        "type",
                        "typecode",
                        "pname",
                        "cityname",
                        "adname",
                    )
                    if key in row
                }
                if safe and safe not in state.map_pois:
                    state.map_pois.append(safe)
            state.map_pois[:] = state.map_pois[-40:]
        return
    if operation == "maps_route":
        route = payload.get("route")
        if isinstance(route, dict) and isinstance(route.get("transits"), list):
            snapshot = {
                "arguments": {
                    key: str((arguments or {}).get(key) or "")[:200]
                    for key in ("origin", "destination", "city", "cityd")
                    if (arguments or {}).get(key) not in (None, "")
                },
                "route": deepcopy(route),
            }
            if snapshot not in state.transit_routes:
                state.transit_routes.append(snapshot)
            state.transit_routes[:] = state.transit_routes[-16:]
        return
    if operation == "lodging":
        rows = payload.get("hotels")
        if not isinstance(rows, list):
            return
        query = payload.get("query") if isinstance(payload.get("query"), dict) else {}
        observation = {
            "provider": str(payload.get("provider") or "ctrip-account-observation")[:80],
            "retrieved_at": str(payload.get("retrieved_at") or "")[:60],
            "query": {
                key: str(query.get(key) or (arguments or {}).get(key) or "")[:120]
                for key in ("city", "checkin", "checkout", "keyword")
            },
            "hotels": [
                {
                    key: deepcopy(row[key])
                    for key in (
                        "name",
                        "rating",
                        "observed_price_per_night_cny",
                        "price_text",
                        "summary",
                        "source_url",
                    )
                    if key in row
                }
                for row in rows[:10]
                if isinstance(row, dict)
            ],
        }
        if observation["hotels"]:
            state.hotel_observations.append(observation)
            state.hotel_observations[:] = state.hotel_observations[-4:]


_TRAIN_ROW_RE = re.compile(
    r"(?m)^(?P<service>[A-Z0-9]+)\(实际车次train_no:[^)]+\)\s+"
    r"(?P<from>.+?)\(telecode:\s*[A-Z0-9]+\)\s*->\s*"
    r"(?P<to>.+?)\(telecode:\s*[A-Z0-9]+\)\s+"
    r"(?P<departure>\d{2}:\d{2})\s*->\s*(?P<arrival>\d{2}:\d{2})\s+"
    r"历时：(?P<duration>\d{2}:\d{2})(?P<seats>(?:\r?\n- [^\r\n]*)*)"
)


def _parse_12306_ticket_rows(output: str) -> list[dict[str, Any]]:
    """Extract bounded train facts across the returned day, not only the list head."""

    rows: list[dict[str, Any]] = []
    for match in _TRAIN_ROW_RE.finditer(str(output or "")[:_MAX_RESULT_PARSE_CHARS]):
        seat, price = _preferred_12306_seat(match.group("seats"))
        hours, minutes = (int(part) for part in match.group("duration").split(":"))
        rows.append(
            {
                "service_name": match.group("service"),
                "from": match.group("from").strip(),
                "to": match.group("to").strip(),
                "departure_time": match.group("departure"),
                "arrival_time": match.group("arrival"),
                "duration_minutes": hours * 60 + minutes,
                "seat": seat,
                "price_cny_per_person": price,
            }
        )
    return rows[:80]


def _preferred_12306_seat(seat_text: str) -> tuple[str, float | None]:
    lines = [line.removeprefix("- ").strip() for line in seat_text.splitlines()]
    for marker in ("二等座", "硬座", "一等座", "软座", "无座"):
        for line in lines:
            if not line.startswith(marker) or "无剩余" in line:
                continue
            price = re.search(r"(\d+(?:\.\d+)?)元", line)
            return marker, float(price.group(1)) if price else None
    return "待复核", None


def _transport_not_on_sale(result: ToolResult) -> bool:
    if str(result.metadata.get("travel_source_status") or "").casefold() == "not_on_sale":
        return True
    return any(
        str(payload.get("status") or "").casefold() == "not_on_sale"
        for payload in _json_objects(result.output)
    )


def _result_retryable(result: ToolResult, category: str) -> bool:
    if category not in {"web", "social", "lodging"}:
        return False
    codes = {
        str(result.metadata.get("code") or "").upper(),
        *(str(payload.get("code") or "").upper() for payload in _json_objects(result.output)),
    }
    return not (codes & _STABLE_SOURCE_ERROR_CODES)


def _stable_source_failure(result: ToolResult) -> bool:
    codes = {
        str(result.metadata.get("code") or "").upper(),
        *(str(payload.get("code") or "").upper() for payload in _json_objects(result.output)),
    }
    return bool(codes & _STABLE_SOURCE_ERROR_CODES)


def _result_has_transit_details(tool_name: str, result: ToolResult) -> bool:
    if result.is_error or source_operation(tool_name) != "maps_route":
        return False
    name = str(tool_name or "").casefold()
    if "transit" not in name:
        return False
    output = str(result.output or "")[:_MAX_RESULT_PARSE_CHARS].casefold()
    return all(marker in output for marker in ('"buslines"', '"departure_stop"', '"arrival_stop"'))


def _compact_travel_source_result(
    tool_name: str,
    result: ToolResult,
    *,
    arguments: dict[str, Any] | None = None,
) -> ToolResult:
    """Keep planning facts while dropping verbose map presentation payloads."""

    category = source_category(tool_name)
    if result.is_error:
        return result
    if category == "web":
        rows = _search_result_rows(_json_objects(result.output), "web")
        if not rows:
            rows = _partial_json_array_rows(result.output, "results")
        if rows:
            compact_rows = []
            for item in rows[:5]:
                if not isinstance(item, dict):
                    continue
                compact_item = {
                    key: str(item.get(key) or "")[:1000]
                    for key in ("title", "url")
                    if item.get(key) is not None
                }
                excerpt = _clean_web_excerpt(
                    item.get("content") or item.get("excerpt") or item.get("snippet")
                )
                if excerpt:
                    compact_item["content"] = excerpt
                compact_rows.append(compact_item)
            return ToolResult(
                output=json.dumps(
                    {
                        "query": str(
                            (arguments or {}).get("query")
                            or (arguments or {}).get("search_query")
                            or ""
                        )[:200],
                        "results": compact_rows,
                    },
                    ensure_ascii=False,
                    separators=(",", ":"),
                ),
                is_error=False,
                metadata={**result.metadata, "travel_result_compacted": True},
            )
        return result
    if category == "social":
        rows = _search_result_rows(_json_objects(result.output), "social")
        if rows:
            compact_rows: list[dict[str, Any]] = []
            for item in rows[:6]:
                card = item.get("noteCard") or item.get("note_card")
                card = card if isinstance(card, dict) else {}
                user = card.get("user") if isinstance(card.get("user"), dict) else {}
                note_id = str(item.get("id") or item.get("note_id") or "").strip()
                row = {
                    "id": note_id,
                    "title": str(
                        card.get("displayTitle")
                        or card.get("display_title")
                        or item.get("title")
                        or item.get("name")
                        or ""
                    )[:300],
                    "author": str(
                        user.get("nickname")
                        or user.get("nickName")
                        or item.get("author")
                        or ""
                    )[:120],
                    "source_url": str(
                        item.get("source_url")
                        or item.get("url")
                        or (
                            f"https://www.xiaohongshu.com/explore/{note_id}"
                            if note_id
                            else ""
                        )
                    )[:2000],
                }
                token = str(item.get("xsecToken") or item.get("xsec_token") or "")
                if token:
                    row["xsecToken"] = token[:1000]
                compact_rows.append(row)
            return ToolResult(
                output=json.dumps(
                    {"feeds": compact_rows},
                    ensure_ascii=False,
                    separators=(",", ":"),
                ),
                is_error=False,
                metadata={**result.metadata, "travel_result_compacted": True},
            )
        return result
    if category != "maps":
        return result
    payloads = _json_objects(result.output)
    if not payloads:
        return result
    payload = payloads[0]
    name = str(tool_name or "").casefold()
    compact: dict[str, Any] | None = None
    if "transit" in name and isinstance(payload.get("route"), dict):
        compact = _compact_amap_transit(payload["route"])
    elif any(marker in name for marker in ("maps_text_search", "text_search")):
        pois = payload.get("pois")
        if isinstance(pois, list):
            pois, city_mismatch = _city_scoped_amap_rows(pois, arguments)
            if city_mismatch and not pois:
                return _amap_city_mismatch_result(tool_name, arguments)
            operation = _admission_operation(tool_name, arguments or {})
            if operation == "maps_lodging_search":
                pois = [item for item in pois if _is_lodging_poi(item)]
                if not pois:
                    return _guard_result(
                        "TRAVEL_HOTEL_POI_NOT_FOUND",
                        "AMap returned no actual lodging POI for this hotel query. Retry once "
                        "with types=100000 and a specific mid-range hotel name in the destination city.",
                    )
            else:
                original_count = len(pois)
                pois = [
                    item
                    for item in pois
                    if _is_relevant_non_lodging_poi(item, arguments)
                ]
                if original_count and not pois:
                    return _amap_poi_mismatch_result(tool_name, arguments)
            compact = {
                "pois": [
                    {
                        key: item[key]
                        for key in (
                            "id",
                            "name",
                            "address",
                            "location",
                            "type",
                            "typecode",
                            "pname",
                            "cityname",
                            "adname",
                            "tel",
                        )
                        if key in item
                    }
                    for item in pois[:10]
                    if isinstance(item, dict)
                ]
            }
    elif any(marker in name for marker in ("maps_geo", "geocode")):
        geocodes = payload.get("return")
        if isinstance(geocodes, list):
            geocodes, city_mismatch = _city_scoped_amap_rows(geocodes, arguments)
            if city_mismatch and not geocodes:
                return _amap_city_mismatch_result(tool_name, arguments)
            compact = {
                "return": [
                    {
                        key: item[key]
                        for key in (
                            "country",
                            "province",
                            "city",
                            "district",
                            "street",
                            "number",
                            "adcode",
                            "location",
                            "level",
                        )
                        if key in item
                    }
                    for item in geocodes[:8]
                    if isinstance(item, dict)
                ]
            }
    if compact is None:
        return result
    return ToolResult(
        output=json.dumps(compact, ensure_ascii=False, separators=(",", ":")),
        is_error=result.is_error,
        metadata={**result.metadata, "travel_result_compacted": True},
    )


def _partial_json_array_rows(value: str, key: str) -> list[dict[str, Any]]:
    """Recover complete leading rows when an MCP JSON document was tail-truncated."""

    if not isinstance(value, str) or not value or len(value) > _MAX_RESULT_PARSE_CHARS:
        return []
    marker = re.search(rf'"{re.escape(key)}"\s*:\s*\[', value)
    if marker is None:
        return []
    decoder = json.JSONDecoder()
    offset = marker.end()
    rows: list[dict[str, Any]] = []
    while offset < len(value) and len(rows) < 5:
        while offset < len(value) and (value[offset].isspace() or value[offset] == ","):
            offset += 1
        if offset >= len(value) or value[offset] == "]":
            break
        try:
            item, end = decoder.raw_decode(value, offset)
        except json.JSONDecodeError:
            break
        if isinstance(item, dict):
            rows.append(item)
        offset = max(end, offset + 1)
    return rows


def _is_lodging_poi(value: object) -> bool:
    """Reject bus stops and parking facilities returned for hotel-name searches."""

    if not isinstance(value, dict):
        return False
    typecode = str(value.get("typecode") or "").strip()
    if typecode.startswith("10"):
        return True
    category = " ".join(
        str(value.get(key) or "") for key in ("type", "category", "typename")
    ).casefold()
    return any(
        marker in category
        for marker in ("住宿", "宾馆", "酒店", "旅馆", "客栈", "民宿", "hotel", "hostel")
    )


def _is_relevant_non_lodging_poi(
    value: object,
    arguments: dict[str, Any] | None,
) -> bool:
    """Reject obvious hotel, restaurant, and parking matches for landmark searches."""

    if not isinstance(value, dict):
        return False
    query = str((arguments or {}).get("keywords") or "").casefold()
    if not query.strip():
        return True
    name = str(value.get("name") or "").casefold()
    category = " ".join(
        str(value.get(key) or "")
        for key in ("type", "category", "typename")
    ).casefold()
    typecode = str(value.get("typecode") or "").strip()
    lodging_markers = ("住宿", "宾馆", "酒店", "旅馆", "客栈", "民宿", "hotel", "hostel")
    dining_markers = ("餐厅", "餐馆", "饭店", "美食", "小吃", "咖啡", "茶馆", "restaurant", "cafe")
    parking_markers = ("停车", "停车场", "parking")
    if not any(marker in query for marker in lodging_markers) and (
        typecode.startswith("10")
        or any(marker in f"{name} {category}" for marker in lodging_markers)
    ):
        return False
    if not any(marker in query for marker in dining_markers) and (
        typecode.startswith("05")
        or any(marker in f"{name} {category}" for marker in dining_markers)
    ):
        return False
    if not any(marker in query for marker in parking_markers) and (
        typecode.startswith("1509")
        or any(marker in f"{name} {category}" for marker in parking_markers)
    ):
        return False
    return True


def _city_scoped_amap_rows(
    rows: list[Any],
    arguments: dict[str, Any] | None,
) -> tuple[list[Any], bool]:
    """Keep AMap candidates in the explicitly requested city.

    AMap geocoding can return same-name landmarks from across China even when the
    optional city argument is present. Those rows must not reach the planner as
    plausible coordinates for a destination-city itinerary.
    """

    city = _city_token((arguments or {}).get("city"))
    if not city:
        return rows, False
    aliases = _city_aliases(city)
    matched: list[Any] = []
    saw_explicit_geography = False
    for row in rows:
        if not isinstance(row, dict):
            continue
        explicit_geography = " ".join(
            str(row.get(key) or "")
            for key in (
                "province",
                "city",
                "district",
                "pname",
                "cityname",
                "adname",
            )
        )
        normalized_explicit = re.sub(r"\s+", "", explicit_geography)
        normalized_address = re.sub(r"\s+", "", str(row.get("address") or ""))
        if normalized_explicit:
            saw_explicit_geography = True
        if any(
            alias in normalized_explicit or alias in normalized_address
            for alias in aliases
        ):
            matched.append(row)
        elif not normalized_explicit:
            # Text search is forced to citylimit=true. Some AMap POIs omit all
            # administrative fields and return only a local street/scenic-area address;
            # that absence is not evidence that the row belongs to another city.
            matched.append(row)
    if matched:
        return matched, len(matched) != len(rows)
    return ([] if saw_explicit_geography else rows), saw_explicit_geography


def _city_token(value: object) -> str:
    text = re.sub(r"\s+", "", str(value or "")).strip()
    if len(text) < 2 or text.isdigit():
        return ""
    for suffix in ("特别行政区", "自治州", "地区", "盟", "市"):
        if text.endswith(suffix) and len(text) > len(suffix) + 1:
            return text[: -len(suffix)]
    return text


def _city_aliases(city: str) -> frozenset[str]:
    """Return conservative administrative aliases for known prefecture-level cities."""

    aliases = {city}
    known = {
        "大理": {"大理市", "大理白族自治州", "下关", "洱源", "宾川", "祥云"},
    }
    aliases.update(known.get(city, set()))
    return frozenset(aliases)


def _amap_city_mismatch_result(
    tool_name: str,
    arguments: dict[str, Any] | None,
) -> ToolResult:
    city = str((arguments or {}).get("city") or "").strip()
    return ToolResult(
        output=json.dumps(
            {
                "status": "error",
                "code": "TRAVEL_MAP_CITY_MISMATCH",
                "message": (
                    f"AMap returned only same-name candidates outside {city or 'the requested city'}. "
                    "Use destination-city text search or a more specific address."
                ),
                "retryable": True,
            },
            ensure_ascii=False,
            separators=(",", ":"),
        ),
        is_error=True,
        metadata={
            "code": "TRAVEL_MAP_CITY_MISMATCH",
            "tool_name": tool_name,
            "travel_city": city,
        },
    )


def _amap_poi_mismatch_result(
    tool_name: str,
    arguments: dict[str, Any] | None,
) -> ToolResult:
    query = str((arguments or {}).get("keywords") or "").strip()
    city = str((arguments or {}).get("city") or "").strip()
    return ToolResult(
        output=json.dumps(
            {
                "status": "error",
                "code": "TRAVEL_MAP_POI_MISMATCH",
                "message": (
                    f"AMap results for {query or 'this place'} in {city or 'the requested city'} "
                    "were hotels, restaurants, or parking facilities rather than the requested landmark. "
                    "Retry once with the official landmark, scenic-area, or station name."
                ),
                "retryable": True,
            },
            ensure_ascii=False,
            separators=(",", ":"),
        ),
        is_error=True,
        metadata={
            "code": "TRAVEL_MAP_POI_MISMATCH",
            "tool_name": tool_name,
            "travel_city": city,
            "travel_query": query,
        },
    )


def _definition_name(definition: dict[str, Any]) -> str:
    function = definition.get("function") if isinstance(definition, dict) else None
    if not isinstance(function, dict):
        return ""
    return str(function.get("name") or "").casefold()


def _compact_amap_transit(route: dict[str, Any]) -> dict[str, Any]:
    transits = route.get("transits")
    compact_transits: list[dict[str, Any]] = []
    if isinstance(transits, list):
        for transit in transits[:3]:
            if not isinstance(transit, dict):
                continue
            compact_segments: list[dict[str, Any]] = []
            segments = transit.get("segments")
            if isinstance(segments, list):
                for segment in segments[:12]:
                    if not isinstance(segment, dict):
                        continue
                    compact_segment: dict[str, Any] = {}
                    walking = segment.get("walking")
                    if isinstance(walking, dict):
                        compact_segment["walking"] = {
                            key: walking[key]
                            for key in ("origin", "destination", "distance", "duration")
                            if key in walking
                        }
                    bus = segment.get("bus")
                    buslines = bus.get("buslines") if isinstance(bus, dict) else None
                    if isinstance(buslines, list):
                        compact_segment["bus"] = {
                            "buslines": [
                                _compact_busline(line)
                                for line in buslines[:3]
                                if isinstance(line, dict)
                            ]
                        }
                    if compact_segment:
                        compact_segments.append(compact_segment)
            compact_transits.append(
                {
                    **{
                        key: transit[key]
                        for key in ("duration", "walking_distance", "cost", "distance")
                        if key in transit
                    },
                    "segments": compact_segments,
                }
            )
    return {
        "route": {
            **{
                key: route[key]
                for key in ("origin", "destination", "distance", "taxi_cost")
                if key in route
            },
            "transits": compact_transits,
        }
    }


def _compact_busline(line: dict[str, Any]) -> dict[str, Any]:
    def stop_name(value: object) -> str:
        return str(value.get("name") or "") if isinstance(value, dict) else str(value or "")

    via_stops = line.get("via_stops")
    return {
        **{
            key: line[key]
            for key in ("name", "distance", "duration", "type")
            if key in line
        },
        "departure_stop": {"name": stop_name(line.get("departure_stop"))},
        "arrival_stop": {"name": stop_name(line.get("arrival_stop"))},
        "via_stops": [
            {"name": stop_name(item)}
            for item in via_stops[:30]
        ] if isinstance(via_stops, list) else [],
    }


def _ticket_station_codes(arguments: dict[str, Any]) -> tuple[str, ...]:
    pairs = (
        ("fromStation", "toStation"),
        ("from_station", "to_station"),
        ("departure_station_code", "arrival_station_code"),
    )
    for from_key, to_key in pairs:
        if from_key not in arguments and to_key not in arguments:
            continue
        return tuple(
            str(arguments.get(key) or "").strip().upper()
            for key in (from_key, to_key)
            if str(arguments.get(key) or "").strip()
        )
    return ()


def _station_codes_from_output(output: str) -> set[str]:
    return set(re.findall(r"(?<![A-Z])[A-Z]{3}(?![A-Z])", str(output or "")[:_MAX_RESULT_PARSE_CHARS]))


def _not_on_sale_result(tool_name: str, arguments: dict[str, Any]) -> ToolResult | None:
    if source_operation(tool_name) != "transport_ticket":
        return None
    raw_date = next(
        (
            arguments.get(key)
            for key in ("date", "train_date", "travel_date", "departure_date")
            if arguments.get(key)
        ),
        None,
    )
    try:
        travel_date = date.fromisoformat(str(raw_date))
    except (TypeError, ValueError):
        return None
    today = _china_today()
    latest_sale_date = today + timedelta(days=14)
    if travel_date <= latest_sale_date:
        return None
    sale_open_date = travel_date - timedelta(days=14)
    payload = {
        "status": "not_on_sale",
        "code": "OK",
        "date": travel_date.isoformat(),
        "sale_open_date": sale_open_date.isoformat(),
        "trains": [],
        "message": (
            "The requested travel date is outside the current 12306 advance-sale window. "
            "Retry on or after the sale-open date."
        ),
    }
    return ToolResult(
        output=json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
        metadata={
            "code": "MCP_OK",
            "tool_name": tool_name,
            "travel_source_status": "not_on_sale",
        },
    )


def _normalize_historical_weather_arguments(
    tool_name: str,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    """Map future historical requests to the latest fully past same-date window."""

    if "historical" not in str(tool_name or "").casefold():
        return arguments
    try:
        start = date.fromisoformat(str(arguments.get("start_date") or ""))
        end = date.fromisoformat(str(arguments.get("end_date") or ""))
    except (TypeError, ValueError):
        return arguments
    today = _china_today()
    if end < today:
        return arguments
    shifted_start = start
    shifted_end = end
    while shifted_end >= today:
        shifted_start = _replace_year_safely(shifted_start, shifted_start.year - 1)
        shifted_end = _replace_year_safely(shifted_end, shifted_end.year - 1)
    return {
        **arguments,
        "start_date": shifted_start.isoformat(),
        "end_date": shifted_end.isoformat(),
    }


def _live_forecast_required_result(
    tool_name: str,
    arguments: dict[str, Any],
) -> ToolResult | None:
    """Reject historical fallback while the requested dates remain forecastable."""

    if "historical" not in str(tool_name or "").casefold():
        return None
    try:
        start = date.fromisoformat(str(arguments.get("start_date") or ""))
        end = date.fromisoformat(str(arguments.get("end_date") or ""))
    except (TypeError, ValueError):
        return None
    today = _china_today()
    if start < today or end > today + timedelta(days=16):
        return None
    return _guard_result(
        "TRAVEL_LIVE_FORECAST_REQUIRED",
        "The trip is inside the live forecast window. Call get_forecast with the "
        "verified destination coordinates; do not replace it with historical weather.",
    )


def _normalize_amap_text_search_arguments(
    tool_name: str,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    """Use the city-strict flag supported by AMap MCP 0.0.8 but omitted from its schema."""

    name = str(tool_name or "").casefold()
    city = str(arguments.get("city") or "").strip()
    if not city or not any(marker in name for marker in ("maps_text_search", "text_search")):
        return arguments
    return {**arguments, "citylimit": "true"}


def _xhs_query_anchors(value: str) -> list[str]:
    """Extract destination/attraction anchors from a model-composed XHS query."""

    generic = {
        "旅行",
        "旅游",
        "攻略",
        "避坑",
        "公共交通",
        "交通",
        "住宿",
        "酒店",
        "推荐",
        "自由行",
        "行程",
    }
    normalized = re.sub(r"(\d+)\s*(?:日|天|月)", " ", str(value or ""))
    pieces = re.split(r"[\s,，、/|+]+", normalized)
    anchors: list[str] = []
    for piece in pieces:
        item = piece.strip("-—·:：;；()（）[]【】")
        for suffix in ("旅游攻略", "旅行攻略", "游玩攻略", "避坑攻略", "攻略", "避坑"):
            if item.endswith(suffix) and len(item) > len(suffix):
                item = item[: -len(suffix)].strip()
                break
        if not item or item in generic or item.isdigit() or item in anchors:
            continue
        anchors.append(item[:30])
    return anchors[:8]


_ATTRACTION_NAME_MARKERS = (
    "山",
    "寺",
    "庙",
    "宫",
    "观",
    "塔",
    "湖",
    "河",
    "峡",
    "谷",
    "瀑布",
    "石窟",
    "古城",
    "古镇",
    "故居",
    "遗址",
    "景区",
    "公园",
    "博物馆",
    "美术馆",
    "纪念馆",
    "动物园",
    "植物园",
    "乐园",
)
_NON_ATTRACTION_TYPE_MARKERS = (
    "酒店",
    "宾馆",
    "民宿",
    "公寓",
    "火车站",
    "高铁站",
    "汽车站",
    "机场",
    "停车场",
)


def _first_specific_map_attraction(
    map_pois: list[dict[str, Any]], destination: str
) -> str:
    for poi in map_pois:
        name = str(poi.get("name") or "").strip()
        poi_type = str(poi.get("type") or "").strip()
        if not name or name == destination:
            continue
        combined = f"{name} {poi_type}"
        if any(marker in combined for marker in _NON_ATTRACTION_TYPE_MARKERS):
            continue
        if _is_specific_attraction_anchor(name, destination, map_pois):
            return name[:30]
    return ""


def _is_specific_attraction_anchor(
    anchor: str,
    destination: str,
    map_pois: list[dict[str, Any]],
) -> bool:
    normalized = str(anchor or "").strip()
    if not normalized or normalized == str(destination or "").strip():
        return False
    if any(
        normalized == str(poi.get("name") or "").strip()
        for poi in map_pois
        if isinstance(poi, dict)
    ):
        return True
    return any(marker in normalized for marker in _ATTRACTION_NAME_MARKERS)


def _social_result_is_real_empty(result: ToolResult) -> bool:
    if result.is_error:
        return False
    code = str(result.metadata.get("code") or "").upper()
    if code and code not in {"OK", "MCP_OK"}:
        return False
    payloads = _json_objects(result.output)
    if not payloads:
        return False
    for payload in payloads:
        status = str(payload.get("status") or "").casefold()
        payload_code = str(payload.get("code") or "").upper()
        if status in {"error", "failed", "failure"}:
            return False
        if payload_code and payload_code not in {"OK", "MCP_OK"}:
            return False
    rows = _search_result_rows(payloads, "social")
    has_explicit_collection = any(
        isinstance(payload.get(key), list)
        for payload in payloads
        for key in ("feeds", "notes", "items", "results")
    )
    return has_explicit_collection and not rows


def _amap_min_interval_seconds() -> float:
    try:
        value = float(os.getenv("AMAP_TRAVEL_MIN_INTERVAL_SECONDS", "0.35"))
    except ValueError:
        value = 0.35
    return min(max(value, 0.1), 3.0)


def _amap_qps_exceeded(result: ToolResult) -> bool:
    return "CUQPS_HAS_EXCEEDED_THE_LIMIT" in str(result.output or "").upper()


def _replace_year_safely(value: date, year: int) -> date:
    try:
        return value.replace(year=year)
    except ValueError:
        return value.replace(year=year, day=28)


def _china_today() -> date:
    from datetime import datetime

    return datetime.now(ZoneInfo("Asia/Shanghai")).date()


def _has_search_rows(payloads: list[dict[str, Any]], category: str) -> bool:
    keys = (
        ("results", "items")
        if category == "web"
        else ("feeds", "notes", "items", "results")
    )
    return any(
        isinstance(payload.get(key), list) and bool(payload[key])
        for payload in payloads
        for key in keys
    )


def _json_objects(value: str) -> list[dict[str, Any]]:
    if not isinstance(value, str) or not value.strip() or len(value) > _MAX_RESULT_PARSE_CHARS:
        return []
    roots: list[dict[str, Any]] = []
    decoder = json.JSONDecoder()
    offset = 0
    while offset < len(value) and len(roots) < 8:
        while offset < len(value) and value[offset].isspace():
            offset += 1
        if offset >= len(value):
            break
        try:
            parsed, end = decoder.raw_decode(value, offset)
        except json.JSONDecodeError:
            next_object = value.find("{", offset + 1)
            if next_object < 0:
                break
            offset = next_object
            continue
        if isinstance(parsed, dict):
            roots.append(parsed)
        offset = max(end, offset + 1)
    expanded: list[dict[str, Any]] = []
    queue: list[tuple[dict[str, Any], int]] = [(item, 0) for item in roots]
    while queue and len(expanded) < 24:
        current, depth = queue.pop(0)
        expanded.append(current)
        if depth >= 4:
            continue
        for key in ("data", "result", "content", "text", "payload", "output"):
            nested = current.get(key)
            if isinstance(nested, dict):
                queue.append((nested, depth + 1))
            elif isinstance(nested, str) and len(nested) <= _MAX_RESULT_PARSE_CHARS:
                queue.extend((item, depth + 1) for item in _json_objects(nested)[:4])
    return expanded


def _safe_search_evidence(category: str, result: ToolResult) -> list[dict[str, Any]]:
    payloads = _json_objects(str(result.output or ""))
    rows = _search_result_rows(payloads, category)
    retrieved_at = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    evidence: list[dict[str, Any]] = []
    seen_urls: set[str] = set()
    for row in rows:
        item = _safe_search_evidence_item(category, row, retrieved_at)
        source_url = str(item.get("source_url") or "") if item else ""
        if not item or not source_url or source_url in seen_urls:
            continue
        seen_urls.add(source_url)
        evidence.append(item)
        if len(evidence) >= 5:
            break
    return evidence


def _search_result_rows(
    payloads: list[dict[str, Any]], category: str
) -> list[dict[str, Any]]:
    keys = ("results", "items") if category == "web" else ("feeds", "notes", "items", "results")
    for payload in payloads:
        for key in keys:
            rows = payload.get(key)
            if isinstance(rows, list) and rows:
                return [row for row in rows if isinstance(row, dict)]
    return []


def _safe_search_evidence_item(
    category: str,
    row: dict[str, Any],
    retrieved_at: str,
) -> dict[str, Any] | None:
    if category == "web":
        title = str(row.get("title") or row.get("name") or "").strip()
        source_url = str(row.get("url") or row.get("source_url") or "").strip()
        excerpt = _clean_web_excerpt(
            row.get("content") or row.get("excerpt") or row.get("snippet") or title
        ) or title
        provider = "Tavily"
        source_type = "web_article"
        confidence = 0.7
    else:
        card = row.get("noteCard") or row.get("note_card")
        card = card if isinstance(card, dict) else {}
        title = str(
            card.get("displayTitle")
            or card.get("display_title")
            or row.get("title")
            or row.get("name")
            or ""
        ).strip()
        note_id = str(row.get("id") or row.get("note_id") or "").strip()
        source_url = str(row.get("url") or row.get("source_url") or "").strip()
        if not source_url and note_id:
            source_url = f"https://www.xiaohongshu.com/explore/{note_id}"
        user = card.get("user") if isinstance(card.get("user"), dict) else {}
        nickname = str(user.get("nickname") or user.get("nickName") or "").strip()
        excerpt = f"{nickname}发布的公开旅行经验" if nickname else title
        provider = "小红书只读"
        source_type = "social_post"
        confidence = 0.55
    source_url = _credential_free_source_url(source_url)
    if not title or not source_url:
        return None
    excerpt = " ".join(excerpt.split())[:500]
    identity = hashlib.sha256(f"{category}\0{source_url}".encode()).hexdigest()[:16]
    return {
        "evidence_id": f"ev-{category}-{identity}",
        "source_type": source_type,
        "provider": provider,
        "title": title[:300],
        "source_url": source_url[:2000],
        "published_at": "",
        "retrieved_at": retrieved_at,
        "data_as_of": retrieved_at,
        "excerpt": excerpt,
        "facts": [],
        "confidence": confidence,
        "freshness": "snapshot",
        "content_hash": hashlib.sha256(excerpt.encode()).hexdigest(),
    }


def _clean_web_excerpt(value: object) -> str:
    """Keep short Chinese travel facts and drop platform/English boilerplate."""

    text = re.sub(r"<[^>]+>", " ", str(value or ""))
    text = re.sub(r"[#*_`>|]+", " ", text)
    text = " ".join(text.split())
    if not text:
        return ""
    chunks = re.split(r"(?<=[。！？.!?；;])\s*|\s{2,}|(?=##)", text)
    kept: list[str] = []
    for chunk in chunks:
        candidate = chunk.strip(" -—·:：;；")
        if not candidate:
            continue
        chinese_count = len(re.findall(r"[\u3400-\u9fff]", candidate))
        latin_count = len(re.findall(r"[A-Za-z]", candidate))
        lowered = candidate.casefold()
        if any(
            marker in lowered
            for marker in (
                "all reactions",
                "like comment",
                "subscribe",
                "sign in",
                "privacy policy",
                "cookie policy",
            )
        ):
            continue
        if chinese_count < 6 or latin_count > chinese_count * 2:
            continue
        kept.append(candidate)
        if sum(len(item) for item in kept) >= 240:
            break
    return " ".join(kept)[:300]


def _credential_free_source_url(value: str) -> str:
    """Keep a usable public citation while removing credential-like query fields."""

    try:
        parsed = urlsplit(str(value or "").strip())
    except ValueError:
        return ""
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return ""
    safe_query = urlencode(
        [
            (key, item)
            for key, item in parse_qsl(parsed.query, keep_blank_values=True)
            if not any(part in key.casefold() for part in _SENSITIVE_URL_KEY_PARTS)
        ],
        doseq=True,
    )
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, safe_query, parsed.fragment))


def _call_fingerprint(tool_name: str, arguments: dict[str, Any]) -> str:
    try:
        canonical = json.dumps(
            arguments,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        )
    except (TypeError, ValueError):
        canonical = repr(arguments)
    payload = f"{str(tool_name).casefold()}\0{canonical}".encode("utf-8", errors="replace")
    return hashlib.sha256(payload).hexdigest()


def _guard_result(code: str, message: str) -> ToolResult:
    return ToolResult(
        output=json.dumps(
            {"status": "error", "code": code, "message": message, "retryable": False},
            ensure_ascii=False,
            separators=(",", ":"),
        ),
        is_error=True,
        metadata={
            "code": code,
            "travel_progress_visibility": "internal",
        },
    )


__all__ = [
    "TRAVEL_SOURCE_CATEGORIES",
    "TravelSourceLedger",
    "TravelSourceSnapshot",
    "TravelGuardedTool",
    "TravelFinalizationRequiredToolProvider",
    "TravelResearchRequiredToolProvider",
    "guard_travel_tools",
    "require_travel_finalization_before_saving",
    "require_travel_research_before_solving",
    "source_category",
    "source_operation",
    "preferred_travel_tool_names",
]
