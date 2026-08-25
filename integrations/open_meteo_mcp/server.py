"""Read-only MCP tools for Open-Meteo geocoding, forecast, and history."""

from __future__ import annotations

import os
from datetime import UTC, date, datetime, timedelta
from typing import Any
from urllib.parse import urlsplit

import httpx
from mcp.server.fastmcp import FastMCP

server = FastMCP("zhice-open-meteo-readonly")
_HTTP_CLIENT = httpx.Client(trust_env=False)

FORECAST_DAILY = (
    "weather_code,temperature_2m_max,temperature_2m_min,"
    "apparent_temperature_max,apparent_temperature_min,precipitation_sum,"
    "precipitation_probability_max,wind_speed_10m_max,sunrise,sunset"
)
FORECAST_HOURLY = "temperature_2m,apparent_temperature,precipitation_probability,weather_code"
HISTORICAL_DAILY = "weather_code,temperature_2m_max,temperature_2m_min,precipitation_sum,wind_speed_10m_max"


@server.tool()
def geocode_place(name: str, count: int = 5, language: str = "zh") -> dict[str, Any]:
    """Resolve a place name to bounded Open-Meteo coordinates without writing data."""

    query = _required_text(name, "name", 120)
    bounded_count = _integer(count, "count", 1, 10)
    lang = _required_text(language, "language", 12)
    payload = _get_json(
        _base_url("OPEN_METEO_GEOCODING_BASE_URL", "https://geocoding-api.open-meteo.com"),
        "/v1/search",
        {"name": query, "count": bounded_count, "language": lang, "format": "json"},
    )
    if payload.get("status") == "error":
        return payload
    results = payload.get("results", [])
    if not isinstance(results, list):
        results = []
    normalized = []
    for item in results[:bounded_count]:
        if not isinstance(item, dict):
            continue
        normalized.append(
            {
                "name": str(item.get("name") or "")[:120],
                "latitude": item.get("latitude"),
                "longitude": item.get("longitude"),
                "country": str(item.get("country") or "")[:80],
                "admin1": str(item.get("admin1") or "")[:80],
                "timezone": str(item.get("timezone") or "")[:80],
            }
        )
    return {
        "status": "success",
        "code": "OK",
        "source_type": "official_api",
        "freshness": "live",
        "provider": "Open-Meteo",
        "retrieved_at": _utc_now(),
        "results": normalized,
    }


@server.tool()
def get_forecast(
    latitude: float,
    longitude: float,
    start_date: str,
    end_date: str,
    timezone: str = "auto",
) -> dict[str, Any]:
    """Get live current or future weather forecasts for today or tomorrow within sixteen days. 实时天气、当前天气、明天天气、天气预报。"""

    lat = _coordinate(latitude, "latitude", -90, 90)
    lon = _coordinate(longitude, "longitude", -180, 180)
    start, end = _date_range(start_date, end_date, maximum_days=16)
    today = datetime.now(UTC).date()
    if start < today - timedelta(days=1) or end > today + timedelta(days=16):
        return {
            "status": "error",
            "code": "TRAVEL_WEATHER_OUT_OF_RANGE",
            "message": "Requested dates are outside the current forecast window; use historical climate reference instead.",
            "freshness": "unknown",
            "retrieved_at": _utc_now(),
        }
    payload = _get_json(
        _base_url("OPEN_METEO_FORECAST_BASE_URL", "https://api.open-meteo.com"),
        "/v1/forecast",
        {
            "latitude": lat,
            "longitude": lon,
            "start_date": start.isoformat(),
            "end_date": end.isoformat(),
            "timezone": _required_text(timezone, "timezone", 80),
            "daily": FORECAST_DAILY,
            "hourly": FORECAST_HOURLY,
        },
    )
    if payload.get("status") == "error":
        return payload
    return _weather_result(payload, freshness="live", data_as_of=_utc_now())


