"""Shared helpers for built-in tools."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from agent.protocols.tool import ToolResult

DEFAULT_MAX_TOOL_OUTPUT_CHARS = 12000


class ToolExecutionError(RuntimeError):
    """Structured tool failure that can be returned to the model."""

    def __init__(self, output: str, code: str, metadata: dict[str, Any] | None = None):
        super().__init__(output)
        self.output = output
        self.code = code
        self.metadata = dict(metadata or {})


class BaseTool:
    """Base class that converts implementation failures into ToolResult."""

    name: str
    description: str
    parameters: dict[str, Any]

    def __init__(self, workspace: Path | str):
        self.workspace = Path(workspace).expanduser().resolve()

    def execute(self, args: dict[str, Any]) -> ToolResult:
        """Execute a concrete read-only tool safely."""

        try:
            return self._execute(args)
        except ToolExecutionError as exc:
            metadata = {"code": exc.code, **exc.metadata}
            return ToolResult(output=exc.output, is_error=True, metadata=metadata)
        except Exception as exc:  # noqa: BLE001 - tools must not break AgentLoop.
            return ToolResult(
                output="Tool execution failed.",
                is_error=True,
                metadata={"code": "INTERNAL_ERROR", "error_type": type(exc).__name__},
            )

    def _execute(self, args: dict[str, Any]) -> ToolResult:
        raise NotImplementedError


def resolve_workspace_path(workspace: Path, value: str | None) -> Path:
    """Resolve a model-supplied path and require it to stay inside workspace."""

    raw_path = Path(value.strip()) if isinstance(value, str) and value.strip() else Path(".")
    candidate = raw_path.expanduser()
    if not candidate.is_absolute():
        candidate = workspace / candidate
    resolved = candidate.resolve(strict=False)
    if not _is_relative_to(resolved, workspace):
        raise ToolExecutionError(
            "Path is outside the workspace.",
            "PATH_OUTSIDE_WORKSPACE",
            {"path": str(value or ".")},
        )
    return resolved


def relative_display_path(workspace: Path, path: Path) -> str:
    """Return a stable slash-separated path for tool output."""

    try:
        return path.relative_to(workspace).as_posix() or "."
    except ValueError:
        return path.name


def truncate_text(
    text: str,
    max_chars: int = DEFAULT_MAX_TOOL_OUTPUT_CHARS,
) -> tuple[str, dict[str, Any]]:
    """Bound tool output length and return truncation metadata."""

    if len(text) <= max_chars:
        return text, {"truncated": False}
    marker = "[truncated]"
    if max_chars <= len(marker):
        truncated = marker[:max_chars]
        return truncated, {
            "truncated": True,
            "original_chars": len(text),
            "returned_chars": len(truncated),
        }
    keep = max_chars - len(marker)
    truncated = f"{text[:keep]}{marker}"
    return truncated, {
        "truncated": True,
        "original_chars": len(text),
        "returned_chars": len(truncated),
    }


def require_int(
    args: dict[str, Any],
    name: str,
    *,
    default: int,
    minimum: int,
    maximum: int,
) -> int:
    """Read a bounded integer argument."""

    value = args.get(name, default)
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum or value > maximum:
        raise ToolExecutionError(
            f"Invalid integer parameter: {name}",
            "INVALID_PARAM",
            {"parameter": name},
        )
    return value


def require_bool(args: dict[str, Any], name: str, *, default: bool) -> bool:
    """Read a boolean argument."""

    value = args.get(name, default)
    if not isinstance(value, bool):
        raise ToolExecutionError(
            f"Invalid boolean parameter: {name}",
            "INVALID_PARAM",
            {"parameter": name},
        )
    return value


def require_string(
    args: dict[str, Any],
    name: str,
    *,
    default: str | None = None,
    required: bool = False,
) -> str:
    """Read a string argument."""

    value = args.get(name, default)
    if required and (value is None or value == ""):
        raise ToolExecutionError(
            f"Missing required parameter: {name}",
            "MISSING_PARAM",
            {"parameter": name},
        )
    if value is None:
        return ""
    if not isinstance(value, str):
        raise ToolExecutionError(
            f"Invalid string parameter: {name}",
            "INVALID_PARAM",
            {"parameter": name},
        )
    return value


def _is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return True
