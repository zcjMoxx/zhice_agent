#!/usr/bin/python3 -I
from __future__ import annotations

import difflib
import json
import os
import re
import secrets
import selectors
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

import yaml

CONTAINER_NAME = "zhice-agent"
RUNTIME_DIR = Path("/etc/zhice-agent/runtime")
BACKUP_DIR = RUNTIME_DIR / "backups"
PENDING_DIR = Path("/var/lib/zhice-ops/pending")
SCRIPTS_DIR = Path("/usr/local/libexec/zhice-ops/scripts")
CONFIG_NAMES = ("config.yml", "models.json", ".env")
BACKUP_ID_RE = re.compile(r"\A[0-9]{8}-[0-9]{6}-[a-f0-9]{6}\Z")
ENV_KEY_RE = re.compile(r"\A[A-Za-z_][A-Za-z0-9_]*\Z")
MAX_CONFIG_BYTES = 262_144
MAX_DIFF_BYTES = 65_536
MAX_LOG_LINES = 500
MAX_SCRIPT_OUTPUT_BYTES = 131_072
MAX_STREAM_LINE_BYTES = 4_096
MAX_STREAM_BYTES_PER_SECOND = 65_536
SAFE_ENV = {
    "LANG": "C.UTF-8",
    "LC_ALL": "C.UTF-8",
    "PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
}
SECRET_KEY_RE = re.compile(
    r"(?i)(api[_-]?key|access[_-]?token|refresh[_-]?token|password|secret)"
    r"([\"'\s:=]+)([^\s,;\"']+)"
)


class OpsError(RuntimeError):
    """A safe operational error that may be printed to the terminal."""


def _audit(event: str, *, result: str, detail: str = "") -> None:
    fields = [f"event={event}", f"result={result}", f"container={CONTAINER_NAME}"]
    if detail:
        fields.append(f"detail={detail}")
    try:
        subprocess.run(
            ["/usr/bin/logger", "-t", "zhice-ops", "--", " ".join(fields)],
            check=False,
            env=SAFE_ENV,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=2,
        )
    except (OSError, subprocess.SubprocessError):
        pass


def _config_path(root: Path, name: str) -> Path:
    if name not in CONFIG_NAMES:
        raise OpsError("config file must be config.yml, models.json, or .env")
    candidate = root / name
    if candidate.parent != root:
        raise OpsError("invalid config path")
    return candidate


def _require_regular(path: Path) -> None:
    if path.is_symlink() or not path.is_file():
        raise OpsError(f"required regular file is unavailable: {path.name}")
    if path.stat().st_size > MAX_CONFIG_BYTES:
        raise OpsError(f"config file exceeds {MAX_CONFIG_BYTES} bytes: {path.name}")


