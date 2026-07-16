"""Actor-bound self diagnostics from structured activity and correlated trace evidence."""

from __future__ import annotations

import json
from collections import Counter, deque
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from agent.auth.store import SQLiteAuthStore
from agent.protocols.auth import ActorContext
from agent.protocols.diagnostics import DiagnosticContext

_MAX_TRACE_LINES_PER_FILE = 2000
_MAX_VISIBLE_EVENTS = 80
_VALID_FOCUS = {"auto", "latency", "failure", "trend"}
_VALID_TARGET = {"auto", "previous_turn", "latest_failure", "recent_activity"}
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
    "error_type",
    "code",
    "endpoint",
    "model",
    "input_preview",
    "output_preview",
)


class RecentActivityDiagnostics:
    """Resolve and diagnose the current user's recent activity automatically."""

    def __init__(self, store: SQLiteAuthStore, logs_dir: Path | str):
        self.store = store
        self.logs_dir = Path(logs_dir).expanduser().resolve()

    def diagnose(
        self,
        actor: ActorContext,
        context: DiagnosticContext,
        filters: dict[str, Any],
    ) -> dict[str, Any]:
        """Return one structured diagnosis without requiring internal correlation ids."""

        if actor.user_id is None:
            raise PermissionError("Database user is required for diagnostics")
        minutes = max(1, min(int(filters.get("minutes") or 30), 10080))
        focus = str(filters.get("focus") or "auto").strip().lower()
        target = str(filters.get("target") or "auto").strip().lower()
        if focus not in _VALID_FOCUS:
            focus = "auto"
        if target not in _VALID_TARGET:
            target = "auto"
        since = datetime.now(UTC) - timedelta(minutes=minutes)
        turns = [
            row
            for row in self.store.list_turn_runs(
                actor_user_id=str(actor.user_id),
                session_id=context.session_id,
                limit=200,
            )
            if str(row.get("turn_id") or "") != context.current_turn_id
            and _row_in_range(row, since, "started_at")
            and str(row.get("status") or "") != "started"
        ]
        tool_rows = self.store.list_tool_call_records(
            actor_user_id=str(actor.user_id),
            session_id=context.session_id,
            limit=500,
        )
        tools_by_turn: dict[str, list[dict[str, Any]]] = {}
        for row in tool_rows:
            tools_by_turn.setdefault(str(row.get("turn_id") or ""), []).append(row)

        if focus == "trend" or target == "recent_activity":
            return self._diagnose_trend(
                actor,
                context,
                turns,
                tools_by_turn,
                minutes=minutes,
            )

        selected = _select_target_turn(turns, tools_by_turn, focus=focus, target=target)
        if selected is None:
            return _insufficient_report(
                focus,
                context,
                "No completed earlier turn was found in the current session and time range.",
            )
        turn_id = str(selected.get("turn_id") or "")
        request_id = str(selected.get("request_id") or "")
        trace_events = self._trace_events(
            actor_user_id=str(actor.user_id),
            session_id=context.session_id,
            turn_id=turn_id,
            request_id=request_id,
            since=since,
        )
        audit_events = self.store.list_audit_events(
            actor_user_id=str(actor.user_id),
            limit=100,
            session_id=context.session_id,
            turn_id=turn_id,
        )
        return _diagnose_turn(
            selected,
            tools_by_turn.get(turn_id, []),
            trace_events,
            audit_events,
            focus=focus,
        )

    def _diagnose_trend(
        self,
        actor: ActorContext,
        context: DiagnosticContext,
        turns: list[dict[str, Any]],
        tools_by_turn: dict[str, list[dict[str, Any]]],
        *,
        minutes: int,
    ) -> dict[str, Any]:
        failures: list[tuple[dict[str, Any], str]] = []
        for turn in turns:
            turn_id = str(turn.get("turn_id") or "")
            code = _turn_failure_code(turn, tools_by_turn.get(turn_id, []))
            if code:
                failures.append((turn, code))
        counts = Counter(code for _, code in failures)
        if not failures:
            return {
                "status": "no_issue",
                "focus": "trend",
                "target": {"session_id": context.session_id},
                "summary": f"No failed turn was found in this session in the last {minutes} minutes.",
                "failure_stage": "",
                "cause_code": "",
                "confirmed_facts": [f"completed_turns={len(turns)}", "failed_turns=0"],
                "probable_cause": "",
                "confidence": "high",
                "evidence": [],
                "next_actions": [],
                "limitations": [],
            }
        common_code, common_count = counts.most_common(1)[0]
        latest_turn = failures[0][0]
        return {
            "status": "diagnosed",
            "focus": "trend",
            "target": {
                "session_id": context.session_id,
                "latest_failed_turn_id": str(latest_turn.get("turn_id") or ""),
            },
            "summary": (
                f"Found {len(failures)} failed turns in the last {minutes} minutes; "
                f"the most frequent cause code was {common_code}."
            ),
            "failure_stage": "multiple",
            "cause_code": common_code,
            "confirmed_facts": [
                f"completed_turns={len(turns)}",
                f"failed_turns={len(failures)}",
                f"most_common_failure={common_code}",
                f"most_common_count={common_count}",
            ],
            "probable_cause": _cause_message(common_code),
            "confidence": "high",
            "evidence": [
                {
                    "turn_id": str(turn.get("turn_id") or ""),
                    "status": str(turn.get("status") or ""),
                    "cause_code": code,
                }
                for turn, code in failures[:10]
            ],
            "next_actions": _next_actions(common_code),
            "limitations": [],
        }

    def _trace_events(
        self,
        *,
        actor_user_id: str,
        session_id: str,
        turn_id: str,
        request_id: str,
        since: datetime,
    ) -> list[dict[str, Any]]:
        events: list[dict[str, Any]] = []
        for path in _trace_paths(self.logs_dir, since):
            for event in _tail_json_objects(path, _MAX_TRACE_LINES_PER_FILE):
                if not _event_in_range(event, since):
                    continue
                event_actor = str(event.get("actor_user_id") or "")
                if event_actor and event_actor != actor_user_id:
                    continue
                if str(event.get("session_id") or "") != session_id:
                    continue
                if str(event.get("turn_id") or "") != turn_id:
                    continue
                event_request_id = str(event.get("request_id") or "")
                if request_id and event_request_id and event_request_id != request_id:
                    continue
                events.append(_safe_trace_event(event))
        events.sort(key=lambda item: str(item.get("ts") or ""))
        return events[:_MAX_VISIBLE_EVENTS]


