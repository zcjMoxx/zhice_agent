"""Workspace isolation and lifecycle helpers for child agents."""

from __future__ import annotations

import os
import re
import subprocess
import threading
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Final


class WorkspaceMode(StrEnum):
    """Supported child workspace isolation modes."""

    SHARED_READONLY = "shared_readonly"
    SHARED_EXCLUSIVE = "shared_exclusive"
    WORKTREE = "worktree"


class WorkspaceIsolationError(RuntimeError):
    """Raised when a requested workspace boundary cannot be established safely."""

    def __init__(self, message: str, *, code: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class WorkspaceChangeSummary:
    """Bounded, workspace-relative summary of child worktree changes."""

    changed_files: tuple[str, ...] = ()
    diff_summary: str = ""
    truncated: bool = False


_SAFE_ID: Final = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}\Z")
_MANAGER_REGISTRY_LOCK = threading.Lock()
_MANAGER_REGISTRY: dict[Path, "WorkspaceManager"] = {}


class _WorkspaceLane:
    """A writer-preferring reader/writer lane for one workspace."""

    def __init__(self) -> None:
        self._condition = threading.Condition()
        self._readers = 0
        self._writer = False
        self._waiting_writers = 0

    def acquire_reader(self) -> None:
        with self._condition:
            self._condition.wait_for(lambda: not self._writer and self._waiting_writers == 0)
            self._readers += 1

    def release_reader(self) -> None:
        with self._condition:
            if self._readers <= 0:
                raise RuntimeError("Workspace reader lane was released without acquisition.")
            self._readers -= 1
            if self._readers == 0:
                self._condition.notify_all()

    def acquire_writer(self) -> None:
        with self._condition:
            self._waiting_writers += 1
            try:
                self._condition.wait_for(lambda: not self._writer and self._readers == 0)
                self._writer = True
            finally:
                self._waiting_writers -= 1

    def release_writer(self) -> None:
        with self._condition:
            if not self._writer:
                raise RuntimeError("Workspace writer lane was released without acquisition.")
            self._writer = False
            self._condition.notify_all()


class WorkspaceLease:
    """Acquired child workspace plus its safe release and summary lifecycle."""

    def __init__(
        self,
        *,
        manager: WorkspaceManager,
        mode: WorkspaceMode,
        workspace: Path,
        batch_id: str,
        task_id: str,
        lane_mode: str,
        worktree_id: str | None = None,
    ) -> None:
        self._manager = manager
        self.mode = mode
        self.workspace = workspace
        self.batch_id = batch_id
        self.task_id = task_id
        self.worktree_id = worktree_id
        self._lane_mode = lane_mode
        self._released = False
        self.change_summary = WorkspaceChangeSummary()

    def summarize_changes(self) -> WorkspaceChangeSummary:
        """Refresh and return a bounded change summary for a worktree lease."""

        if self.mode is not WorkspaceMode.WORKTREE:
            return self.change_summary
        self.change_summary = self._manager._summarize_worktree(self.workspace)
        return self.change_summary

    def release(self, *, force_cleanup: bool = False) -> WorkspaceChangeSummary:
        """Release the lane, cleaning only an unchanged worktree by default."""

        if self._released:
            if (
                force_cleanup
                and self.mode is WorkspaceMode.WORKTREE
                and self.workspace.exists()
            ):
                self._manager._remove_worktree(self.workspace, True)
            return self.change_summary

        cleanup_error: Exception | None = None
        try:
            if self.mode is WorkspaceMode.WORKTREE:
                self.change_summary = self.summarize_changes()
                if force_cleanup or not self.change_summary.changed_files:
                    try:
                        self._manager._remove_worktree(self.workspace, force_cleanup)
                    except Exception as exc:  # release the lane even if Git cleanup fails.
                        cleanup_error = exc
        finally:
            self._released = True
            if self._lane_mode == "writer":
                self._manager._lane.release_writer()
            else:
                self._manager._lane.release_reader()

        if cleanup_error is not None:
            raise cleanup_error
        return self.change_summary

    def __enter__(self) -> WorkspaceLease:
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        self.release()


