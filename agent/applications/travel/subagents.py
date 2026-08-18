"""Travel-owned bounded Subagent profile assembled at the application boundary."""

from __future__ import annotations

import json
import re
from copy import deepcopy
from types import MappingProxyType
from typing import Any, Callable

from agent.protocols.subagent import SubagentProfile
from agent.protocols.tool import ToolExecutionContext, ToolProvider, ToolResult
from agent.subagents.config import SubagentConfig

TRAVEL_TRANSPORT_WEATHER_PROFILE = "travel-transport-weather"
TRAVEL_STAY_POI_PROFILE = "travel-stay-poi"
TRAVEL_GUIDES_PROFILE = "travel-guides"
TRAVEL_FINAL_STAY_PROFILE = "travel-final-stay"
TRAVEL_FINAL_ROUTE_PROFILE = "travel-final-route"
TRAVEL_FINAL_WEATHER_PROFILE = "travel-final-weather"
TRAVEL_CANDIDATE_RESEARCH_PROFILES = frozenset(
    {
        TRAVEL_TRANSPORT_WEATHER_PROFILE,
        TRAVEL_STAY_POI_PROFILE,
        TRAVEL_GUIDES_PROFILE,
    }
)
TRAVEL_FINALIZATION_PROFILES = frozenset(
    {TRAVEL_FINAL_STAY_PROFILE, TRAVEL_FINAL_ROUTE_PROFILE}
)
TRAVEL_FINALIZATION_REPAIR_PROFILES = frozenset({TRAVEL_FINAL_WEATHER_PROFILE})
TRAVEL_RESEARCH_PROFILES = (
    TRAVEL_CANDIDATE_RESEARCH_PROFILES
    | TRAVEL_FINALIZATION_PROFILES
    | TRAVEL_FINALIZATION_REPAIR_PROFILES
)
_AMAP_TRANSIT_TOOL = "mcp__amap-maps__maps_direction_transit_integrated"
_AMAP_DRIVING_TOOL = "mcp__amap-maps__maps_direction_driving"
_MAX_TRANSIT_VIA_STOPS = 8