@server.tool()
def get_historical_weather(
    latitude: float,
    longitude: float,
    start_date: str,
    end_date: str,
    timezone: str = "auto",
) -> dict[str, Any]:
    """Get past archived weather observations for historical climate reference. 历史天气、气候参考。"""

    lat = _coordinate(latitude, "latitude", -90, 90)
    lon = _coordinate(longitude, "longitude", -180, 180)
    start, end = _date_range(start_date, end_date, maximum_days=62)
    if end >= datetime.now(UTC).date():
        return {
            "status": "error",
            "code": "TRAVEL_WEATHER_OUT_OF_RANGE",
            "message": "Historical weather dates must be in the past.",
            "freshness": "historical",
            "retrieved_at": _utc_now(),
        }
    payload = _get_json(
        _base_url("OPEN_METEO_ARCHIVE_BASE_URL", "https://archive-api.open-meteo.com"),
        "/v1/archive",
        {
            "latitude": lat,
            "longitude": lon,
            "start_date": start.isoformat(),
            "end_date": end.isoformat(),
            "timezone": _required_text(timezone, "timezone", 80),
            "daily": HISTORICAL_DAILY,
        },
    )
    if payload.get("status") == "error":
        return payload
    return _weather_result(payload, freshness="historical", data_as_of=end.isoformat())


def _weather_result(payload: dict[str, Any], *, freshness: str, data_as_of: str) -> dict[str, Any]:
    return {
        "status": "success",
        "code": "OK",
        "source_type": "official_api",
        "freshness": freshness,
        "provider": "Open-Meteo",
        "retrieved_at": _utc_now(),
        "data_as_of": data_as_of,
        "latitude": payload.get("latitude"),
        "longitude": payload.get("longitude"),
        "timezone": payload.get("timezone"),
        "daily": payload.get("daily", {}),
        "hourly": payload.get("hourly", {}),
    }


def _get_json(base_url: str, path: str, params: dict[str, Any]) -> dict[str, Any]:
    timeout = float(os.getenv("OPEN_METEO_TIMEOUT_SECONDS", "15"))
    try:
        response = _HTTP_CLIENT.get(f"{base_url.rstrip('/')}{path}", params=params, timeout=timeout)
        response.raise_for_status()
        payload = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        code = "TRAVEL_SOURCE_RATE_LIMITED" if isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code == 429 else "TRAVEL_SOURCE_UNAVAILABLE"
        return {
            "status": "error",
            "code": code,
            "message": "Open-Meteo request failed safely.",
            "retrieved_at": _utc_now(),
            "error_type": type(exc).__name__,
        }
    if not isinstance(payload, dict):
        return {
            "status": "error",
            "code": "TRAVEL_SOURCE_UNAVAILABLE",
            "message": "Open-Meteo returned an invalid response.",
            "retrieved_at": _utc_now(),
        }
    return payload


def _base_url(env_name: str, default: str) -> str:
    value = os.getenv(env_name, default).strip().rstrip("/")
    parsed = urlsplit(value)
    if parsed.scheme != "https" and parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
        raise ValueError(f"{env_name} must use HTTPS outside local tests")
    if not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError(f"{env_name} is invalid")
    return value


def _date_range(start_value: str, end_value: str, *, maximum_days: int) -> tuple[date, date]:
    try:
        start = date.fromisoformat(start_value)
        end = date.fromisoformat(end_value)
    except (TypeError, ValueError) as exc:
        raise ValueError("start_date and end_date must be ISO dates") from exc
    if end < start or (end - start).days + 1 > maximum_days:
        raise ValueError("weather date range is invalid")
    return start, end


def _coordinate(value: Any, field: str, minimum: float, maximum: float) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ValueError(f"{field} must be numeric")
    result = float(value)
    if not minimum <= result <= maximum:
        raise ValueError(f"{field} is outside range")
    return result


def _integer(value: Any, field: str, minimum: int, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise ValueError(f"{field} is outside range")
    return value


def _required_text(value: Any, field: str, maximum: int) -> str:
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > maximum:
        raise ValueError(f"{field} is invalid")
    return value.strip()


def _utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


if __name__ == "__main__":
    server.run(transport="stdio")
