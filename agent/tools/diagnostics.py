"""Actor-bound safe diagnostics tool."""

from __future__ import annotations

import json
from typing import Any

from agent.protocols.auth import ActorContext
from agent.protocols.diagnostics import DiagnosticContext
from agent.protocols.tool import ToolResult
from agent.subagents.presentation import (
    GENERIC_SUBAGENT_UNAVAILABLE_TEXT,
    can_view_subagent_details,
)
from agent.tools.base import BaseTool, require_int, require_string


class DiagnoseRecentActivityTool(BaseTool):
    """Collect current-user trace and audit evidence for the LLM to diagnose."""

    name = "diagnose_my_recent_activity"
    description = (
        "Inspect why the current session's previous turn was slow or failed. Returns a bounded, "
        "actor-scoped chronological trace event sequence plus activity facts so you can determine "
        "the concrete cause. Internal session, turn, and request ids are resolved automatically."
    )
    parameters = {
        "type": "object",
        "properties": {
            "focus": {
                "type": "string",
                "enum": ["auto", "latency", "failure", "trend"],
            },
            "target": {
                "type": "string",
                "enum": ["auto", "previous_turn", "latest_failure", "recent_activity"],
            },
            "minutes": {"type": "integer", "minimum": 1, "maximum": 10080},
        },
        "additionalProperties": False,
    }

    def __init__(
        self,
        workspace,
        *,
        actor: ActorContext,
        diagnostics,
        context: DiagnosticContext | None,
    ):
        super().__init__(workspace)
        self.actor = actor
        self.diagnostics = diagnostics
        self.context = context

    def _execute(self, args: dict[str, Any]) -> ToolResult:
        if self.context is None:
            return ToolResult(
                output="Diagnostic context is unavailable for this turn.",
                is_error=True,
                metadata={"code": "DIAGNOSTIC_CONTEXT_UNAVAILABLE"},
            )
        payload = self.diagnostics.diagnose(
            self.actor,
            self.context,
            {
                "focus": require_string(args, "focus", default="auto"),
                "target": require_string(args, "target", default="auto"),
                "minutes": require_int(args, "minutes", default=30, minimum=1, maximum=10080),
            },
        )
        cause_code = str(payload.get("cause_code") or "")
        if cause_code.startswith("SUBAGENT_") and not can_view_subagent_details(self.actor):
            payload = {
                "status": "diagnosed",
                "focus": str(payload.get("focus") or "failure"),
                "summary": GENERIC_SUBAGENT_UNAVAILABLE_TEXT,
                "failure_stage": "subagent",
                "cause_code": "",
                "confirmed_facts": [],
                "probable_cause": "",
                "confidence": "high",
                "evidence": [],
                "next_actions": ["Contact an administrator."],
                "limitations": ["Internal capability details are restricted."],
            }
        return ToolResult(
            output=json.dumps(payload, ensure_ascii=False, indent=2),
            metadata={
                "diagnostic": True,
                "status": str(payload.get("status") or ""),
                "cause_code": str(payload.get("cause_code") or ""),
            },
        )


class DiagnoseSystemActivityTool(BaseTool):
    """Privileged cross-user diagnostics backed by deterministic runtime evidence."""

    name = "diagnose_system_activity"
    description = (
        "Inspect bounded cross-user runtime incidents and timelines. Requires the explicit "
        "diagnostics.system.use permission and returns only allowlisted redacted evidence."
    )
    parameters = {
        "type": "object",
        "properties": {
            "minutes": {"type": "integer", "minimum": 1, "maximum": 10080},
            "limit": {"type": "integer", "minimum": 1, "maximum": 500},
            **{
                key: {"type": "string", "maxLength": 256}
                for key in (
                    "actor_user_id", "session_id", "turn_id", "request_id", "channel",
                    "component", "endpoint", "model", "tool_name", "mcp_server", "status",
                    "error_code", "incident_id",
                )
            },
        },
        "additionalProperties": False,
    }

    def __init__(self, workspace, *, actor: ActorContext, diagnostics):
        super().__init__(workspace)
        self.actor = actor
        self.diagnostics = diagnostics

    def _execute(self, args: dict[str, Any]) -> ToolResult:
        if not self.actor.has_permission("diagnostics.system.use"):
            return ToolResult(
                output="System diagnostics permission is required.",
                is_error=True,
                metadata={"code": "AUTH_PERMISSION_DENIED"},
            )
        payload = self.diagnostics.diagnose(args)
        return ToolResult(
            output=json.dumps(payload, ensure_ascii=False, indent=2),
            metadata={
                "diagnostic": True,
                "system": True,
                "incident_count": int(payload.get("summary", {}).get("incidents", 0)),
            },
        )
