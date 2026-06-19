"""Read-only local workspace tools."""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any, Iterable

from agent.protocols.tool import ToolResult
from agent.tools.base import (
    DEFAULT_MAX_TOOL_OUTPUT_CHARS,
    BaseTool,
    ToolExecutionError,
    relative_display_path,
    require_bool,
    require_int,
    require_string,
    resolve_workspace_path,
    truncate_text,
)

_SKIPPED_DIRS = {".git", ".pytest_cache", ".ruff_cache", "__pycache__", ".venv", "venv", "node_modules"}


class ListDirTool(BaseTool):
    """List direct children of a workspace directory."""

    name = "list_dir"
    description = "List files and directories inside the workspace."
    parameters = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Directory path. Defaults to '.'."},
            "max_entries": {
                "type": "integer",
                "minimum": 1,
                "maximum": 500,
                "description": "Maximum entries to return.",
            },
            "include_hidden": {
                "type": "boolean",
                "description": "Whether to include hidden entries.",
            },
        },
        "required": [],
        "additionalProperties": False,
    }

    def _execute(self, args: dict[str, Any]) -> ToolResult:
        """List one workspace directory with hidden-file and count controls."""

        path_value = require_string(args, "path", default=".")
        max_entries = require_int(args, "max_entries", default=200, minimum=1, maximum=500)
        include_hidden = require_bool(args, "include_hidden", default=False)

        target = resolve_workspace_path(self.workspace, path_value)
        if not target.exists():
            raise ToolExecutionError("Path does not exist.", "NOT_FOUND", {"path": path_value})
        if not target.is_dir():
            raise ToolExecutionError("Path is not a directory.", "NOT_DIRECTORY", {"path": path_value})

        entries = [
            entry
            for entry in target.iterdir()
            if include_hidden or not _is_hidden(entry, self.workspace)
        ]
        entries.sort(key=lambda item: (not item.is_dir(), item.name.lower()))
        selected = entries[:max_entries]

        lines = [_format_dir_entry(entry) for entry in selected]
        was_entry_truncated = len(entries) > len(selected)
        if was_entry_truncated:
            lines.append("[truncated]")
        output, truncation = truncate_text("\n".join(lines), DEFAULT_MAX_TOOL_OUTPUT_CHARS)
        metadata = {
            "path": relative_display_path(self.workspace, target),
            "total_entries": len(entries),
            "returned_entries": len(selected),
            "truncated": was_entry_truncated or truncation["truncated"],
        }
        if truncation["truncated"]:
            metadata.update(truncation)
        return ToolResult(output=output, metadata=metadata)


class ReadFileTool(BaseTool):
    """Read a UTF-8 text file inside the workspace."""

    name = "read_file"
    description = "Read a UTF-8 text file inside the workspace."
    parameters = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "File path inside the workspace."},
            "start_line": {"type": "integer", "minimum": 1, "description": "1-based start line."},
            "max_lines": {
                "type": "integer",
                "minimum": 1,
                "maximum": 1000,
                "description": "Maximum lines to return.",
            },
            "max_chars": {
                "type": "integer",
                "minimum": 1,
                "maximum": 50000,
                "description": "Maximum characters to return.",
            },
        },
        "required": ["path"],
        "additionalProperties": False,
    }

    def _execute(self, args: dict[str, Any]) -> ToolResult:
        """Read a bounded UTF-8 slice from one workspace file."""

        path_value = require_string(args, "path", required=True)
        start_line = require_int(args, "start_line", default=1, minimum=1, maximum=1_000_000)
        max_lines = require_int(args, "max_lines", default=300, minimum=1, maximum=1000)
        max_chars = require_int(args, "max_chars", default=DEFAULT_MAX_TOOL_OUTPUT_CHARS, minimum=1, maximum=50000)

        target = resolve_workspace_path(self.workspace, path_value)
        if not target.exists():
            raise ToolExecutionError("File does not exist.", "NOT_FOUND", {"path": path_value})
        if not target.is_file():
            raise ToolExecutionError("Path is not a file.", "NOT_FILE", {"path": path_value})

        lines: list[str] = []
        returned_lines = 0
        truncated = False
        try:
            with target.open("r", encoding="utf-8") as file:
                for line_number, raw_line in enumerate(file, start=1):
                    if line_number < start_line:
                        continue
                    if returned_lines >= max_lines:
                        truncated = True
                        break
                    line_text = raw_line.rstrip("\r\n")
                    formatted = f"{line_number}: {line_text}"
                    projected = len("\n".join([*lines, formatted]))
                    if projected > max_chars:
                        lines.append(formatted)
                        returned_lines += 1
                        truncated = True
                        break
                    lines.append(formatted)
                    returned_lines += 1
        except UnicodeDecodeError:
            return ToolResult(
                output="File is not valid UTF-8 text.",
                is_error=True,
                metadata={"code": "DECODE_ERROR", "path": path_value},
            )

        output = "\n".join(lines)
        if truncated and len(output) < max_chars:
            output = f"{output}\n[truncated]" if output else "[truncated]"
        output, truncation = truncate_text(output, max_chars)
        metadata = {
            "path": relative_display_path(self.workspace, target),
            "start_line": start_line,
            "returned_lines": returned_lines,
            "truncated": truncated or truncation["truncated"],
        }
        if truncation["truncated"]:
            metadata.update(truncation)
        return ToolResult(output=output, metadata=metadata)


