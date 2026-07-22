from __future__ import annotations

import subprocess
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from agent.subagents.workspace import (
    WorkspaceIsolationError,
    WorkspaceManager,
    WorkspaceMode,
)


def _git(cwd: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        timeout=10,
        shell=False,
    )
    assert completed.returncode == 0, completed.stderr
    return completed.stdout


def _init_repo(path: Path) -> Path:
    path.mkdir()
    _git(path, "init")
    _git(path, "config", "user.email", "tests@example.invalid")
    _git(path, "config", "user.name", "Workspace Tests")
    (path / "shared.txt").write_text("base\n", encoding="utf-8")
    _git(path, "add", "shared.txt")
    _git(path, "commit", "-m", "initial")
    return path.resolve()


def test_guard_rejects_outside_paths_and_unsafe_ids(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    manager = WorkspaceManager(workspace)

    with pytest.raises(WorkspaceIsolationError, match="outside the workspace") as outside:
        manager.guard_path(tmp_path / "outside")
    assert outside.value.code == "PATH_OUTSIDE_WORKSPACE"

    with pytest.raises(WorkspaceIsolationError, match="Invalid batch_id") as invalid:
        manager.acquire(WorkspaceMode.SHARED_READONLY, batch_id="../escape", task_id="task")
    assert invalid.value.code == "INVALID_WORKSPACE_ID"


def test_readers_run_together_and_exclusive_waits_for_same_workspace(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    manager = WorkspaceManager(workspace)
    first_reader = manager.acquire("shared_readonly", batch_id="batch", task_id="reader1")
    second_acquired = threading.Event()
    release_second = threading.Event()
    exclusive_acquired = threading.Event()

    def hold_second_reader() -> None:
        with manager.acquire("shared_readonly", batch_id="batch", task_id="reader2"):
            second_acquired.set()
            assert release_second.wait(3)

    def acquire_exclusive() -> None:
        with manager.acquire("shared_exclusive", batch_id="batch", task_id="writer"):
            exclusive_acquired.set()

    with ThreadPoolExecutor(max_workers=2) as executor:
        reader_future = executor.submit(hold_second_reader)
        assert second_acquired.wait(1), "second readonly lease should not be serialized"
        writer_future = executor.submit(acquire_exclusive)
        assert not exclusive_acquired.wait(0.15)
        first_reader.release()
        assert not exclusive_acquired.wait(0.15)
        release_second.set()
        assert exclusive_acquired.wait(2)
        reader_future.result(timeout=2)
        writer_future.result(timeout=2)


def test_exclusive_blocks_worktree_lane_until_release(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path / "repo")
    manager = WorkspaceManager(repo)
    exclusive = manager.acquire("shared_exclusive", batch_id="batch", task_id="writer")
    worktree_acquired = threading.Event()

    def create_worktree() -> None:
        lease = manager.acquire("worktree", batch_id="batch", task_id="developer")
        worktree_acquired.set()
        lease.release(force_cleanup=True)

    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(create_worktree)
        assert not worktree_acquired.wait(0.2)
        exclusive.release()
        assert worktree_acquired.wait(10)
        future.result(timeout=10)


def test_two_worktrees_modify_same_file_without_touching_main_checkout(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path / "repo")
    manager = WorkspaceManager(repo)
    first = manager.acquire("worktree", batch_id="batch", task_id="task1")
    second = manager.acquire("worktree", batch_id="batch", task_id="task2")

    try:
        assert first.workspace == (repo / ".zhice/subagents/batch/task1").resolve()
        assert second.workspace == (repo / ".zhice/subagents/batch/task2").resolve()
        assert first.workspace != second.workspace
        (first.workspace / "shared.txt").write_text("first\n", encoding="utf-8")
        (second.workspace / "shared.txt").write_text("second\n", encoding="utf-8")

        assert (repo / "shared.txt").read_text(encoding="utf-8") == "base\n"
        assert first.summarize_changes().changed_files == ("shared.txt",)
        assert second.summarize_changes().changed_files == ("shared.txt",)
        assert "shared.txt" in first.change_summary.diff_summary
    finally:
        first.release(force_cleanup=True)
        second.release(force_cleanup=True)


def test_clean_worktree_is_removed_but_dirty_worktree_is_retained(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path / "repo")
    manager = WorkspaceManager(repo)

    clean = manager.acquire("worktree", batch_id="batch", task_id="clean")
    clean_path = clean.workspace
    clean.release()
    assert not clean_path.exists()

    dirty = manager.acquire("worktree", batch_id="batch", task_id="dirty")
    dirty_path = dirty.workspace
    (dirty_path / "new.txt").write_text("keep\n", encoding="utf-8")
    summary = dirty.release()
    assert summary.changed_files == ("new.txt",)
    assert "Untracked files: 1" in summary.diff_summary
    assert dirty_path.is_dir()

    dirty.release(force_cleanup=True)
    assert not dirty_path.exists()


def test_change_summary_is_bounded(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path / "repo")
    manager = WorkspaceManager(repo, max_changed_files=1, max_diff_chars=20)
    lease = manager.acquire("worktree", batch_id="batch", task_id="bounded")
    try:
        (lease.workspace / "one.txt").write_text("one\n", encoding="utf-8")
        (lease.workspace / "two.txt").write_text("two\n", encoding="utf-8")
        (lease.workspace / "shared.txt").write_text("changed content\n", encoding="utf-8")

        summary = lease.summarize_changes()
        assert len(summary.changed_files) == 1
        assert len(summary.diff_summary) <= 20
        assert summary.truncated is True
    finally:
        lease.release(force_cleanup=True)


def test_worktree_failure_does_not_fall_back_to_shared_write(tmp_path: Path) -> None:
    workspace = tmp_path / "not-a-repo"
    workspace.mkdir()
    manager = WorkspaceManager(workspace)

    with pytest.raises(WorkspaceIsolationError) as error:
        manager.acquire("worktree", batch_id="batch", task_id="task")

    assert error.value.code == "WORKSPACE_NOT_GIT_ROOT"
    assert not (workspace / ".zhice/subagents/batch/task").exists()
