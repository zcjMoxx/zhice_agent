"""Stable local names for remotely discovered MCP Tools."""

from __future__ import annotations

import hashlib
import re

_UNSAFE = re.compile(r"[^A-Za-z0-9_-]+")
MAX_TOOL_NAME_CHARS = 64


def local_tool_name(server_id: str, remote_name: str) -> str:
    """Return a Registry-safe, bounded, stable MCP Tool name."""

    server = _normalize(server_id) or "server"
    remote = _normalize(remote_name) or "tool"
    candidate = f"mcp__{server}__{remote}"
    if len(candidate) <= MAX_TOOL_NAME_CHARS:
        return candidate
    digest = hashlib.sha256(candidate.encode("utf-8")).hexdigest()[:10]
    return f"{candidate[: MAX_TOOL_NAME_CHARS - 12]}__{digest}"


def _normalize(value: str) -> str:
    return _UNSAFE.sub("_", value.strip()).strip("_")
