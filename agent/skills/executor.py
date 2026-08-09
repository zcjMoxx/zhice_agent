"""Safe executable Skill runtime for the explicit ``ndjson-v1`` protocol."""

from __future__ import annotations

import json
import os
import queue
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

import jsonschema

from agent.logging_utils import redact_text
from agent.process_tree import ManagedProcessTree
from agent.protocols.skill import (
    ProgressSink,
    SkillInfo,
    SkillProgress,
    SkillResult,
    SkillRunRequest,
)

DEFAULT_MAX_STDOUT_BYTES = 64 * 1024
DEFAULT_MAX_STDERR_BYTES = 16 * 1024
DEFAULT_MAX_STDOUT_LINES = 500
DEFAULT_MAX_LINE_BYTES = 16 * 1024
DEFAULT_MAX_PROGRESS_CHARS = 500
DEFAULT_MAX_PARAMS_BYTES = 64 * 1024


class PythonSkillExecutor:
    """Run trusted, source-controlled Python Skill entrypoints without a shell."""

    def __init__(
        self,
        *,
        python_executable: str | Path | None = None,
        allowed_environment: dict[str, str] | None = None,
        max_stdout_bytes: int = DEFAULT_MAX_STDOUT_BYTES,
        max_stderr_bytes: int = DEFAULT_MAX_STDERR_BYTES,
        max_stdout_lines: int = DEFAULT_MAX_STDOUT_LINES,
        max_line_bytes: int = DEFAULT_MAX_LINE_BYTES,
        max_progress_chars: int = DEFAULT_MAX_PROGRESS_CHARS,
        max_params_bytes: int = DEFAULT_MAX_PARAMS_BYTES,
    ):
        self.python_executable = str(python_executable or sys.executable)
        self.allowed_environment = _validated_environment(allowed_environment or {})
        self.max_stdout_bytes = _positive(max_stdout_bytes, "max_stdout_bytes")
        self.max_stderr_bytes = _positive(max_stderr_bytes, "max_stderr_bytes")
        self.max_stdout_lines = _positive(max_stdout_lines, "max_stdout_lines")
        self.max_line_bytes = _positive(max_line_bytes, "max_line_bytes")
        self.max_progress_chars = _positive(max_progress_chars, "max_progress_chars")
        self.max_params_bytes = _positive(max_params_bytes, "max_params_bytes")

    def run(
        self,
        request: SkillRunRequest,
        skill: SkillInfo,
        *,
        progress_sink: ProgressSink | None = None,
    ) -> SkillResult:
        """Execute one Skill and convert every terminal path into SkillResult."""

        started = time.perf_counter()
        executable = skill.executable
        if executable is None:
            return _failure("SKILL_NOT_EXECUTABLE", "Skill is not executable.", started)
        if request.qualified_name != skill.qualified_name:
            return _failure("SKILL_IDENTITY_MISMATCH", "Skill identity is invalid.", started)
        entrypoint = executable.entrypoint.resolve(strict=False)
        skill_root = skill.root.resolve(strict=False)
        if not _is_relative_to(entrypoint, skill_root) or not entrypoint.is_file():
            return _failure("INVALID_SKILL_ENTRYPOINT", "Skill entrypoint is invalid.", started)
        if _cancelled(request):
            return _failure("SKILL_CANCELLED", "Skill execution was cancelled.", started, cancelled=True)

        try:
            params_json = _encode_params(
                request.params,
                max_bytes=self.max_params_bytes,
                schema=executable.params_schema,
            )
        except ValueError as exc:
            return _failure("INVALID_SKILL_PARAMS", str(exc), started)

        timeout = executable.timeout_seconds
        if request.timeout_seconds is not None:
            timeout = min(timeout, max(1, request.timeout_seconds))
        command = [self.python_executable, str(entrypoint), "--params", params_json]
        try:
            tree = ManagedProcessTree.spawn(
                command,
                cwd=skill_root,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=_minimal_environment(self.allowed_environment),
                shell=False,
            )
        except Exception as exc:  # noqa: BLE001 - executor always returns structured failures.
            return _failure(
                "SKILL_START_FAILED",
                "Skill process could not be started.",
                started,
                metadata={"error_type": type(exc).__name__},
            )

        events: queue.Queue[tuple[str, bytes | str]] = queue.Queue()
        stdout_thread = threading.Thread(
            target=_read_stdout,
            args=(tree.process.stdout, events),
            kwargs={
                "max_bytes": self.max_stdout_bytes,
                "max_lines": self.max_stdout_lines,
                "max_line_bytes": self.max_line_bytes,
            },
            daemon=True,
            name="zcagent-skill-stdout",
        )
        stderr_thread = threading.Thread(
            target=_read_stderr,
            args=(tree.process.stderr, events),
            kwargs={"max_bytes": self.max_stderr_bytes},
            daemon=True,
            name="zcagent-skill-stderr",
        )
        stdout_thread.start()
        stderr_thread.start()

        result_payload: dict[str, Any] | None = None
        explicit_result = False
        stdout_done = False
        stderr_done = False
        last_nonempty_json: dict[str, Any] | None = None
        last_nonempty_was_legacy = False
        non_json_lines = 0
        typed_progress_seen = False
        exit_code: int | None = None
        terminated = False
        protocol_error: tuple[str, str] | None = None
        deadline = started + timeout

        try:
            while not (stdout_done and stderr_done and exit_code is not None):
                if _cancelled(request):
                    protocol_error = ("SKILL_CANCELLED", "Skill execution was cancelled.")
                    break
                if time.perf_counter() >= deadline:
                    protocol_error = ("SKILL_TIMEOUT", "Skill execution timed out.")
                    break
                if exit_code is None:
                    exit_code = tree.process.poll()
                    if exit_code is not None and not terminated:
                        tree.terminate()
                        terminated = True
                try:
                    kind, value = events.get(timeout=0.03)
                except queue.Empty:
                    continue
                if kind == "stdout_done":
                    stdout_done = True
                    continue
                if kind == "stderr_done":
                    stderr_done = True
                    continue
                if kind == "overflow":
                    protocol_error = (str(value), "Skill output exceeded the allowed limit.")
                    break
                if kind != "stdout_line":
                    continue
                try:
                    line = bytes(value).decode("utf-8", errors="strict")
                except UnicodeDecodeError:
                    protocol_error = ("SKILL_PROTOCOL_ERROR", "Skill output is not valid UTF-8.")
                    break
                if not line.strip():
                    continue
                if explicit_result:
                    protocol_error = (
                        "SKILL_PROTOCOL_ERROR",
                        "Skill emitted output after its final result.",
                    )
                    break
                try:
                    payload = json.loads(line)
                except json.JSONDecodeError:
                    non_json_lines += 1
                    last_nonempty_json = None
                    last_nonempty_was_legacy = False
                    continue
                if not isinstance(payload, dict):
                    last_nonempty_json = None
                    last_nonempty_was_legacy = False
                    continue
                message_type = payload.get("type")
                if message_type == "progress":
                    typed_progress_seen = True
                    progress, error = self._progress(request, payload)
                    if error is not None:
                        protocol_error = error
                        break
                    _emit_progress(progress_sink, progress)
                    last_nonempty_json = None
                    last_nonempty_was_legacy = False
                    continue
                if message_type == "result":
                    if result_payload is not None:
                        protocol_error = ("SKILL_PROTOCOL_ERROR", "Skill emitted multiple results.")
                        break
                    result_payload = payload
                    explicit_result = True
                    continue
                last_nonempty_json = payload
                last_nonempty_was_legacy = _looks_like_legacy_result(payload)

            if protocol_error is not None:
                if not terminated:
                    tree.terminate()
                    terminated = True
                code, message = protocol_error
                return _failure(
                    code,
                    message,
                    started,
                    cancelled=code == "SKILL_CANCELLED",
                    metadata={"timeout_seconds": timeout, "non_json_lines": non_json_lines},
                )
            if not terminated:
                exit_code = tree.process.wait(timeout=1.0)
                tree.terminate()
                terminated = True
            if result_payload is None and last_nonempty_was_legacy and not typed_progress_seen:
                result_payload = last_nonempty_json
            if result_payload is None:
                return _failure(
                    "SKILL_RESULT_MISSING",
                    "Skill did not emit a final result.",
                    started,
                    metadata={"exit_code": exit_code, "non_json_lines": non_json_lines},
                )
            parsed, error = _validated_result(result_payload, started)
            if error is not None:
                return error
            if exit_code != 0 and parsed.status == "success":
                return _failure(
                    "SKILL_PROCESS_FAILED",
                    "Skill process exited unsuccessfully.",
                    started,
                    metadata={"exit_code": exit_code},
                )
            return SkillResult(
                status=parsed.status,
                code=parsed.code,
                data=parsed.data,
                message=parsed.message,
                error_stack=parsed.error_stack,
                duration_ms=_duration_ms(started),
                metadata={
                    **parsed.metadata,
                    "exit_code": exit_code,
                    "non_json_lines": non_json_lines,
                },
            )
        finally:
            if not terminated:
                tree.terminate()
            stdout_thread.join(timeout=0.5)
            stderr_thread.join(timeout=0.5)

    def _progress(
        self,
        request: SkillRunRequest,
        payload: dict[str, Any],
    ) -> tuple[SkillProgress, None] | tuple[None, tuple[str, str]]:
        message = payload.get("message")
        if not isinstance(message, str) or not message.strip():
            return None, ("SKILL_PROTOCOL_ERROR", "Skill progress message is invalid.")
        safe_message = redact_text(" ".join(message.split()))[: self.max_progress_chars]
        percent = payload.get("percent")
        if percent is not None and (
            isinstance(percent, bool) or not isinstance(percent, int) or not 0 <= percent <= 100
        ):
            return None, ("SKILL_PROTOCOL_ERROR", "Skill progress percent is invalid.")
        return (
            SkillProgress(
                run_id=request.run_id,
                qualified_name=request.qualified_name,
                message=safe_message,
                percent=percent,
            ),
            None,
        )


