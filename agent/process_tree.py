"""Cross-platform subprocess tree ownership and bounded termination."""

from __future__ import annotations

import ctypes
import os
import signal
import subprocess
import time
from ctypes import wintypes
from dataclasses import dataclass
from typing import Any


@dataclass
class ManagedProcessTree:
    """Own one subprocess and every descendant it creates."""

    process: subprocess.Popen[bytes]
    _windows_job: int | None = None
    _closed: bool = False

    @classmethod
    def spawn(cls, args: list[str], **kwargs: Any) -> ManagedProcessTree:
        """Start a process in a new POSIX group or a Windows kill-on-close Job."""

        job_handle: int | None = None
        if os.name == "nt":
            job_handle = _create_windows_kill_job()
        else:
            kwargs["start_new_session"] = True
        try:
            process = subprocess.Popen(args, **kwargs)  # noqa: S603
        except Exception:
            if job_handle is not None:
                _close_windows_handle(job_handle)
            raise
        if job_handle is not None:
            try:
                _assign_windows_job(job_handle, process)
            except OSError:
                process.kill()
                process.wait(timeout=1.0)
                _close_windows_handle(job_handle)
                raise
        return cls(process=process, _windows_job=job_handle)

    def terminate(self, *, grace_seconds: float = 0.5) -> None:
        """Terminate the complete process tree and release native resources."""

        if self._closed:
            return
        self._closed = True
        if os.name == "nt":
            self._terminate_windows()
        else:
            self._terminate_posix(grace_seconds)

    def _terminate_windows(self) -> None:
        job_handle = self._windows_job
        try:
            if job_handle is not None:
                try:
                    _terminate_windows_job(job_handle)
                except OSError:
                    # KILL_ON_JOB_CLOSE remains the authoritative fallback.
                    pass
        finally:
            if job_handle is not None:
                _close_windows_handle(job_handle)
                self._windows_job = None
        _wait_or_kill_root(self.process)

    def _terminate_posix(self, grace_seconds: float) -> None:
        process_group = self.process.pid
        _signal_process_group(process_group, signal.SIGTERM)
        deadline = time.monotonic() + grace_seconds
        while _process_group_exists(process_group) and time.monotonic() < deadline:
            self.process.poll()
            time.sleep(0.01)
        if _process_group_exists(process_group):
            _signal_process_group(process_group, signal.SIGKILL)
        _wait_or_kill_root(self.process)


def _wait_or_kill_root(process: subprocess.Popen[bytes]) -> None:
    try:
        process.wait(timeout=1.0)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=1.0)


def _signal_process_group(process_group: int, sig: signal.Signals) -> None:
    try:
        os.killpg(process_group, sig)
    except ProcessLookupError:
        pass


def _process_group_exists(process_group: int) -> bool:
    try:
        os.killpg(process_group, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _create_windows_kill_job() -> int:
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateJobObjectW.argtypes = (wintypes.LPVOID, wintypes.LPCWSTR)
    kernel32.CreateJobObjectW.restype = wintypes.HANDLE
    handle = kernel32.CreateJobObjectW(None, None)
    if not handle:
        raise ctypes.WinError(ctypes.get_last_error())

    information = _JobObjectExtendedLimitInformation()
    information.BasicLimitInformation.LimitFlags = 0x00002000
    kernel32.SetInformationJobObject.argtypes = (
        wintypes.HANDLE,
        ctypes.c_int,
        wintypes.LPVOID,
        wintypes.DWORD,
    )
    kernel32.SetInformationJobObject.restype = wintypes.BOOL
    if not kernel32.SetInformationJobObject(
        handle,
        9,
        ctypes.byref(information),
        ctypes.sizeof(information),
    ):
        error = ctypes.get_last_error()
        _close_windows_handle(int(handle))
        raise ctypes.WinError(error)
    return int(handle)


def _assign_windows_job(handle: int, process: subprocess.Popen[bytes]) -> None:
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.AssignProcessToJobObject.argtypes = (wintypes.HANDLE, wintypes.HANDLE)
    kernel32.AssignProcessToJobObject.restype = wintypes.BOOL
    process_handle = wintypes.HANDLE(int(process._handle))  # type: ignore[attr-defined]
    if not kernel32.AssignProcessToJobObject(wintypes.HANDLE(handle), process_handle):
        raise ctypes.WinError(ctypes.get_last_error())


def _terminate_windows_job(handle: int) -> None:
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.TerminateJobObject.argtypes = (wintypes.HANDLE, wintypes.UINT)
    kernel32.TerminateJobObject.restype = wintypes.BOOL
    if not kernel32.TerminateJobObject(wintypes.HANDLE(handle), 1):
        error = ctypes.get_last_error()
        if error != 6:  # ERROR_INVALID_HANDLE: another close path already won.
            raise ctypes.WinError(error)


def _close_windows_handle(handle: int) -> None:
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
    kernel32.CloseHandle.restype = wintypes.BOOL
    kernel32.CloseHandle(wintypes.HANDLE(handle))


class _IoCounters(ctypes.Structure):
    _fields_ = [
        ("ReadOperationCount", ctypes.c_uint64),
        ("WriteOperationCount", ctypes.c_uint64),
        ("OtherOperationCount", ctypes.c_uint64),
        ("ReadTransferCount", ctypes.c_uint64),
        ("WriteTransferCount", ctypes.c_uint64),
        ("OtherTransferCount", ctypes.c_uint64),
    ]


class _JobObjectBasicLimitInformation(ctypes.Structure):
    _fields_ = [
        ("PerProcessUserTimeLimit", ctypes.c_int64),
        ("PerJobUserTimeLimit", ctypes.c_int64),
        ("LimitFlags", wintypes.DWORD),
        ("MinimumWorkingSetSize", ctypes.c_size_t),
        ("MaximumWorkingSetSize", ctypes.c_size_t),
        ("ActiveProcessLimit", wintypes.DWORD),
        ("Affinity", ctypes.c_size_t),
        ("PriorityClass", wintypes.DWORD),
        ("SchedulingClass", wintypes.DWORD),
    ]


class _JobObjectExtendedLimitInformation(ctypes.Structure):
    _fields_ = [
        ("BasicLimitInformation", _JobObjectBasicLimitInformation),
        ("IoInfo", _IoCounters),
        ("ProcessMemoryLimit", ctypes.c_size_t),
        ("JobMemoryLimit", ctypes.c_size_t),
        ("PeakProcessMemoryUsed", ctypes.c_size_t),
        ("PeakJobMemoryUsed", ctypes.c_size_t),
    ]
