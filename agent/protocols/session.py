"""Session persistence protocol.

AgentLoop will depend on this protocol instead of a concrete JSONL store, which
keeps later storage changes local to the session implementation.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from agent.message import Message


@dataclass
class SessionState:
    """A loaded session snapshot."""

    session_id: str
    messages: list[Message]
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class SessionSummary:
    """Compact metadata used to render a session list in the CLI."""

    session_id: str
    preview: str
    updated_at: float
    message_count: int


class SessionStore(Protocol):
    """Minimal persistence contract used by the Agent runtime."""

    def load(self, session_id: str) -> SessionState:
        """Load a session, returning an empty state when it does not exist."""

    def append(self, session_id: str, messages: list[Message]) -> None:
        """Append messages to a session without rewriting existing history."""

    def clear(self, session_id: str) -> None:
        """Remove all persisted messages for a session."""

    def list_sessions(self) -> list[SessionSummary]:
        """Return stored sessions ordered for CLI presentation."""
