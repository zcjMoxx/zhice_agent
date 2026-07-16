"""Runtime diagnostic context contracts."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class DiagnosticContext:
    """Current invocation data used to resolve recent activity automatically."""

    session_id: str
    current_turn_id: str
    current_request_id: str = ""
    channel: str = ""
