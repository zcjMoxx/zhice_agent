"""Loopback-published, fixed-target Docker Ops UI for local Compose."""

from __future__ import annotations

import http.client
import json
import os
import re
import shlex
import socket
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, urlsplit

CONTAINER_NAME = "zhice-agent"
DOCKER_SOCKET = "/var/run/docker.sock"
MAX_LOG_LINES = 500
_API_VERSION = ""
OPS_PAGE = Path("/opt/zhice-ops/ops.html")
LOCAL_OPS_HELP = """ZhiCe restricted local operations commands:
  status
  logs [1..500]
  logs-follow
  diagnose
  restart
  help
  exit

Server-only configuration commands are unavailable in local Docker mode.
"""


class DockerError(RuntimeError):
    pass


class UnixConnection(http.client.HTTPConnection):
    def connect(self) -> None:
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(self.timeout)
        self.sock.connect(DOCKER_SOCKET)


def docker_request(method: str, path: str) -> tuple[int, bytes]:
    global _API_VERSION
    if not _API_VERSION:
        status, payload = _raw_docker_request("GET", "/version")
        if status != HTTPStatus.OK:
            raise DockerError(f"Docker version negotiation failed safely ({status})")
        try:
            version = str(json.loads(payload).get("ApiVersion", ""))
        except (ValueError, AttributeError, json.JSONDecodeError) as exc:
            raise DockerError("Docker version negotiation returned invalid data") from exc
        if not re.fullmatch(r"1\.[0-9]{2,3}", version):
            raise DockerError("Docker daemon returned an invalid API version")
        _API_VERSION = version
    return _raw_docker_request(method, f"/v{_API_VERSION}{path}")


def _raw_docker_request(method: str, path: str) -> tuple[int, bytes]:
    connection = UnixConnection("localhost", timeout=45)
    try:
        connection.request(method, path, headers={"Host": "localhost"})
        response = connection.getresponse()
        return response.status, response.read(1_048_577)
    except (OSError, http.client.HTTPException) as exc:
        raise DockerError("Docker daemon is unavailable") from exc
    finally:
        connection.close()


def container_status() -> dict[str, object]:
    code, body = docker_request("GET", f"/containers/{quote(CONTAINER_NAME, safe='')}/json")
    if code == HTTPStatus.NOT_FOUND:
        return {
            "mode": "local_docker",
            "target_type": "container",
            "target": CONTAINER_NAME,
            "target_name": CONTAINER_NAME,
            "status": "missing",
        }
    if code != HTTPStatus.OK:
        raise DockerError(f"Docker inspect failed safely ({code})")
    data = json.loads(body)
    state = data.get("State", {}) if isinstance(data, dict) else {}
    config = data.get("Config", {}) if isinstance(data, dict) else {}
    return {
        "mode": "local_docker",
        "target_type": "container",
        "target": CONTAINER_NAME,
        "target_name": CONTAINER_NAME,
        "status": state.get("Status", "unknown"),
        "running": bool(state.get("Running", False)),
        "health": (state.get("Health") or {}).get("Status", "none"),
        "exit_code": state.get("ExitCode"),
        "oom_killed": bool(state.get("OOMKilled", False)),
        "started_at": state.get("StartedAt", ""),
        "image": config.get("Image", ""),
        "restart_count": data.get("RestartCount", 0),
    }


def container_logs(lines: int) -> str:
    lines = max(1, min(lines, MAX_LOG_LINES))
    path = f"/containers/{quote(CONTAINER_NAME, safe='')}/logs?stdout=1&stderr=1&tail={lines}"
    code, body = docker_request("GET", path)
    if code == HTTPStatus.NOT_FOUND:
        return "Container is missing"
    if code != HTTPStatus.OK:
        raise DockerError(f"Docker logs failed safely ({code})")
    return _decode_docker_stream(body)


def restart_container() -> dict[str, object]:
    code, _body = docker_request(
        "POST", f"/containers/{quote(CONTAINER_NAME, safe='')}/restart?t=30"
    )
    if code not in {HTTPStatus.NO_CONTENT, HTTPStatus.OK}:
        raise DockerError(f"Docker restart failed safely ({code})")
    return container_status()


def _decode_docker_stream(body: bytes) -> str:
    output = bytearray()
    offset = 0
    while offset + 8 <= len(body) and body[offset] in {0, 1, 2}:
        size = int.from_bytes(body[offset + 4 : offset + 8], "big")
        offset += 8
        output.extend(body[offset : offset + size])
        offset += size
    if not output:
        output = bytearray(body)
    return output.decode("utf-8", errors="replace")[:262_144]


