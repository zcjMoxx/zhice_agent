from __future__ import annotations

import base64
import http.client
import importlib.machinery
import importlib.util
import json
import threading
from pathlib import Path
from types import ModuleType
from urllib.parse import urlencode

import pytest

ROOT = Path(__file__).resolve().parents[3]
OPS = ROOT / "deploy" / "ops"


def load_source(name: str, path: Path) -> ModuleType:
    loader = importlib.machinery.SourceFileLoader(name, str(path))
    spec = importlib.util.spec_from_loader(name, loader)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


shell = load_source("zhice_ops_shell_test", OPS / "bin" / "zhice-ops-shell")
root_ops = load_source(
    "zhice_ops_root_test", OPS / "libexec" / "zhice_ops_root.py"
)
dashboard = load_source(
    "zhice_ops_dashboard_test", OPS / "libexec" / "zhice_ops_dashboard.py"
)


@pytest.mark.parametrize(
    "command",
    [
        "status",
        "logs",
        "logs 500",
        "logs-follow",
        "diagnose",
        "config view config.yml",
        "config view models.json",
        "config view .env",
        "config edit config.yml",
        "config validate",
        "config diff",
        "config backup",
        "config restore 20260809-120000-abcdef",
        "config apply",
        "restart",
        "help",
        "exit",
    ],
)
def test_restricted_shell_accepts_only_declared_grammar(command: str) -> None:
    assert shell.parse_command(command)


@pytest.mark.parametrize(
    "command",
    [
        "bash",
        "sh",
        "sudo -i",
        "docker ps",
        "config list",
        "status extra",
        "logs 0",
        "logs 501",
        "logs --tail 10",
        "logs; docker ps",
        "config view /etc/passwd",
        "config view ../config.yml",
        "config edit models.json extra",
        "config restore ../../root",
        "config unknown",
        "restart now",
    ],
)
def test_restricted_shell_rejects_shell_docker_path_and_extra_args(command: str) -> None:
    with pytest.raises(shell.CommandRejected):
        shell.parse_command(command)


def test_config_edit_uses_stdin_and_never_builds_a_shell_command(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[tuple[str, ...], bytes | None]] = []
    answers = iter(["schema_version: 1", ".save"])
    monkeypatch.setattr(
        shell,
        "_run_root",
        lambda args, input_bytes=None: calls.append((args, input_bytes)) or 0,
    )

    keep_running = shell.execute_command(
        ("config", "edit", "config.yml"), input_func=lambda _prompt: next(answers)
    )

    assert keep_running
    assert calls == [
        (("config-stage", "config.yml"), b"schema_version: 1\n")
    ]


def write_runtime(root: Path, *, env_secret: str = "top-secret-value") -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / ".env").write_text(
        f"ZHICE_API_KEY={env_secret}\nPUBLIC_VALUE=hello\n", encoding="utf-8"
    )
    (root / "config.yml").write_text(
        "schema_version: 1\nlogging: {}\n", encoding="utf-8"
    )
    (root / "models.json").write_text(
        '{"schema_version":1,"routing":{},"chat":{}}\n', encoding="utf-8"
    )


