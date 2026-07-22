"""Transport-neutral runtime capability availability contracts."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

CapabilityState = Literal["available", "unavailable", "degraded", "disabled"]


@dataclass(frozen=True)
class CapabilityStatus:
    """Safe startup and runtime status for one optional capability."""

    name: str
    state: CapabilityState
    code: str = ""
    message: str = ""
    hint: str = ""
    details: dict[str, Any] = field(default_factory=dict)

    @property
    def available(self) -> bool:
        """Return whether callers may expose and execute the capability."""

        return self.state == "available"

    def to_dict(self) -> dict[str, Any]:
        """Return a stable JSON-ready status payload."""

        return {
            "name": self.name,
            "state": self.state,
            "code": self.code,
            "message": self.message,
            "hint": self.hint,
            "details": dict(self.details),
        }
