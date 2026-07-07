"""Common message models used by the Agent runtime.

The first stage only stores user messages, but the model already includes
assistant/tool fields so later AgentLoop and ToolRegistry work can reuse it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

Role = Literal["system", "user", "assistant", "tool"]


@dataclass
class Message:
    """A single conversation message in the Agent context."""

    role: Role
    content: str
    name: str | None = None
    tool_call_id: str | None = None
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    turn_id: str | None = None
    turn_index: int | None = None
    parent_turn_id: str | None = None

    def __post_init__(self) -> None:
        """Keep message content predictable for persistence and prompt assembly."""

        if not isinstance(self.content, str):
            raise TypeError("Message.content must be a string")
        if self.turn_index is not None and not isinstance(self.turn_index, int):
            raise TypeError("Message.turn_index must be an integer or None")