def point_root_ops_at_tmp(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    runtime = tmp_path / "runtime"
    monkeypatch.setattr(root_ops, "RUNTIME_DIR", runtime)
    monkeypatch.setattr(root_ops, "BACKUP_DIR", runtime / "backups")
    monkeypatch.setattr(root_ops, "PENDING_DIR", tmp_path / "pending")


def test_root_config_validator_covers_env_yaml_json_and_schema(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    point_root_ops_at_tmp(monkeypatch, tmp_path)
    write_runtime(root_ops.RUNTIME_DIR)
    root_ops.validate_all()

    (root_ops.RUNTIME_DIR / "models.json").write_text(
        '{"schema_version":2,"routing":{},"chat":{}}', encoding="utf-8"
    )
    with pytest.raises(root_ops.OpsError, match="schema_version"):
        root_ops.validate_all()

    (root_ops.RUNTIME_DIR / "models.json").write_text(
        '{"schema_version":1,"routing":{},"chat":{}}', encoding="utf-8"
    )
    (root_ops.RUNTIME_DIR / ".env").write_text("BROKEN\n", encoding="utf-8")
    with pytest.raises(root_ops.OpsError, match="KEY=VALUE"):
        root_ops.validate_all()

    (root_ops.RUNTIME_DIR / ".env").write_text(
        "ZHICE_AGENT_WORKSPACE=/tmp/escape\n", encoding="utf-8"
    )
    with pytest.raises(root_ops.OpsError, match="must not override"):
        root_ops.validate_all()


def test_config_apply_is_atomic_clears_pending_and_keeps_backup(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    point_root_ops_at_tmp(monkeypatch, tmp_path)
    write_runtime(root_ops.RUNTIME_DIR)
    root_ops.PENDING_DIR.mkdir()
    (root_ops.PENDING_DIR / "config.yml").write_text(
        "schema_version: 1\nlogging:\n  level: INFO\n", encoding="utf-8"
    )
    monkeypatch.setattr(root_ops, "_run_script", lambda *_args, **_kwargs: 0)
    monkeypatch.setattr(root_ops, "_audit", lambda *_args, **_kwargs: None)

    root_ops._apply()

    assert "level: INFO" in (root_ops.RUNTIME_DIR / "config.yml").read_text(
        encoding="utf-8"
    )
    assert not (root_ops.PENDING_DIR / "config.yml").exists()
    backups = list(root_ops.BACKUP_DIR.iterdir())
    assert len(backups) == 1
    assert (backups[0] / "config.yml").is_file()


def test_config_apply_recreate_failure_restores_active_files(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    point_root_ops_at_tmp(monkeypatch, tmp_path)
    write_runtime(root_ops.RUNTIME_DIR)
    original = (root_ops.RUNTIME_DIR / "config.yml").read_text(encoding="utf-8")
    root_ops.PENDING_DIR.mkdir()
    (root_ops.PENDING_DIR / "config.yml").write_text(
        "schema_version: 1\nlogging:\n  level: DEBUG\n", encoding="utf-8"
    )
    results = iter([1, 0])
    monkeypatch.setattr(
        root_ops, "_run_script", lambda *_args, **_kwargs: next(results)
    )
    monkeypatch.setattr(root_ops, "_audit", lambda *_args, **_kwargs: None)

    with pytest.raises(root_ops.OpsError, match="recreate"):
        root_ops._apply()

    assert (root_ops.RUNTIME_DIR / "config.yml").read_text(encoding="utf-8") == original


def test_log_redaction_uses_env_and_model_secret_values(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    point_root_ops_at_tmp(monkeypatch, tmp_path)
    write_runtime(root_ops.RUNTIME_DIR, env_secret="never-print-this")
    (root_ops.RUNTIME_DIR / "models.json").write_text(
        '{"schema_version":1,"routing":{},"chat":{"main":'
        '{"api_key":"model-secret"}}}',
        encoding="utf-8",
    )

    redacted = root_ops._redact(
        "api_key=generic-secret never-print-this model-secret safe-text"
    )

    assert "generic-secret" not in redacted
    assert "never-print-this" not in redacted
    assert "model-secret" not in redacted
    assert redacted.count("[REDACTED]") == 3
    assert "safe-text" in redacted


def test_log_follow_chunking_bounds_single_lines_and_preserves_redaction(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    point_root_ops_at_tmp(monkeypatch, tmp_path)
    write_runtime(root_ops.RUNTIME_DIR, env_secret="never-print-this")
    pending = bytearray()

    discarding, window_bytes, limited = root_ops._emit_stream_chunk(
        b"api_key=never-print-this " + b"x" * 5000,
        pending=pending,
        discarding_line=False,
        window_bytes=0,
        limited=False,
    )
    discarding, _window_bytes, _limited = root_ops._emit_stream_chunk(
        b"discarded-tail\nsafe-next-line\n",
        pending=pending,
        discarding_line=discarding,
        window_bytes=window_bytes,
        limited=limited,
    )

    output = capsys.readouterr().out
    assert discarding is False
    assert "never-print-this" not in output
    assert "discarded-tail" not in output
    assert "[line truncated]" in output
    assert "safe-next-line" in output
    assert len(output.encode("utf-8")) < 5000


def test_ops_assets_pin_ttyd_harden_systemd_and_keep_sudo_exact() -> None:
    version = (OPS / "ttyd-version.env").read_text(encoding="utf-8")
    gateway_service = (OPS / "systemd" / "zhice-ops.service").read_text(encoding="utf-8")
    terminal_service = (OPS / "systemd" / "zhice-ops-terminal.service").read_text(
        encoding="utf-8"
    )
    dashboard_service = (OPS / "systemd" / "zhice-ops-dashboard.service").read_text(
        encoding="utf-8"
    )
    caddy = (OPS / "config" / "Caddyfile").read_text(encoding="utf-8")
    sudoers = (OPS / "sudoers.d" / "zhice-ops").read_text(encoding="utf-8")
    installer = (OPS / "install.sh").read_text(encoding="utf-8")

    assert "TTYD_VERSION=1.7.7" in version
    assert "TTYD_SHA256=8a217c968aba172e0dbf3f34447218dc" in version
    assert "sha256sum --check --status" in installer
    assert "--interface lo" in terminal_service
    assert "--base-path /terminal" in terminal_service
    assert "--interface 127.0.0.1" not in terminal_service
    assert "IPAddressDeny=any" in terminal_service
    assert "IPAddressAllow=localhost" in terminal_service
    assert "--check-origin" in terminal_service
    assert "--max-clients 1" in terminal_service
    assert "/usr/local/bin/zhice-ops-shell" in terminal_service
    assert "User=zhice-operator" in terminal_service
    assert "NoNewPrivileges=false" in terminal_service
    assert "ReadWritePaths=/var/lib/zhice-ops /etc/zhice-agent/runtime" in terminal_service
    assert "/usr/bin/caddy run" in gateway_service
    assert "NoNewPrivileges=true" in gateway_service
    assert "http://:{$ZHICE_OPS_PORT}" in caddy
    assert "bind 127.0.0.1" in caddy
    assert "basicauth" not in caddy
    assert "forward_auth 127.0.0.1:{$ZHICE_OPS_DASHBOARD_PORT}" in caddy
    assert "uri /auth/check" in caddy
    assert 'header_up Authorization "Basic {$ZHICE_OPS_BASIC_AUTH}"' in caddy
    assert "ZHICE_OPS_BASIC_AUTH=" in installer
    assert "caddy hash-password" not in installer
    assert "route /terminal/*" in caddy
    assert "route /api/*" in caddy
    assert "zhice_ops_dashboard.py" in dashboard_service
    assert "/usr/bin/python3 -I /usr/local/libexec/zhice-ops/zhice_ops_root.py *" in sudoers
    assert "/bin/bash" not in sudoers
    assert "/usr/bin/docker" not in sudoers


def test_cloud_deploy_uses_fixed_container_and_read_only_host_config() -> None:
    deploy = (ROOT / "deploy" / "scripts" / "deploy.sh").read_text(encoding="utf-8")
    diagnose = (ROOT / "deploy" / "scripts" / "diagnose.sh").read_text(
        encoding="utf-8"
    )

    assert "CONTAINER_NAME=zhice-agent" in deploy
    assert "ZHICE_CONTAINER_NAME" not in deploy
    assert "RUNTIME_DIR=$RUNTIME_PARENT/runtime" in deploy
    assert deploy.count("readonly\"") == 4  # one validator mount plus three runtime mounts
    assert "refusing replacement" in deploy
    assert "docker cp" in deploy
    assert "config.yml schema" in deploy
    assert "models.json schema" in deploy
    assert "docker logs --tail" not in deploy
    assert "CONTAINER_NAME=zhice-agent" in diagnose
    assert "ZHICE_PUBLIC_HEALTH_URL" in diagnose
    assert "PUBLIC_HEALTH_URL=https://" not in diagnose
    assert "ZHICE_OPS_MODE=server_docker" in deploy
    assert "deployment.spec" in deploy
    assert "head -c 65536" in diagnose


def test_root_wrapper_has_only_fixed_paths_and_no_shell_execution() -> None:
    source = (OPS / "libexec" / "zhice_ops_root.py").read_text(encoding="utf-8")

    assert 'CONTAINER_NAME = "zhice-agent"' in source
    assert 'Path("/etc/zhice-agent/runtime")' in source
    assert "shell=True" not in source
    assert "os.system" not in source
    assert "eval(" not in source
    assert "exec(" not in source
    assert "subprocess.Popen" in source
    assert "MAX_LOG_LINES = 500" in source
    assert "MAX_CONFIG_BYTES = 262_144" in source
    assert "MAX_SCRIPT_OUTPUT_BYTES = 131_072" in source
    assert "MAX_STREAM_LINE_BYTES = 4_096" in source
    assert "capture_output=True" not in source
    assert "start_new_session=True" in source


def test_server_dashboard_parses_only_safe_fixed_status_fields() -> None:
    status = dashboard.parse_status_output(
        "name=zhice-agent exists=true status=running health=healthy "
        "exit_code=0 oom_killed=false restarts=2 image=registry/zhice@sha256:abc"
    )

    assert status["mode"] == "server_docker"
    assert status["target_type"] == "container"
    assert status["target_name"] == "zhice-agent"
    assert status["exists"] is True
    assert status["oom_killed"] is False
    assert status["restarts"] == 2


def test_server_ops_session_is_long_lived_signed_and_rotation_safe(monkeypatch) -> None:
    first_secret = "a" * 48
    monkeypatch.setenv("ZHICE_OPS_CREDENTIAL", f"owner:{first_secret}")

    token = dashboard.issue_session_token(now=1_000)

    assert dashboard.validate_session_token(token, now=1_001)
    assert dashboard.validate_session_token(
        token, now=1_000 + dashboard.SESSION_MAX_AGE_SECONDS - 1
    )
    assert not dashboard.validate_session_token(
        token, now=1_000 + dashboard.SESSION_MAX_AGE_SECONDS
    )
    assert not dashboard.validate_session_token(token + "tampered", now=1_001)

    monkeypatch.setenv("ZHICE_OPS_CREDENTIAL", f"owner:{'b' * 48}")
    assert not dashboard.validate_session_token(token, now=1_001)


@pytest.mark.parametrize(
    ("candidate", "expected"),
    [
        ("/", "/"),
        ("/terminal/", "/terminal/"),
        ("https://attacker.invalid", "/"),
        ("//attacker.invalid", "/"),
        ("/safe\r\nLocation: https://attacker.invalid", "/"),
        (None, "/"),
    ],
)
def test_server_ops_login_rejects_unsafe_redirects(candidate, expected) -> None:
    assert dashboard.safe_next_path(candidate) == expected


def test_server_ops_login_sets_secure_persistent_cookie_and_can_logout(
    monkeypatch,
) -> None:
    secret = "c" * 48
    monkeypatch.setenv("ZHICE_OPS_CREDENTIAL", f"owner:{secret}")
    server = dashboard.ThreadingHTTPServer(("127.0.0.1", 0), dashboard.DashboardHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    connection = http.client.HTTPConnection(
        "127.0.0.1", server.server_address[1], timeout=5
    )
    try:
        connection.request("GET", "/auth/login")
        response = connection.getresponse()
        assert response.status == 200
        assert "长期保持" in response.read().decode("utf-8")

        wrong_body = urlencode(
            {"username": "owner", "password": "wrong", "next": "/"}
        )
        connection.request(
            "POST",
            "/auth/login",
            body=wrong_body,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        response = connection.getresponse()
        assert response.status == 401
        assert response.getheader("Set-Cookie") is None
        response.read()

        login_body = urlencode(
            {"username": "owner", "password": secret, "next": "/terminal/"}
        )
        connection.request(
            "POST",
            "/auth/login",
            body=login_body,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        response = connection.getresponse()
        assert response.status == 303
        assert response.getheader("Location") == "/terminal/"
        set_cookie = response.getheader("Set-Cookie")
        assert set_cookie is not None
        assert f"Max-Age={dashboard.SESSION_MAX_AGE_SECONDS}" in set_cookie
        assert "Secure" in set_cookie
        assert "HttpOnly" in set_cookie
        assert "SameSite=Strict" in set_cookie
        cookie = set_cookie.split(";", 1)[0]
        response.read()

        basic = base64.b64encode(f"owner:{secret}".encode()).decode()
        connection.request(
            "GET",
            "/auth/check",
            headers={
                "Authorization": f"Basic {basic}",
                "X-Forwarded-Uri": "/terminal/",
            },
        )
        response = connection.getresponse()
        assert response.status == 303
        assert response.getheader("Location") == "/terminal/"
        migrated_cookie = response.getheader("Set-Cookie")
        assert migrated_cookie is not None
        assert dashboard.SESSION_COOKIE_NAME in migrated_cookie
        assert "HttpOnly" in migrated_cookie
        response.read()

        connection.request("GET", "/auth/check", headers={"Cookie": cookie})
        response = connection.getresponse()
        assert response.status == 204
        response.read()

        connection.request("GET", "/auth/logout", headers={"Cookie": cookie})
        response = connection.getresponse()
        assert response.status == 303
        assert "Max-Age=0" in response.getheader("Set-Cookie")
        response.read()
    finally:
        connection.close()
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_server_dashboard_exposes_only_fixed_monitor_routes(monkeypatch) -> None:
    calls: list[tuple[str, ...]] = []

    def operation(args, *, timeout_seconds=90):
        calls.append(args)
        if args == ("status",):
            return "name=zhice-agent status=running health=healthy exit_code=0"
        if args[0] == "logs":
            return "INFO: fixed log\n"
        if args == ("diagnose",):
            return "docker=available container=running\n"
        if args == ("restart",):
            return "Restarted zhice-agent\n"
        raise AssertionError(args)

    monkeypatch.setattr(dashboard, "run_operation", operation)
    server = dashboard.ThreadingHTTPServer(("127.0.0.1", 0), dashboard.DashboardHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    connection = http.client.HTTPConnection("127.0.0.1", server.server_address[1], timeout=5)
    try:
        connection.request("GET", "/api/meta")
        meta = json.loads(connection.getresponse().read())
        assert meta["terminal_kind"] == "ttyd"
        assert meta["terminal_url"] == "/terminal/"
        assert meta["auth_logout_url"] == "/auth/logout"

        connection.request("GET", "/api/logs?lines=20")
        logs = json.loads(connection.getresponse().read())
        assert logs == {"logs": "INFO: fixed log\n"}

        body = json.dumps({"confirm": "restart"}).encode()
        connection.request(
            "POST",
            "/api/restart",
            body=body,
            headers={"Content-Type": "application/json", "Content-Length": str(len(body))},
        )
        assert connection.getresponse().status == 200

        connection.request("GET", "/api/config")
        assert connection.getresponse().status == 404
    finally:
        connection.close()
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)

    assert ("logs", "20") in calls
    assert ("restart",) in calls
    assert all(call[0] in {"status", "logs", "diagnose", "restart"} for call in calls)
