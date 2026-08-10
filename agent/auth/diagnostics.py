"""Actor-bound self diagnostics from structured activity and correlated trace evidence."""

from __future__ import annotations

import hashlib
import json
from collections import Counter, deque
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from agent.auth.store import SQLiteAuthStore
from agent.log_paths import BEIJING_TIMEZONE, daily_trace_path
from agent.logging_utils import redact_value
from agent.protocols.auth import ActorContext
from agent.protocols.diagnostics import DiagnosticContext, SystemDiagnosticQuery

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
    "root_session_id",
    "root_turn_id",
    "parent_session_id",
    "parent_turn_id",
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
    "error_message",
    "code",
    "status",
    "stage",
    "batch_id",
    "task_id",
    "subagent_id",
    "profile",
    "workspace_mode",
    "endpoint",
    "model",
    "endpoint_name",
    "attempt_index",
    "http_status",
    "retryable",
    "backoff_ms",
    "skip_reason",
    "cooldown_until",
    "server_name",
    "mcp_server",
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
        report = _diagnose_turn(
            selected,
            tools_by_turn.get(turn_id, []),
            trace_events,
            audit_events,
            focus=focus,
        )
        report["trace_events"] = trace_events
        report["diagnostic_instruction"] = (
            "Analyze the chronological trace_events directly. Prefer a specific safe "
            "error_message and the surrounding stage/code sequence over a generic wrapper code."
        )
        return report

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
        generic_subagent_failure = common_code == "SUBAGENT_FAILED"
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
            "confidence": "medium" if generic_subagent_failure else "high",
            "evidence": [
                {
                    "turn_id": str(turn.get("turn_id") or ""),
                    "status": str(turn.get("status") or ""),
                    "cause_code": code,
                }
                for turn, code in failures[:10]
            ],
            "next_actions": _next_actions(common_code),
            "limitations": (
                [
                    "Trend records contain the parent SUBAGENT_FAILED code but do not identify "
                    "a correlated child terminal cause."
                ]
                if generic_subagent_failure
                else []
            ),
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
                is_parent_event = (
                    str(event.get("session_id") or "") == session_id
                    and str(event.get("turn_id") or "") == turn_id
                )
                is_child_event = (
                    str(event.get("root_session_id") or "") == session_id
                    and str(event.get("root_turn_id") or "") == turn_id
                )
                if not is_parent_event and not is_child_event:
                    continue
                event_request_id = str(event.get("request_id") or "")
                if (
                    is_parent_event
                    and request_id
                    and event_request_id
                    and event_request_id != request_id
                ):
                    continue
                events.append(_safe_trace_event(event))
        events.sort(key=lambda item: str(item.get("ts") or ""))
        return events[:_MAX_VISIBLE_EVENTS]