def _read_stdout(stream, events, *, max_bytes: int, max_lines: int, max_line_bytes: int) -> None:
    total = 0
    lines = 0
    pending = bytearray()
    read_chunk = getattr(stream, "read1", stream.read)
    try:
        while True:
            # BufferedReader.read(size) may wait for ``size`` bytes or EOF on a
            # pipe.  ``read1`` performs at most one raw read, so a flushed
            # NDJSON progress line reaches the sink while the Skill is still
            # running.
            chunk = read_chunk(4096)
            if not chunk:
                break
            total += len(chunk)
            if total > max_bytes:
                events.put(("overflow", "SKILL_STDOUT_LIMIT"))
                return
            pending.extend(chunk)
            if len(pending) > max_line_bytes and b"\n" not in pending:
                events.put(("overflow", "SKILL_OUTPUT_LINE_LIMIT"))
                return
            while b"\n" in pending:
                raw, _, rest = pending.partition(b"\n")
                pending = bytearray(rest)
                if len(raw) > max_line_bytes:
                    events.put(("overflow", "SKILL_OUTPUT_LINE_LIMIT"))
                    return
                lines += 1
                if lines > max_lines:
                    events.put(("overflow", "SKILL_OUTPUT_LINE_LIMIT"))
                    return
                events.put(("stdout_line", bytes(raw.rstrip(b"\r"))))
        if pending:
            lines += 1
            if lines > max_lines or len(pending) > max_line_bytes:
                events.put(("overflow", "SKILL_OUTPUT_LINE_LIMIT"))
                return
            events.put(("stdout_line", bytes(pending)))
    finally:
        events.put(("stdout_done", b""))


