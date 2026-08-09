"""Loopback-only Ops supervisor for a locally launched Gateway process."""

from __future__ import annotations

import html
import json
import os
import re
import shlex
import subprocess
import sys
import threading
import time
import uuid
from collections import deque
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.resources import files
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from agent.console import console
from agent.operations.runtime import (
    OperationsRuntimeState,
    clear_operations_runtime_state,
    write_operations_runtime_state,
)
from agent.process_tree import ManagedProcessTree

DEFAULT_OPS_PORT = 17681
MAX_OPS_PORT = 17690
MAX_LOG_LINES = 500
MAX_CAPTURED_LOG_LINES = 2_000
MAX_CAPTURED_LINE_CHARS = 8_192
ANSI_ESCAPE_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
_FORCE_TERMINAL_COLOR_ENV = "ZHICE_FORCE_TERMINAL_COLOR"
LOCAL_OPS_HELP = """ZhiCe restricted local operations commands:
  status
  logs [1..500]
  logs-follow
  diagnose
  restart
  help
  exit

Server-only configuration commands are unavailable in local process mode.
"""


class LocalOpsSupervisor:
    """Own exactly one Gateway child and expose fixed loopback operations."""

    def __init__(self, *, state_dir: Path, logs_dir: Path, child_argv: list[str]):
        self.state_dir = state_dir
        self.logs_dir = logs_dir
        self.child_argv = list(child_argv)
        self.instance_id = uuid.uuid4().hex
        self.started_at = time.time()
        self._tree: ManagedProcessTree | None = None
        self._lock = threading.RLock()
        self._server: ThreadingHTTPServer | None = None
        self._port = 0
        self._log_lines: deque[str] = deque(maxlen=MAX_CAPTURED_LOG_LINES)
        self._log_lock = threading.Lock()
        self._reader_thread: threading.Thread | None = None

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self._port}"

    def run(self) -> int:
        """Start Ops first, then the Gateway child, and supervise until interrupted."""

        self._server = self._bind_server()
        self._port = int(self._server.server_address[1])
        write_operations_runtime_state(
            self.state_dir,
            OperationsRuntimeState(
                mode="local_process",
                target_type="process",
                target_name="zcagent-gateway",
                url=self.url,
                instance_id=self.instance_id,
                supervisor_pid=os.getpid(),
            ),
        )
        self._start_child()
        server_thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        server_thread.start()
        print(_ops_startup_line(self.url))
        try:
            while True:
                time.sleep(0.2)
        except KeyboardInterrupt:
            return 0
        finally:
            self.close()

    def close(self) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
            self._server = None
        with self._lock:
            self._stop_child()
        clear_operations_runtime_state(self.state_dir, instance_id=self.instance_id)

    def status(self) -> dict[str, object]:
        with self._lock:
            process = self._tree.process if self._tree is not None else None
            return_code = process.poll() if process is not None else None
            return {
                "mode": "local_process",
                "target_type": "process",
                "target_name": "zcagent-gateway",
                "status": "running" if process is not None and return_code is None else "stopped",
                "pid": process.pid if process is not None else 0,
                "exit_code": return_code,
                "uptime_seconds": max(0, int(time.time() - self.started_at)),
                "ops_url": self.url,
            }

    def restart(self) -> dict[str, object]:
        with self._lock:
            self._stop_child()
            self._start_child()
            self.started_at = time.time()
        return self.status()

    def logs(self, lines: int) -> list[str]:
        lines = max(1, min(lines, MAX_LOG_LINES))
        with self._log_lock:
            return list(self._log_lines)[-lines:]

    def diagnose(self) -> dict[str, object]:
        status = self.status()
        status["state_file"] = str(self.state_dir / "operations.json")
        status["terminal_output_available"] = bool(self.logs(1))
        status["workspace_state_writable"] = self.state_dir.exists()
        return status

    def _start_child(self) -> None:
        child_env = dict(os.environ)
        child_env["PYTHONUNBUFFERED"] = "1"
        child_env["PYTHONIOENCODING"] = "utf-8"
        child_env["PYTHONUTF8"] = "1"
        if _terminal_supports_color():
            child_env[_FORCE_TERMINAL_COLOR_ENV] = "1"
        else:
            child_env.pop(_FORCE_TERMINAL_COLOR_ENV, None)
        self._tree = ManagedProcessTree.spawn(
            self.child_argv,
            env=child_env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
        if self._tree.process.stdout is not None:
            self._reader_thread = threading.Thread(
                target=self._read_child_output,
                args=(self._tree.process,),
                daemon=True,
            )
            self._reader_thread.start()

    def _stop_child(self) -> None:
        tree = self._tree
        reader = self._reader_thread
        self._tree = None
        self._reader_thread = None
        if tree is not None:
            tree.terminate(grace_seconds=3)
        if reader is not None:
            reader.join(timeout=1)

    def _read_child_output(self, process: subprocess.Popen[bytes]) -> None:
        stream = process.stdout
        if stream is None:
            return
        while True:
            raw = stream.readline()
            if not raw:
                break
            text = raw.decode("utf-8", errors="replace")
            try:
                sys.stdout.write(text)
                sys.stdout.flush()
            except (OSError, UnicodeError):
                pass
            safe_line = ANSI_ESCAPE_RE.sub("", text).rstrip("\r\n")
            if len(safe_line) > MAX_CAPTURED_LINE_CHARS:
                safe_line = safe_line[:MAX_CAPTURED_LINE_CHARS] + " [line truncated]"
            with self._log_lock:
                self._log_lines.append(safe_line)

    def _bind_server(self) -> ThreadingHTTPServer:
        handler = self._handler_type()
        for port in range(DEFAULT_OPS_PORT, MAX_OPS_PORT + 1):
            try:
                server = ThreadingHTTPServer(("127.0.0.1", port), handler)
                server.daemon_threads = True
                return server
            except OSError:
                continue
        raise OSError(f"no loopback Ops port is available in {DEFAULT_OPS_PORT}..{MAX_OPS_PORT}")

    def _handler_type(self) -> type[BaseHTTPRequestHandler]:
        supervisor = self

        class Handler(BaseHTTPRequestHandler):
            server_version = "ZhiCeLocalOps/1"

            def do_GET(self) -> None:  # noqa: N802
                parsed = urlsplit(self.path)
                if parsed.path == "/":
                    self._send_html(_local_ops_html())
                    return
                if parsed.path == "/api/meta":
                    self._send_json(
                        {
                            "mode": "local_process",
                            "target_type": "process",
                            "target_name": "zcagent-gateway",
                            "terminal_kind": "local",
                            "config_supported": False,
                        }
                    )
                    return
                if parsed.path == "/api/status":
                    self._send_json(supervisor.status())
                    return
                if parsed.path == "/api/diagnose":
                    self._send_json(supervisor.diagnose())
                    return
                if parsed.path == "/api/logs":
                    query = parse_qs(parsed.query)
                    try:
                        lines = int(query.get("lines", ["200"])[0])
                    except ValueError:
                        lines = 200
                    self._send_json({"lines": supervisor.logs(lines)})
                    return
                self.send_error(HTTPStatus.NOT_FOUND)

            def do_POST(self) -> None:  # noqa: N802
                if self.path not in {"/api/restart", "/api/command"}:
                    self.send_error(HTTPStatus.NOT_FOUND)
                    return
                try:
                    payload = self._read_json()
                except (ValueError, json.JSONDecodeError):
                    self.send_error(HTTPStatus.BAD_REQUEST)
                    return
                if self.path == "/api/restart":
                    if payload != {"confirm": "restart"}:
                        self.send_error(HTTPStatus.BAD_REQUEST)
                        return
                    self._send_json(supervisor.restart())
                    return
                command = payload.get("command")
                if not isinstance(command, str):
                    self.send_error(HTTPStatus.BAD_REQUEST)
                    return
                try:
                    self._send_json(_execute_local_command(supervisor, command))
                except ValueError as exc:
                    self._send_json(
                        {"status": "error", "message": str(exc)},
                        status=HTTPStatus.BAD_REQUEST,
                    )

            def log_message(self, _format: str, *_args: object) -> None:
                return

            def _read_json(self) -> dict[str, object]:
                content_type = self.headers.get("Content-Type", "").split(";", 1)[0]
                if content_type != "application/json":
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

            def _send_json(self, payload: object, *, status: int = HTTPStatus.OK) -> None:
                content = json.dumps(payload, ensure_ascii=False).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(content)))
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Security-Policy", "default-src 'none'; frame-ancestors 'self'")
                self.end_headers()
                self.wfile.write(content)

            def _send_html(self, content: str) -> None:
                body = content.encode("utf-8")
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

        return Handler


