#!/usr/bin/python3 -I
"""Loopback-only dashboard adapter for the fixed ZhiCe server target."""

from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import html
import json
import os
import shlex
import signal
import subprocess
import time
from email.utils import formatdate
from http import HTTPStatus
from http.cookies import CookieError, SimpleCookie
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
MAX_AUTH_REQUEST_BYTES = 4_096
MAX_OUTPUT_BYTES = 131_072
MAX_LOG_LINES = 500
SESSION_COOKIE_NAME = "__Host-zhice_ops_session"
SESSION_MAX_AGE_SECONDS = 10 * 365 * 24 * 60 * 60
SESSION_CLOCK_SKEW_SECONDS = 60
SAFE_ENV = {
    "LANG": "C.UTF-8",
    "LC_ALL": "C.UTF-8",
    "PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
}


class DashboardError(RuntimeError):
    """A safe adapter error suitable for an authenticated response."""


def _credential_secret() -> str:
    credential = os.environ.get("ZHICE_OPS_CREDENTIAL", "")
    username, separator, secret = credential.partition(":")
    if (
        username != "owner"
        or separator != ":"
        or len(secret) != 48
        or any(character not in "0123456789abcdef" for character in secret)
    ):
        raise DashboardError("Ops authentication is unavailable")
    return secret


def issue_session_token(*, now: int | None = None) -> str:
    issued_at = int(time.time()) if now is None else now
    expires_at = issued_at + SESSION_MAX_AGE_SECONDS
    payload = f"v1.{issued_at}.{expires_at}"
    signature = hmac.new(
        bytes.fromhex(_credential_secret()),
        payload.encode("ascii"),
        hashlib.sha256,
    ).digest()
    encoded_signature = base64.urlsafe_b64encode(signature).decode("ascii").rstrip("=")
    return f"{payload}.{encoded_signature}"


def validate_session_token(token: str, *, now: int | None = None) -> bool:
    try:
        version, raw_issued_at, raw_expires_at, encoded_signature = token.split(".")
        issued_at = int(raw_issued_at)
        expires_at = int(raw_expires_at)
    except (TypeError, ValueError):
        return False
    current_time = int(time.time()) if now is None else now
    if (
        version != "v1"
        or issued_at > current_time + SESSION_CLOCK_SKEW_SECONDS
        or expires_at <= current_time
        or expires_at - issued_at != SESSION_MAX_AGE_SECONDS
    ):
        return False
    payload = f"{version}.{issued_at}.{expires_at}"
    expected_signature = base64.urlsafe_b64encode(
        hmac.new(
            bytes.fromhex(_credential_secret()),
            payload.encode("ascii"),
            hashlib.sha256,
        ).digest()
    ).decode("ascii").rstrip("=")
    return hmac.compare_digest(encoded_signature, expected_signature)