def _read_stderr(stream, events, *, max_bytes: int) -> None:
    total = 0
    read_chunk = getattr(stream, "read1", stream.read)
    try:
        while True:
            chunk = read_chunk(4096)
            if not chunk:
                break
            total += len(chunk)
            if total > max_bytes:
                events.put(("overflow", "SKILL_STDERR_LIMIT"))
                return
    finally:
        events.put(("stderr_done", b""))


def _validated_result(
    payload: dict[str, Any],
    started: float,
) -> tuple[SkillResult, None] | tuple[None, SkillResult]:
    status = payload.get("status")
    code = payload.get("code")
    message = payload.get("message")
    if status not in {"success", "error", "failed", "cancelled"}:
        return None, _failure("SKILL_RESULT_INVALID", "Skill result status is invalid.", started)
    if not isinstance(code, str) or not code.strip() or len(code) > 100:
        return None, _failure("SKILL_RESULT_INVALID", "Skill result code is invalid.", started)
    if not isinstance(message, str) or len(message) > 4000:
        return None, _failure("SKILL_RESULT_INVALID", "Skill result message is invalid.", started)
    error_stack = payload.get("error_stack", "")
    if not isinstance(error_stack, str):
        return None, _failure("SKILL_RESULT_INVALID", "Skill result error_stack is invalid.", started)
    normalized_status = "error" if status == "failed" else status
    return (
        SkillResult(
            status=normalized_status,
            code=code.strip(),
            data=payload.get("data"),
            message=redact_text(message)[:4000],
            error_stack=redact_text(error_stack)[:1500],
        ),
        None,
    )