def _local_ops_html() -> str:
    return files("agent.operations").joinpath("static", "ops.html").read_text(encoding="utf-8")


def _legacy_local_ops_html() -> str:
    title = html.escape("ZhiCe-Agent Ops")
    return """<!doctype html>
<html><head><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'>
<title>__TITLE__</title><style>
html{color-scheme:dark;scrollbar-width:thin;scrollbar-color:#64748b #111827}
html,body,pre{scrollbar-width:thin;scrollbar-color:#64748b #111827}
*::-webkit-scrollbar,pre::-webkit-scrollbar{width:10px;height:10px}*::-webkit-scrollbar-track,pre::-webkit-scrollbar-track{background:#111827}*::-webkit-scrollbar-thumb,pre::-webkit-scrollbar-thumb{border:2px solid #111827;border-radius:999px;background:#64748b}*::-webkit-scrollbar-thumb:hover,pre::-webkit-scrollbar-thumb:hover{background:#94a3b8}*::-webkit-scrollbar-corner,pre::-webkit-scrollbar-corner{background:#111827}
body{font:14px system-ui;background:#10131a;color:#e8edf7;margin:0;padding:24px}
main{max-width:980px;margin:auto}section{background:#171c26;border:1px solid #2b3444;border-radius:14px;padding:18px;margin:14px 0}
button{background:#71a7ff;color:#07101f;border:0;border-radius:8px;padding:9px 14px;margin-right:8px;font-weight:700;cursor:pointer;box-shadow:0 2px 0 #315d9f;transition:background .14s ease,transform .08s ease,box-shadow .08s ease,filter .14s ease}
button:hover{filter:brightness(1.12)}button:active{transform:translateY(2px);box-shadow:0 0 0 #315d9f}button:focus-visible{outline:3px solid #dbeafe;outline-offset:3px}button:disabled{cursor:wait;opacity:.72;transform:none;box-shadow:none}.is-paused{background:#f59e0b;box-shadow:0 2px 0 #92400e}
pre{white-space:pre-wrap;overflow-wrap:anywhere;word-break:break-word;max-height:55vh;overflow:auto;color:#b9c7dc}
code{color:#8fc5ff}.log-line{display:block;min-height:1.35em}.log-time,.log-level-info,.log-component.ws{color:#22c55e}.log-level-debug,.log-action.agent{color:#22d3ee}.log-level-warning,.log-action.tool{color:#eab308}.log-level-error{color:#dc2626}.log-level-critical,.agent-severity-warning{color:#ef4444;font-weight:700}.agent-severity-error,.agent-severity-critical{color:#dc2626;font-weight:700}.log-action.web{color:#d946ef}.log-action.gateway,.log-component.gateway{color:#3b82f6}.log-action.ws{color:#22c55e}.log-action.zcagent{color:#e5e7eb}.log-fields{color:#b9c7dc}
</style></head><body><main><h1>__TITLE__</h1><p>Restricted local process monitor · <code>zcagent-gateway</code></p>
<section><h2>Status</h2><pre id=status>Loading…</pre><button id=restart onclick=restart()>Restart Gateway</button></section>
<section><h2>Logs</h2><button id=follow aria-pressed=false onclick=toggleFollow()>Pause follow</button><pre id=logs onscroll=trackScroll()></pre></section>
</main><script>
const logBox=document.querySelector('#logs'),followButton=document.querySelector('#follow'),restartButton=document.querySelector('#restart');let following=true;
function nearBottom(){return logBox.scrollHeight-logBox.scrollTop-logBox.clientHeight<28}
function renderFollow(){followButton.textContent=following?'Pause follow':'Continue follow';followButton.classList.toggle('is-paused',!following);followButton.setAttribute('aria-pressed',String(!following))}
function trackScroll(){following=nearBottom();renderFollow()}
function toggleFollow(){following=!following;renderFollow();if(following){logBox.scrollTop=logBox.scrollHeight;logs()}}
function part(text,className){const node=document.createElement('span');node.className=className;node.textContent=text;return node}
function actionClass(action){const value=action.trim();if(value.startsWith('TOOL '))return 'tool';for(const name of ['agent','web','gateway','ws'])if(value===name||value.startsWith(name+'.'))return name;return 'zcagent'}
function renderLine(line){
 const row=document.createElement('span');row.className='log-line';
 let match=line.match(/^(\\[[^\\]]+\\]) \\| (DEBUG|INFO|WARNING|ERROR|CRITICAL) \\| ([^|]+)(.*)$/);
 if(match){const level=match[2].toLowerCase(),action=actionClass(match[3]);row.classList.add('agent-severity-'+level);row.append(part(match[1],'log-time'),' | ',part(match[2],'log-level'),' | ',part(match[3],'log-action '+action),part(match[4],'log-fields'));return row}
 match=line.match(/^(DEBUG|INFO|WARNING|ERROR|CRITICAL):\\s*(\\[[^\\]]+\\])?\\s*(.*)$/);
 if(match){const level=match[1].toLowerCase(),component=match[2]?match[2].slice(1,-1):'';row.append(part(match[1]+':','log-level-'+level),' ');if(match[2])row.append(part(match[2],'log-component '+component),' ');row.append(part(match[3],'log-fields'));return row}
 row.textContent=line;return row
}
function renderLogs(lines){const fragment=document.createDocumentFragment();for(const line of lines)fragment.append(renderLine(String(line)));logBox.replaceChildren(fragment)}
async function status(){document.querySelector('#status').textContent=JSON.stringify(await(await fetch('/api/status')).json(),null,2)}
async function logs(){const stick=following||nearBottom(),d=await(await fetch('/api/logs?lines=500')).json();renderLogs(Array.isArray(d.lines)?d.lines:[]);if(stick)logBox.scrollTop=logBox.scrollHeight}
function delay(ms){return new Promise(resolve=>setTimeout(resolve,ms))}
async function restart(){if(!confirm('Restart the managed Gateway?'))return;restartButton.disabled=true;restartButton.textContent='Restarting…';try{const response=await fetch('/api/restart',{method:'POST'});if(!response.ok)throw new Error('restart failed');await status();restartButton.textContent='Restarted'}catch(_error){restartButton.textContent='Restart failed'}await delay(900);restartButton.textContent='Restart Gateway';restartButton.disabled=false}
renderFollow();status();logs();setInterval(status,3000);setInterval(()=>{if(following)logs()},1000)
</script></body></html>""".replace("__TITLE__", title)