class TravelExactDelegationToolProvider:
    """Require every fixed travel lane in one batch before creating any child."""

    def __init__(
        self,
        delegate: ToolProvider,
        *,
        expected_profiles: frozenset[str],
        final_stay_context: str = "",
        final_weather_context: str = "",
        on_profiles_completed: Callable[[frozenset[str]], None] | None = None,
    ) -> None:
        self._delegate = delegate
        self._expected_profiles = expected_profiles
        self._final_stay_context = final_stay_context
        self._final_weather_context = final_weather_context
        self._on_profiles_completed = on_profiles_completed

    def definitions(self) -> list[dict[str, Any]]:
        return self._delegate.definitions()

    def execute(self, name: str, args: dict[str, Any]) -> ToolResult:
        guarded = self._guard(name, args)
        if guarded is not None:
            return guarded
        enriched = self._enrich(name, args)
        result = self._delegate.execute(name, enriched)
        self._record_completed_profiles(name, enriched, result)
        return result

    def execute_with_context(
        self,
        name: str,
        args: dict[str, Any],
        context: ToolExecutionContext,
    ) -> ToolResult:
        guarded = self._guard(name, args)
        if guarded is not None:
            return guarded
        enriched = self._enrich(name, args)
        contextual = getattr(self._delegate, "execute_with_context", None)
        if callable(contextual):
            result = contextual(name, enriched, context)
        else:
            result = self._delegate.execute(name, enriched)
        self._record_completed_profiles(name, enriched, result)
        return result

    def _record_completed_profiles(
        self,
        name: str,
        args: dict[str, Any],
        result: ToolResult,
    ) -> None:
        if (
            str(name).casefold() != "delegate_tasks"
            or self._on_profiles_completed is None
        ):
            return
        profiles = _completed_delegation_profiles(args, result)
        if profiles:
            self._on_profiles_completed(profiles)

    def _enrich(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
        if str(name).casefold() != "delegate_tasks":
            return args
        enriched = deepcopy(args)
        for task in enriched.get("tasks") or []:
            if not isinstance(task, dict):
                continue
            if task.get("profile") == TRAVEL_FINAL_STAY_PROFILE and self._final_stay_context:
                task["task"] = (
                    str(task.get("task") or "")
                    + "\n服务端已附上本 Session 候选阶段的真实携程观察。若本轮 dated 酒店查询失败，"
                    "必须从这些候选中选择有观察价的准确酒店名，继续用高德查询该精确名称。若首个"
                    "酒店在高德无严格同名酒店 POI，最多再尝试五个不同的携程准确酒店名，优先普通"
                    "品牌名而非含特殊符号的民宿名；选择首个高德可核验项，不得改写别名，也不得因为"
                    "重查失败而丢弃全部已有住宿候选：\n"
                    + self._final_stay_context
                )
            if task.get("profile") == TRAVEL_FINAL_WEATHER_PROFILE and self._final_weather_context:
                task["task"] = (
                    str(task.get("task") or "")
                    + "\n以下服务端日期窗口事实不可由模型改写。先解析目的地坐标，再仅调用一次"
                    " get_forecast；禁止改查 historical weather：\n"
                    + self._final_weather_context
                )
        return enriched

    def _guard(self, name: str, args: dict[str, Any]) -> ToolResult | None:
        if str(name).casefold() != "delegate_tasks":
            return None
        tasks = args.get("tasks") if isinstance(args, dict) else None
        profiles = [
            str(task.get("profile") or "").strip()
            for task in tasks
            if isinstance(task, dict)
        ] if isinstance(tasks, list) else []
        if (
            len(profiles) == len(self._expected_profiles)
            and len(set(profiles)) == len(profiles)
            and set(profiles) == self._expected_profiles
        ):
            return None
        expected = sorted(self._expected_profiles)
        return ToolResult(
            output=json.dumps(
                {
                    "status": "error",
                    "code": "TRAVEL_SUBAGENT_BATCH_INVALID",
                    "message": (
                        "Travel research must delegate one task for every required profile "
                        f"in the same batch: {', '.join(expected)}."
                    ),
                    "expected_profiles": expected,
                },
                ensure_ascii=False,
                separators=(",", ":"),
            ),
            is_error=True,
            metadata={
                "code": "TRAVEL_SUBAGENT_BATCH_INVALID",
                "travel_progress_visibility": "internal",
            },
        )


class TravelFinalRouteResultToolProvider:
    """Compact verbose AMap transit/driving payloads before the route Child sees them."""

    def __init__(self, delegate: ToolProvider) -> None:
        self._delegate = delegate

    def definitions(self) -> list[dict[str, Any]]:
        return self._delegate.definitions()

    def execute(self, name: str, args: dict[str, Any]) -> ToolResult:
        return _compact_final_route_result(name, self._delegate.execute(name, args))

    def execute_with_context(
        self,
        name: str,
        args: dict[str, Any],
        context: ToolExecutionContext,
    ) -> ToolResult:
        contextual = getattr(self._delegate, "execute_with_context", None)
        result = (
            contextual(name, args, context)
            if callable(contextual)
            else self._delegate.execute(name, args)
        )
        return _compact_final_route_result(name, result)


def compact_travel_final_route_results(provider: ToolProvider) -> ToolProvider:
    """Return the travel-final-route result projector without widening Tool access."""

    return TravelFinalRouteResultToolProvider(provider)


def require_exact_travel_delegation(
    provider: ToolProvider,
    *,
    finalization: bool,
    final_stay_context: str = "",
    final_weather_context: str = "",
    expected_profiles: frozenset[str] | None = None,
    on_profiles_completed: Callable[[frozenset[str]], None] | None = None,
) -> ToolProvider:
    """Enforce the persisted travel stage's complete fixed-lane batch."""

    return TravelExactDelegationToolProvider(
        provider,
        expected_profiles=expected_profiles or (
            TRAVEL_FINALIZATION_PROFILES if finalization else TRAVEL_CANDIDATE_RESEARCH_PROFILES
        ),
        final_stay_context=final_stay_context,
        final_weather_context=final_weather_context,
        on_profiles_completed=on_profiles_completed,
    )


def _completed_delegation_profiles(
    args: dict[str, Any],
    result: ToolResult,
) -> frozenset[str]:
    """Return only profiles whose delegated task completed with an explicit OK."""

    tasks = args.get("tasks") if isinstance(args, dict) else None
    if not isinstance(tasks, list):
        return frozenset()
    task_profiles = {
        str(task.get("id") or "").strip(): str(task.get("profile") or "").strip()
        for task in tasks
        if isinstance(task, dict)
        and str(task.get("id") or "").strip()
        and str(task.get("profile") or "").strip()
    }
    try:
        payload = json.loads(result.output)
    except (TypeError, ValueError, json.JSONDecodeError):
        return frozenset()
    rows = payload.get("results") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        return frozenset()
    return frozenset(
        task_profiles[task_id]
        for row in rows
        if isinstance(row, dict)
        and (task_id := str(row.get("id") or "").strip()) in task_profiles
        and str(row.get("status") or "").casefold() == "completed"
        and str(row.get("code") or "").upper() == "OK"
    )


def with_travel_research_profile(config: SubagentConfig) -> SubagentConfig:
    """Add the built-in read-only travel profile without overriding operator policy."""

    if not config.enabled:
        return config
    profiles = dict(config.profiles)
    builtins = (
        SubagentProfile(
            name=TRAVEL_TRANSPORT_WEATHER_PROFILE,
            description=(
                "Query both railway applicability and weather before returning. Use the city "
                "station-code lookup once. Only when both endpoints have verified station codes, "
                "query outbound and return tickets once each. If either endpoint "
                "has no returned station code, report railway as not applicable and preserve "
                "road-coach, driving, or hired-car fallback assumptions instead of guessing a "
                "station code or omitting the transport conclusion. Always call one weather "
                "Tool. Resolve the destination city coordinates with the "
                "city-scoped AMap geocoder first and pass those coordinates directly to the "
                "weather endpoint; use Open-Meteo place geocoding only as a fallback, and reject "
                "same-name places whose province or country conflicts with the requested destination. "
                "When forecast is unavailable or outside its window, "
                "call historical weather for the previous year's same month/day range and label "
                "it historical; never pass future dates to a historical endpoint. The station-code and ticket "
                "tools are already active: a station-code success never replaces both dated "
                "ticket calls. Preserve not_on_sale and sale_open_date exactly. Never substitute "
                "model memory."
            ),
            tools=(
                "mcp__12306__get-station-code-of-citys",
                "mcp__12306__get-tickets",
                "mcp__amap-maps__maps_geo",
                "mcp__open-meteo__geocode_place",
                "mcp__open-meteo__get_forecast",
                "mcp__open-meteo__get_historical_weather",
            ),
            initial_tools=(
                "mcp__12306__get-station-code-of-citys",
                "mcp__12306__get-tickets",
                "mcp__amap-maps__maps_geo",
                "mcp__open-meteo__geocode_place",
                "mcp__open-meteo__get_forecast",
                "mcp__open-meteo__get_historical_weather",
            ),
            denied_tools=("delegate_tasks",),
            workspace_mode="shared_readonly",
            max_tool_iterations=6,
            timeout_seconds=120,
            max_result_chars=8_000,
            allow_model_invocation=True,
            model_role="fast",
        ),
        SubagentProfile(
            name=TRAVEL_STAY_POI_PROFILE,
            description=(
                "Collect candidate-stage dated hotel prices and destination-city POIs. Call "
                "search_travel_hotels exactly once for the city, dates, occupancy, and price cap; "
                "do not repeat it per attraction. Use only city-scoped AMap text/detail search, "
                "keep at most the best match per requested place. After one narrower official-name "
                "retry for a missing POI, keep the available partial result instead of cycling "
                "through more aliases, then return immediately."
            ),
            tools=(
                "mcp__amap-maps__maps_text_search",
                "mcp__amap-maps__maps_search_detail",
                "search_travel_hotels",
            ),
            initial_tools=(
                "mcp__amap-maps__maps_text_search",
                "mcp__amap-maps__maps_search_detail",
                "search_travel_hotels",
            ),
            denied_tools=("delegate_tasks",),
            workspace_mode="shared_readonly",
            max_tool_iterations=6,
            timeout_seconds=150,
            max_result_chars=8_000,
            allow_model_invocation=True,
            model_role="fast",
        ),
        SubagentProfile(
            name=TRAVEL_GUIDES_PROFILE,
            description=(
                "Query Tavily once and Xiaohongshu once for concise travel tips. The first "
                "Xiaohongshu keyword must be exactly the broad destination plus 旅游攻略, for "
                "example 河南旅游攻略; do not add dates, transport, lodging, 避坑, or multiple "
                "cities. Only when that search returns a real empty row set, retry Xiaohongshu "
                "exactly once with one attraction plus 攻略, for example 老君山攻略. Never retry "
                "with a province or city name: 洛阳攻略 is not an attraction-level retry. Use a "
                "concrete attraction already named by the user or returned by the map lane; if "
                "none is available, do not make the second call. Never retry "
                "after usable rows, authentication failure, timeout, or rate limiting. Keep "
                "Xiaohongshu max_results at or below six. "
                "Only when a selected Xiaohongshu result lacks a usable summary may one bounded detail call "
                "follow. Return filtered titles, URLs, and short summaries; do not retry a source "
                "that already produced usable rows and do not query other lanes."
            ),
            tools=(
                "mcp__tavily__tavily_search",
                "mcp__xhs-readonly__search_notes",
                "mcp__xhs-readonly__get_note_detail",
            ),
            initial_tools=(
                "mcp__tavily__tavily_search",
                "mcp__xhs-readonly__search_notes",
                "mcp__xhs-readonly__get_note_detail",
            ),
            denied_tools=("delegate_tasks",),
            workspace_mode="shared_readonly",
            max_tool_iterations=4,
            timeout_seconds=180,
            max_result_chars=8_000,
            allow_model_invocation=True,
            model_role="fast",
        ),
        SubagentProfile(
            name=TRAVEL_FINAL_STAY_PROFILE,
            description=(
                "After candidate selection, resolve every distinct overnight area in the selected "
                "itinerary. Call search_travel_hotels once per distinct overnight city or area, "
                "with that stay's exact check-in/check-out dates, without a generic preference "
                "phrase as keyword, and use the selected candidate budget or "
                "price cap when available. Choose a dated result whose observed price fits the "
                "trip budget; do not default to a chain brand or invent a cheap price for an "
                "expensive branch. Then use city-scoped AMap text search with types=100000 for "
                "that exact returned hotel name. Never search a bare landmark name and never "
                "accept a bus stop or parking facility as a hotel. Keep its name, address, and "
                "location; call detail only if location is missing. These exact "
                "tools are already active; do not spend an iteration rediscovering aliases. If "
                "dated prices are unavailable, return the concrete identity plus an explicit "
                "planning-estimate gap. If the current dated hotel query fails but the task "
                "includes server-verified candidate_hotel_observations, choose one exact observed "
                "hotel name from that bounded list and still complete its AMap identity lookup. "
                "If the first exact hotel has no strict AMap lodging POI, try other distinct "
                "observed hotel names and keep the first verifiable identity. Return one compact "
                "record per overnight area; never cover a multi-area itinerary with only the "
                "destination-city hotel."
            ),
            tools=(
                "mcp__amap-maps__maps_text_search",
                "mcp__amap-maps__maps_search_detail",
                "search_travel_hotels",
            ),
            initial_tools=(
                "mcp__amap-maps__maps_text_search",
                "mcp__amap-maps__maps_search_detail",
                "search_travel_hotels",
            ),
            denied_tools=("delegate_tasks",),
            workspace_mode="shared_readonly",
            max_tool_iterations=10,
            timeout_seconds=240,
            max_result_chars=8_000,
            allow_model_invocation=True,
            model_role="fast",
        ),
        SubagentProfile(
            name=TRAVEL_FINAL_ROUTE_PROFILE,
            description=(
                "After candidate selection, resolve every selected-itinerary local route segment "
                "of at least 2 km that uses public transit or has an unresolved mode, including "
                "station-to-hotel and hotel-to-station transfers as well as destination-city POI "
                "coordinates. Use complete AMap public-transit segments required by the selected itinerary. Search "
                "POIs with an explicit city, reject same-name results from other cities, keep line "
                "names and boarding/alighting stops, and include both outbound and return legs for "
                "remote attractions. The exact map tools are already active; do not rediscover them. "
                "Reuse coordinates supplied in the task. For missing anchors, issue one parallel "
                "city-scoped text-search batch, accept the first valid in-city POI, and never retry "
                "an equivalent place name or geocode every anchor in bulk. Then issue all remaining "
                "transit calls in one parallel batch. When AMap returns transits=[] for a "
                "remote attraction, do not keep querying the same scenic-area center and do "
                "not invent a bus line. Search once for a clearly related reachable anchor in "
                "the same district, preferring the attraction's 游客中心, 主入口, 售票处, or "
                "景区接驳点, then retry transit to that anchor. If the reachable anchor still "
                "has no transit, call AMap driving exactly once for that segment and return a "
                "transparent taxi/ride-hailing fallback with verified distance and duration. "
                "A driving fallback is not public transit and must not contain fabricated line "
                "or stop fields. Then return immediately. "
                "Return one compact structured summary keyed by each from/to pair only after every selected-candidate route "
                "segment of at least 2 km has evidence. Keep only the best transit option per "
                "segment, cap via stops at eight representative names, omit raw paths and repeated "
                "alternatives, and keep the final JSON under 8000 characters. The runtime already "
                "compacts every AMap transit result, so reuse those compact rows directly instead "
                "of restating raw responses. Never research "
                "hotels, weather, railway, web, or social sources."
            ),
            tools=(
                "mcp__amap-maps__maps_text_search",
                "mcp__amap-maps__maps_search_detail",
                "mcp__amap-maps__maps_geo",
                "mcp__amap-maps__maps_direction_transit_integrated",
                "mcp__amap-maps__maps_direction_driving",
            ),
            initial_tools=(
                "mcp__amap-maps__maps_text_search",
                "mcp__amap-maps__maps_search_detail",
                "mcp__amap-maps__maps_geo",
                "mcp__amap-maps__maps_direction_transit_integrated",
                "mcp__amap-maps__maps_direction_driving",
            ),
            denied_tools=("delegate_tasks",),
            workspace_mode="shared_readonly",
            max_tool_iterations=12,
            timeout_seconds=150,
            max_result_chars=8_000,
            allow_model_invocation=True,
            model_role="fast",
        ),
        SubagentProfile(
            name=TRAVEL_FINAL_WEATHER_PROFILE,
            description=(
                "Repair only a finalizer-requested live forecast gap. Resolve the supplied "
                "destination with geocode_place once, then call get_forecast exactly once for "
                "the server-supplied start and end dates. Never call historical weather, "
                "railway, maps, lodging, web, or social tools. Preserve provider, retrieved_at, "
                "freshness=live, data_as_of, and daily values, then return immediately."
            ),
            tools=(
                "mcp__open-meteo__geocode_place",
                "mcp__open-meteo__get_forecast",
            ),
            initial_tools=(
                "mcp__open-meteo__geocode_place",
                "mcp__open-meteo__get_forecast",
            ),
            denied_tools=("delegate_tasks",),
            workspace_mode="shared_readonly",
            max_tool_iterations=3,
            timeout_seconds=90,
            max_result_chars=8_000,
            allow_model_invocation=True,
            model_role="fast",
        ),
    )
    changed = False
    for profile in builtins:
        if profile.name not in profiles:
            profiles[profile.name] = profile
            changed = True
    if not changed:
        return config
    return SubagentConfig(
        enabled=config.enabled,
        max_parallel=min(3, config.max_parallel),
        max_tasks_per_call=min(3, config.max_tasks_per_call),
        max_depth=config.max_depth,
        max_subagents_per_parent_turn=min(3, config.max_subagents_per_parent_turn),
        max_batches_per_parent_turn=1,
        max_batch_result_chars=config.max_batch_result_chars,
        profiles=MappingProxyType(profiles),
    )


def _compact_final_route_result(name: str, result: ToolResult) -> ToolResult:
    if name == _AMAP_DRIVING_TOOL and not result.is_error:
        return _compact_driving_result(result)
    if name != _AMAP_TRANSIT_TOOL or result.is_error:
        return result
    try:
        payload = json.loads(result.output)
    except (TypeError, ValueError, json.JSONDecodeError):
        payload = _truncated_amap_route_payload(result.output)
    if not isinstance(payload, dict):
        return result
    route = payload.get("route")
    if not isinstance(route, dict):
        return result
    transits = route.get("transits")
    if not isinstance(transits, list):
        return result
    candidates = [
        transit
        for transit in transits
        if isinstance(transit, dict) and _transit_buslines(transit)
    ]
    if not candidates:
        return result
    walkable = [
        transit
        for transit in candidates
        if _non_negative_int(transit.get("walking_distance"), 2_001) <= 2_000
    ]
    best = min(walkable or candidates, key=_transit_sort_key)
    compact_segments: list[dict[str, Any]] = []
    for segment in best.get("segments") or []:
        if not isinstance(segment, dict):
            continue
        bus = segment.get("bus")
        buslines = bus.get("buslines") if isinstance(bus, dict) else None
        compact_lines = [
            compact
            for line in buslines or []
            if isinstance(line, dict) and (compact := _compact_busline(line)) is not None
        ]
        if compact_lines:
            compact_segments.append({"bus": {"buslines": compact_lines}})
    compact_route = {
        key: route[key]
        for key in ("origin", "destination", "distance", "taxi_cost")
        if route.get(key) not in (None, "")
    }
    compact_transit = {
        key: best[key]
        for key in ("duration", "walking_distance", "cost")
        if best.get(key) not in (None, "")
    }
    compact_transit["segments"] = compact_segments
    compact_route["transits"] = [compact_transit]
    compact_output = json.dumps(
        {"route": compact_route},
        ensure_ascii=False,
        separators=(",", ":"),
    )
    if len(compact_output) >= len(result.output):
        return result
    return ToolResult(
        output=compact_output,
        is_error=False,
        metadata={
            **result.metadata,
            "travel_route_compacted": True,
            "original_output_chars": len(result.output),
        },
    )


def _compact_driving_result(result: ToolResult) -> ToolResult:
    """Keep only the best AMap road route facts needed for a transparent fallback."""

    try:
        payload = json.loads(result.output)
    except (TypeError, ValueError, json.JSONDecodeError):
        return result
    if not isinstance(payload, dict):
        return result
    route = payload.get("route")
    if not isinstance(route, dict):
        return result
    paths = route.get("paths")
    if not isinstance(paths, list):
        return result
    candidates = [path for path in paths if isinstance(path, dict)]
    if not candidates:
        return result
    best = min(
        candidates,
        key=lambda path: (
            _non_negative_int(path.get("duration"), 2**31 - 1),
            _non_negative_int(path.get("distance"), 2**31 - 1),
        ),
    )
    compact_route = {
        key: route[key]
        for key in ("origin", "destination", "taxi_cost")
        if route.get(key) not in (None, "")
    }
    compact_path = {
        key: best[key]
        for key in ("distance", "duration", "strategy", "tolls", "toll_distance")
        if best.get(key) not in (None, "")
    }
    compact_route["paths"] = [compact_path]
    compact_output = json.dumps(
        {"route": compact_route},
        ensure_ascii=False,
        separators=(",", ":"),
    )
    if len(compact_output) >= len(result.output):
        return result
    return ToolResult(
        output=compact_output,
        is_error=False,
        metadata={
            **result.metadata,
            "travel_route_compacted": True,
            "original_output_chars": len(result.output),
        },
    )


def _truncated_amap_route_payload(output: str) -> dict[str, Any]:
    """Recover complete leading transit objects from an MCP-truncated JSON document."""

    if not isinstance(output, str):
        return {}
    marker = re.search(r'"transits"\s*:\s*\[', output)
    if marker is None:
        return {}
    route_prefix = output[: marker.start()]
    route = {
        key: value
        for key in ("origin", "destination", "distance", "taxi_cost")
        if (value := _first_json_string_field(route_prefix, key))
    }
    transits: list[dict[str, Any]] = []
    cursor = marker.end()
    while cursor < len(output) and len(transits) < 8:
        start = output.find("{", cursor)
        if start < 0:
            break
        fragment, end = _balanced_json_object(output, start)
        if not fragment:
            break
        try:
            parsed = json.loads(fragment)
        except json.JSONDecodeError:
            break
        if isinstance(parsed, dict):
            transits.append(parsed)
        cursor = end
        remainder = output[cursor:]
        comma = re.match(r"\s*,", remainder)
        if comma is None:
            break
        cursor += comma.end()
    if not transits:
        return {}
    route["transits"] = transits
    return {"route": route}


def _balanced_json_object(text: str, start: int) -> tuple[str, int]:
    depth = 0
    quoted = False
    escaped = False
    for index in range(start, len(text)):
        character = text[index]
        if quoted:
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == '"':
                quoted = False
            continue
        if character == '"':
            quoted = True
        elif character == "{":
            depth += 1
        elif character == "}":
            depth -= 1
            if depth == 0:
                return text[start : index + 1], index + 1
    return "", start


def _first_json_string_field(text: str, key: str) -> str:
    match = re.search(rf'"{re.escape(key)}"\s*:\s*"((?:\\.|[^"\\])*)"', text)
    if match is None:
        return ""
    try:
        return str(json.loads(f'"{match.group(1)}"'))
    except json.JSONDecodeError:
        return ""


def _transit_buslines(transit: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for segment in transit.get("segments") or []:
        if not isinstance(segment, dict):
            continue
        bus = segment.get("bus")
        buslines = bus.get("buslines") if isinstance(bus, dict) else None
        if isinstance(buslines, list):
            rows.extend(line for line in buslines if isinstance(line, dict))
    return rows


def _compact_busline(line: dict[str, Any]) -> dict[str, Any] | None:
    name = str(line.get("name") or "").strip()
    departure = _nested_name(line.get("departure_stop"))
    arrival = _nested_name(line.get("arrival_stop"))
    if not name or not departure or not arrival:
        return None
    compact: dict[str, Any] = {
        "name": name,
        "departure_stop": {"name": departure},
        "arrival_stop": {"name": arrival},
    }
    for key in ("distance", "duration"):
        if line.get(key) not in (None, ""):
            compact[key] = line[key]
    via_names = [
        value
        for row in line.get("via_stops") or []
        if (value := _nested_name(row))
    ][:_MAX_TRANSIT_VIA_STOPS]
    compact["via_stops"] = [{"name": value} for value in via_names]
    return compact


def _nested_name(value: Any) -> str:
    if isinstance(value, dict):
        return str(value.get("name") or "").strip()
    return str(value or "").strip()


def _transit_sort_key(transit: dict[str, Any]) -> tuple[int, int]:
    return (
        _non_negative_int(transit.get("duration"), 2**31 - 1),
        _non_negative_int(transit.get("walking_distance"), 2**31 - 1),
    )


def _non_negative_int(value: Any, fallback: int) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return fallback


def travel_subagent_config_for_stage(
    config: SubagentConfig,
    *,
    finalization: bool,
    repair_profiles: frozenset[str] = frozenset(),
) -> SubagentConfig:
    """Expose only the travel Profiles valid for the persisted candidate stage."""

    effective = with_travel_research_profile(config)
    if not effective.enabled:
        return effective
    allowed = repair_profiles or (
        TRAVEL_FINALIZATION_PROFILES if finalization else TRAVEL_CANDIDATE_RESEARCH_PROFILES
    )
    profiles = {
        name: profile
        for name, profile in effective.profiles.items()
        if name in allowed
    }
    stage_limit = len(profiles)
    return SubagentConfig(
        enabled=effective.enabled,
        max_parallel=min(stage_limit, effective.max_parallel),
        max_tasks_per_call=min(stage_limit, effective.max_tasks_per_call),
        max_depth=effective.max_depth,
        max_subagents_per_parent_turn=min(
            stage_limit, effective.max_subagents_per_parent_turn
        ),
        max_batches_per_parent_turn=1,
        max_batch_result_chars=effective.max_batch_result_chars,
        profiles=MappingProxyType(profiles),
    )


__all__ = [
    "TRAVEL_CANDIDATE_RESEARCH_PROFILES",
    "TRAVEL_FINAL_ROUTE_PROFILE",
    "TRAVEL_FINAL_STAY_PROFILE",
    "TRAVEL_FINAL_WEATHER_PROFILE",
    "TRAVEL_FINALIZATION_PROFILES",
    "TRAVEL_FINALIZATION_REPAIR_PROFILES",
    "TRAVEL_GUIDES_PROFILE",
    "TRAVEL_RESEARCH_PROFILES",
    "TravelFinalRouteResultToolProvider",
    "compact_travel_final_route_results",
    "TRAVEL_STAY_POI_PROFILE",
    "TRAVEL_TRANSPORT_WEATHER_PROFILE",
    "TravelExactDelegationToolProvider",
    "require_exact_travel_delegation",
    "travel_subagent_config_for_stage",
    "with_travel_research_profile",
]