def _select_target_turn(
    turns: list[dict[str, Any]],
    tools_by_turn: dict[str, list[dict[str, Any]]],
    *,
    focus: str,
    target: str,
) -> dict[str, Any] | None:
    if not turns:
        return None
    previous = turns[0]
    if target == "previous_turn" or focus in {"auto", "latency"}:
        return previous
    failures = [
        turn
        for turn in turns
        if _turn_failure_code(
            turn,
            tools_by_turn.get(str(turn.get("turn_id") or ""), []),
        )
    ]
    if target == "latest_failure" or focus == "failure":
        previous_code = _turn_failure_code(
            previous,
            tools_by_turn.get(str(previous.get("turn_id") or ""), []),
        )
        return previous if previous_code else (failures[0] if failures else previous)
    return previous


def _diagnose_turn(
    turn: dict[str, Any],
    tools: list[dict[str, Any]],
    trace_events: list[dict[str, Any]],
    audit_events: list[dict[str, Any]],
    *,
    focus: str,
) -> dict[str, Any]:
    target = {
        "session_id": str(turn.get("session_id") or ""),
        "turn_id": str(turn.get("turn_id") or ""),
        "request_id": str(turn.get("request_id") or ""),
        "status": str(turn.get("status") or ""),
    }
    tool_failure = next((row for row in tools if bool(row.get("is_error"))), None)
    llm_error = next((event for event in trace_events if event.get("event") == "llm.error"), None)
    save_error = next(
        (event for event in trace_events if event.get("event") == "session.save_failed"),
        None,
    )
    denied = next(
        (
            event
            for event in audit_events
            if str(event.get("decision") or "") in {"deny", "denied"}
        ),
        None,
    )
    failure = tool_failure or llm_error or save_error or denied
    if failure is not None or str(turn.get("status") or "") == "error":
        if tool_failure is not None:
            tool_name = str(tool_failure.get("tool_name") or "tool")
            code = str(tool_failure.get("result_code") or "TOOL_EXECUTION_FAILED")
            stage = f"tool.{tool_name}"
            facts = [
                f"tool={tool_name}",
                f"result_code={code}",
                f"duration_ms={_tool_duration_ms(tool_failure)}",
            ]
            evidence = [_safe_tool_evidence(tool_failure)]
        elif llm_error is not None:
            code = str(llm_error.get("error_code") or llm_error.get("error_type") or "LLM_ERROR")
            stage = "llm"
            facts = [f"llm_error={code}", f"duration_ms={llm_error.get('duration_ms', '')}"]
            evidence = [llm_error]
        elif save_error is not None:
            code = str(save_error.get("error_code") or "SESSION_SAVE_FAILED")
            stage = "session.save"
            facts = ["session save failed after the turn ran"]
            evidence = [save_error]
        elif denied is not None:
            code = str(denied.get("reason_code") or "AUTH_PERMISSION_DENIED")
            stage = "authorization"
            facts = [f"security_decision={denied.get('decision')}", f"reason_code={code}"]
            evidence = [
                {
                    "action": denied.get("action"),
                    "decision": denied.get("decision"),
                    "reason_code": code,
                }
            ]
        else:
            code = str(turn.get("error_code") or "TURN_ERROR")
            stage = "turn"
            facts = [f"turn_status={turn.get('status')}", f"error_code={code}"]
            evidence = []
        return {
            "status": "diagnosed",
            "focus": "failure" if focus == "auto" else focus,
            "target": target,
            "summary": f"The selected turn failed in {stage} with cause code {code}.",
            "failure_stage": stage,
            "cause_code": code,
            "confirmed_facts": facts,
            "probable_cause": _cause_message(code),
            "confidence": "high" if code not in {"TURN_ERROR", "TOOL_EXECUTION_FAILED"} else "medium",
            "evidence": evidence,
            "next_actions": _next_actions(code),
            "limitations": [],
        }
    return _latency_report(turn, tools, trace_events, target=target, focus=focus)


