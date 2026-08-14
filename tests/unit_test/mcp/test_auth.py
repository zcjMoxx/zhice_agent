from __future__ import annotations

import asyncio
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from agent.mcp.auth import McpCredentialManager
from agent.protocols.mcp import McpOAuthSpec, McpServerSpec


class _TokenHandler(BaseHTTPRequestHandler):
    def do_POST(self):  # noqa: N802 - stdlib HTTP handler contract.
        length = int(self.headers.get("Content-Length", "0"))
        self.rfile.read(length)
        payload = json.dumps({"access_token": "refreshed-token", "expires_in": 3600}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, _format, *_args):
        return


@pytest.mark.integration
def test_refreshes_oauth_token_and_builds_bearer_header(monkeypatch):
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:1")
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:1")
    monkeypatch.setenv("ALL_PROXY", "http://127.0.0.1:1")
    server = ThreadingHTTPServer(("127.0.0.1", 0), _TokenHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        spec = McpServerSpec(
            server_id="oauth",
            transport="streamable_http",
            url="https://example.com/mcp",
            oauth=McpOAuthSpec(
                token_url=f"http://127.0.0.1:{server.server_port}/token",
                refresh_token="refresh-secret",
                client_id="client",
                client_secret="client-secret",
            ),
        )

        headers = asyncio.run(McpCredentialManager().headers_for(spec))

        assert headers == {"Authorization": "Bearer refreshed-token"}
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