def _parse_local_command(command: str) -> tuple[str, ...]:
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


def _format_fields(payload: dict[str, object]) -> str:
    return "\n".join(f"{key}={value}" for key, value in payload.items())


def _execute_local_command(
    supervisor: LocalOpsSupervisor, command: str
) -> dict[str, object]:
    parsed = _parse_local_command(command)
    if parsed == ("help",):
        return {"status": "ok", "output": LOCAL_OPS_HELP}
    if parsed == ("exit",):
        return {"status": "ok", "exit": True}
    if parsed == ("status",):
        return {"status": "ok", "output": _format_fields(supervisor.status())}
    if parsed == ("diagnose",):
        return {"status": "ok", "output": _format_fields(supervisor.diagnose())}
    if parsed == ("restart",):
        return {"status": "ok", "output": _format_fields(supervisor.restart())}
    if parsed == ("logs-follow",):
        return {
            "status": "ok",
            "output": "Log follow is controlled by this terminal session.",
        }
    lines = int(parsed[1]) if len(parsed) == 2 else 200
    return {"status": "ok", "output": "\n".join(supervisor.logs(lines))}


def _terminal_supports_color() -> bool:
    """Return whether the supervisor is teeing to an interactive terminal."""

    if os.getenv("NO_COLOR"):
        return False
    isatty = getattr(sys.stdout, "isatty", None)
    return bool(callable(isatty) and isatty())


def _ops_startup_line(url: str) -> str:
    """Render the local Ops startup line with the existing CLI color scheme."""

    return (
        f"{console.bold('ZhiCe-Agent Ops')} listening on "
        f"{console.command(url)} (local process)"
    )