class Handler(BaseHTTPRequestHandler):
    server_version = "ZhiCeDockerOps/1"

    def do_GET(self) -> None:  # noqa: N802
        path = urlsplit(self.path)
        if path.path == "/":
            self._html()
            return
        try:
            if path.path == "/api/meta":
                self._json(
                    {
                        "mode": "local_docker",
                        "target_type": "container",
                        "target_name": CONTAINER_NAME,
                        "terminal_kind": "local",
                        "config_supported": False,
                    }
                )
                return
            if path.path == "/api/status":
                self._json(container_status())
                return
            if path.path == "/api/diagnose":
                status = container_status()
                status["docker_daemon"] = "available"
                self._json(status)
                return
            if path.path == "/api/logs":
                query = parse_qs(path.query)
                try:
                    lines = int(query.get("lines", ["200"])[0])
                except ValueError:
                    lines = 200
                self._json({"logs": container_logs(lines)})
                return
        except (ValueError, json.JSONDecodeError) as exc:
            self._json(
                {"status": "error", "message": str(exc)},
                status=HTTPStatus.BAD_REQUEST,
            )
        except DockerError as exc:
            self._json({"status": "error", "message": str(exc)}, status=503)
            return
        self.send_error(HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:  # noqa: N802
        if self.path not in {"/api/restart", "/api/command"}:
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        try:
            payload = self._read_json()
            if self.path == "/api/restart":
                if payload != {"confirm": "restart"}:
                    raise ValueError("restart confirmation is required")
                self._json(restart_container())
                return
            command = payload.get("command")
            if not isinstance(command, str):
                raise ValueError("command must be a string")
            self._json(execute_local_command(command))
        except (DockerError, ValueError, json.JSONDecodeError) as exc:
            self._json({"status": "error", "message": str(exc)}, status=503)

    def log_message(self, _format: str, *_args: object) -> None:
        return

    def _json(self, payload: object, *, status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self) -> dict[str, object]:
        if self.headers.get("Content-Type", "").split(";", 1)[0] != "application/json":
            raise ValueError("JSON content type is required")
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError as exc:
            raise ValueError("invalid content length") from exc
        if not 1 <= length <= 512:
            raise ValueError("request body is outside the allowed size")
        payload = json.loads(self.rfile.read(length))
        if not isinstance(payload, dict):
            raise ValueError("request body must be an object")
        return payload

    def _html(self) -> None:
        body = ops_page_html().encode("utf-8")
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; frame-ancestors 'self' http://127.0.0.1:*",
        )
        self.end_headers()
        self.wfile.write(body)


def ops_page_html() -> str:
    source_fallback = Path(__file__).resolve().parents[2] / "agent" / "operations" / "static" / "ops.html"
    path = OPS_PAGE if OPS_PAGE.is_file() else source_fallback
    return path.read_text(encoding="utf-8")


def parse_local_command(command: str) -> tuple[str, ...]:
    if not command or len(command) > 120 or "\x00" in command:
        raise ValueError("command is empty or too long")
    try:
        parts = tuple(shlex.split(command, posix=True))
    except ValueError as exc:
        raise ValueError("command quoting is invalid") from exc
    if not parts:
        raise ValueError("command is empty")
    if parts[0] in {"status", "logs-follow", "diagnose", "restart", "help", "exit"}:
        if len(parts) != 1:
            raise ValueError(f"{parts[0]} does not accept arguments")
        return parts
    if parts[0] == "logs" and len(parts) in {1, 2}:
        if len(parts) == 2 and (
            not parts[1].isdigit() or not 1 <= int(parts[1]) <= MAX_LOG_LINES
        ):
            raise ValueError(f"logs line count must be 1..{MAX_LOG_LINES}")
        return parts
    raise ValueError("unknown or unavailable local Ops command")


def format_fields(payload: dict[str, object]) -> str:
    return "\n".join(f"{key}={value}" for key, value in payload.items())


def execute_local_command(command: str) -> dict[str, object]:
    parsed = parse_local_command(command)
    if parsed == ("help",):
        return {"status": "ok", "output": LOCAL_OPS_HELP}
    if parsed == ("exit",):
        return {"status": "ok", "exit": True}
    if parsed == ("status",):
        return {"status": "ok", "output": format_fields(container_status())}
    if parsed == ("diagnose",):
        payload = container_status()
        payload["docker_daemon"] = "available"
        return {"status": "ok", "output": format_fields(payload)}
    if parsed == ("restart",):
        return {"status": "ok", "output": format_fields(restart_container())}
    if parsed == ("logs-follow",):
        return {"status": "ok", "output": "Log follow is controlled by this terminal session."}
    lines = int(parsed[1]) if len(parsed) == 2 else 200
    return {"status": "ok", "output": container_logs(lines)}


def main() -> None:
    if not os.path.exists(DOCKER_SOCKET):
        raise SystemExit("Docker socket is unavailable")
    server = ThreadingHTTPServer(("0.0.0.0", 17681), Handler)
    server.daemon_threads = True
    server.serve_forever()


if __name__ == "__main__":
    main()
