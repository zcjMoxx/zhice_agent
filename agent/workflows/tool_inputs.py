"""User-facing workflow inputs adapted to stable MCP tool arguments."""

from __future__ import annotations

import re
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Any
from urllib.parse import parse_qs, urlsplit

_WEATHER_TOOLS = {
    "mcp__open-meteo__get_forecast",
    "mcp__open-meteo__get_historical_weather",
}
_XHS_DETAIL_TOOL = "mcp__xhs-readonly__get_note_detail"
_GEOCODE_TOOL = "mcp__open-meteo__geocode_place"
_AMAP_SEARCH_TOOL = "mcp__amap-maps__maps_text_search"
_AMAP_DETAIL_TOOL = "mcp__amap-maps__maps_search_detail"
_TICKET_TOOL = "mcp__12306__get-tickets"
_STATION_LOOKUP_TOOL = "mcp__12306__get-station-code-of-citys"
_STATION_CODE = re.compile(r"^[A-Z]{3}$")


def with_required_query_helpers(configured: set[str] | frozenset[str]) -> frozenset[str]:
    """Add reviewed helpers only when their user-facing target is enabled."""

    result = set(configured)
    if result.intersection(_WEATHER_TOOLS):
        result.update({_GEOCODE_TOOL, _AMAP_SEARCH_TOOL, _AMAP_DETAIL_TOOL})
    if _TICKET_TOOL in result:
        result.add(_STATION_LOOKUP_TOOL)
    return frozenset(result)


def is_internal_workflow_helper(name: str) -> bool:
    return name in {_GEOCODE_TOOL, _AMAP_DETAIL_TOOL, _STATION_LOOKUP_TOOL}


def prepare_tool_arguments(
    name: str,
    arguments: dict[str, Any],
    invoke_query: Callable[[str, dict[str, Any]], Any],
) -> dict[str, Any]:
    """Translate bounded task inputs without weakening the target tool schema."""

    prepared = dict(arguments)
    if name in _WEATHER_TOOLS and prepared.get("place_name"):
        place = str(prepared.pop("place_name")).strip()
        if not place or len(place) > 120:
            raise ValueError("WORKFLOW_TOOL_ARGUMENTS_INVALID")
        geocoded = invoke_query(_GEOCODE_TOOL, {"name": place, "count": 1, "language": "zh"})
        if isinstance(geocoded, dict) and geocoded.get("status") == "error":
            raise RuntimeError("WORKFLOW_LOCATION_SERVICE_UNAVAILABLE")
        results = geocoded.get("results") if isinstance(geocoded, dict) else None
        first = results[0] if isinstance(results, list) and results else None
        coordinates = _geocode_coordinates(first)
        if coordinates is None:
            coordinates = _amap_coordinates(place, invoke_query)
        if coordinates is None:
            raise ValueError("WORKFLOW_LOCATION_NOT_FOUND")
        prepared["latitude"], prepared["longitude"] = coordinates
        if name == "mcp__open-meteo__get_forecast":
            days = prepared.pop("forecast_days", 2)
        if name == "mcp__open-meteo__get_forecast" and not prepared.get("start_date"):
            if not isinstance(days, int) or isinstance(days, bool) or not 1 <= days <= 16:
                raise ValueError("WORKFLOW_TOOL_ARGUMENTS_INVALID")
            start = datetime.now().astimezone().date()
            prepared["start_date"] = start.isoformat()
            prepared["end_date"] = (start + timedelta(days=days - 1)).isoformat()

    if name == _TICKET_TOOL and (
        prepared.get("departure_name") or prepared.get("arrival_name")
    ):
        departure = str(prepared.pop("departure_name", "")).strip()
        arrival = str(prepared.pop("arrival_name", "")).strip()
        if not departure or not arrival or len(departure) > 80 or len(arrival) > 80:
            raise ValueError("WORKFLOW_TOOL_ARGUMENTS_INVALID")
        station_result = invoke_query(
            _STATION_LOOKUP_TOOL,
            {"citys": f"{departure}|{arrival}"},
        )
        if isinstance(station_result, dict) and station_result.get("status") == "error":
            raise RuntimeError("WORKFLOW_STATION_SERVICE_UNAVAILABLE")
        codes = _station_codes(station_result, (departure, arrival))
        if codes is None:
            raise ValueError("WORKFLOW_STATION_NOT_FOUND")
        prepared["fromStation"], prepared["toStation"] = codes

    if name == _XHS_DETAIL_TOOL and prepared.get("note_url"):
        feed_id, token = _parse_xhs_note_url(str(prepared.pop("note_url")))
        prepared["feed_id"] = feed_id
        prepared["xsec_token"] = token
    return prepared