def _latency_report(
    turn: dict[str, Any],
    tools: list[dict[str, Any]],
    trace_events: list[dict[str, Any]],
    *,
    target: dict[str, Any],
    focus: str,
) -> dict[str, Any]:
    total_ms = int(turn.get("duration_ms") or _timestamp_duration_ms(turn) or 0)
    llm_ms = sum(
        int(event.get("duration_ms") or 0)
        for event in trace_events
        if event.get("event") in {"llm.done", "llm.error"}
    )
    tool_ms = sum(_tool_duration_ms(row) for row in tools)
    other_ms = max(0, total_ms - llm_ms - tool_ms)
    stages = [
        {"stage": "llm", "duration_ms": llm_ms},
        {"stage": "tools", "duration_ms": tool_ms},
        {"stage": "other", "duration_ms": other_ms},
    ]
    dominant = max(stages, key=lambda item: int(item["duration_ms"])) if total_ms else None
    if dominant is None:
        return _insufficient_report(
            "latency" if focus == "auto" else focus,
            DiagnosticContext(
                session_id=str(target.get("session_id") or ""),
                current_turn_id="",
            ),
            "The previous turn was found, but no reliable duration evidence was recorded.",
            target=target,
        )
    stage = str(dominant["stage"])
    code = {
        "llm": "LLM_PRIMARY_LATENCY",
        "tools": "TOOL_PRIMARY_LATENCY",
        "other": "RUNTIME_OVERHEAD_LATENCY",
    }[stage]
    return {
        "status": "diagnosed",
        "focus": "latency" if focus == "auto" else focus,
        "target": target,
        "summary": (
            f"The selected turn took {total_ms} ms; the largest measured stage was "
            f"{stage} at {dominant['duration_ms']} ms."
        ),
        "failure_stage": "",
        "cause_code": code,
        "confirmed_facts": [
            f"total_ms={total_ms}",
            f"llm_ms={llm_ms}",
            f"tool_ms={tool_ms}",
            f"other_ms={other_ms}",
        ],
        "probable_cause": _cause_message(code),
        "confidence": "high" if stage in {"llm", "tools"} and dominant["duration_ms"] else "medium",
        "evidence": stages,
        "next_actions": _next_actions(code),
        "limitations": (
            ["The other stage combines session persistence and uninstrumented runtime overhead."]
            if other_ms
            else []
        ),
    }


