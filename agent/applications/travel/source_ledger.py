"""Bounded process-lifetime evidence that travel Sessions actually queried sources."""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass, field
from typing import Any

from agent.protocols.tool import ToolResult

TRAVEL_SOURCE_CATEGORIES = frozenset({"maps", "weather", "transport", "web", "social"})
_MAX_SESSIONS = 128


@dataclass
class _SessionSources:
    expected: set[str] = field(default_factory=set)
    attempted: set[str] = field(default_factory=set)
    successful: set[str] = field(default_factory=set)
    retryable: set[str] = field(default_factory=set)
    attempt_counts: dict[str, int] = field(default_factory=dict)


@dataclass(frozen=True)
class TravelSourceSnapshot:
    expected: frozenset[str]
    attempted: frozenset[str]
    successful: frozenset[str]
    retryable: frozenset[str]
    attempt_counts: tuple[tuple[str, int], ...]

    @property
    def missing_attempts(self) -> tuple[str, ...]:
        return tuple(sorted(self.expected - self.attempted))

    @property
    def retry_required(self) -> tuple[str, ...]:
        counts = dict(self.attempt_counts)
        return tuple(
            sorted(
                category
                for category in self.retryable - self.successful
                if counts.get(category, 0) < 2
            )
        )


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
            self._sessions.setdefault(session_id, _SessionSources()).expected.update(categories)

    def observe(self, session_id: str, tool_name: str, result: ToolResult) -> None:
        category = source_category(tool_name)
        if not session_id or not category:
            return
        with self._lock:
            state = self._sessions.setdefault(session_id, _SessionSources())
            state.attempted.add(category)
            state.attempt_counts[category] = state.attempt_counts.get(category, 0) + 1
            if _result_succeeded(result, category):
                state.successful.add(category)
                state.retryable.discard(category)
            elif category not in state.successful and _result_retryable(result, category):
                state.retryable.add(category)

    def snapshot(self, session_id: str) -> TravelSourceSnapshot:
        with self._lock:
            state = self._sessions.get(session_id, _SessionSources())
            return TravelSourceSnapshot(
                expected=frozenset(state.expected),
                attempted=frozenset(state.attempted),
                successful=frozenset(state.successful),
                retryable=frozenset(state.retryable),
                attempt_counts=tuple(sorted(state.attempt_counts.items())),
            )

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
    if "tavily" in name:
        return "web"
    if "xhs" in name or "xiaohongshu" in name:
        return "social"
    return ""


def preferred_travel_tool_names(tool_names: list[str]) -> tuple[str, ...]:
    """Select useful read-only Tools, including map search, geocoding, and routing."""

    preferences = {
        "maps": ("maps_text_search", "text_search", "search"),
        "weather": ("get_forecast", "historical_weather", "forecast", "weather"),
        "transport": ("get-tickets", "query", "train", "rail"),
        "web": ("tavily_search", "search"),
        "social": ("search_notes", "search"),
    }
    selected: list[str] = []
    for category in ("maps", "weather", "transport", "web", "social"):
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
    return True


def _result_retryable(result: ToolResult, category: str) -> bool:
    if category not in {"web", "social"}:
        return False
    codes = {
        str(result.metadata.get("code") or "").upper(),
        *(str(payload.get("code") or "").upper() for payload in _json_objects(result.output)),
    }
    return "TRAVEL_SOURCE_AUTH_REQUIRED" not in codes


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
    if not isinstance(value, str) or not value.strip() or len(value) > 20_000:
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
        for key in ("data", "result", "content", "text", "payload"):
            nested = current.get(key)
            if isinstance(nested, dict):
                queue.append((nested, depth + 1))
            elif isinstance(nested, str) and len(nested) <= 16_000:
                queue.extend((item, depth + 1) for item in _json_objects(nested)[:4])
    return expanded


__all__ = [
    "TRAVEL_SOURCE_CATEGORIES",
    "TravelSourceLedger",
    "TravelSourceSnapshot",
    "source_category",
    "preferred_travel_tool_names",
]
