"""Actor-bound safe diagnostics tool."""

from __future__ import annotations

import json
from typing import Any

from agent.protocols.auth import ActorContext
from agent.protocols.diagnostics import DiagnosticContext
from agent.protocols.tool import ToolResult
from agent.tools.base import BaseTool, require_int, require_string


class DiagnoseRecentActivityTool(BaseTool):
    """Collect current-user trace and audit evidence for the LLM to diagnose."""

    name = "diagnose_my_recent_activity"
    description = (
        "Diagnose why the current session's previous turn was slow or failed, or summarize "
        "recent repeated failures. Internal session, turn, and request ids are resolved automatically."
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
        return ToolResult(
            output=json.dumps(payload, ensure_ascii=False, indent=2),
            metadata={
                "diagnostic": True,
                "status": str(payload.get("status") or ""),
                "cause_code": str(payload.get("cause_code") or ""),
            },
        )
