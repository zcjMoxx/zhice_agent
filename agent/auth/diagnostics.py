"""Actor-bound safe diagnostics assembled from bounded trace and audit evidence."""

from __future__ import annotations

import json
from collections import deque
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from agent.auth.store import SQLiteAuthStore
from agent.protocols.auth import ActorContext

_MAX_TRACE_LINES_PER_FILE = 2000
_MAX_VISIBLE_EVENTS = 200
_SAFE_TRACE_FIELDS = (
    "ts",
    "level",
    "component",
    "event",
    "session_id",
    "turn_id",
    "request_id",
    "channel",
    "route",
    "status_code",
    "tool",
    "tool_call_id",
    "ok",
    "duration_ms",
    "reason_code",
    "error_code",
    "input_preview",
    "output_preview",
)


class RecentActivityDiagnostics:
    """Return current-user trace/audit evidence for LLM-led diagnosis."""

    def __init__(self, store: SQLiteAuthStore, logs_dir: Path | str):
        self.store = store
        self.logs_dir = Path(logs_dir).expanduser().resolve()

    def diagnose(self, actor: ActorContext, filters: dict[str, Any]) -> dict[str, Any]:
        """Return bounded safe evidence without making the final causal judgment."""

        if actor.user_id is None:
            raise PermissionError("Database user is required for diagnostics")
        minutes = max(1, min(int(filters.get("minutes") or 30), 10080))
        session_id = str(filters.get("session_id") or "").strip()
        turn_id = str(filters.get("turn_id") or "").strip()
        event_type = str(filters.get("event_type") or "").strip()
        since = datetime.now(UTC) - timedelta(minutes=minutes)
        owned_session_ids = {
            str(row["session_id"])
            for row in self.store.session_index_list(str(actor.user_id))
        }
        if session_id and session_id not in owned_session_ids:
            owned_session_ids = set()

        trace_events = self._trace_events(
            actor_user_id=str(actor.user_id),
            owned_session_ids=owned_session_ids,
            since=since,
            session_id=session_id,
            turn_id=turn_id,
            event_type=event_type,
        )
        audit_events = self.store.list_audit_events(
            actor_user_id=str(actor.user_id),
            limit=500,
            session_id=session_id,
            turn_id=turn_id,
        )
        audit_events = [
            event
            for event in audit_events
            if _event_in_range(event, since)
            and (not event_type or str(event.get("action") or "") == event_type)
        ][:_MAX_VISIBLE_EVENTS]
        failures = [event for event in trace_events if _is_trace_failure(event)]
        failures.extend(event for event in audit_events if _is_audit_failure(event))
        return {
            "summary": (
                f"Collected {len(trace_events)} trace events and {len(audit_events)} audit events "
                f"for the current user in the last {minutes} minutes."
            ),
            "scope": {
                "minutes": minutes,
                "session_id": session_id,
                "turn_id": turn_id,
                "event_type": event_type,
            },
            "failure_candidates": failures[:50],
            "trace_events": trace_events,
            "audit_events": audit_events,
            "analysis_instruction": (
                "Use these facts to distinguish confirmed evidence from inference, then explain the "
                "probable cause, confidence, and next action."
            ),
        }

    def _trace_events(
        self,
        *,
        actor_user_id: str,
        owned_session_ids: set[str],
        since: datetime,
        session_id: str,
        turn_id: str,
        event_type: str,
    ) -> list[dict[str, Any]]:
        events: list[dict[str, Any]] = []
        for path in _trace_paths(self.logs_dir, since):
            for event in _tail_json_objects(path, _MAX_TRACE_LINES_PER_FILE):
                if not _event_in_range(event, since):
                    continue
                event_actor = str(event.get("actor_user_id") or "")
                event_session = str(event.get("session_id") or "")
                if event_actor != actor_user_id and event_session not in owned_session_ids:
                    continue
                if session_id and event_session != session_id:
                    continue
                if turn_id and str(event.get("turn_id") or "") != turn_id:
                    continue
                if event_type and str(event.get("event") or "") != event_type:
                    continue
                events.append(_safe_trace_event(event))
        events.sort(key=lambda item: str(item.get("ts") or ""), reverse=True)
        return events[:_MAX_VISIBLE_EVENTS]


def _trace_paths(logs_dir: Path, since: datetime) -> list[Path]:
    current = since.astimezone().date()
    today = datetime.now().astimezone().date()
    paths: list[Path] = []
    while current <= today:
        path = logs_dir / current.isoformat() / "trace.log"
        if path.is_file():
            paths.append(path)
        current += timedelta(days=1)
    return paths


def _tail_json_objects(path: Path, max_lines: int) -> list[dict[str, Any]]:
    lines: deque[str] = deque(maxlen=max_lines)
    try:
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            for line in handle:
                lines.append(line)
    except OSError:
        return []
    events: list[dict[str, Any]] = []
    for line in lines:
        try:
            payload = json.loads(line)
        except (json.JSONDecodeError, TypeError):
            continue
        if isinstance(payload, dict):
            events.append(payload)
    return events


def _safe_trace_event(event: dict[str, Any]) -> dict[str, Any]:
    return {key: event[key] for key in _SAFE_TRACE_FIELDS if key in event}


def _event_in_range(event: dict[str, Any], since: datetime) -> bool:
    try:
        timestamp = datetime.fromisoformat(str(event.get("ts") or ""))
    except ValueError:
        return False
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=UTC)
    return timestamp >= since


def _is_trace_failure(event: dict[str, Any]) -> bool:
    level = str(event.get("level") or "").upper()
    name = str(event.get("event") or "")
    return (
        level in {"ERROR", "CRITICAL"}
        or event.get("ok") is False
        or name.endswith((".error", ".failed", ".denied", ".expired"))
        or bool(event.get("reason_code") or event.get("error_code"))
    )


def _is_audit_failure(event: dict[str, Any]) -> bool:
    action = str(event.get("action") or "")
    decision = str(event.get("decision") or "")
    return action.endswith(("_error", "_failed", "_denied", "_expired")) or decision in {
        "deny",
        "denied",
        "error",
        "expired",
    }
