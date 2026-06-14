"""Tests for the safe local exec tool."""

import sys

from agent.tools.exec import ExecTool


def test_exec_tool_runs_safe_command_inside_workspace(tmp_path):
    """A simple non-interactive command should return captured output."""

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    command = _python_command("print('ok')")

    result = ExecTool(workspace).execute({"command": command})

    assert result.is_error is False
    assert result.metadata["exit_code"] == 0
    assert result.metadata["cwd"] == "."
    assert "stdout:\nok" in result.output


def test_exec_tool_respects_workspace_relative_cwd(tmp_path):
    """The command cwd can move inside the workspace only."""

    workspace = tmp_path / "workspace"
    subdir = workspace / "sub"
    subdir.mkdir(parents=True)
    command = _python_command("import pathlib; print(pathlib.Path.cwd().name)")

    result = ExecTool(workspace).execute({"command": command, "cwd": "sub"})

    assert result.is_error is False
    assert result.metadata["cwd"] == "sub"
    assert "stdout:\nsub" in result.output


def test_exec_tool_rejects_workspace_escape_cwd(tmp_path):
    """cwd must not resolve outside the workspace."""

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()

    result = ExecTool(workspace).execute(
        {"command": _python_command("print('nope')"), "cwd": "../outside"}
    )

    assert result.is_error is True
    assert result.metadata["code"] == "PATH_OUTSIDE_WORKSPACE"


def test_exec_tool_reports_nonzero_exit_code(tmp_path):
    """A failing command is a structured tool error, not an exception."""

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    command = _python_command("import sys; print('bad'); sys.exit(3)")

    result = ExecTool(workspace).execute({"command": command})

    assert result.is_error is True
    assert result.metadata["code"] == "COMMAND_FAILED"
    assert result.metadata["exit_code"] == 3
    assert "stdout:\nbad" in result.output


def test_exec_tool_times_out(tmp_path):
    """Long-running commands should be stopped by timeout_seconds."""

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    command = _python_command("import time; time.sleep(2)")

    result = ExecTool(workspace).execute({"command": command, "timeout_seconds": 1})

    assert result.is_error is True
    assert result.metadata["code"] == "COMMAND_TIMEOUT"
    assert result.metadata["timed_out"] is True


def test_exec_tool_truncates_long_output(tmp_path):
    """Command output should be bounded before entering session context."""

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    command = _python_command("print('x' * 2000)")

    result = ExecTool(workspace).execute({"command": command, "max_output_chars": 1000})

    assert result.is_error is False
    assert result.metadata["truncated"] is True
    assert "[truncated]" in result.output


def test_exec_tool_blocks_policy_denied_commands(tmp_path):
    """Blocked commands should never reach subprocess."""

    workspace = tmp_path / "workspace"
    workspace.mkdir()

    dangerous = ExecTool(workspace).execute({"command": "rm -rf ."})
    network = ExecTool(workspace).execute({"command": "python -m pip install requests"})
    chained = ExecTool(workspace).execute({"command": "python -m pytest && python -m ruff check ."})

    assert dangerous.is_error is True
    assert dangerous.metadata["code"] == "DESTRUCTIVE_COMMAND_BLOCKED"
    assert network.is_error is True
    assert network.metadata["code"] == "NETWORK_COMMAND_BLOCKED"
    assert chained.is_error is True
    assert chained.metadata["code"] == "UNSUPPORTED_SHELL_SYNTAX"


def test_exec_tool_redacts_secret_like_output(tmp_path):
    """stdout and stderr should be redacted before returning to the model."""

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    command = _python_command("print('OPENAI_API_KEY=sk-testsecret123456')")

    result = ExecTool(workspace).execute({"command": command})

    assert result.is_error is False
    assert "sk-testsecret123456" not in result.output
    assert "OPENAI_API_KEY=<redacted>" in result.output


def _python_command(code: str) -> str:
    escaped = code.replace('"', r"\"")
    return f'"{sys.executable}" -c "{escaped}"'
