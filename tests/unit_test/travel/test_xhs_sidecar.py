from __future__ import annotations

import time
from pathlib import Path

from agent.applications.travel import xhs_sidecar
from agent.applications.travel.xhs_sidecar import LocalXhsSidecarSupervisor
from agent.protocols.mcp import McpServerSpec


def test_local_xhs_supervisor_derives_loopback_port_and_fixed_binary(monkeypatch, tmp_path):
    binary = tmp_path / "integrations" / "xhs" / "bin" / "xiaohongshu-mcp-windows-amd64.exe"
    binary.parent.mkdir(parents=True)
    binary.write_bytes(b"binary")
    monkeypatch.setattr(xhs_sidecar.sys, "platform", "win32")
    supervisor = LocalXhsSidecarSupervisor.from_specs(
        tmp_path,
        (_spec("http://127.0.0.1:18060/mcp"),),
    )

    assert supervisor.enabled is True
    assert supervisor.host == "127.0.0.1"
    assert supervisor.port == 18060
    assert supervisor.binary == binary


def test_local_xhs_supervisor_prefers_latest_rednote_compatible_binary(
    monkeypatch, tmp_path
):
    bin_dir = tmp_path / "integrations" / "xhs" / "bin"
    bin_dir.mkdir(parents=True)
    fallback = bin_dir / "xiaohongshu-mcp-windows-amd64.exe"
    older = bin_dir / "xiaohongshu-mcp-rednote-v2.4.3.exe"
    latest = bin_dir / "xiaohongshu-mcp-rednote-v2.10.0.exe"
    for binary in (fallback, older, latest):
        binary.write_bytes(b"binary")
    monkeypatch.setattr(xhs_sidecar.sys, "platform", "win32")

    supervisor = LocalXhsSidecarSupervisor.from_specs(
        tmp_path,
        (_spec("http://127.0.0.1:18060/mcp"),),
    )

    assert supervisor.binary == latest


def test_local_xhs_supervisor_detects_new_and_updated_cookie_file(monkeypatch, tmp_path):
    binary = tmp_path / "integrations" / "xhs" / "bin" / "xiaohongshu-mcp-windows-amd64.exe"
    binary.parent.mkdir(parents=True)
    binary.write_bytes(b"binary")
    monkeypatch.setattr(xhs_sidecar.sys, "platform", "win32")
    supervisor = LocalXhsSidecarSupervisor.from_specs(
        tmp_path,
        (_spec("http://127.0.0.1:18060/mcp"),),
    )
    cookie_file = tmp_path / "integrations" / "xhs" / "data" / "cookies.json"
    cookie_file.parent.mkdir(parents=True)

    assert supervisor.cookie_file == cookie_file
    assert supervisor._cookie_changed() is False

    cookie_file.write_text('[{"name":"first"}]', encoding="utf-8")
    assert supervisor._cookie_changed() is True
    assert supervisor._cookie_changed() is False

    cookie_file.write_text('[{"name":"first"}]', encoding="utf-8")
    assert supervisor._cookie_changed() is False

    cookie_file.write_text('[{"name":"second"}]', encoding="utf-8")
    assert supervisor._cookie_changed() is True


def test_local_xhs_supervisor_starts_one_fixed_login_helper(monkeypatch, tmp_path):
    bin_dir = tmp_path / "integrations" / "xhs" / "bin"
    bin_dir.mkdir(parents=True)
    (bin_dir / "xiaohongshu-mcp-windows-amd64.exe").write_bytes(b"mcp")
    login = bin_dir / "xiaohongshu-login-windows-amd64.exe"
    login.write_bytes(b"login")
    monkeypatch.setattr(xhs_sidecar.sys, "platform", "win32")
    monkeypatch.setattr(xhs_sidecar.os, "name", "nt")
    tree = _ProcessTree()
    calls = []

    def spawn(args, **kwargs):
        calls.append((args, kwargs))
        return tree

    monkeypatch.setattr(xhs_sidecar.ManagedProcessTree, "spawn", spawn)
    supervisor = LocalXhsSidecarSupervisor.from_specs(
        tmp_path,
        (_spec("http://127.0.0.1:18060/mcp"),),
    )

    assert supervisor.start_login() == "XHS_LOGIN_STARTED"
    assert supervisor.start_login() == "XHS_LOGIN_ALREADY_RUNNING"
    assert calls[0][0] == [str(login)]
    assert calls[0][1]["env"]["COOKIES_PATH"].endswith("cookies.json")
    assert supervisor.admin_snapshot()["login_in_progress"] is True

    supervisor.stop()
    assert tree.terminated is True