class GrepTool(BaseTool):
    """Search UTF-8 text files inside the workspace."""

    name = "grep"
    description = "Search text inside workspace files."
    parameters = {
        "type": "object",
        "properties": {
            "pattern": {"type": "string", "description": "Regular expression pattern."},
            "path": {"type": "string", "description": "File or directory path. Defaults to '.'."},
            "case_sensitive": {"type": "boolean", "description": "Whether matching is case sensitive."},
            "max_matches": {
                "type": "integer",
                "minimum": 1,
                "maximum": 500,
                "description": "Maximum matching lines to return.",
            },
            "include_hidden": {
                "type": "boolean",
                "description": "Whether to search hidden files and directories.",
            },
        },
        "required": ["pattern"],
        "additionalProperties": False,
    }

    def _execute(self, args: dict[str, Any]) -> ToolResult:
        """Search workspace text files with a bounded regular-expression scan."""

        pattern = require_string(args, "pattern", required=True)
        path_value = require_string(args, "path", default=".")
        case_sensitive = require_bool(args, "case_sensitive", default=False)
        max_matches = require_int(args, "max_matches", default=100, minimum=1, maximum=500)
        include_hidden = require_bool(args, "include_hidden", default=False)

        flags = 0 if case_sensitive else re.IGNORECASE
        try:
            compiled = re.compile(pattern, flags)
        except re.error as exc:
            return ToolResult(
                output=f"Invalid regular expression: {exc}",
                is_error=True,
                metadata={"code": "INVALID_PATTERN"},
            )

        target = resolve_workspace_path(self.workspace, path_value)
        if not target.exists():
            raise ToolExecutionError("Path does not exist.", "NOT_FOUND", {"path": path_value})

        matches: list[str] = []
        skipped_files = 0
        truncated = False
        for file_path in _iter_search_files(target, self.workspace, include_hidden):
            if len(matches) >= max_matches:
                truncated = True
                break
            try:
                for line_number, line in _iter_text_lines(file_path):
                    if compiled.search(line):
                        rel = relative_display_path(self.workspace, file_path)
                        line_text = line.rstrip("\r\n")
                        matches.append(f"{rel}:{line_number}: {line_text}")
                        if len(matches) >= max_matches:
                            truncated = True
                            break
            except UnicodeDecodeError:
                skipped_files += 1
            if len(matches) >= max_matches:
                break

        output, truncation = truncate_text("\n".join(matches), DEFAULT_MAX_TOOL_OUTPUT_CHARS)
        metadata = {
            "path": relative_display_path(self.workspace, target),
            "matches": len(matches),
            "skipped_files": skipped_files,
            "truncated": truncated or truncation["truncated"],
        }
        if truncation["truncated"]:
            metadata.update(truncation)
        return ToolResult(output=output, metadata=metadata)


def _format_dir_entry(entry: Path) -> str:
    """Render one directory listing row with type and file size."""

    if entry.is_dir():
        return f"DIR  {entry.name}"
    try:
        size = entry.stat().st_size
    except OSError:
        size = 0
    return f"FILE {entry.name} {size} bytes"


def _is_hidden(path: Path, workspace: Path) -> bool:
    """Return whether any relative path part starts with a dot."""

    try:
        relative_parts = path.relative_to(workspace).parts
    except ValueError:
        relative_parts = path.parts
    return any(part.startswith(".") for part in relative_parts)


def _iter_search_files(target: Path, workspace: Path, include_hidden: bool) -> Iterable[Path]:
    """Yield searchable files under a file or directory target."""

    if target.is_file():
        if _is_safe_file(target, workspace) and (include_hidden or not _is_hidden(target, workspace)):
            yield target
        return
    if not target.is_dir():
        raise ToolExecutionError("Path is not a file or directory.", "INVALID_PARAM")

    for root, dirnames, filenames in os.walk(target):
        root_path = Path(root)
        dirnames[:] = sorted(
            [
                dirname
                for dirname in dirnames
                if _should_visit_dir(root_path / dirname, workspace, include_hidden)
            ],
            key=str.lower,
        )
        for filename in sorted(filenames, key=str.lower):
            file_path = root_path / filename
            if include_hidden or not _is_hidden(file_path, workspace):
                if _is_safe_file(file_path, workspace):
                    yield file_path


def _should_visit_dir(path: Path, workspace: Path, include_hidden: bool) -> bool:
    """Decide whether grep should descend into one directory."""

    if path.name in _SKIPPED_DIRS:
        return False
    if not include_hidden and _is_hidden(path, workspace):
        return False
    return _is_safe_path(path, workspace)


def _is_safe_file(path: Path, workspace: Path) -> bool:
    """Return whether path is a regular file inside the workspace."""

    return path.is_file() and _is_safe_path(path, workspace)


def _is_safe_path(path: Path, workspace: Path) -> bool:
    """Return whether a path resolves inside the workspace root."""

    try:
        path.resolve(strict=False).relative_to(workspace)
    except ValueError:
        return False
    return True


def _iter_text_lines(path: Path) -> Iterable[tuple[int, str]]:
    """Yield UTF-8 text lines and reject binary-looking NUL content."""

    with path.open("r", encoding="utf-8") as file:
        for line_number, line in enumerate(file, start=1):
            if "\x00" in line:
                raise UnicodeDecodeError("utf-8", b"", 0, 1, "NUL byte in text")
            yield line_number, line