def _failure(
    code: str,
    message: str,
    started: float,
    *,
    cancelled: bool = False,
    metadata: dict[str, Any] | None = None,
) -> SkillResult:
    return SkillResult(
        status="cancelled" if cancelled else "error",
        code=code,
        data=None,
        message=redact_text(message),
        duration_ms=_duration_ms(started),
        metadata=dict(metadata or {}),
    )


def _encode_params(
    params: dict[str, Any],
    *,
    max_bytes: int,
    schema: dict[str, Any] | None,
) -> str:
    if not isinstance(params, dict):
        raise ValueError("Skill params must be a JSON object.")
    try:
        encoded = json.dumps(params, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise ValueError("Skill params must be JSON serializable.") from exc
    if len(encoded.encode("utf-8")) > max_bytes:
        raise ValueError("Skill params exceed the allowed size.")
    if _json_depth(params) > 20:
        raise ValueError("Skill params are nested too deeply.")
    if schema is not None:
        try:
            jsonschema.Draft202012Validator.check_schema(schema)
            jsonschema.validate(params, schema)
        except jsonschema.SchemaError as exc:
            raise ValueError("Skill parameter schema is invalid.") from exc
        except jsonschema.ValidationError as exc:
            raise ValueError("Skill params do not match the declared schema.") from exc
    return encoded


def _json_depth(value: Any, depth: int = 0) -> int:
    if isinstance(value, dict):
        return max((_json_depth(item, depth + 1) for item in value.values()), default=depth)
    if isinstance(value, list):
        return max((_json_depth(item, depth + 1) for item in value), default=depth)
    return depth


def _looks_like_legacy_result(payload: dict[str, Any]) -> bool:
    return {"status", "code", "data", "message", "error_stack"}.issubset(payload)


def _emit_progress(sink: ProgressSink | None, progress: SkillProgress | None) -> None:
    if sink is None or progress is None:
        return
    try:
        sink.emit(progress)
    except Exception:  # noqa: BLE001 - progress transport cannot break execution.
        pass


def _minimal_environment(extra: dict[str, str]) -> dict[str, str]:
    keep = ("SystemRoot", "WINDIR", "TEMP", "TMP", "LANG", "LC_ALL", "TZ")
    environment = {name: os.environ[name] for name in keep if os.environ.get(name)}
    environment.update(extra)
    environment["PYTHONUNBUFFERED"] = "1"
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    return environment


def _validated_environment(value: dict[str, str]) -> dict[str, str]:
    result: dict[str, str] = {}
    for key, item in value.items():
        if not isinstance(key, str) or not key or not key.replace("_", "").isalnum():
            raise ValueError("allowed environment names must be alphanumeric")
        if not isinstance(item, str):
            raise ValueError("allowed environment values must be strings")
        result[key] = item
    return result


def _cancelled(request: SkillRunRequest) -> bool:
    return bool(request.cancellation_token and request.cancellation_token.is_cancelled())


def _duration_ms(started: float) -> int:
    return max(0, int((time.perf_counter() - started) * 1000))


def _is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return True


def _positive(value: int, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{name} must be positive")
    return value