def test_local_xhs_login_cookie_update_closes_helper_and_reloads_before_completion(
    monkeypatch, tmp_path
):
    bin_dir = tmp_path / "integrations" / "xhs" / "bin"
    bin_dir.mkdir(parents=True)
    (bin_dir / "xiaohongshu-mcp-windows-amd64.exe").write_bytes(b"mcp")
    (bin_dir / "xiaohongshu-login-windows-amd64.exe").write_bytes(b"login")
    cookie = tmp_path / "integrations" / "xhs" / "data" / "cookies.json"
    cookie.parent.mkdir(parents=True)
    cookie.write_text('[{"name":"before"}]', encoding="utf-8")
    monkeypatch.setattr(xhs_sidecar.sys, "platform", "win32")
    monkeypatch.setattr(xhs_sidecar.os, "name", "nt")
    monkeypatch.setattr(xhs_sidecar, "_LOGIN_COOKIE_STABLE_SECONDS", 0.01)
    login_tree = _ProcessTree()
    sidecar_tree = _Tree()
    monkeypatch.setattr(
        xhs_sidecar.ManagedProcessTree,
        "spawn",
        lambda *_args, **_kwargs: login_tree,
    )
    supervisor = LocalXhsSidecarSupervisor.from_specs(
        tmp_path,
        (_spec("http://127.0.0.1:18060/mcp"),),
    )
    supervisor._tree = sidecar_tree
    reloaded = []
    monkeypatch.setattr(supervisor, "_spawn_and_wait", lambda: reloaded.append(True) or True)

    assert supervisor.start_login() == "XHS_LOGIN_STARTED"
    cookie.write_text('[{"name":"after"}]', encoding="utf-8")

    assert _wait_until(lambda: not supervisor.admin_snapshot()["login_in_progress"])
    snapshot = supervisor.admin_snapshot()
    assert login_tree.terminated is True
    assert sidecar_tree.terminated is True
    assert reloaded == [True]
    assert snapshot["state"] == "unknown"
    assert snapshot["code"] == "XHS_AUTH_RECHECK_PENDING"
    supervisor.stop()


def test_local_xhs_supervisor_restarts_only_an_owned_sidecar(monkeypatch, tmp_path):
    supervisor = LocalXhsSidecarSupervisor(tmp_path, None)
    supervisor.host, supervisor.port = "127.0.0.1", 18060
    supervisor.binary = Path(__file__)

    assert supervisor.restart() == "XHS_RESTART_NOT_OWNED"

    tree = _Tree()
    supervisor._tree = tree
    monkeypatch.setattr(supervisor, "_spawn_and_wait", lambda: True)

    assert supervisor.restart() == "XHS_RESTARTED"
    assert tree.terminated is True


def test_local_xhs_supervisor_ignores_remote_and_missing_binary(monkeypatch, tmp_path):
    monkeypatch.setattr(xhs_sidecar.sys, "platform", "win32")
    remote = LocalXhsSidecarSupervisor.from_specs(
        tmp_path,
        (_spec("https://xhs.example.com/mcp"),),
    )
    missing = LocalXhsSidecarSupervisor.from_specs(
        tmp_path,
        (_spec("http://127.0.0.1:18060/mcp"),),
    )

    assert remote.enabled is False
    assert missing.enabled is False
    assert remote.start() is False


def test_local_xhs_supervisor_reuses_existing_listener_without_ownership(monkeypatch, tmp_path):
    supervisor = LocalXhsSidecarSupervisor(tmp_path, None)
    supervisor.host, supervisor.port = "127.0.0.1", 18060
    supervisor.binary = Path(__file__)
    monkeypatch.setattr(supervisor, "_port_ready", lambda: True)

    assert supervisor.start() is True
    assert supervisor._tree is None
    supervisor.stop()


def test_local_xhs_supervisor_stops_only_owned_tree(tmp_path):
    supervisor = LocalXhsSidecarSupervisor(tmp_path, None)
    tree = _Tree()
    supervisor._tree = tree

    supervisor.stop()

    assert tree.terminated is True


def _spec(url: str) -> McpServerSpec:
    return McpServerSpec(
        server_id="xhs-readonly",
        transport="stdio",
        command="python",
        env={
            "XHS_READONLY_UPSTREAM_URL": url,
            "XHS_READONLY_COOKIE_FILE": "cookies.json",
        },
    )


class _Tree:
    def __init__(self):
        self.terminated = False

    def terminate(self, *, grace_seconds=0.5):
        del grace_seconds
        self.terminated = True


class _Process:
    def poll(self):
        return None


class _ProcessTree(_Tree):
    def __init__(self):
        super().__init__()
        self.process = _Process()


def _wait_until(predicate, timeout=3.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return False
