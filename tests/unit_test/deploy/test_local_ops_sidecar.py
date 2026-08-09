from __future__ import annotations

import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SOURCE = ROOT / "deploy" / "ops" / "local_sidecar.py"
SPEC = importlib.util.spec_from_file_location("zhice_local_ops_sidecar_test", SOURCE)
assert SPEC is not None and SPEC.loader is not None
sidecar = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(sidecar)


def test_local_sidecar_inspects_only_fixed_container(monkeypatch):
    paths: list[tuple[str, str]] = []
    payload = {
        "State": {"Status": "running", "Running": True, "ExitCode": 0},
        "Config": {"Image": "zhice-agent:local"},
        "RestartCount": 2,
    }

    def request(method: str, path: str):
        paths.append((method, path))
        return 200, json.dumps(payload).encode()

    monkeypatch.setattr(sidecar, "docker_request", request)

    status = sidecar.container_status()

    assert paths == [("GET", "/containers/zhice-agent/json")]
    assert status["target"] == "zhice-agent"
    assert status["status"] == "running"


def test_local_sidecar_restart_uses_fixed_container(monkeypatch):
    paths: list[tuple[str, str]] = []

    def request(method: str, path: str):
        paths.append((method, path))
        if method == "POST":
            return 204, b""
        return 200, json.dumps({"State": {}, "Config": {}}).encode()

    monkeypatch.setattr(sidecar, "docker_request", request)

    sidecar.restart_container()

    assert paths[0] == ("POST", "/containers/zhice-agent/restart?t=30")
    assert all("zhice-agent" in path for _method, path in paths)


def test_docker_request_negotiates_daemon_api_version(monkeypatch):
    calls: list[tuple[str, str]] = []

    def raw(method: str, path: str):
        calls.append((method, path))
        if path == "/version":
            return 200, b'{"ApiVersion":"1.52"}'
        return 200, b"ok"

    monkeypatch.setattr(sidecar, "_API_VERSION", "")
    monkeypatch.setattr(sidecar, "_raw_docker_request", raw)

    assert sidecar.docker_request("GET", "/containers/zhice-agent/json") == (200, b"ok")
    assert calls == [
        ("GET", "/version"),
        ("GET", "/v1.52/containers/zhice-agent/json"),
    ]


def test_compose_starts_fixed_agent_and_loopback_ops_sidecar():
    compose = (ROOT / "deploy" / "docker-compose.yml").read_text(encoding="utf-8")

    assert "container_name: zhice-agent\n" in compose
    assert "container_name: zhice-agent-ops" in compose
    assert '"127.0.0.1:${ZHICE_OPS_PORT:-17681}:17681"' in compose
    assert "ZHICE_OPS_MODE: local_docker" in compose
    assert "/var/run/docker.sock:/var/run/docker.sock:ro" in compose


def test_docker_ops_page_continuously_follows_logs_with_pause_control():
    source = sidecar.ops_page_html()

    assert "监控面板" in source
    assert "运维终端" in source
    assert "暂停跟随" in source
    assert "继续跟随" in source
    assert "setInterval(loadLogs,1000)" in source
    assert "box.scrollTop=box.scrollHeight" in source
    assert "overflow-wrap:anywhere" in source
    assert "color-scheme:dark" in source
    assert "scrollbar-width:thin" in source
    assert "*::-webkit-scrollbar-thumb" in source
    assert "Refresh now" not in source


def test_docker_local_terminal_rejects_shell_docker_and_server_config():
    for command in ("bash", "docker ps", "sudo -i", "config view .env", "logs 501"):
        try:
            sidecar.parse_local_command(command)
        except ValueError:
            continue
        raise AssertionError(f"unsafe command accepted: {command}")

    assert sidecar.parse_local_command("status") == ("status",)
    assert sidecar.parse_local_command("logs 20") == ("logs", "20")
