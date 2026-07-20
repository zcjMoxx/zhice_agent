"""Validation and snapshot construction for MCP ``tools/list`` results."""

from __future__ import annotations

import json
from typing import Any, Iterable

from agent.mcp.naming import local_tool_name
from agent.protocols.mcp import McpToolDescriptor

MAX_TOOLS_PER_SERVER = 64
MAX_TOOL_DESCRIPTION_CHARS = 1200
MAX_TOOL_SCHEMA_CHARS = 16000
MAX_SCHEMA_DEPTH = 12


def build_tool_descriptors(
    server_id: str,
    raw_tools: Iterable[Any],
    *,
    reserved_names: set[str] | None = None,
) -> tuple[tuple[McpToolDescriptor, ...], tuple[str, ...]]:
    """Validate remote Tool metadata and return accepted descriptors plus errors."""

    descriptors: list[McpToolDescriptor] = []
    errors: list[str] = []
    seen = set(reserved_names or ())
    for index, tool in enumerate(raw_tools):
        if index >= MAX_TOOLS_PER_SERVER:
            errors.append("MCP_TOOL_LIMIT_EXCEEDED")
            break
        remote_name = str(getattr(tool, "name", "") or "").strip()
        description = str(getattr(tool, "description", "") or "")[:MAX_TOOL_DESCRIPTION_CHARS]
        schema = getattr(tool, "inputSchema", None)
        if schema is None:
            schema = getattr(tool, "input_schema", None)
        annotations_raw = getattr(tool, "annotations", None)
        annotations = _model_dump(annotations_raw) if annotations_raw is not None else {}
        local_name = local_tool_name(server_id, remote_name)
        error = _schema_error(schema)
        if not remote_name or error:
            errors.append(f"{remote_name or index}:{error or 'MCP_SCHEMA_INVALID'}")
            continue
        if local_name in seen:
            errors.append(f"{remote_name}:MCP_TOOL_NAME_COLLISION")
            continue
        seen.add(local_name)
        descriptors.append(
            McpToolDescriptor(
                server_id=server_id,
                remote_name=remote_name,
                local_name=local_name,
                description=description or f"MCP Tool {remote_name} from {server_id}",
                input_schema=dict(schema),
                annotations=annotations,
            )
        )
    return tuple(descriptors), tuple(errors)


def _schema_error(schema: Any) -> str:
    if not isinstance(schema, dict) or schema.get("type") != "object":
        return "MCP_SCHEMA_INVALID"
    try:
        encoded = json.dumps(schema, ensure_ascii=False, separators=(",", ":"))
    except (TypeError, ValueError):
        return "MCP_SCHEMA_INVALID"
    if len(encoded) > MAX_TOOL_SCHEMA_CHARS or _depth(schema) > MAX_SCHEMA_DEPTH:
        return "MCP_SCHEMA_INVALID"
    return ""


def _depth(value: Any, current: int = 0) -> int:
    if isinstance(value, dict):
        return max([current, *(_depth(item, current + 1) for item in value.values())])
    if isinstance(value, list):
        return max([current, *(_depth(item, current + 1) for item in value)])
    return current


def _model_dump(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    method = getattr(value, "model_dump", None)
    return method(exclude_none=True) if callable(method) else {}
