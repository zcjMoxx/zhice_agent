from __future__ import annotations

import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

from agent.mcp.runtime import McpRuntime
from agent.protocols.auth import ActorContext
from agent.protocols.mcp import McpServerSpec


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _wait_for_port(port: int, process: subprocess.Popen) -> None:
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise AssertionError(
                f"fake MCP server exited before listening; returncode={process.returncode}"
            )
        with socket.socket() as sock:
            sock.settimeout(0.2)
            if sock.connect_ex(("127.0.0.1", port)) == 0:
                return
        time.sleep(0.05)
    raise AssertionError(f"fake MCP server did not listen on port {port}")


def _actor() -> ActorContext:
    return ActorContext(
        actor_type="user",
        user_id="user-1",
        username="tester",
        display_name="Tester",
        role_keys=frozenset({"viewer"}),
        permission_keys=frozenset(),
        channel="web",
    )


@pytest.mark.parametrize(
    ("server_transport", "runtime_transport", "path"),
    [
        ("streamable-http", "streamable_http", "/mcp"),
        ("sse", "sse", "/sse"),
    ],
)
@pytest.mark.integration
def test_remote_transports_discover_and_call(
    tmp_path,
    server_transport,
    runtime_transport,
    path,
):
    port = _free_port()
    script = Path(__file__).with_name("fake_http_server.py").resolve()
    process = subprocess.Popen(  # noqa: S603 - fixed interpreter and local test script.
        [sys.executable, str(script), "--transport", server_transport, "--port", str(port)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    runtime = None
    try:
        _wait_for_port(port, process)
        runtime = McpRuntime(
            [
                McpServerSpec(
                    server_id="remote",
                    transport=runtime_transport,
                    url=f"http://127.0.0.1:{port}{path}",
                    startup_timeout_seconds=10,
                    connect_timeout_seconds=10,
                    call_timeout_seconds=10,
                )
            ],
            workspace=tmp_path,
        )
        tool = runtime.tools_for_actor(_actor(), tmp_path / "files")[0]
        result = tool.execute({"text": server_transport})

        assert not result.is_error
        assert f"remote:{server_transport}" in result.output
    finally:
        if runtime is not None:
            runtime.close()
        process.terminate()
        process.wait(timeout=10)