class SystemDiagnosticsService:
    """Privileged bounded diagnostics over cross-user runtime facts and safe trace evidence."""

    def __init__(self, store: SQLiteAuthStore, logs_dir: Path | str):
        self.store = store
        self.logs_dir = Path(logs_dir).expanduser().resolve()

    def diagnose(self, filters: dict[str, Any]) -> dict[str, Any]:
        query = SystemDiagnosticQuery.from_mapping(filters)
        since = datetime.now(UTC) - timedelta(minutes=query.minutes)
        activity = self.store.list_system_diagnostic_activity(
            actor_user_id=query.actor_user_id,
            session_id=query.session_id,
            turn_id=query.turn_id,
            request_id=query.request_id,
            channel=query.channel,
            status=query.status,
            error_code=query.error_code,
            tool_name=query.tool_name,
            from_ts=since.isoformat(),
            limit=query.limit,
        )
        turns = [_safe_system_turn(row) for row in activity["turns"]]
        tools = [_safe_system_tool(row) for row in activity["tools"]]
        trace_events = self._trace_events(query, since)
        timeline = _system_timeline(turns, tools, trace_events, query.limit)
        incidents = _aggregate_incidents(timeline)
        if query.incident_id:
            incidents = [row for row in incidents if row["incident_id"] == query.incident_id]
            evidence_ids = {
                str(event.get("evidence_id") or "")
                for incident in incidents
                for event in incident.get("evidence", [])
            }
            timeline = [row for row in timeline if row.get("evidence_id") in evidence_ids]
        return {
            "status": "ok",
            "window_minutes": query.minutes,
            "filters": {
                key: value
                for key, value in query.__dict__.items()
                if key not in {"minutes", "limit"} and value
            },
            "summary": {
                "turns": len(turns),
                "tools": len(tools),
                "trace_events": len(trace_events),
                "incidents": len(incidents),
                "errors": sum(1 for row in timeline if bool(row.get("is_error"))),
            },
            "incidents": incidents[: query.limit],
            "timeline": timeline[: query.limit],
            "limitations": [
                "Only allowlisted, redacted and bounded runtime evidence is returned.",
                "Incidents are deterministic fact groupings, not model-inferred root causes.",
            ],
        }

    def _trace_events(
        self,
        query: SystemDiagnosticQuery,
        since: datetime,
    ) -> list[dict[str, Any]]:
        events: list[dict[str, Any]] = []
        for path in _trace_paths(self.logs_dir, since):
            for raw in _tail_json_objects(path, _MAX_TRACE_LINES_PER_FILE):
                if not _event_in_range(raw, since) or not _matches_system_trace(raw, query):
                    continue
                event = _safe_trace_event(raw)
                if raw.get("actor_user_id"):
                    event["actor_user_id"] = redact_value(raw["actor_user_id"])
                events.append(event)
        events.sort(key=lambda item: str(item.get("ts") or ""))
        return events[-query.limit :]


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
    child_failures = [
        event
        for event in reversed(trace_events)
        if str(event.get("event") or "")
        in {
            "subagent.task_failed",
            "subagent.task_timed_out",
            "subagent.task_cancelled",
        }
        and str(event.get("code") or "") not in {"", "OK"}
    ]
    child_failure = child_failures[0] if child_failures else None
    failure = child_failure or tool_failure or llm_error or save_error or denied
    if failure is not None or str(turn.get("status") or "") == "error":
        limitations: list[str] = []
        if child_failure is not None:
            code_counts = Counter(
                str(event.get("code") or "SUBAGENT_FAILED") for event in child_failures
            )
            code, common_count = code_counts.most_common(1)[0]
            common_failures = [
                event for event in child_failures if str(event.get("code") or "") == code
            ]
            representative = common_failures[0]
            child_stage = str(representative.get("stage") or "child")
            stage = f"subagent.{child_stage}"
            facts = [
                f"child_failure_count={len(child_failures)}",
                f"common_child_failure_count={common_count}",
                f"child_status={representative.get('status', '')}",
                f"child_stage={child_stage}",
                f"child_code={code}",
            ]
            for key in ("task_id", "subagent_id", "profile", "workspace_mode"):
                if representative.get(key) not in {None, ""}:
                    facts.append(f"{key}={representative[key]}")
            evidence = common_failures[:5]
        elif tool_failure is not None:
            tool_name = str(tool_failure.get("tool_name") or "tool")
            code = str(tool_failure.get("result_code") or "TOOL_EXECUTION_FAILED")
            stage = f"tool.{tool_name}"
            facts = [
                f"tool={tool_name}",
                f"result_code={code}",
                f"duration_ms={_tool_duration_ms(tool_failure)}",
            ]
            evidence = [_safe_tool_evidence(tool_failure)]
            if code == "SUBAGENT_FAILED":
                limitations.append(
                    "The parent delegate_tasks call failed, but no correlated child terminal "
                    "failure was present in the available trace."
                )
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
            "confidence": _failure_confidence(code, child_terminal=child_failure is not None),
            "evidence": evidence,
            "next_actions": _next_actions(code),
            "limitations": limitations,
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


def _failure_confidence(code: str, *, child_terminal: bool) -> str:
    """Keep generic parent Subagent failures below high confidence without a child cause."""

    if code in {"TURN_ERROR", "TOOL_EXECUTION_FAILED"}:
        return "medium"
    if code == "SUBAGENT_FAILED" and not child_terminal:
        return "medium"
    return "high"


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
    current = since.astimezone(BEIJING_TIMEZONE).date()
    today = datetime.now(BEIJING_TIMEZONE).date()
    paths: list[Path] = []
    while current <= today:
        path = daily_trace_path(logs_dir, current)
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
    return {
        key: redact_value(event[key])
        for key in _SAFE_TRACE_FIELDS
        if key in event
    }


def _event_in_range(event: dict[str, Any], since: datetime) -> bool:
    try:
        timestamp = datetime.fromisoformat(str(event.get("ts") or ""))
    except ValueError:
        return False
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=UTC)
    return timestamp >= since


def _safe_system_turn(row: dict[str, Any]) -> dict[str, Any]:
    return {
        key: row.get(key)
        for key in (
            "actor_user_id",
            "session_id",
            "turn_id",
            "request_id",
            "channel",
            "status",
            "started_at",
            "finished_at",
            "duration_ms",
            "error_code",
        )
        if row.get(key) not in {None, ""}
    }


