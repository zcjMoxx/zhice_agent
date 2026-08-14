"""Provider-neutral MCP configuration, catalog, and interaction contracts."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Literal, Protocol

from agent.protocols.auth import ActorContext
from agent.protocols.tool import Tool, ToolResult

McpTransport = Literal["stdio", "streamable_http", "sse"]
McpProxyMode = Literal["direct", "environment"]


@dataclass(frozen=True)
class McpOAuthSpec:
    """OAuth refresh data resolved from workspace configuration."""

    token_url: str
    access_token: str = ""
    refresh_token: str = ""
    client_id: str = ""
    client_secret: str = ""
    scope: str = ""
    expires_at: float | None = None


@dataclass(frozen=True)
class McpServerSpec:
    """Normalized configuration for one workspace-shared MCP Server."""

    server_id: str
    transport: McpTransport
    command: str = ""
    args: tuple[str, ...] = ()
    cwd: str = ""
    env: dict[str, str] = field(default_factory=dict)
    url: str = ""
    headers: dict[str, str] = field(default_factory=dict)
    proxy_mode: McpProxyMode = "direct"
    oauth: McpOAuthSpec | None = None
    startup_timeout_seconds: float = 15.0
    connect_timeout_seconds: float = 15.0
    call_timeout_seconds: float = 60.0


@dataclass(frozen=True)
class McpToolDescriptor:
    """Validated remote Tool metadata exposed through ToolProvider."""

    server_id: str
    remote_name: str
    local_name: str
    description: str
    input_schema: dict[str, Any]
    annotations: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class McpServerStatus:
    """Public, credential-free Server health summary."""

    server_id: str
    state: Literal["disabled", "connecting", "ready", "degraded", "closed"]
    tool_count: int = 0
    error_code: str = ""


@dataclass(frozen=True)
class McpCatalogSnapshot:
    """Immutable process-wide Tool Catalog snapshot."""

    tools: tuple[McpToolDescriptor, ...] = ()
    servers: tuple[McpServerStatus, ...] = ()
    version: int = 0
    generated_at: float = 0.0


@dataclass(frozen=True)
class McpConnectionEvent:
    """One bounded, credential-free Server connection history entry."""

    server_id: str
    state: str
    timestamp: float
    reason_code: str = ""


@dataclass(frozen=True)
class McpToolStats:
    """Process-lifetime latency and outcome counters for one remote Tool."""

    server_id: str
    tool_name: str
    call_count: int = 0
    success_count: int = 0
    error_count: int = 0
    cancelled_count: int = 0
    total_duration_ms: int = 0
    max_duration_ms: int = 0
    last_error_code: str = ""


@dataclass(frozen=True)
class McpOAuthStatus:
    """Credential-free OAuth lifecycle status for one Server."""

    server_id: str
    state: Literal["disabled", "configured", "refreshing", "ready", "error"]
    refresh_count: int = 0
    last_error_code: str = ""
    expires_at: float | None = None


@dataclass(frozen=True)
class McpRuntimeStatsSnapshot:
    """Immutable diagnostic snapshot for MCP health and Tool execution."""

    catalog_version: int = 0
    active_calls: int = 0
    catalog_refresh_count: int = 0
    list_changed_count: int = 0
    reconnect_count: int = 0
    connection_history: tuple[McpConnectionEvent, ...] = ()
    tools: tuple[McpToolStats, ...] = ()
    oauth: tuple[McpOAuthStatus, ...] = ()


@dataclass(frozen=True)
class McpInteractionRequest:
    """Server-originated user interaction request."""

    interaction_id: str
    server_id: str
    mode: Literal["form", "url"]
    message: str
    requested_schema: dict[str, Any] = field(default_factory=dict)
    url: str = ""


@dataclass(frozen=True)
class McpInteractionResponse:
    """User answer returned to an MCP Server."""

    action: Literal["accept", "decline", "cancel"]
    content: dict[str, Any] | None = None


McpInteractionNotifier = Callable[[McpInteractionRequest], None]


class McpRuntimeFacade(Protocol):
    """Synchronous facade consumed by application and Tool layers."""

    def snapshot(self) -> McpCatalogSnapshot:
        """Return the current credential-free Catalog."""

    def tools_for_actor(
        self,
        actor: ActorContext,
        files_dir: Path,
        *,
        session_id: str = "",
        interaction_notifier: McpInteractionNotifier | None = None,
    ) -> list[Tool]:
        """Create actor-bound adapters over the shared Catalog."""

    def call_tool_sync(
        self,
        descriptor: McpToolDescriptor,
        args: dict[str, Any],
        *,
        actor: ActorContext,
        files_dir: Path,
        session_id: str = "",
        interaction_notifier: McpInteractionNotifier | None = None,
    ) -> ToolResult:
        """Call one remote Tool without exposing asyncio to AgentLoop."""

    def submit_interaction(
        self,
        interaction_id: str,
        response: McpInteractionResponse,
    ) -> bool:
        """Resolve one pending Server-originated interaction."""

    def refresh_catalog(self, server_id: str) -> bool:
        """Atomically refresh one Server's Tool Catalog."""

    def reload(self, specs: tuple[McpServerSpec, ...] | list[McpServerSpec]) -> bool:
        """Apply a validated Server configuration without restarting the Gateway."""

    def reconnect(self, server_id: str) -> bool:
        """Reconnect one Server without restarting the Gateway."""

    def cancel_active_calls(
        self,
        server_id: str | None = None,
        *,
        user_id: str | None = None,
        session_id: str | None = None,
    ) -> int:
        """Propagate cancellation to matching active MCP calls."""

    def stats_snapshot(self) -> McpRuntimeStatsSnapshot:
        """Return credential-free process-lifetime MCP diagnostics."""

    def close(self) -> None:
        """Close connections, subprocesses, and the event-loop thread."""
