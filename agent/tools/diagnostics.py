"""Actor-bound safe diagnostics tool."""

from __future__ import annotations

import json
from typing import Any

from agent.protocols.auth import ActorContext
from agent.protocols.tool import ToolResult
from agent.tools.base import BaseTool, require_int, require_string


class DiagnoseRecentActivityTool(BaseTool):
    """Collect current-user trace and audit evidence for the LLM to diagnose."""

    name = "diagnose_my_recent_activity"
    description = "Collect safe recent trace and audit evidence for diagnosing the current user's failures."
    parameters = {
        "type": "object",
        "properties": {
            "minutes": {"type": "integer", "minimum": 1, "maximum": 10080},
            "session_id": {"type": "string"},
            "turn_id": {"type": "string"},
            "event_type": {"type": "string"},
        },
        "additionalProperties": False,
    }

    def __init__(self, workspace, *, actor: ActorContext, diagnostics):
        super().__init__(workspace)
        self.actor = actor
        self.diagnostics = diagnostics

    def _execute(self, args: dict[str, Any]) -> ToolResult:
        payload = self.diagnostics.diagnose(
            self.actor,
            {
                "minutes": require_int(args, "minutes", default=30, minimum=1, maximum=10080),
                "session_id": require_string(args, "session_id", default=""),
                "turn_id": require_string(args, "turn_id", default=""),
                "event_type": require_string(args, "event_type", default=""),
            },
        )
        return ToolResult(
            output=json.dumps(payload, ensure_ascii=False, indent=2),
            metadata={"diagnostic": True, "events": len(payload.get("events") or [])},
        )
