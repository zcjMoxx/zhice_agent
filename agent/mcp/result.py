"""Normalize MCP SDK results into bounded provider-neutral ToolResult values."""

from __future__ import annotations

import base64
import json
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

from agent.mcp.artifacts import (
    MAX_ARTIFACTS_PER_CALL,
    MAX_CALL_ARTIFACT_BYTES,
    McpArtifactError,
    McpArtifactGateway,
)
from agent.protocols.tool import ToolResult
from agent.tools.base import truncate_text

MAX_STRUCTURED_RESULT_CHARS = 16000
MAX_STRUCTURED_LIST_ITEMS = 5
MAX_STRUCTURED_STRING_CHARS = 1000
MAX_STRUCTURED_OUTPUT_CHARS = 10000
_LARGE_CONTENT_KEYS = frozenset(
    {"raw_content", "rawcontent", "raw_html", "html", "images", "image_descriptions"}
)


def normalize_mcp_result(
    result: Any,
    *,
    server_id: str,
    files_dir: Path,
    temp_root: Path,
    artifact_gateway: McpArtifactGateway,
) -> ToolResult:
    """Convert text/structured/binary MCP content and import safe artifacts."""

    parts: list[str] = []
    artifacts: list[str] = []
    artifact_bytes = 0
    structured_truncated = False
    structured = getattr(result, "structuredContent", None)
    if structured is None:
        structured = getattr(result, "structured_content", None)
    if structured is not None:
        encoded = json.dumps(structured, ensure_ascii=False, separators=(",", ":"), default=str)
        if len(encoded) > MAX_STRUCTURED_RESULT_CHARS:
            structured = _compact_structured(structured)
            encoded = json.dumps(structured, ensure_ascii=False, separators=(",", ":"), default=str)
            if len(encoded) > MAX_STRUCTURED_OUTPUT_CHARS:
                structured = {
                    "truncated": True,
                    "preview": encoded[: MAX_STRUCTURED_OUTPUT_CHARS - 100],
                }
                encoded = json.dumps(
                    structured, ensure_ascii=False, separators=(",", ":"), default=str
                )
            structured_truncated = True
        parts.append(encoded)
    try:
        for index, item in enumerate(getattr(result, "content", ()) or ()):
            item_type = str(getattr(item, "type", "") or "")
            if item_type == "text":
                parts.append(str(getattr(item, "text", "") or ""))
            elif item_type in {"image", "audio"}:
                if len(artifacts) >= MAX_ARTIFACTS_PER_CALL:
                    raise McpArtifactError(
                        "MCP result contains too many artifacts", "MCP_OUTPUT_TOO_LARGE"
                    )
                data = base64.b64decode(str(getattr(item, "data", "") or ""), validate=True)
                artifact_bytes += len(data)
                if artifact_bytes > MAX_CALL_ARTIFACT_BYTES:
                    raise McpArtifactError(
                        "MCP result artifact total exceeds the size limit",
                        "MCP_ARTIFACT_TOO_LARGE",
                    )
                mime = str(getattr(item, "mimeType", "") or getattr(item, "mime_type", ""))
                suffix = _mime_suffix(mime, item_type)
                path = artifact_gateway.save_bytes(
                    files_dir, server_id, f"{item_type}-{index + 1}{suffix}", data
                )
                artifacts.append(_display_path(files_dir, path))
            elif item_type in {"resource_link", "resource"}:
                uri = str(getattr(item, "uri", "") or "")
                name = str(getattr(item, "name", "") or "")
                parsed = urlparse(uri)
                if parsed.scheme == "file":
                    if len(artifacts) >= MAX_ARTIFACTS_PER_CALL:
                        raise McpArtifactError(
                            "MCP result contains too many artifacts", "MCP_OUTPUT_TOO_LARGE"
                        )
                    source = Path(unquote(parsed.path.lstrip("/")) if _windows_uri(parsed.path) else unquote(parsed.path))
                    _, source_size = artifact_gateway.inspect_temp_file(temp_root, source)
                    artifact_bytes += source_size
                    if artifact_bytes > MAX_CALL_ARTIFACT_BYTES:
                        raise McpArtifactError(
                            "MCP result artifact total exceeds the size limit",
                            "MCP_ARTIFACT_TOO_LARGE",
                        )
                    path = artifact_gateway.import_temp_file(
                        files_dir, server_id, temp_root, source, suggested_name=name
                    )
                    artifacts.append(_display_path(files_dir, path))
                else:
                    parts.append(f"Resource: {name or uri} ({uri})")
            else:
                dumped = _model_dump(item)
                if dumped:
                    parts.append(json.dumps(dumped, ensure_ascii=False, default=str))
    except (ValueError, OSError, McpArtifactError) as exc:
        return _error(str(exc) or "MCP artifact import failed", getattr(exc, "code", "MCP_ARTIFACT_INVALID"))
    if artifacts:
        parts.append("Artifacts:\n" + "\n".join(f"- {path}" for path in artifacts))
    output, truncation = truncate_text("\n\n".join(part for part in parts if part), 12000)
    is_error = bool(getattr(result, "isError", False) or getattr(result, "is_error", False))
    metadata = {
        "code": "MCP_REMOTE_ERROR" if is_error else "MCP_OK",
        "server_id": server_id,
        "artifacts": artifacts,
        **({"structured_truncated": True} if structured_truncated else {}),
        **truncation,
    }
    return ToolResult(output=output, is_error=is_error, metadata=metadata)


def _error(message: str, code: str) -> ToolResult:
    return ToolResult(output=message, is_error=True, metadata={"code": code})


def _model_dump(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    method = getattr(value, "model_dump", None)
    return method(exclude_none=True) if callable(method) else {}


def _compact_structured(value: Any, *, depth: int = 0) -> Any:
    """Preserve useful JSON shape while removing high-volume untrusted fields."""

    if depth > 8:
        return "[truncated]"
    if isinstance(value, dict):
        compact: dict[str, Any] = {}
        for key, item in value.items():
            normalized = str(key).casefold().replace("-", "_")
            if normalized in _LARGE_CONTENT_KEYS:
                continue
            compact[str(key)] = _compact_structured(item, depth=depth + 1)
        if depth == 0:
            compact["truncated"] = True
        return compact
    if isinstance(value, list | tuple):
        return [
            _compact_structured(item, depth=depth + 1)
            for item in value[:MAX_STRUCTURED_LIST_ITEMS]
        ]
    if isinstance(value, str) and len(value) > MAX_STRUCTURED_STRING_CHARS:
        return value[:MAX_STRUCTURED_STRING_CHARS] + "…"
    if value is None or isinstance(value, bool | int | float | str):
        return value
    return str(value)[:MAX_STRUCTURED_STRING_CHARS]


def _mime_suffix(mime: str, fallback: str) -> str:
    return {
        "image/png": ".png",
        "image/jpeg": ".jpg",
        "image/webp": ".webp",
        "image/gif": ".gif",
        "audio/mpeg": ".mp3",
        "audio/wav": ".wav",
        "audio/ogg": ".ogg",
    }.get(mime.lower(), f".{fallback}")


def _display_path(files_dir: Path, path: Path) -> str:
    try:
        return path.relative_to(files_dir.resolve()).as_posix()
    except ValueError:
        return path.name


def _windows_uri(path: str) -> bool:
    return len(path) >= 3 and path[0] == "/" and path[2] == ":"
