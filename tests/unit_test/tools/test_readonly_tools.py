"""Tests for read-only workspace tools."""

import os

import pytest

from agent.tools.readonly import GrepTool, ListDirTool, ReadFileTool


def test_list_dir_lists_direct_children_with_directories_first(tmp_path):
    """list_dir should produce a compact deterministic directory listing."""

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "b.txt").write_text("bbb", encoding="utf-8")
    (workspace / "a_dir").mkdir()
    (workspace / ".hidden").write_text("hidden", encoding="utf-8")

    result = ListDirTool(workspace).execute({"path": "."})

    assert result.is_error is False
    assert result.output.splitlines() == ["DIR  a_dir", "FILE b.txt 3 bytes"]
    assert result.metadata["total_entries"] == 2


def test_list_dir_can_include_hidden_and_reports_non_directory(tmp_path):
    """Hidden files are opt-in and files cannot be listed as directories."""

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    target = workspace / "file.txt"
    target.write_text("hello", encoding="utf-8")
    (workspace / ".hidden").write_text("hidden", encoding="utf-8")

    hidden = ListDirTool(workspace).execute({"include_hidden": True})
    not_directory = ListDirTool(workspace).execute({"path": "file.txt"})

    assert ".hidden" in hidden.output
    assert not_directory.is_error is True
    assert not_directory.metadata["code"] == "NOT_DIRECTORY"


def test_read_file_reads_text_with_line_numbers_and_ranges(tmp_path):
    """read_file should return UTF-8 text with stable line references."""

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "notes.txt").write_text("one\ntwo\nthree\n", encoding="utf-8")

    result = ReadFileTool(workspace).execute(
        {"path": "notes.txt", "start_line": 2, "max_lines": 1}
    )

    assert result.is_error is False
    assert result.output == "2: two\n[truncated]"
    assert result.metadata["returned_lines"] == 1
    assert result.metadata["truncated"] is True


def test_read_file_rejects_directories_and_truncates_long_output(tmp_path):
    """read_file should distinguish files from directories and bound output."""

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "long.txt").write_text("abcdefghijklmnopqrstuvwxyz\n", encoding="utf-8")

    not_file = ReadFileTool(workspace).execute({"path": "."})
    truncated = ReadFileTool(workspace).execute({"path": "long.txt", "max_chars": 12})

    assert not_file.is_error is True
    assert not_file.metadata["code"] == "NOT_FILE"
    assert truncated.metadata["truncated"] is True
    assert "[truncated]" in truncated.output


def test_read_file_reports_decode_errors(tmp_path):
    """Binary or non-UTF-8 files should not be guessed."""

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "bad.bin").write_bytes(b"\xff\xfe\x00")

    result = ReadFileTool(workspace).execute({"path": "bad.bin"})

    assert result.is_error is True
    assert result.metadata["code"] == "DECODE_ERROR"


def test_grep_finds_matches_and_respects_case_sensitivity(tmp_path):
    """grep should search text files without shelling out."""

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "a.txt").write_text("Alpha\nbeta\n", encoding="utf-8")
    (workspace / "b.txt").write_text("alpha\n", encoding="utf-8")

    insensitive = GrepTool(workspace).execute({"pattern": "alpha"})
    sensitive = GrepTool(workspace).execute({"pattern": "alpha", "case_sensitive": True})

    assert "a.txt:1: Alpha" in insensitive.output
    assert "b.txt:1: alpha" in insensitive.output
    assert "a.txt:1: Alpha" not in sensitive.output
    assert "b.txt:1: alpha" in sensitive.output


def test_grep_reports_invalid_pattern_and_skips_hidden_by_default(tmp_path):
    """grep should structure regex errors and ignore dot paths unless requested."""

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / ".hidden.txt").write_text("needle\n", encoding="utf-8")

    invalid = GrepTool(workspace).execute({"pattern": "["})
    hidden_default = GrepTool(workspace).execute({"pattern": "needle"})
    hidden_included = GrepTool(workspace).execute({"pattern": "needle", "include_hidden": True})

    assert invalid.is_error is True
    assert invalid.metadata["code"] == "INVALID_PATTERN"
    assert hidden_default.output == ""
    assert ".hidden.txt:1: needle" in hidden_included.output


@pytest.mark.parametrize(
    ("tool", "args"),
    [
        (ListDirTool, {"path": "../outside.txt"}),
        (ReadFileTool, {"path": "../outside.txt"}),
        (GrepTool, {"path": "../outside.txt", "pattern": "outside"}),
    ],
)
def test_tools_reject_relative_workspace_escape(tmp_path, tool, args):
    """Every read-only tool should reject .. paths that escape the workspace."""

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (tmp_path / "outside.txt").write_text("outside", encoding="utf-8")

    result = tool(workspace).execute(args)

    assert result.is_error is True
    assert result.metadata["code"] == "PATH_OUTSIDE_WORKSPACE"


def test_tools_reject_absolute_workspace_escape(tmp_path):
    """Absolute paths are allowed only when they resolve inside the workspace."""

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("outside", encoding="utf-8")

    result = ReadFileTool(workspace).execute({"path": str(outside)})

    assert result.is_error is True
    assert result.metadata["code"] == "PATH_OUTSIDE_WORKSPACE"


def test_symlink_escape_is_rejected_when_supported(tmp_path):
    """Symlinks pointing outside the workspace should not bypass the guard."""

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("outside", encoding="utf-8")
    link = workspace / "link.txt"
    try:
        os.symlink(outside, link)
    except OSError:
        pytest.skip("symlink creation is not available in this environment")

    result = ReadFileTool(workspace).execute({"path": "link.txt"})

    assert result.is_error is True
    assert result.metadata["code"] == "PATH_OUTSIDE_WORKSPACE"
