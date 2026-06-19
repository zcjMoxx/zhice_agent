"""Gateway entry scaffold for ZhiCe-Agent.

This module only owns the command contract and a minimal health surface. Full
Web UI, WebSocket, channel integration, authentication, and background service
management belong to later gateway/Web milestones.
"""

from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from agent.config import AppConfig
from agent.console import console


def run_gateway(
    config: AppConfig,
    *,
    host: str = "127.0.0.1",
    port: int = 18791,
) -> None:
    """Start the scaffold gateway and serve until interrupted."""

    config.ensure_dirs()
    server = ThreadingHTTPServer((host, port), _build_handler(config))
    print(
        f"{console.bold('ZhiCe-Agent gateway')} listening on "
        f"{console.command(f'http://{host}:{port}')}"
    )
    print(f"workspace: {console.path(config.workspace)}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print(console.warning("gateway stopped"))
    finally:
        server.server_close()


def gateway_status(config: AppConfig) -> dict[str, str]:
    """Return the status payload exposed by the scaffold health endpoint."""

    return {
        "status": "ok",
        "name": "ZhiCe-Agent",
        "workspace": str(config.workspace),
        "config_dir": str(config.config_dir),
        "sessions_dir": str(config.sessions_dir),
    }


def _build_handler(config: AppConfig):
    """Create a request handler class bound to one resolved AppConfig."""

    status_payload = gateway_status(config)

    class ZhiCeGatewayHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            """Serve health/status JSON for the scaffold gateway."""

            if self.path in {"/", "/health"}:
                self._send_json(status_payload)
                return
            self._send_json({"status": "not_found"}, status=404)

        def log_message(self, format: str, *args) -> None:  # noqa: A002
            """Suppress default HTTP request logs for quiet CLI output."""

            return

        def _send_json(self, payload: dict[str, str], *, status: int = 200) -> None:
            """Write one UTF-8 JSON response."""

            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    return ZhiCeGatewayHandler


def format_gateway_check(config: AppConfig, *, host: str, port: int) -> str:
    """Format a non-blocking gateway readiness check for CLI tests and setup."""

    payload = gateway_status(config)
    lines = [
        console.success("ZhiCe-Agent gateway check ok"),
        f"url: {console.command(f'http://{host}:{port}')}",
        f"workspace: {console.path(payload['workspace'])}",
        f"config: {console.path(Path(payload['config_dir']))}",
    ]
    return "\n".join(lines)
