"""Runtime diagnostic context contracts."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class DiagnosticContext:
    """Current invocation data used to resolve recent activity automatically."""

    session_id: str
    current_turn_id: str
    current_request_id: str = ""
    channel: str = ""


@dataclass(frozen=True)
class SystemDiagnosticQuery:
    """Bounded transport-neutral filters for privileged runtime diagnostics."""

    minutes: int = 60
    limit: int = 100
    actor_user_id: str = ""
    session_id: str = ""
    turn_id: str = ""
    request_id: str = ""
    channel: str = ""
    component: str = ""
    endpoint: str = ""
    model: str = ""
    tool_name: str = ""
    mcp_server: str = ""
    status: str = ""
    error_code: str = ""
    incident_id: str = ""

    @classmethod
    def from_mapping(cls, value: dict[str, Any]) -> "SystemDiagnosticQuery":
        """Normalize untrusted API or Tool input into conservative bounds."""

        def text(name: str) -> str:
            return str(value.get(name) or "").strip()[:256]

        return cls(
            minutes=max(1, min(int(value.get("minutes") or 60), 10080)),
            limit=max(1, min(int(value.get("limit") or 100), 500)),
            actor_user_id=text("actor_user_id"),
            session_id=text("session_id"),
            turn_id=text("turn_id"),
            request_id=text("request_id"),
            channel=text("channel"),
            component=text("component"),
            endpoint=text("endpoint"),
            model=text("model"),
            tool_name=text("tool_name"),
            mcp_server=text("mcp_server"),
            status=text("status"),
            error_code=text("error_code"),
            incident_id=text("incident_id"),
        )
