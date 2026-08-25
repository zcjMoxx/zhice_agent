"""Single-process APScheduler projection for persisted workflow schedules."""
from __future__ import annotations

import ctypes
import json
import os
import threading
from ctypes import wintypes
from datetime import datetime
from pathlib import Path
from typing import Any, Callable
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from apscheduler.executors.pool import ThreadPoolExecutor
from apscheduler.jobstores.memory import MemoryJobStore
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date import DateTrigger
from apscheduler.triggers.interval import IntervalTrigger

from agent.workflows.schemas import utc_now
from agent.workflows.store import WorkflowStore


class WorkflowScheduler:
    """Rebuild jobs from SQLite; APScheduler is never the business source of truth."""
    _held_locks: set[Path] = set()
    _lock_guard = threading.Lock()

    def __init__(self, store: WorkflowStore, run_callback: Callable[[str, str], Any], *, workspace: str | Path, max_workers: int = 4) -> None:
        self.store, self.run_callback = store, run_callback
        self.lock_path = Path(workspace).resolve() / "state" / "workflow-scheduler.lock"
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock_descriptor: int | None = None
        self._accepting = False
        self._scheduler = BackgroundScheduler(jobstores={"default": MemoryJobStore()}, executors={"default": ThreadPoolExecutor(max_workers=max(1, max_workers))}, timezone="UTC")

    @property
    def ready(self) -> bool:
        return self._accepting and self._scheduler.running

    def start(self) -> None:
        if self.ready:
            return
        self._acquire_lock()
        try:
            self._scheduler.start(paused=True)
            self._accepting = True
            for schedule in self.store.list_active_schedules():
                self.register(schedule)
            self._scheduler.resume()
        except Exception:
            self._accepting = False
            if self._scheduler.running:
                self._scheduler.shutdown(wait=False)
            self._release_lock()
            raise

    def register(self, schedule: dict[str, Any]) -> None:
        workflow_id = str(schedule["workflow_id"])
        self.store.enable_schedule(workflow_id)
        trigger = self._build_trigger(str(schedule["trigger_type"]), dict(schedule.get("trigger") or {}), str(schedule.get("timezone") or "Asia/Shanghai"))
        job = self._scheduler.add_job(self._dispatch, trigger=trigger, id=f"workflow:{workflow_id}", args=[workflow_id], replace_existing=True, max_instances=1, coalesce=bool(schedule.get("coalesce", True)), misfire_grace_time=max(1, int(schedule.get("misfire_grace_seconds", 900))))
        if job.next_run_time:
            self.store.update_schedule_state(workflow_id, next_run_at=job.next_run_time.isoformat())

    def unregister(self, workflow_id: str) -> None:
        job = self._scheduler.get_job(f"workflow:{workflow_id}")
        if job:
            self._scheduler.remove_job(job.id)
        self.store.disable_schedule(workflow_id)

    def shutdown(self, *, wait: bool = True) -> None:
        self._accepting = False
        try:
            if self._scheduler.running:
                self._scheduler.shutdown(wait=wait)
        finally:
            self._release_lock()

    def jobs(self) -> tuple[str, ...]:
        return tuple(sorted(job.id for job in self._scheduler.get_jobs()))

    def _dispatch(self, workflow_id: str) -> None:
        if not self._accepting:
            return
        scheduled_at = utc_now()
        self.store.update_schedule_state(workflow_id, last_scheduled_at=scheduled_at, last_started_at=scheduled_at)
        try:
            self.run_callback(workflow_id, scheduled_at)
        finally:
            job = self._scheduler.get_job(f"workflow:{workflow_id}")
            next_run = job.next_run_time.isoformat() if job and job.next_run_time else None
            self.store.update_schedule_state(workflow_id, last_finished_at=utc_now(), next_run_at=next_run)

    @staticmethod
    def _build_trigger(kind: str, values: dict[str, Any], timezone: str):
        try:
            zone = ZoneInfo(timezone)
        except ZoneInfoNotFoundError as exc:
            raise ValueError("WORKFLOW_TIMEZONE_INVALID") from exc
        if kind == "date":
            raw = values.get("run_at") or values.get("date")
            if not isinstance(raw, str):
                raise ValueError("WORKFLOW_TRIGGER_INVALID")
            run_at = datetime.fromisoformat(raw)
            return DateTrigger(run_date=run_at if run_at.tzinfo else run_at.replace(tzinfo=zone), timezone=zone)
        if kind == "interval":
            allowed = {key: values[key] for key in ("weeks", "days", "hours", "minutes", "seconds", "start_date", "end_date") if key in values}
            if not any(key in allowed for key in ("weeks", "days", "hours", "minutes", "seconds")):
                raise ValueError("WORKFLOW_TRIGGER_INVALID")
            return IntervalTrigger(timezone=zone, **allowed)
        if kind == "cron":
            expression = values.get("expression")
            if isinstance(expression, str):
                return CronTrigger.from_crontab(expression, timezone=zone)
            allowed = {key: values[key] for key in ("year", "month", "day", "week", "day_of_week", "hour", "minute", "second", "start_date", "end_date") if key in values}
            if not allowed:
                raise ValueError("WORKFLOW_TRIGGER_INVALID")
            return CronTrigger(timezone=zone, **allowed)
        raise ValueError("WORKFLOW_TRIGGER_INVALID")

    def _acquire_lock(self) -> None:
        resolved = self.lock_path.resolve()
        with self._lock_guard:
            if resolved in self._held_locks:
                raise RuntimeError("WORKFLOW_SCHEDULER_ALREADY_RUNNING")
            if os.name != "nt":
                import fcntl

                descriptor = os.open(resolved, os.O_CREAT | os.O_RDWR, 0o600)
                try:
                    fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError as exc:
                    os.close(descriptor)
                    raise RuntimeError("WORKFLOW_SCHEDULER_ALREADY_RUNNING") from exc
                try:
                    os.ftruncate(descriptor, 0)
                    os.write(descriptor, json.dumps({"pid": os.getpid()}).encode())
                    os.fsync(descriptor)
                except Exception:
                    fcntl.flock(descriptor, fcntl.LOCK_UN)
                    os.close(descriptor)
                    raise
                self._lock_descriptor = descriptor
                self._held_locks.add(resolved)
                return
            if resolved.exists():
                try:
                    lock_payload = json.loads(resolved.read_text(encoding="utf-8"))
                    owner = int(lock_payload["pid"])
                except (OSError, ValueError, KeyError, json.JSONDecodeError):
                    lock_payload = {}
                    owner = -1
                if owner > 0 and _process_owns_lock(owner, lock_payload, resolved):
                    raise RuntimeError("WORKFLOW_SCHEDULER_ALREADY_RUNNING")
                resolved.unlink(missing_ok=True)
            descriptor = os.open(resolved, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            try:
                lock_payload = {"pid": os.getpid()}
                process_created_at = _process_created_at(os.getpid())
                if process_created_at is not None:
                    lock_payload["process_created_at"] = process_created_at
                os.write(descriptor, json.dumps(lock_payload).encode())
            finally:
                os.close(descriptor)
            self._held_locks.add(resolved)

    def _release_lock(self) -> None:
        resolved = self.lock_path.resolve()
        with self._lock_guard:
            self._held_locks.discard(resolved)
            if os.name != "nt":
                import fcntl

                descriptor, self._lock_descriptor = self._lock_descriptor, None
                if descriptor is not None:
                    try:
                        fcntl.flock(descriptor, fcntl.LOCK_UN)
                    finally:
                        os.close(descriptor)
                return
            try:
                if int(json.loads(resolved.read_text(encoding="utf-8")).get("pid", -1)) == os.getpid():
                    resolved.unlink(missing_ok=True)
            except (OSError, ValueError, json.JSONDecodeError):
                pass


def _process_exists(pid: int) -> bool:
    if pid <= 0:
        return False
    if pid == os.getpid():
        return True
    if os.name == "nt":
        return _windows_process_info(pid)[0]
    try:
        os.kill(pid, 0)
    except (OSError, ValueError):
        return False
    return True


def _process_created_at(pid: int) -> int | None:
    if os.name != "nt":
        return None
    exists, created_at = _windows_process_info(pid)
    return created_at if exists else None


def _process_owns_lock(pid: int, lock_payload: dict[str, Any], lock_path: Path) -> bool:
    if os.name != "nt":
        return _process_exists(pid)
    exists, created_at = _windows_process_info(pid)
    if not exists:
        return False
    expected_created_at = lock_payload.get("process_created_at")
    if isinstance(expected_created_at, int) and created_at is not None:
        return expected_created_at == created_at
    if created_at is None:
        return True
    try:
        lock_modified_at_ns = lock_path.stat().st_mtime_ns
    except OSError:
        return True
    filetime_unix_epoch_ticks = 116_444_736_000_000_000
    process_created_at_ns = (created_at - filetime_unix_epoch_ticks) * 100
    return process_created_at_ns <= lock_modified_at_ns


def _windows_process_info(pid: int) -> tuple[bool, int | None]:
    """Query Windows process liveness and creation time without sending a signal."""

    process_query_limited_information = 0x1000
    still_active = 259
    error_access_denied = 5
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.GetExitCodeProcess.argtypes = (wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD))
    kernel32.GetExitCodeProcess.restype = wintypes.BOOL
    kernel32.GetProcessTimes.argtypes = (
        wintypes.HANDLE,
        ctypes.POINTER(wintypes.FILETIME),
        ctypes.POINTER(wintypes.FILETIME),
        ctypes.POINTER(wintypes.FILETIME),
        ctypes.POINTER(wintypes.FILETIME),
    )
    kernel32.GetProcessTimes.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
    kernel32.CloseHandle.restype = wintypes.BOOL
    handle = kernel32.OpenProcess(process_query_limited_information, False, pid)
    if not handle:
        return ctypes.get_last_error() == error_access_denied, None
    try:
        exit_code = wintypes.DWORD()
        if not kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
            return True, None
        if exit_code.value != still_active:
            return False, None
        creation_time = wintypes.FILETIME()
        exit_time = wintypes.FILETIME()
        kernel_time = wintypes.FILETIME()
        user_time = wintypes.FILETIME()
        if not kernel32.GetProcessTimes(
            handle,
            ctypes.byref(creation_time),
            ctypes.byref(exit_time),
            ctypes.byref(kernel_time),
            ctypes.byref(user_time),
        ):
            return True, None
        created_at = (creation_time.dwHighDateTime << 32) | creation_time.dwLowDateTime
        return True, created_at
    finally:
        kernel32.CloseHandle(handle)
