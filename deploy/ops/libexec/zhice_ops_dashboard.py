#!/usr/bin/python3 -I
"""Loopback-only dashboard adapter for the fixed ZhiCe server target."""

from __future__ import annotations

import argparse
import json
import os
import shlex
import signal
import subprocess
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlsplit

ROOT_COMMAND = (
    "/usr/bin/sudo",
    "-n",
    "/usr/bin/python3",
    "-I",
    "/usr/local/libexec/zhice-ops/zhice_ops_root.py",
)
MAX_REQUEST_BYTES = 512
MAX_OUTPUT_BYTES = 131_072
MAX_LOG_LINES = 500
SAFE_ENV = {
    "LANG": "C.UTF-8",
    "LC_ALL": "C.UTF-8",
    "PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
}


class DashboardError(RuntimeError):
    """A safe adapter error suitable for an authenticated response."""


def run_operation(args: tuple[str, ...], *, timeout_seconds: int = 90) -> str:
    process = subprocess.Popen(
        [*ROOT_COMMAND, *args],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        env=SAFE_ENV,
        start_new_session=True,
    )
    try:
        output, _ = process.communicate(timeout=timeout_seconds)
    except subprocess.TimeoutExpired as exc:
        _terminate_process_group(process)
        raise DashboardError("operation timed out safely") from exc
    if len(output) > MAX_OUTPUT_BYTES:
        raise DashboardError("operation output exceeded the safe limit")
    text = output.decode("utf-8", errors="replace")
    if process.returncode != 0:
        raise DashboardError(text.strip() or "operation failed safely")
    return text


def _terminate_process_group(process: subprocess.Popen[bytes]) -> None:
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    try:
        process.wait(timeout=1)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.wait(timeout=5)


def parse_status_output(output: str) -> dict[str, Any]:
    fields: dict[str, Any] = {
        "mode": "server_docker",
        "target_type": "container",
        "target": "zhice-agent",
        "target_name": "zhice-agent",
    }
    try:
        tokens = shlex.split(output.replace("\r", " ").replace("\n", " "), posix=True)
    except ValueError:
        tokens = output.split()
    for token in tokens:
        key, separator, value = token.partition("=")
        if not separator or not key.replace("_", "").isalnum():
            continue
        if value in {"true", "false"}:
            fields[key] = value == "true"
        elif value.lstrip("-").isdigit():
            fields[key] = int(value)
        else:
            fields[key] = value
    if "name" in fields:
        fields["target_name"] = fields["name"]
    return fields


class DashboardHandler(BaseHTTPRequestHandler):
    server_version = "ZhiCeServerOpsDashboard/1"

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlsplit(self.path)
        try:
            if parsed.path == "/api/meta":
                self._json(
                    {
                        "mode": "server_docker",
                        "target_type": "container",
                        "target_name": "zhice-agent",
                        "terminal_kind": "ttyd",
                        "terminal_url": "/terminal/",
                        "config_supported": True,
                    }
                )
                return
            if parsed.path == "/api/status":
                self._json(parse_status_output(run_operation(("status",))))
                return
            if parsed.path == "/api/logs":
                lines = self._bounded_lines(parse_qs(parsed.query))
                self._json({"logs": run_operation(("logs", str(lines)))})
                return
            if parsed.path == "/api/diagnose":
                self._json({"status": "ok", "output": run_operation(("diagnose",))})
                return
        except DashboardError as exc:
            self._json(
                {"status": "error", "message": str(exc)},
                status=HTTPStatus.SERVICE_UNAVAILABLE,
            )
            return
        self.send_error(HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:  # noqa: N802
        if urlsplit(self.path).path != "/api/restart":
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        try:
            payload = self._read_json()
            if payload != {"confirm": "restart"}:
                raise ValueError("restart confirmation is required")
            output = run_operation(("restart",), timeout_seconds=120)
            self._json(
                {
                    "status": "ok",
                    "output": output,
                    "target": parse_status_output(run_operation(("status",))),
                }
            )
        except (DashboardError, ValueError, json.JSONDecodeError) as exc:
            self._json(
                {"status": "error", "message": str(exc)},
                status=HTTPStatus.BAD_REQUEST,
            )

    def log_message(self, _format: str, *_args: object) -> None:
        return

    def _bounded_lines(self, query: dict[str, list[str]]) -> int:
        raw = query.get("lines", ["200"])[0]
        if not raw.isdigit() or not 1 <= int(raw) <= MAX_LOG_LINES:
            raise DashboardError(f"logs line count must be 1..{MAX_LOG_LINES}")
        return int(raw)

    def _read_json(self) -> dict[str, object]:
        if self.headers.get("Content-Type", "").split(";", 1)[0] != "application/json":
            raise ValueError("JSON content type is required")
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError as exc:
            raise ValueError("invalid content length") from exc
        if not 1 <= length <= MAX_REQUEST_BYTES:
            raise ValueError("request body is outside the allowed size")
        payload = json.loads(self.rfile.read(length))
        if not isinstance(payload, dict):
            raise ValueError("request body must be an object")
        return payload

    def _json(self, payload: object, *, status: int = HTTPStatus.OK) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)


def main() -> None:
    parser = argparse.ArgumentParser(description="ZhiCe loopback Ops dashboard adapter")
    parser.add_argument("--port", type=int, default=7683)
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        raise SystemExit("invalid dashboard port")
    server = ThreadingHTTPServer(("127.0.0.1", args.port), DashboardHandler)
    server.daemon_threads = True
    server.serve_forever()


if __name__ == "__main__":
    main()
