"""Safe local exec tool."""

from __future__ import annotations

import subprocess
import time
from pathlib import Path
from typing import Any

from agent.protocols.tool import ToolResult
from agent.tools.base import (
    BaseTool,
    ToolExecutionError,
    relative_display_path,
    require_int,
    require_string,
    resolve_workspace_path,
    truncate_text,
)
from agent.tools.shell_policy import redact_secrets, validate_command

DEFAULT_EXEC_TIMEOUT_SECONDS = 30
MAX_EXEC_TIMEOUT_SECONDS = 120
DEFAULT_EXEC_OUTPUT_CHARS = 12000


class ExecTool(BaseTool):
    """Run a bounded non-interactive command inside the workspace."""

    name = "exec"
    description = "Run a safe non-interactive command inside the workspace."
    parameters = {
        "type": "object",
        "properties": {
            "command": {
                "type": "string",
                "description": "Single non-interactive command to run.",
            },
            "cwd": {
                "type": "string",
                "description": "Working directory relative to the workspace. Defaults to '.'.",
            },
            "timeout_seconds": {
                "type": "integer",
                "minimum": 1,
                "maximum": MAX_EXEC_TIMEOUT_SECONDS,
                "description": "Command timeout in seconds.",
            },
            "max_output_chars": {
                "type": "integer",
                "minimum": 1000,
                "maximum": 50000,
                "description": "Maximum combined stdout and stderr characters.",
            },
        },
        "required": ["command"],
        "additionalProperties": False,
    }

    def __init__(self, workspace: Path | str, *, allow_confirmable: bool = False):
        """Keep risky categories blocked unless an outer policy/confirmation layer exists."""

        super().__init__(workspace)
        self.allow_confirmable = allow_confirmable

    def _execute(self, args: dict[str, Any]) -> ToolResult:
        """Validate parameters, enforce policy, run the command, and format output."""

        command = require_string(args, "command", required=True).strip()
        if not command:
            raise ToolExecutionError(
                "Missing required parameter: command",
                "MISSING_PARAM",
                {"parameter": "command"},
            )
        cwd_value = require_string(args, "cwd", default=".")
        timeout_seconds = require_int(
            args,
            "timeout_seconds",
            default=DEFAULT_EXEC_TIMEOUT_SECONDS,
            minimum=1,
            maximum=MAX_EXEC_TIMEOUT_SECONDS,
        )
        max_output_chars = require_int(
            args,
            "max_output_chars",
            default=DEFAULT_EXEC_OUTPUT_CHARS,
            minimum=1000,
            maximum=50000,
        )

        cwd = resolve_workspace_path(self.workspace, cwd_value)
        if not cwd.exists():
            raise ToolExecutionError("Working directory does not exist.", "NOT_FOUND", {"cwd": cwd_value})
        if not cwd.is_dir():
            raise ToolExecutionError("Working directory is not a directory.", "NOT_DIRECTORY", {"cwd": cwd_value})

        policy = validate_command(command)
        if not policy.allowed:
            raise ToolExecutionError(
                policy.message,
                policy.code,
                {"command_category": policy.category},
            )
        if policy.requires_confirmation and not self.allow_confirmable:
            code = (
                "NETWORK_COMMAND_BLOCKED"
                if policy.risk_category == "network"
                else "DESTRUCTIVE_COMMAND_BLOCKED"
            )
            raise ToolExecutionError(
                "High-risk command requires an execution policy and explicit confirmation.",
                code,
                {
                    "command_category": policy.category,
                    "risk_category": policy.risk_category,
                    "requires_confirmation": True,
                },
            )

        started = time.monotonic()
        try:
            completed = subprocess.run(
                command,
                cwd=cwd,
                shell=True,
                text=True,
                capture_output=True,
                timeout=timeout_seconds,
                encoding="utf-8",
                errors="replace",
            )
        except subprocess.TimeoutExpired as exc:
            duration = time.monotonic() - started
            return _timeout_result(
                workspace=self.workspace,
                cwd=cwd,
                timeout_seconds=timeout_seconds,
                duration_seconds=duration,
                stdout=_coerce_output(exc.stdout),
                stderr=_coerce_output(exc.stderr),
                max_output_chars=max_output_chars,
            )
        except OSError as exc:
            raise ToolExecutionError(
                "Command execution failed.",
                "COMMAND_EXECUTION_ERROR",
                {"error_type": type(exc).__name__},
            ) from exc

        duration = time.monotonic() - started
        stdout = redact_secrets(completed.stdout or "")
        stderr = redact_secrets(completed.stderr or "")
        output = _format_exec_output(
            exit_code=completed.returncode,
            cwd=relative_display_path(self.workspace, cwd),
            stdout=stdout,
            stderr=stderr,
        )
        output, truncation = truncate_text(output, max_output_chars)
        metadata = {
            "cwd": relative_display_path(self.workspace, cwd),
            "exit_code": completed.returncode,
            "duration_seconds": round(duration, 3),
            "timed_out": False,
            "stdout_chars": len(stdout),
            "stderr_chars": len(stderr),
            "stdout_tail": stdout[-500:],
            "stderr_tail": stderr[-500:],
            "truncated": truncation["truncated"],
            "risk_category": policy.risk_category,
        }
        if truncation["truncated"]:
            metadata.update(truncation)
        if completed.returncode != 0:
            metadata["code"] = "COMMAND_FAILED"
        return ToolResult(
            output=output,
            is_error=completed.returncode != 0,
            metadata=metadata,
        )


def _timeout_result(
    *,
    workspace: Path,
    cwd: Path,
    timeout_seconds: int,
    duration_seconds: float,
    stdout: str,
    stderr: str,
    max_output_chars: int,
) -> ToolResult:
    """Build the structured ToolResult returned when subprocess timeout fires."""

    stdout = redact_secrets(stdout)
    stderr = redact_secrets(stderr)
    output = _format_exec_output(
        exit_code=None,
        cwd=relative_display_path(workspace, cwd),
        stdout=stdout,
        stderr=stderr,
        header=f"Command timed out after {timeout_seconds} seconds.",
    )
    output, truncation = truncate_text(output, max_output_chars)
    metadata = {
        "code": "COMMAND_TIMEOUT",
        "cwd": relative_display_path(workspace, cwd),
        "duration_seconds": round(duration_seconds, 3),
        "timeout_seconds": timeout_seconds,
        "timed_out": True,
        "stdout_chars": len(stdout),
        "stderr_chars": len(stderr),
        "stdout_tail": stdout[-500:],
        "stderr_tail": stderr[-500:],
        "truncated": truncation["truncated"],
    }
    if truncation["truncated"]:
        metadata.update(truncation)
    return ToolResult(output=output, is_error=True, metadata=metadata)


def _format_exec_output(
    *,
    exit_code: int | None,
    cwd: str,
    stdout: str,
    stderr: str,
    header: str | None = None,
) -> str:
    """Render command metadata, stdout, and stderr into one text block."""

    lines: list[str] = []
    if header:
        lines.append(header)
    if exit_code is not None:
        lines.append(f"exit_code: {exit_code}")
    lines.append(f"cwd: {cwd}")
    lines.append("")
    lines.append("stdout:")
    lines.append(stdout.rstrip("\r\n"))
    lines.append("")
    lines.append("stderr:")
    lines.append(stderr.rstrip("\r\n"))
    return "\n".join(lines).rstrip()


def _coerce_output(value: str | bytes | None) -> str:
    """Normalize subprocess timeout output into UTF-8 text."""

    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value