class WorkspaceManager:
    """Create guarded workspace leases and isolated Git worktrees."""

    def __init__(
        self,
        workspace: Path | str,
        *,
        max_changed_files: int = 100,
        max_diff_chars: int = 4000,
        git_timeout_seconds: float = 30.0,
    ) -> None:
        resolved = Path(workspace).expanduser().resolve()
        if not resolved.is_dir():
            raise WorkspaceIsolationError(
                "Workspace directory does not exist.",
                code="WORKSPACE_NOT_FOUND",
            )
        if max_changed_files < 1 or max_diff_chars < 1 or git_timeout_seconds <= 0:
            raise ValueError("Workspace summary and timeout limits must be positive.")
        self.workspace = resolved
        self.max_changed_files = max_changed_files
        self.max_diff_chars = max_diff_chars
        self.git_timeout_seconds = git_timeout_seconds
        self._lane = _WorkspaceLane()
    def acquire(
        self,
        mode: WorkspaceMode | str,
        *,
        batch_id: str,
        task_id: str,
    ) -> WorkspaceLease:
        """Acquire a workspace mode without weakening a failed isolation request."""

        try:
            selected_mode = WorkspaceMode(mode)
        except ValueError as exc:
            raise WorkspaceIsolationError(
                f"Unsupported workspace mode: {mode}",
                code="INVALID_WORKSPACE_MODE",
            ) from exc
        _validate_identifier(batch_id, "batch_id")
        _validate_identifier(task_id, "task_id")

        lane_mode = "writer" if selected_mode is WorkspaceMode.SHARED_EXCLUSIVE else "reader"
        if lane_mode == "writer":
            self._lane.acquire_writer()
        else:
            self._lane.acquire_reader()

        try:
            child_workspace = self.workspace
            worktree_id = None
            if selected_mode is WorkspaceMode.WORKTREE:
                child_workspace = self._create_worktree(batch_id, task_id)
                worktree_id = f"{batch_id}/{task_id}"
            return WorkspaceLease(
                manager=self,
                mode=selected_mode,
                workspace=child_workspace,
                batch_id=batch_id,
                task_id=task_id,
                lane_mode=lane_mode,
                worktree_id=worktree_id,
            )
        except BaseException:
            if lane_mode == "writer":
                self._lane.release_writer()
            else:
                self._lane.release_reader()
            raise

    def guard_path(self, candidate: Path | str) -> Path:
        """Resolve a path and require it to remain under the actor workspace."""

        path = Path(candidate).expanduser()
        if not path.is_absolute():
            path = self.workspace / path
        resolved = path.resolve(strict=False)
        if not _is_relative_to(resolved, self.workspace):
            raise WorkspaceIsolationError(
                "Path is outside the workspace.",
                code="PATH_OUTSIDE_WORKSPACE",
            )
        return resolved

    def _create_worktree(self, batch_id: str, task_id: str) -> Path:
        repo_root = self._git_output(
            self.workspace,
            "rev-parse",
            "--show-toplevel",
        ).strip()
        if not repo_root:
            raise WorkspaceIsolationError(
                "Git did not return a repository root for the workspace.",
                code="WORKTREE_CREATE_FAILED",
            )
        resolved_repo_root = Path(repo_root).resolve()
        if resolved_repo_root != self.workspace:
            raise WorkspaceIsolationError(
                "Workspace must be the Git repository root before creating a child worktree.",
                code="WORKSPACE_NOT_GIT_ROOT",
            )

        target = self.guard_path(Path(".zhice") / "subagents" / batch_id / task_id)
        if target.exists():
            raise WorkspaceIsolationError(
                "Child worktree path already exists.",
                code="WORKTREE_PATH_EXISTS",
            )
        target.parent.mkdir(parents=True, exist_ok=True)
        self._git_output(
            self.workspace,
            "worktree",
            "add",
            "--detach",
            "--",
            str(target),
            "HEAD",
        )
        if not target.is_dir():
            raise WorkspaceIsolationError(
                "Git reported success but the child worktree was not created.",
                code="WORKTREE_CREATE_FAILED",
            )
        return target

    def _summarize_worktree(self, worktree: Path) -> WorkspaceChangeSummary:
        status = self._git_output(
            worktree,
            "status",
            "--porcelain=v1",
            "-z",
            "--untracked-files=all",
        )
        all_files = _parse_porcelain_paths(status)
        changed_files = tuple(all_files[: self.max_changed_files])
        files_truncated = len(all_files) > self.max_changed_files

        diff = self._git_output(
            worktree,
            "diff",
            "--stat",
            "--no-ext-diff",
            "HEAD",
            "--",
        ).strip()
        untracked_count = sum(1 for record in status.split("\0") if record.startswith("?? "))
        if untracked_count:
            diff = f"{diff}\nUntracked files: {untracked_count}".strip()
        diff = _sanitize_summary(diff)
        diff_truncated = len(diff) > self.max_diff_chars
        if diff_truncated:
            marker = "\n[truncated]"
            keep = max(0, self.max_diff_chars - len(marker))
            diff = f"{diff[:keep]}{marker}"[: self.max_diff_chars]
        return WorkspaceChangeSummary(
            changed_files=changed_files,
            diff_summary=diff,
            truncated=files_truncated or diff_truncated,
        )

    def _remove_worktree(self, worktree: Path, force: bool) -> None:
        guarded = self.guard_path(worktree)
        args = ["worktree", "remove"]
        if force:
            args.append("--force")
        args.extend(["--", str(guarded)])
        self._git_output(self.workspace, *args)
        self._git_output(self.workspace, "worktree", "prune")

    def _git_output(self, cwd: Path, *args: str) -> str:
        command = ["git", "-c", "core.quotepath=false", *args]
        environment = os.environ.copy()
        environment.update(
            {
                "GIT_TERMINAL_PROMPT": "0",
                "GCM_INTERACTIVE": "Never",
                "LC_ALL": "C.UTF-8",
                "LANG": "C.UTF-8",
            }
        )
        creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        try:
            completed = subprocess.run(
                command,
                cwd=cwd,
                env=environment,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=self.git_timeout_seconds,
                check=False,
                shell=False,
                creationflags=creation_flags,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise WorkspaceIsolationError(
                f"Git worktree command could not run: {type(exc).__name__}.",
                code="GIT_COMMAND_FAILED",
            ) from exc
        if completed.returncode != 0:
            detail = _sanitize_summary(completed.stderr.strip() or completed.stdout.strip())
            detail = detail[:500]
            raise WorkspaceIsolationError(
                f"Git worktree command failed: {detail or 'unknown git error'}",
                code="GIT_COMMAND_FAILED",
            )
        return completed.stdout


def _validate_identifier(value: str, name: str) -> None:
    if not isinstance(value, str) or _SAFE_ID.fullmatch(value) is None:
        raise WorkspaceIsolationError(
            f"Invalid {name}; use 1-64 ASCII letters, digits, underscores, or hyphens.",
            code="INVALID_WORKSPACE_ID",
        )


def _parse_porcelain_paths(status: str) -> list[str]:
    records = status.split("\0")
    paths: list[str] = []
    index = 0
    while index < len(records):
        record = records[index]
        index += 1
        if not record:
            continue
        code = record[:2]
        path = record[3:] if len(record) > 3 else ""
        if path:
            paths.append(_sanitize_path(path))
        if "R" in code or "C" in code:
            index += 1  # -z emits the original path as a second NUL field.
    return paths


def _sanitize_path(value: str) -> str:
    return "".join(character if character.isprintable() else "?" for character in value).replace(
        "\\", "/"
    )


def _sanitize_summary(value: str) -> str:
    return "".join(
        character if character in "\n\t" or character.isprintable() else "?"
        for character in value
    )


def _is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return True


def shared_workspace_manager(workspace: Path | str) -> WorkspaceManager:
    """Return the process-wide lane owner for one resolved actor workspace."""

    resolved = Path(workspace).expanduser().resolve()
    with _MANAGER_REGISTRY_LOCK:
        manager = _MANAGER_REGISTRY.get(resolved)
        if manager is None:
            manager = WorkspaceManager(resolved)
            _MANAGER_REGISTRY[resolved] = manager
        return manager