def _safe_system_tool(row: dict[str, Any]) -> dict[str, Any]:
    """Exclude args, cwd, command and output even for privileged diagnostics."""

    return {
        key: row.get(key)
        for key in (
            "actor_user_id",
            "session_id",
            "turn_id",
            "tool_name",
            "decision",
            "decision_code",
            "permission_key",
            "risk_category",
            "started_at",
            "finished_at",
            "duration_seconds",
            "is_error",
            "result_code",
            "exit_code",
            "timeout_seconds",
        )
        if row.get(key) not in {None, ""}
    }


def _matches_system_trace(event: dict[str, Any], query: SystemDiagnosticQuery) -> bool:
    exact = {
        "actor_user_id": query.actor_user_id,
        "session_id": query.session_id,
        "turn_id": query.turn_id,
        "request_id": query.request_id,
        "channel": query.channel,
        "component": query.component,
        "endpoint": query.endpoint,
        "model": query.model,
        "mcp_server": query.mcp_server,
    }
    for key, expected in exact.items():
        actual = event.get(key)
        if key == "mcp_server" and not actual:
            actual = event.get("server_name")
        if expected and str(actual or "") != expected:
            return False
    if query.tool_name and str(event.get("tool") or event.get("tool_name") or "") != query.tool_name:
        return False
    if query.status and str(event.get("status") or "") != query.status:
        return False
    if query.error_code and str(
        event.get("error_code") or event.get("code") or event.get("reason_code") or ""
    ) != query.error_code:
        return False
    return True


def _system_timeline(
    turns: list[dict[str, Any]],
    tools: list[dict[str, Any]],
    traces: list[dict[str, Any]],
    limit: int,
) -> list[dict[str, Any]]:
    timeline: list[dict[str, Any]] = []
    for row in turns:
        item = {
            **row,
            "ts": row.get("started_at", ""),
            "kind": "turn",
            "component": "turn",
            "code": row.get("error_code", ""),
            "is_error": row.get("status") == "error",
        }
        timeline.append(item)
    for row in tools:
        item = {
            **row,
            "ts": row.get("started_at", ""),
            "kind": "tool",
            "component": "tool",
            "code": row.get("result_code") or row.get("decision_code") or "",
            "is_error": bool(row.get("is_error")),
        }
        timeline.append(item)
    for row in traces:
        code = str(row.get("error_code") or row.get("code") or row.get("reason_code") or "")
        event = str(row.get("event") or "")
        item = {
            **row,
            "kind": "trace",
            "code": code,
            "is_error": bool(code) or event.endswith((".error", "_failed", ".failed")),
        }
        timeline.append(item)
    timeline.sort(key=lambda item: str(item.get("ts") or ""), reverse=True)
    for item in timeline:
        material = "|".join(
            str(item.get(key) or "")
            for key in ("ts", "kind", "session_id", "turn_id", "event", "tool_name", "code")
        )
        item["evidence_id"] = "evt-" + hashlib.sha256(material.encode()).hexdigest()[:16]
    return timeline[: max(1, min(limit * 3, 500))]


def _aggregate_incidents(timeline: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
    for row in timeline:
        if not row.get("is_error"):
            continue
        component = str(row.get("component") or row.get("kind") or "runtime")
        code = str(row.get("code") or "RUNTIME_ERROR")
        subject = str(
            row.get("endpoint")
            or row.get("endpoint_name")
            or row.get("mcp_server")
            or row.get("server_name")
            or row.get("tool_name")
            or ""
        )
        groups.setdefault((component, code, subject), []).append(row)
    incidents: list[dict[str, Any]] = []
    for (component, code, subject), evidence in groups.items():
        evidence.sort(key=lambda item: str(item.get("ts") or ""))
        identity = f"{component}|{code}|{subject}|{evidence[0].get('ts', '')[:13]}"
        incident_id = "inc-" + hashlib.sha256(identity.encode()).hexdigest()[:16]
        incidents.append(
            {
                "incident_id": incident_id,
                "component": component,
                "code": code,
                "subject": subject,
                "count": len(evidence),
                "first_seen_at": evidence[0].get("ts", ""),
                "last_seen_at": evidence[-1].get("ts", ""),
                "status": "active",
                "rule": "same_component_code_subject_within_query_window",
                "evidence": evidence[-10:],
            }
        )
    incidents.sort(key=lambda item: str(item.get("last_seen_at") or ""), reverse=True)
    return incidents
