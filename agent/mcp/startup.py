"""Startup boundary for the optional MCP capability."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from agent.logging_utils import log_event
from agent.mcp.config import McpConfigError, load_mcp_server_specs
from agent.protocols.capability import CapabilityStatus
from agent.protocols.mcp import McpServerSpec

startup_logger = logging.getLogger("zcagent.agent.mcp")


@dataclass(frozen=True)
class McpStartupResult:
    """Validated MCP specs plus safe optional-capability status."""

    specs: tuple[McpServerSpec, ...]
    status: CapabilityStatus


def check_mcp_startup(config_dir: Path | str) -> McpStartupResult:
    """Fail closed for MCP while allowing the core Agent to start."""

    path = Path(config_dir).expanduser().resolve() / "config.yml"
    if not path.exists():
        return _disabled("MCP_CONFIG_MISSING", "MCP is not configured for this workspace.")
    try:
        specs = load_mcp_server_specs(path.parent)
    except (McpConfigError, OSError) as exc:
        log_event(
            startup_logger,
            logging.WARNING,
            "mcp.runtime_unavailable",
            code="MCP_CONFIG_INVALID",
            config_file=path.name,
            error_type=type(exc).__name__,
        )
        return McpStartupResult(
            specs=(),
            status=CapabilityStatus(
                name="mcp",
                state="unavailable",
                code="MCP_CONFIG_INVALID",
                message="MCP configuration is invalid.",
                hint="Fix the mcp section in config/config.yml, then restart the process.",
                details={"config_file": path.name, "error_type": type(exc).__name__},
            ),
        )
    if not specs:
        return _disabled("MCP_DISABLED", "MCP has no configured servers.")
    return McpStartupResult(
        specs=specs,
        status=CapabilityStatus(
            name="mcp",
            state="available",
            code="MCP_AVAILABLE",
            message=f"MCP is configured with {len(specs)} server(s).",
            details={"server_count": len(specs)},
        ),
    )


def _disabled(code: str, message: str) -> McpStartupResult:
    return McpStartupResult(
        specs=(),
        status=CapabilityStatus(
            name="mcp",
            state="disabled",
            code=code,
            message=message,
            hint="Configure the mcp.servers section in config/config.yml to enable MCP.",
        ),
    )
