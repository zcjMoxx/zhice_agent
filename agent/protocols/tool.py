"""Tool protocol and shared result structures."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass
class ToolResult:
    """Provider-neutral result returned by every tool."""

    output: str
    is_error: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.output is None:
            self.output = ""


class Tool(Protocol):
    """Executable capability exposed to the Agent loop."""

    name: str
    description: str
    parameters: dict[str, Any]

    def execute(self, args: dict[str, Any]) -> ToolResult:
        """Execute the tool with decoded JSON object arguments."""


class ToolProvider(Protocol):
    """Registry contract consumed by AgentLoop."""

    def definitions(self) -> list[dict[str, Any]]:
        """Return OpenAI-compatible tool definitions."""

    def execute(self, name: str, args: dict[str, Any]) -> ToolResult:
        """Execute one registered tool by name."""
