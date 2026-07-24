"""Tool protocol and shared result structures."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

from agent.protocols.auth import ActorContext


@dataclass
class ToolResult:
    """Provider-neutral result returned by every tool."""

    output: str
    is_error: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        """Normalize missing tool output to an empty string."""

        if self.output is None:
            self.output = ""


@dataclass(frozen=True)
class ToolExecutionContext:
    """Stable actor/session/turn context for one parsed tool call."""

    actor: ActorContext
    session_id: str
    turn_id: str
    turn_index: int | None
    channel: str
    conversation_type: str = ""
    source: str = "llm"
    request_id: str = ""
    tool_name: str = ""
    tool_call_id: str = ""
    tool_call_record_id: str = ""
    tool_started_event_id: str = ""
    root_session_id: str = ""
    root_turn_id: str = ""
    parent_session_id: str = ""
    parent_turn_id: str = ""
    subagent_id: str = ""
    task_id: str = ""


@dataclass(frozen=True)
class ToolExecutionDecision:
    """Policy result used by AgentLoop before dispatching a tool."""

    action: Literal["allow", "deny", "confirm"]
    code: str
    message: str
    permission_key: str
    risk_level: Literal["low", "medium", "high", "critical"] = "low"
    risk_category: str = "safe"
    audit_metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ToolConfirmationResult:
    """Final user decision for one pending high-risk tool call."""

    status: Literal["approved", "denied", "expired", "cancelled"]
    confirmation_id: str
    message: str = ""


class ToolExecutionPolicy(Protocol):
    """Authorize one tool request without querying auth state from AgentLoop."""

    def decide(
        self,
        tool_name: str,
        args: dict[str, Any],
        context: ToolExecutionContext,
    ) -> ToolExecutionDecision:
        """Return allow, deny, or confirmation-required."""


class ToolConfirmationBroker(Protocol):
    """Request and await an explicit decision for a high-risk tool call."""

    def request(
        self,
        decision: ToolExecutionDecision,
        context: ToolExecutionContext,
        args: dict[str, Any],
        *,
        on_requested=None,
        is_cancelled=None,
    ) -> ToolConfirmationResult:
        """Wait for approve, deny, expiry, or cancellation."""


class Tool(Protocol):
    """Executable capability exposed to the Agent loop."""

    name: str
    description: str
    parameters: dict[str, Any]

    def execute(self, args: dict[str, Any]) -> ToolResult:
        """Execute the tool with decoded JSON object arguments."""


class ContextualTool(Tool, Protocol):
    """Optional Tool extension for dispatch that needs trusted turn context."""

    def execute_with_context(
        self,
        args: dict[str, Any],
        context: ToolExecutionContext,
    ) -> ToolResult:
        """Execute with actor/session/turn context supplied by the runtime."""


class ToolProvider(Protocol):
    """Registry contract consumed by AgentLoop."""

    def definitions(self) -> list[dict[str, Any]]:
        """Return OpenAI-compatible tool definitions."""

    def execute(self, name: str, args: dict[str, Any]) -> ToolResult:
        """Execute one registered tool by name."""


class ContextualToolProvider(ToolProvider, Protocol):
    """Optional provider extension for trusted per-call execution context."""

    def execute_with_context(
        self,
        name: str,
        args: dict[str, Any],
        context: ToolExecutionContext,
    ) -> ToolResult:
        """Execute a registered tool with trusted runtime context."""