def safe_next_path(value: str | None) -> str:
    if (
        not value
        or not value.startswith("/")
        or value.startswith("//")
        or "\r" in value
        or "\n" in value
    ):
        return "/"
    return value


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
            if parsed.path == "/auth/login":
                if self._has_valid_session():
                    self._redirect(safe_next_path(parse_qs(parsed.query).get("next", ["/"])[0]))
                    return
                self._login_page(
                    next_path=safe_next_path(
                        parse_qs(parsed.query).get("next", ["/"])[0]
                    )
                )
                return
            if parsed.path == "/auth/check":
                if self._has_valid_session():
                    self.send_response(HTTPStatus.NO_CONTENT)
                    self.send_header("Cache-Control", "no-store")
                    self.end_headers()
                elif self._has_valid_basic_authorization():
                    self.send_response(HTTPStatus.SEE_OTHER)
                    self.send_header(
                        "Location",
                        safe_next_path(self.headers.get("X-Forwarded-Uri")),
                    )
                    self.send_header("Cache-Control", "no-store")
                    self.send_header("Set-Cookie", self._session_cookie(issue_session_token()))
                    self.end_headers()
                else:
                    self._redirect("/auth/login")
                return
            if parsed.path == "/auth/logout":
                self._clear_session_cookie()
                return
            if parsed.path == "/api/meta":
                self._json(
                    {
                        "mode": "server_docker",
                        "target_type": "container",
                        "target_name": "zhice-agent",
                        "terminal_kind": "ttyd",
                        "terminal_url": "/terminal/",
                        "config_supported": True,
                        "auth_logout_url": "/auth/logout",
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
        parsed = urlsplit(self.path)
        if parsed.path == "/auth/login":
            self._authenticate()
            return
        if parsed.path != "/api/restart":
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

    def _read_form(self) -> dict[str, list[str]]:
        if (
            self.headers.get("Content-Type", "").split(";", 1)[0]
            != "application/x-www-form-urlencoded"
        ):
            raise ValueError("form content type is required")
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError as exc:
            raise ValueError("invalid content length") from exc
        if not 1 <= length <= MAX_AUTH_REQUEST_BYTES:
            raise ValueError("form body is outside the allowed size")
        return parse_qs(
            self.rfile.read(length).decode("utf-8"),
            keep_blank_values=True,
            strict_parsing=False,
        )

    def _authenticate(self) -> None:
        try:
            form = self._read_form()
            username = form.get("username", [""])[0]
            password = form.get("password", [""])[0]
            next_path = safe_next_path(form.get("next", ["/"])[0])
            valid = hmac.compare_digest(username, "owner") and hmac.compare_digest(
                password, _credential_secret()
            )
        except (DashboardError, UnicodeDecodeError, ValueError):
            valid = False
            next_path = "/"
        if not valid:
            self._login_page(
                next_path=next_path,
                error="用户名或密码不正确",
                status=HTTPStatus.UNAUTHORIZED,
            )
            return
        token = issue_session_token()
        self.send_response(HTTPStatus.SEE_OTHER)
        self.send_header("Location", next_path)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Set-Cookie", self._session_cookie(token))
        self.end_headers()

    def _has_valid_session(self) -> bool:
        raw_cookie = self.headers.get("Cookie", "")
        if not raw_cookie or len(raw_cookie) > MAX_AUTH_REQUEST_BYTES:
            return False
        cookie = SimpleCookie()
        try:
            cookie.load(raw_cookie)
        except CookieError:
            return False
        morsel = cookie.get(SESSION_COOKIE_NAME)
        if morsel is None:
            return False
        try:
            return validate_session_token(morsel.value)
        except DashboardError:
            return False

    def _has_valid_basic_authorization(self) -> bool:
        authorization = self.headers.get("Authorization", "")
        if not authorization.startswith("Basic ") or len(authorization) > 256:
            return False
        try:
            decoded = base64.b64decode(authorization[6:], validate=True).decode("utf-8")
            username, separator, password = decoded.partition(":")
            return (
                separator == ":"
                and hmac.compare_digest(username, "owner")
                and hmac.compare_digest(password, _credential_secret())
            )
        except (DashboardError, UnicodeDecodeError, ValueError):
            return False

    def _session_cookie(self, token: str) -> str:
        return (
            f"{SESSION_COOKIE_NAME}={token}; Path=/; "
            f"Max-Age={SESSION_MAX_AGE_SECONDS}; "
            f"Expires={formatdate(time.time() + SESSION_MAX_AGE_SECONDS, usegmt=True)}; "
            "Secure; HttpOnly; SameSite=Strict"
        )

    def _clear_session_cookie(self) -> None:
        self.send_response(HTTPStatus.SEE_OTHER)
        self.send_header("Location", "/auth/login")
        self.send_header("Cache-Control", "no-store")
        self.send_header(
            "Set-Cookie",
            f"{SESSION_COOKIE_NAME}=; Path=/; Max-Age=0; "
            "Expires=Thu, 01 Jan 1970 00:00:00 GMT; Secure; HttpOnly; SameSite=Strict",
        )
        self.end_headers()

    def _redirect(self, location: str) -> None:
        self.send_response(HTTPStatus.SEE_OTHER)
        self.send_header("Location", location)
        self.send_header("Cache-Control", "no-store")
        self.end_headers()

    def _login_page(
        self,
        *,
        next_path: str,
        error: str = "",
        status: int = HTTPStatus.OK,
    ) -> None:
        escaped_next = html.escape(next_path, quote=True)
        error_markup = f'<p class="error">{html.escape(error)}</p>' if error else ""
        body = f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>ZhiCe Server Ops 登录</title><style>
:root{{color-scheme:dark;font-family:Inter,"Microsoft YaHei",system-ui,sans-serif;background:#0b0f15;color:#e8ebf2}}
*{{box-sizing:border-box}}body{{min-height:100vh;margin:0;display:grid;place-items:center;padding:24px;background:radial-gradient(circle at top,#172238,#0b0f15 58%)}}
main{{width:min(420px,100%);padding:30px;border:1px solid #2b374a;border-radius:18px;background:#121822;box-shadow:0 22px 70px #0008}}
h1{{margin:0;font-size:27px}}p{{color:#96a6bc;line-height:1.65}}label{{display:block;margin-top:18px;color:#cbd6e5;font-size:14px}}
input{{width:100%;margin-top:7px;padding:12px 13px;border:1px solid #344258;border-radius:10px;background:#0d131c;color:#f2f6fc;font:inherit;outline:none}}
input:focus{{border-color:#71a7ff;box-shadow:0 0 0 3px #71a7ff22}}button{{width:100%;margin-top:22px;padding:12px;border:0;border-radius:10px;background:#71a7ff;color:#07101f;font:700 15px inherit;cursor:pointer;box-shadow:0 3px 0 #315d9f}}
button:active{{transform:translateY(2px);box-shadow:none}}.error{{padding:10px 12px;border:1px solid #713838;border-radius:9px;background:#351d22;color:#ff9b9b}}
small{{display:block;margin-top:18px;color:#74849a;line-height:1.6}}
</style></head><body><main><h1>ZhiCe Server Ops</h1><p>使用服务器独立 Owner 凭证登录。成功后将长期保持，终端空闲退出不会注销登录。</p>{error_markup}
<form method="post" action="/auth/login"><input type="hidden" name="next" value="{escaped_next}">
<label>用户名<input name="username" value="owner" autocomplete="username" required></label>
<label>密码<input name="password" type="password" autocomplete="current-password" required autofocus></label>
<button type="submit">登录 Ops</button></form><small>凭证只提交给宿主机 Ops，不会进入 Agent 容器、网页脚本或日志。</small></main></body></html>""".encode(
            "utf-8"
        )
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

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
