from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[3]
HELPER_PATH = ROOT / "deploy" / "scripts" / "remote_ops.py"
SPEC = importlib.util.spec_from_file_location("zhice_remote_ops", HELPER_PATH)
assert SPEC is not None and SPEC.loader is not None
remote_ops = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = remote_ops
SPEC.loader.exec_module(remote_ops)


def test_helper_preflight_does_not_emit_paramiko_cryptography_warning() -> None:
    completed = subprocess.run(
        [sys.executable, str(HELPER_PATH), "--help"],
        check=False,
        capture_output=True,
        text=True,
        timeout=15,
    )

    assert completed.returncode == 0
    assert "CryptographyDeprecationWarning" not in completed.stderr


def write_config(tmp_path: Path, **overrides: Any) -> Path:
    config: dict[str, Any] = {
        "Registry": "registry.example.test/team",
        "SshHost": "server.example.test",
        "SshUser": "operator",
        "SshPassword": "ssh-secret",
        "RemoteOpsDir": "/home/operator/zhice-ops",
        "PublicUrl": "https://agent.example.test",
        "OpsUrl": "https://ops.example.test",
        "Port": 10086,
    }
    config.update(overrides)
    path = tmp_path / "cloud-target.json"
    path.write_text(json.dumps(config, ensure_ascii=False), encoding="utf-8")
    return path


def test_load_config_requires_remote_ops_dir(tmp_path: Path) -> None:
    current = remote_ops.load_config(write_config(tmp_path, RemoteOpsDir="/srv/current-ops/"))
    assert current["RemoteOpsDir"] == "/srv/current-ops"

    with pytest.raises(remote_ops.RemoteOpsError, match="RemoteOpsDir"):
        remote_ops.load_config(write_config(tmp_path, RemoteOpsDir=None))


def test_validate_https_origin_rejects_path_credentials_and_whitespace() -> None:
    assert (
        remote_ops.validate_https_origin("https://ops.example.test/", field="OpsUrl")
        == "https://ops.example.test"
    )
    for value in (
        "http://ops.example.test",
        "https://user@ops.example.test",
        "https://ops.example.test/path",
        "https://ops.example.test\n",
    ):
        with pytest.raises(remote_ops.RemoteOpsError):
            remote_ops.validate_https_origin(value, field="OpsUrl")


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"SshPassword": "云服务器SSH登录密码"}, "Chinese placeholder"),
        ({"SshPassword": "line1\nline2"}, "line breaks"),
    ],
)
def test_load_config_rejects_placeholder_and_line_break_password(
    tmp_path: Path, overrides: dict[str, Any], message: str
) -> None:
    with pytest.raises(remote_ops.RemoteOpsError, match=message):
        remote_ops.load_config(write_config(tmp_path, **overrides))


def test_public_config_never_returns_password(tmp_path: Path) -> None:
    config = remote_ops.load_config(write_config(tmp_path))
    public = remote_ops.public_config(config)

    assert "SshPassword" not in public
    assert "ssh-secret" not in json.dumps(public)


class FakeSftp:
    def __init__(self) -> None:
        self.puts: list[tuple[str, str]] = []
        self.chmods: list[tuple[str, int]] = []
        self.renames: list[tuple[str, str]] = []

    def __enter__(self) -> FakeSftp:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def put(self, local: str, remote: str) -> None:
        self.puts.append((local, remote))

    def chmod(self, remote: str, mode: int) -> None:
        self.chmods.append((remote, mode))

    def rename(self, source: str, target: str) -> None:
        self.renames.append((source, target))


class FakeUploadClient:
    def __init__(self) -> None:
        self.sftp = FakeSftp()

    def open_sftp(self) -> FakeSftp:
        return self.sftp