def _turn_failure_code(turn: dict[str, Any], tools: list[dict[str, Any]]) -> str:
    tool_failure = next((row for row in tools if bool(row.get("is_error"))), None)
    if tool_failure is not None:
        return str(tool_failure.get("result_code") or "TOOL_EXECUTION_FAILED")
    if str(turn.get("status") or "") == "error":
        return str(turn.get("error_code") or "TURN_ERROR")
    return ""


def _safe_tool_evidence(row: dict[str, Any]) -> dict[str, Any]:
    return {
        key: row.get(key)
        for key in (
            "tool_name",
            "decision",
            "decision_code",
            "risk_category",
            "result_code",
            "exit_code",
            "timeout_seconds",
            "stdout_tail",
            "stderr_tail",
            "output_preview",
        )
        if row.get(key) not in {None, ""}
    }


def _tool_duration_ms(row: dict[str, Any]) -> int:
    try:
        return int(float(row.get("duration_seconds") or 0) * 1000)
    except (TypeError, ValueError):
        return 0


def _timestamp_duration_ms(turn: dict[str, Any]) -> int:
    try:
        started = datetime.fromisoformat(str(turn.get("started_at") or ""))
        finished = datetime.fromisoformat(str(turn.get("finished_at") or ""))
    except ValueError:
        return 0
    return max(0, int((finished - started).total_seconds() * 1000))


def _cause_message(code: str) -> str:
    normalized = str(code or "").upper()
    if "TIMEOUT" in normalized:
        return "The selected operation exceeded its configured time limit."
    if "PERMISSION" in normalized or normalized.startswith("AUTH_"):
        return "The operation was rejected by an authentication, privilege, or ownership boundary."
    if normalized.startswith("LLM_") or "PROVIDER" in normalized:
        return "The primary evidence points to the LLM provider or model call stage."
    if normalized == "TOOL_PRIMARY_LATENCY":
        return "Tool execution accounted for the largest measured share of the turn latency."
    if normalized == "RUNTIME_OVERHEAD_LATENCY":
        return "The remaining runtime or persistence stages accounted for the largest unclassified latency."
    if normalized == "SESSION_SAVE_FAILED":
        return "The turn ran, but its session messages could not be persisted successfully."
    return "The structured runtime record identifies this cause code, but more specific evidence is limited."


def _next_actions(code: str) -> list[str]:
    normalized = str(code or "").upper()
    if "TIMEOUT" in normalized:
        return ["Inspect the recorded stdout/stderr tail and rerun the narrow failing operation."]
    if "PERMISSION" in normalized or normalized.startswith("AUTH_"):
        return ["Verify the target ownership boundary or the required privileged operation."]
    if normalized.startswith("LLM_") or "PROVIDER" in normalized:
        return ["Check endpoint health, provider errors, failover attempts, and model response latency."]
    if normalized == "TOOL_PRIMARY_LATENCY":
        return ["Inspect the slowest tool call and narrow or optimize that operation."]
    if normalized == "RUNTIME_OVERHEAD_LATENCY":
        return ["Inspect session persistence and remaining runtime spans for this turn."]
    return ["Use the evidence and cause code to reproduce the smallest failing path."]


def _insufficient_report(
    focus: str,
    context: DiagnosticContext,
    reason: str,
    *,
    target: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "status": "insufficient_evidence",
        "focus": focus,
        "target": target or {"session_id": context.session_id},
        "summary": reason,
        "failure_stage": "",
        "cause_code": "",
        "confirmed_facts": [],
        "probable_cause": "",
        "confidence": "low",
        "evidence": [],
        "next_actions": ["Retry the operation once so a complete correlated activity record is available."],
        "limitations": [reason],
    }


def _row_in_range(row: dict[str, Any], since: datetime, key: str) -> bool:
    try:
        timestamp = datetime.fromisoformat(str(row.get(key) or ""))
    except ValueError:
        return False
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=UTC)
    return timestamp >= since


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
