"""Bounded no-shell subprocess Runner for configured Python Hooks."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, BinaryIO

from agent.hooks.config import MAX_HOOK_INPUT_CHARS, HookSpec
from agent.process_tree import ManagedProcessTree


class HookExecutionError(RuntimeError):
    """Stable Hook process failure used by stage-specific policies."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


class HookProcessRunner:
    """Execute one validated local Python Hook with bounded I/O and time."""

    def __init__(self, workspace: Path):
        self.workspace = Path(workspace).expanduser().resolve()

    def run(self, spec: HookSpec, payload: dict[str, Any]) -> dict[str, Any]:
        encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        if len(encoded) > MAX_HOOK_INPUT_CHARS:
            raise HookExecutionError("HOOK_INPUT_LIMIT", "Hook input exceeds the configured limit.")
        try:
            tree = ManagedProcessTree.spawn(
                [sys.executable, str(spec.script)],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                cwd=self.workspace,
                env=_minimal_hook_environment(self.workspace),
                shell=False,
                creationflags=_creation_flags(),
            )
        except OSError as exc:
            raise HookExecutionError("HOOK_PROCESS_FAILED", "Hook process could not start.") from exc
        process = tree.process
        stdout = bytearray()
        stderr = bytearray()
        overflow = threading.Event()
        readers = [
            threading.Thread(
                target=_read_limited,
                args=(process.stdout, stdout, spec.max_output_chars, overflow),
                daemon=True,
            ),
            threading.Thread(
                target=_read_limited,
                args=(process.stderr, stderr, min(spec.max_output_chars, 8192), overflow),
                daemon=True,
            ),
        ]
        for reader in readers:
            reader.start()
        timed_out = False
        try:
            try:
                if process.stdin is None:
                    raise HookExecutionError("HOOK_PROCESS_FAILED", "Hook stdin is unavailable.")
                process.stdin.write(encoded)
                process.stdin.close()
            except (BrokenPipeError, OSError) as exc:
                raise HookExecutionError(
                    "HOOK_PROCESS_FAILED", "Hook process rejected its input."
                ) from exc

            deadline = time.monotonic() + spec.timeout_seconds
            while process.poll() is None:
                if overflow.is_set():
                    break
                if time.monotonic() >= deadline:
                    timed_out = True
                    break
                time.sleep(0.01)
        finally:
            # Also close a normally completed Hook's tree so a script cannot
            # detach a background child and outlive the bounded invocation.
            tree.terminate()
        for reader in readers:
            reader.join(timeout=1.0)
        if timed_out:
            raise HookExecutionError("HOOK_TIMEOUT", "Hook execution timed out.")
        if overflow.is_set():
            raise HookExecutionError("HOOK_OUTPUT_LIMIT", "Hook output exceeds the configured limit.")
        if process.returncode != 0:
            raise HookExecutionError("HOOK_PROCESS_FAILED", "Hook process failed.")
        try:
            decoded = bytes(stdout).decode("utf-8")
            result = json.loads(decoded)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise HookExecutionError("HOOK_INVALID_OUTPUT", "Hook stdout must be one JSON object.") from exc
        if not isinstance(result, dict):
            raise HookExecutionError("HOOK_INVALID_OUTPUT", "Hook stdout must be one JSON object.")
        return result


def _read_limited(
    stream: BinaryIO | None,
    target: bytearray,
    limit: int,
    overflow: threading.Event,
) -> None:
    if stream is None:
        return
    try:
        while True:
            chunk = stream.read(4096)
            if not chunk:
                return
            remaining = limit - len(target)
            if remaining <= 0 or len(chunk) > remaining:
                if remaining > 0:
                    target.extend(chunk[:remaining])
                overflow.set()
                return
            target.extend(chunk)
    except OSError:
        return


def _minimal_hook_environment(workspace: Path) -> dict[str, str]:
    environment = {
        "PYTHONUTF8": "1",
        "PYTHONIOENCODING": "utf-8",
        "ZHICE_AGENT_WORKSPACE": str(workspace),
    }
    for key in ("SYSTEMROOT", "WINDIR", "TEMP", "TMP", "TMPDIR"):
        value = os.environ.get(key)
        if value:
            environment[key] = value
    return environment


def _creation_flags() -> int:
    if os.name == "nt":
        return int(getattr(subprocess, "CREATE_NO_WINDOW", 0))
    return 0