def test_upload_release_uploads_six_scripts_validates_and_switches_atomically(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for name in remote_ops.SCRIPT_NAMES:
        (tmp_path / name).write_text("#!/usr/bin/env sh\n", encoding="utf-8")
    for name in remote_ops.PYTHON_SCRIPT_NAMES:
        (tmp_path / name).write_text("print('ok')\n", encoding="utf-8")
    commands: list[str] = []
    monkeypatch.setattr(
        remote_ops,
        "run",
        lambda _client, command: (commands.append(command) or ("", "")),
    )
    client = FakeUploadClient()

    current = remote_ops.upload_release(
        client, tmp_path, "/home/operator/zhice-ops", "20260804-release"
    )

    assert current == "/home/operator/zhice-ops/current"
    expected_scripts = set(remote_ops.SCRIPT_NAMES + remote_ops.PYTHON_SCRIPT_NAMES)
    assert len(client.sftp.puts) == len(expected_scripts)
    assert {Path(local).name for local, _remote in client.sftp.puts} == set(
        expected_scripts
    )
    assert all(mode == 0o700 for _remote, mode in client.sftp.chmods)
    assert len(client.sftp.renames) == len(expected_scripts)
    syntax_command = next(command for command in commands if "sh -n" in command)
    assert all(name in syntax_command for name in remote_ops.SCRIPT_NAMES)
    assert "deployment_smoke.py" in syntax_command
    switch_command = next(command for command in commands if "mv -Tf" in command)
    assert "ln -sfn" in switch_command
    assert "/home/operator/zhice-ops/current" in switch_command


def test_upload_release_includes_fixed_public_ops_assets(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    scripts = tmp_path / "scripts"
    ops = tmp_path / "ops"
    scripts.mkdir()
    for name in remote_ops.SCRIPT_NAMES:
        (scripts / name).write_text("#!/usr/bin/env sh\n", encoding="utf-8")
    for name in remote_ops.PYTHON_SCRIPT_NAMES:
        (scripts / name).write_text("print('ok')\n", encoding="utf-8")
    for relative_name in remote_ops.OPS_FILES:
        path = ops / relative_name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("# public ops asset\n", encoding="utf-8")
    commands: list[str] = []
    monkeypatch.setattr(
        remote_ops,
        "run",
        lambda _client, command: (commands.append(command) or ("", "")),
    )
    client = FakeUploadClient()

    remote_ops.upload_release(
        client,
        scripts,
        "/home/operator/zhice-ops",
        "20260809-ops",
        ops,
    )

    uploaded = {Path(local).as_posix() for local, _remote in client.sftp.puts}
    assert all((ops / name).as_posix() in uploaded for name in remote_ops.OPS_FILES)
    assert any("python3 -m py_compile" in command for command in commands)
    assert any("ops/install.sh" in command for command in commands)


def test_ops_service_requires_persistent_cookie_and_loopback_basic_auth() -> None:
    gateway = (ROOT / "deploy/ops/systemd/zhice-ops.service").read_text(encoding="utf-8")
    terminal = (ROOT / "deploy/ops/systemd/zhice-ops-terminal.service").read_text(
        encoding="utf-8"
    )
    caddy = (ROOT / "deploy/ops/config/Caddyfile").read_text(encoding="utf-8")
    installer = (ROOT / "deploy/ops/install.sh").read_text(encoding="utf-8")
    sudoers = (ROOT / "deploy/ops/sudoers.d/zhice-ops").read_text(encoding="utf-8")

    assert "--credential ${ZHICE_OPS_CREDENTIAL}" in terminal
    assert "--interface lo" in terminal
    assert "--interface 127.0.0.1" not in terminal
    assert "IPAddressDeny=any" in terminal
    assert "IPAddressAllow=localhost" in terminal
    assert "/usr/bin/caddy run" in gateway
    assert "basicauth" not in caddy
    assert "forward_auth 127.0.0.1:{$ZHICE_OPS_DASHBOARD_PORT}" in caddy
    assert "uri /auth/check" in caddy
    assert 'header_up Authorization "Basic {$ZHICE_OPS_BASIC_AUTH}"' in caddy
    assert "ZHICE_OPS_CREDENTIAL=$ops_credential" in installer
    assert "ZHICE_OPS_BASIC_AUTH=$ops_basic_auth" in installer
    assert "printf 'owner:%s' \"$credential_secret\" | base64" in installer
    assert "caddy hash-password" not in installer
    assert "od -An -N24 -tx1 /dev/urandom" in installer
    assert "chmod 0600" in installer
    assert "root-only Ops credential was preserved or generated" in installer
    assert "echo \"$ops_credential\"" not in installer
    assert "visudo -cf /etc/sudoers" in installer
    assert "previous policy restored" in installer
    assert "Cmnd_Alias" not in sudoers
    assert "zhice_ops_root.py *" in sudoers


class FakeChannel:
    def __init__(self, output: bytes, *, exits: bool = True) -> None:
        self.output = [output]
        self.exits = exits
        self.sent: list[bytes] = []
        self.command = ""
        self.pty_requested = False
        self.write_shutdown = False
        self.closed = False

    def get_pty(self) -> None:
        self.pty_requested = True

    def exec_command(self, command: str) -> None:
        self.command = command

    def sendall(self, value: bytes) -> None:
        self.sent.append(value)

    def shutdown_write(self) -> None:
        self.write_shutdown = True

    def exit_status_ready(self) -> bool:
        return self.exits

    def recv_ready(self) -> bool:
        return bool(self.output)

    def recv(self, _size: int) -> bytes:
        return self.output.pop(0)

    def recv_stderr_ready(self) -> bool:
        return False

    def recv_stderr(self, _size: int) -> bytes:
        return b""

    def recv_exit_status(self) -> int:
        return 0

    def close(self) -> None:
        self.closed = True


class FakeTransport:
    def __init__(self, channel: FakeChannel) -> None:
        self.channel = channel

    def open_session(self) -> FakeChannel:
        return self.channel


class FakeDeployClient:
    def __init__(self, channel: FakeChannel) -> None:
        self.transport = FakeTransport(channel)

    def get_transport(self) -> FakeTransport:
        return self.transport


def test_sudo_deploy_sends_password_only_to_stdin_and_redacts_output() -> None:
    password = "never-print-this"
    channel = FakeChannel(f"before {password} after\n".encode())

    out, err = remote_ops.sudo_deploy(
        FakeDeployClient(channel),
        password,
        "/home/operator/zhice-ops/current",
        "registry.example.test/team/zhice-agent@sha256:" + "a" * 64,
        10086,
        "https://agent.example.test",
        "https://ops.example.test",
    )

    assert channel.pty_requested
    assert channel.sent == [(password + "\n").encode()]
    assert password not in channel.command
    assert "deploy.sh" in channel.command
    assert "ops/install.sh" in channel.command
    assert channel.command.index("deploy.sh") < channel.command.index("ops/install.sh")
    assert channel.command.index("ops/install.sh") < channel.command.index("status.sh")
    assert " 0 &&" in channel.command
    assert password not in out
    assert "[REDACTED]" in out
    assert err == ""


def test_sudo_deploy_can_skip_only_external_smoke() -> None:
    channel = FakeChannel(b"ok\n")

    remote_ops.sudo_deploy(
        FakeDeployClient(channel),
        "secret",
        "/home/operator/zhice-ops/current",
        "registry.example.test/team/zhice-agent@sha256:" + "b" * 64,
        10086,
        "https://agent.example.test",
        "https://ops.example.test",
        True,
    )

    assert " 1 &&" in channel.command


def test_sudo_deploy_times_out_and_closes_channel() -> None:
    channel = FakeChannel(b"", exits=False)

    with pytest.raises(remote_ops.RemoteOpsError, match="timed out"):
        remote_ops.sudo_deploy(
            FakeDeployClient(channel),
            "secret",
            "/home/operator/zhice-ops/current",
            "registry.example.test/team/zhice-agent@sha256:" + "a" * 64,
            10086,
            "https://agent.example.test",
            "https://ops.example.test",
            timeout_seconds=0,
        )

    assert channel.closed


def test_verify_public_health_runs_remote_curl_and_requires_ok(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    commands: list[str] = []

    def fake_run(_client: object, command: str) -> tuple[str, str]:
        commands.append(command)
        return ('{"status":"ok"}\n', "")

    monkeypatch.setattr(remote_ops, "run", fake_run)
    url = remote_ops.verify_public_health(object(), "https://agent.example.test/")

    assert url == "https://agent.example.test/health"
    assert commands == [
        "curl --fail --silent --show-error --max-time 20 -- "
        "https://agent.example.test/health"
    ]


def test_verify_public_health_rejects_unexpected_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        remote_ops,
        "run",
        lambda _client, _command: ('{"status":"degraded"}', ""),
    )

    with pytest.raises(remote_ops.RemoteOpsError, match="unexpected status"):
        remote_ops.verify_public_health(object(), "https://agent.example.test")