def _geocode_coordinates(value: Any) -> tuple[float, float] | None:
    if not isinstance(value, dict):
        return None
    latitude, longitude = value.get("latitude"), value.get("longitude")
    if isinstance(latitude, (int, float)) and isinstance(longitude, (int, float)):
        return float(latitude), float(longitude)
    return None


def _amap_coordinates(
    place: str,
    invoke_query: Callable[[str, dict[str, Any]], Any],
) -> tuple[float, float] | None:
    """Resolve detailed Chinese place names without exposing POI IDs to users."""

    searched = invoke_query(_AMAP_SEARCH_TOOL, {"keywords": place})
    if isinstance(searched, dict) and searched.get("status") == "error":
        raise RuntimeError("WORKFLOW_LOCATION_SERVICE_UNAVAILABLE")
    pois = searched.get("pois") if isinstance(searched, dict) else None
    first = pois[0] if isinstance(pois, list) and pois else None
    poi_id = first.get("id") if isinstance(first, dict) else None
    if not isinstance(poi_id, str) or not poi_id:
        return None
    detailed = invoke_query(_AMAP_DETAIL_TOOL, {"id": poi_id})
    if isinstance(detailed, dict) and detailed.get("status") == "error":
        raise RuntimeError("WORKFLOW_LOCATION_SERVICE_UNAVAILABLE")
    location = detailed.get("location") if isinstance(detailed, dict) else None
    if not isinstance(location, str):
        return None
    parts = [part.strip() for part in location.split(",")]
    if len(parts) != 2:
        return None
    try:
        longitude, latitude = (float(part) for part in parts)
    except ValueError:
        return None
    return latitude, longitude


def _station_codes(value: Any, names: tuple[str, str]) -> tuple[str, str] | None:
    """Accept the common 12306 MCP response shapes while preserving endpoint order."""

    named: dict[str, str] = {}
    ordered: list[str] = []

    def visit(item: Any, label: str = "") -> None:
        if isinstance(item, str):
            code = item.strip().upper()
            if _STATION_CODE.fullmatch(code):
                ordered.append(code)
                if label:
                    named[label] = code
            else:
                ordered.extend(
                    re.findall(r"(?<![A-Z])[A-Z]{3}(?![A-Z])", item.upper())
                )
            return
        if isinstance(item, list):
            for child in item:
                visit(child)
            return
        if not isinstance(item, dict):
            return
        item_label = str(
            item.get("city")
            or item.get("station_name")
            or item.get("stationName")
            or item.get("name")
            or label
            or ""
        ).strip()
        for key in ("station_code", "stationCode", "code"):
            if key in item:
                visit(item[key], item_label)
        for key, child in item.items():
            visit(child, str(key))

    visit(value)
    resolved: list[str] = []
    for name in names:
        match = next(
            (code for label, code in named.items() if name == label or name in label or label in name),
            None,
        )
        if match is None:
            break
        resolved.append(match)
    if len(resolved) == 2:
        return resolved[0], resolved[1]
    unique = list(dict.fromkeys(ordered))
    return (unique[0], unique[1]) if len(unique) == 2 else None


def _parse_xhs_note_url(value: str) -> tuple[str, str]:
    parsed = urlsplit(value.strip())
    hostname = (parsed.hostname or "").lower()
    if parsed.scheme != "https" or not (
        hostname == "xiaohongshu.com" or hostname.endswith(".xiaohongshu.com")
    ):
        raise ValueError("WORKFLOW_XHS_LINK_INVALID")
    segments = [item for item in parsed.path.split("/") if item]
    feed_id = segments[-1] if segments else ""
    token = (parse_qs(parsed.query).get("xsec_token") or [""])[0]
    if not feed_id or len(feed_id) > 200 or not token or len(token) > 1000:
        raise ValueError("WORKFLOW_XHS_LINK_INVALID")
    return feed_id, token