def _parse_env(text: str) -> dict[str, str]:
    result: dict[str, str] = {}
    for line_number, raw_line in enumerate(text.splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        if "=" not in line:
            raise OpsError(f".env line {line_number} must use KEY=VALUE")
        key, value = line.split("=", 1)
        key = key.strip()
        if not ENV_KEY_RE.fullmatch(key):
            raise OpsError(f".env line {line_number} has an invalid key")
        if key in result:
            raise OpsError(f".env contains duplicate key: {key}")
        result[key] = value
    if "ZHICE_AGENT_WORKSPACE" in result:
        raise OpsError(".env must not override ZHICE_AGENT_WORKSPACE in the container")
    return result


def _validate_config(name: str, text: str) -> Any:
    if "\x00" in text:
        raise OpsError(f"{name} contains a NUL byte")
    if name == ".env":
        return _parse_env(text)
    if name == "models.json":
        try:
            value = json.loads(text)
        except json.JSONDecodeError as exc:
            raise OpsError(f"models.json is invalid at line {exc.lineno}, column {exc.colno}") from exc
        if not isinstance(value, dict):
            raise OpsError("models.json must contain an object")
        if value.get("schema_version") != 1:
            raise OpsError("models.json schema_version must be 1")
        if not isinstance(value.get("routing"), dict) or not isinstance(value.get("chat"), dict):
            raise OpsError("models.json routing and chat must be objects")
        return value
    try:
        value = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        mark = getattr(exc, "problem_mark", None)
        location = f" at line {mark.line + 1}, column {mark.column + 1}" if mark else ""
        raise OpsError(f"config.yml is invalid{location}") from exc
    if not isinstance(value, dict):
        raise OpsError("config.yml must contain a mapping")
    if value.get("schema_version") != 1:
        raise OpsError("config.yml schema_version must be 1")
    for section in (
        "context",
        "skills",
        "subagents",
        "channels",
        "hooks",
        "mcp",
        "logging",
    ):
        if section in value and not isinstance(value[section], dict):
            raise OpsError(f"config.yml {section} must be a mapping")
    return value


def _merged_texts() -> dict[str, str]:
    values: dict[str, str] = {}
    for name in CONFIG_NAMES:
        pending = _config_path(PENDING_DIR, name)
        active = _config_path(RUNTIME_DIR, name)
        path = pending if pending.exists() else active
        _require_regular(path)
        values[name] = path.read_text(encoding="utf-8")
    return values


def validate_all() -> None:
    values = _merged_texts()
    for name, text in values.items():
        _validate_config(name, text)


def _atomic_write(path: Path, content: bytes, *, reference: Path | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if path.parent.is_symlink():
        raise OpsError("config destination directory must not be a symlink")
    descriptor, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temp_path = Path(temp_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        mode = 0o600
        uid = 0
        gid = 0
        if reference is not None:
            _require_regular(reference)
            stat = reference.stat()
            mode = stat.st_mode & 0o777
            uid = stat.st_uid
            gid = stat.st_gid
        os.chmod(temp_path, mode)
        if hasattr(os, "chown"):
            os.chown(temp_path, uid, gid)
        os.replace(temp_path, path)
        if os.name == "posix":
            directory_fd = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
    finally:
        temp_path.unlink(missing_ok=True)


def _backup() -> str:
    if BACKUP_DIR.is_symlink():
        raise OpsError("backup directory must not be a symlink")
    BACKUP_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(BACKUP_DIR, 0o700)
    backup_id = f"{time.strftime('%Y%m%d-%H%M%S', time.gmtime())}-{secrets.token_hex(3)}"
    target = BACKUP_DIR / backup_id
    target.mkdir(mode=0o700)
    for name in CONFIG_NAMES:
        source = _config_path(RUNTIME_DIR, name)
        _require_regular(source)
        shutil.copy2(source, _config_path(target, name), follow_symlinks=False)
    return backup_id


def _restore_active(backup_id: str) -> None:
    source_root = BACKUP_DIR / backup_id
    if source_root.is_symlink() or not source_root.is_dir():
        raise OpsError("backup is unavailable")
    for name in CONFIG_NAMES:
        source = _config_path(source_root, name)
        target = _config_path(RUNTIME_DIR, name)
        _require_regular(source)
        _atomic_write(target, source.read_bytes(), reference=target)


def _secret_values() -> tuple[str, ...]:
    values: set[str] = set()
    try:
        env_data = _parse_env(_config_path(RUNTIME_DIR, ".env").read_text(encoding="utf-8"))
        values.update(value.strip("\"'") for value in env_data.values() if len(value) >= 4)
        models = json.loads(
            _config_path(RUNTIME_DIR, "models.json").read_text(encoding="utf-8")
        )

        def collect(value: Any, key: str = "") -> None:
            if isinstance(value, dict):
                for child_key, child_value in value.items():
                    collect(child_value, str(child_key))
            elif isinstance(value, list):
                for child in value:
                    collect(child, key)
            elif isinstance(value, str) and re.search(
                r"(?i)(key|token|secret|password)", key
            ) and not value.startswith("${") and len(value) >= 4:
                values.add(value)

        collect(models)
    except (OSError, UnicodeError, ValueError, OpsError):
        return ()
    return tuple(sorted(values, key=len, reverse=True))


def _redact(text: str) -> str:
    for value in _secret_values():
        text = text.replace(value, "[REDACTED]")
    return SECRET_KEY_RE.sub(lambda match: f"{match.group(1)}{match.group(2)}[REDACTED]", text)


def _script(name: str) -> Path:
    path = SCRIPTS_DIR / name
    if path.parent != SCRIPTS_DIR or path.is_symlink() or not path.is_file():
        raise OpsError(f"installed operation script is unavailable: {name}")
    return path


def _run_script(name: str, args: tuple[str, ...] = (), *, stream: bool = False) -> int:
    command = ["/bin/sh", str(_script(name)), *args]
    script_env = dict(SAFE_ENV)
    try:
        for line in Path("/etc/zhice-ops/ops.env").read_text(encoding="utf-8").splitlines():
            key, separator, value = line.partition("=")
            if separator and key == "ZHICE_PUBLIC_HEALTH_URL":
                script_env[key] = value
    except (OSError, UnicodeError):
        pass
    if stream:
        process = subprocess.Popen(
            command,
            env=script_env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        assert process.stdout is not None
        selector = selectors.DefaultSelector()
        selector.register(process.stdout, selectors.EVENT_READ)
        window_started = time.monotonic()
        deadline = window_started + 900
        window_bytes = 0
        limited = False
        timed_out = False
        pending = bytearray()
        discarding_line = False
        try:
            while selector.get_map():
                now = time.monotonic()
                if now >= deadline:
                    print("Log follow stopped after the 15 minute session limit", flush=True)
                    timed_out = True
                    _terminate_process_group(process)
                    break
                if now - window_started >= 1:
                    window_started = now
                    window_bytes = 0
                    limited = False
                ready = selector.select(timeout=1)
                if not ready and process.poll() is not None:
                    continue
                for key, _mask in ready:
                    chunk = os.read(key.fileobj.fileno(), 4096)
                    if not chunk:
                        selector.unregister(key.fileobj)
                        break
                    discarding_line, window_bytes, limited = _emit_stream_chunk(
                        chunk,
                        pending=pending,
                        discarding_line=discarding_line,
                        window_bytes=window_bytes,
                        limited=limited,
                    )
            if pending and not discarding_line:
                _emit_bounded_line(bytes(pending), window_bytes=window_bytes, limited=limited)
        finally:
            selector.close()
            if process.poll() is None:
                _terminate_process_group(process)
        result = process.wait(timeout=5)
        return 124 if timed_out else result
    result, output_bytes, truncated, timed_out = _run_bounded_process(
        command,
        env=script_env,
        timeout_seconds=90,
        max_bytes=MAX_SCRIPT_OUTPUT_BYTES,
    )
    output = _redact(output_bytes.decode("utf-8", errors="replace"))
    if output:
        target = sys.stdout if result == 0 or truncated else sys.stderr
        print(output, file=target, end="" if output.endswith("\n") else "\n")
    if truncated:
        print("[output truncated at 131072 bytes]", flush=True)
        return 0
    return 124 if timed_out else result


def _run_bounded_process(
    command: list[str],
    *,
    env: dict[str, str],
    timeout_seconds: int,
    max_bytes: int,
) -> tuple[int, bytes, bool, bool]:
    process = subprocess.Popen(
        command,
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    assert process.stdout is not None
    selector = selectors.DefaultSelector()
    selector.register(process.stdout, selectors.EVENT_READ)
    output = bytearray()
    truncated = False
    timed_out = False
    deadline = time.monotonic() + timeout_seconds
    try:
        while selector.get_map():
            if time.monotonic() >= deadline:
                timed_out = True
                _terminate_process_group(process)
                break
            ready = selector.select(timeout=0.5)
            if not ready and process.poll() is not None:
                continue
            for key, _mask in ready:
                chunk = os.read(key.fileobj.fileno(), 4096)
                if not chunk:
                    selector.unregister(key.fileobj)
                    break
                remaining = max_bytes - len(output)
                if remaining > 0:
                    output.extend(chunk[:remaining])
                if len(chunk) > remaining:
                    truncated = True
                    _terminate_process_group(process)
                    break
            if truncated:
                break
    finally:
        selector.close()
        if process.poll() is None:
            _terminate_process_group(process)
    result = process.wait(timeout=5)
    return result, bytes(output), truncated, timed_out


def _emit_stream_chunk(
    chunk: bytes,
    *,
    pending: bytearray,
    discarding_line: bool,
    window_bytes: int,
    limited: bool,
) -> tuple[bool, int, bool]:
    remaining = chunk
    while remaining:
        if discarding_line:
            _discarded, separator, remaining = remaining.partition(b"\n")
            if not separator:
                return True, window_bytes, limited
            discarding_line = False
            continue
        segment, separator, rest = remaining.partition(b"\n")
        available = MAX_STREAM_LINE_BYTES - len(pending)
        pending.extend(segment[:available])
        overlong = len(segment) > available
        if separator:
            window_bytes, limited = _emit_bounded_line(
                bytes(pending),
                window_bytes=window_bytes,
                limited=limited,
                truncated=overlong,
            )
            pending.clear()
            remaining = rest
            continue
        if overlong:
            window_bytes, limited = _emit_bounded_line(
                bytes(pending),
                window_bytes=window_bytes,
                limited=limited,
                truncated=True,
            )
            pending.clear()
            discarding_line = True
        return discarding_line, window_bytes, limited
    return discarding_line, window_bytes, limited


def _emit_bounded_line(
    raw: bytes,
    *,
    window_bytes: int,
    limited: bool,
    truncated: bool = False,
) -> tuple[int, bool]:
    safe_line = _redact(raw.decode("utf-8", errors="replace"))
    if truncated:
        safe_line += " [line truncated]"
    encoded_size = len(safe_line.encode("utf-8", errors="replace")) + 1
    window_bytes += encoded_size
    if window_bytes > MAX_STREAM_BYTES_PER_SECOND:
        if not limited:
            print("[output rate limited]", flush=True)
        return window_bytes, True
    print(safe_line, flush=True)
    return window_bytes, limited


def _terminate_process_group(process: subprocess.Popen[bytes]) -> None:
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    try:
        process.wait(timeout=1)
        return
    except subprocess.TimeoutExpired:
        pass
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    process.wait(timeout=5)


def _stage(name: str) -> None:
    content = sys.stdin.buffer.read(MAX_CONFIG_BYTES + 1)
    if len(content) > MAX_CONFIG_BYTES:
        raise OpsError(f"edited content exceeds {MAX_CONFIG_BYTES} bytes")
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise OpsError("edited content must be UTF-8") from exc
    _validate_config(name, text)
    backup_id = _backup()
    target = _config_path(PENDING_DIR, name)
    _atomic_write(target, content)
    try:
        validate_all()
    except OpsError:
        _audit("config.edit", result="validation_failed", detail=name)
        raise
    _audit("config.edit", result="staged", detail=name)
    print(f"Staged {name}; backup={backup_id}. Run config apply to activate it.")


def _diff() -> None:
    found = False
    written = 0
    for name in CONFIG_NAMES:
        pending = _config_path(PENDING_DIR, name)
        if not pending.exists():
            continue
        _require_regular(pending)
        active = _config_path(RUNTIME_DIR, name)
        _require_regular(active)
        found = True
        diff = difflib.unified_diff(
            active.read_text(encoding="utf-8").splitlines(keepends=True),
            pending.read_text(encoding="utf-8").splitlines(keepends=True),
            fromfile=f"active/{name}",
            tofile=f"pending/{name}",
        )
        for line in diff:
            encoded = line.encode("utf-8")
            if written + len(encoded) > MAX_DIFF_BYTES:
                print("[diff truncated]")
                return
            print(line, end="")
            written += len(encoded)
    if not found:
        print("No pending configuration changes")


def _restore_to_pending(backup_id: str) -> None:
    if not BACKUP_ID_RE.fullmatch(backup_id):
        raise OpsError("invalid backup id")
    source_root = BACKUP_DIR / backup_id
    if source_root.is_symlink() or not source_root.is_dir():
        raise OpsError("backup is unavailable")
    for name in CONFIG_NAMES:
        source = _config_path(source_root, name)
        _require_regular(source)
        _atomic_write(_config_path(PENDING_DIR, name), source.read_bytes())
    validate_all()
    _audit("config.restore", result="staged", detail=backup_id)
    print(f"Staged backup {backup_id}; run config apply to activate it")


def _apply() -> None:
    pending_names = [name for name in CONFIG_NAMES if _config_path(PENDING_DIR, name).exists()]
    if not pending_names:
        raise OpsError("no pending configuration changes")
    validate_all()
    backup_id = _backup()
    try:
        for name in pending_names:
            pending = _config_path(PENDING_DIR, name)
            _require_regular(pending)
            active = _config_path(RUNTIME_DIR, name)
            _atomic_write(active, pending.read_bytes(), reference=active)
        if _run_script("apply.sh") != 0:
            raise OpsError("container recreate or health verification failed")
    except Exception:
        _restore_active(backup_id)
        _run_script("apply.sh")
        _audit("config.apply", result="failed_rolled_back", detail=backup_id)
        raise
    for name in pending_names:
        _config_path(PENDING_DIR, name).unlink(missing_ok=True)
    _audit("config.apply", result="success", detail=backup_id)
    print(f"Applied configuration; rollback backup={backup_id}")


def dispatch(argv: tuple[str, ...]) -> int:
    if not argv:
        raise OpsError("missing operation")
    if argv == ("status",):
        return _run_script("status.sh")
    if argv and argv[0] == "logs":
        if len(argv) > 2:
            raise OpsError("logs accepts at most one line count")
        lines = 200
        if len(argv) == 2:
            if not argv[1].isdigit() or not 1 <= int(argv[1]) <= MAX_LOG_LINES:
                raise OpsError(f"logs line count must be 1..{MAX_LOG_LINES}")
            lines = int(argv[1])
        _audit("container.logs_read", result="requested", detail=f"lines-{lines}")
        return _run_script("logs.sh", (str(lines),))
    if argv == ("logs-follow",):
        _audit("container.logs_read", result="follow")
        return _run_script("logs.sh", ("--follow",), stream=True)
    if argv == ("diagnose",):
        _audit("host_diagnose_requested", result="requested")
        return _run_script("diagnose.sh")
    if argv == ("restart",):
        _audit("container.restart_requested", result="requested")
        result = _run_script("restart.sh")
        _audit("container.restart_completed" if result == 0 else "container.restart_failed", result=str(result))
        return result
    if len(argv) == 3 and argv[:2] == ("config", "view"):
        path = _config_path(RUNTIME_DIR, argv[2])
        _require_regular(path)
        _audit("config.view", result="success", detail=argv[2])
        sys.stdout.write(path.read_text(encoding="utf-8"))
        return 0
    if argv == ("config", "validate"):
        validate_all()
        _audit("config.validate", result="success")
        print("Configuration is valid")
        return 0
    if argv == ("config", "diff"):
        _audit("config.diff", result="requested")
        _diff()
        return 0
    if argv == ("config", "backup"):
        backup_id = _backup()
        _audit("config.backup", result="success", detail=backup_id)
        print(backup_id)
        return 0
    if len(argv) == 3 and argv[:2] == ("config", "restore"):
        _restore_to_pending(argv[2])
        return 0
    if argv == ("config", "apply"):
        _apply()
        return 0
    if len(argv) == 2 and argv[0] == "config-stage":
        _stage(argv[1])
        return 0
    raise OpsError("operation is not allowed")


def main() -> int:
    try:
        return dispatch(tuple(sys.argv[1:]))
    except OpsError as exc:
        print(f"Operation failed: {exc}", file=sys.stderr)
        return 2
    except (OSError, UnicodeError, subprocess.SubprocessError):
        _audit("ops.internal_failure", result="failed")
        print("Operation failed safely; inspect the ZhiCe Ops journal", file=sys.stderr)
        return 1
    except Exception:
        _audit("ops.internal_failure", result="failed")
        print("Operation failed safely; inspect the ZhiCe Ops journal", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
