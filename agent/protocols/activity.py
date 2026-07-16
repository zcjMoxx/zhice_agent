"""Provider-neutral runtime activity contracts."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from agent.protocols.auth import ActorContext


@dataclass(frozen=True)
class RuntimeActivityEvent:
    """Structured turn/tool activity used for runtime indexes, not security audit."""

    action: str
    actor: ActorContext | None = None
    resource_id: str = ""
    request_id: str = ""
    channel: str = ""
    session_id: str = ""
    turn_id: str = ""
    tool_call_record_id: str = ""
    decision: str = ""
    reason_code: str = ""
    risk_category: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


class RuntimeActivitySink(Protocol):
    """Persist structured runtime activity without creating security audit rows."""

    def record(self, event: RuntimeActivityEvent) -> None:
        """Persist or emit one runtime activity event."""
