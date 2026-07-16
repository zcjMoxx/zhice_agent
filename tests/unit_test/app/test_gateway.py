from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from agent.app.auth import AuthService
from agent.app.gateway import create_app, format_gateway_check, gateway_status, run_gateway
from agent.app.logging import GatewayLoggingResult, GatewayLogOptions
from agent.auth.store import SQLiteAuthStore
from agent.cli import main
from agent.config import AppConfig

REPOSITORY_STATIC_DIR = Path(__file__).resolve().parents[3] / "web" / "static"


def test_gateway_serves_static_index(tmp_path):
    static_dir = tmp_path / "static"
    static_dir.mkdir()
    static_dir.joinpath("index.html").write_text("<html><body>Chat UI</body></html>", encoding="utf-8")
    client = TestClient(create_app(config=_config(tmp_path), runtime=_FakeRuntime(), static_dir=static_dir))

    response = client.get("/")

    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert "Chat UI" in response.text


def test_gateway_serves_admin_route_from_static_application(tmp_path):
    static_dir = tmp_path / "static"
    static_dir.mkdir()
    static_dir.joinpath("index.html").write_text(
        "<html><body>Administration UI</body></html>", encoding="utf-8"
    )
    client = TestClient(create_app(config=_config(tmp_path), runtime=_FakeRuntime(), static_dir=static_dir))

    response = client.get("/admin")

    assert response.status_code == 200
    assert "Administration UI" in response.text


def test_web_admin_is_a_route_and_diagnostics_is_tool_only():
    html = REPOSITORY_STATIC_DIR.joinpath("index.html").read_text(encoding="utf-8")
    javascript = REPOSITORY_STATIC_DIR.joinpath("app.js").read_text(encoding="utf-8")

    assert 'id="adminPage"' in html
    assert 'id="managementDialog"' not in html
    assert "window.location.assign(\"/admin\")" in javascript
    assert "const isAdminRoute" in javascript
    assert "function resetAccountScopedState" in javascript
    assert "Recent diagnostics" not in html
    assert "diagnosticsDialog" not in javascript
    assert "function refreshAuthorizationAfterFailure" in javascript
    assert "HTTP ${status} (${code})" in javascript


def test_password_inputs_have_persistent_visibility_controls():
    html = REPOSITORY_STATIC_DIR.joinpath("index.html").read_text(encoding="utf-8")
    css = REPOSITORY_STATIC_DIR.joinpath("styles.css").read_text(encoding="utf-8")
    javascript = REPOSITORY_STATIC_DIR.joinpath("app.js").read_text(encoding="utf-8")
    password_input_ids = [
        "loginPassword",
        "currentPassword",
        "newPassword",
        "confirmPassword",
        "bootstrapPassword",
        "bootstrapSetupToken",
        "registerPassword",
        "registerPasswordConfirm",
    ]

    for input_id in password_input_ids:
        assert f'id="{input_id}" type="password"' in html
        assert f'data-password-toggle="{input_id}"' in html
        assert f'aria-controls="{input_id}"' in html
    assert html.count('tabindex="-1"') >= len(password_input_ids)
    assert "function initializePasswordToggles" in javascript
    assert "function resetPasswordVisibility" in javascript
    assert 'data-password-toggle="adminCreatePassword"' in javascript
    assert 'aria-controls="adminCreatePassword"' in javascript
    assert 'tabindex="-1"' in javascript
    assert ".password-toggle" in css
    assert ".password-input-wrap" in css
    assert "::-ms-reveal" in css
    assert "::-ms-clear" in css
    assert "20260716-background-memory-trace" in html
    assert 'id="confirmationEdit"' not in html
    assert 'id="confirmationContent"' not in html
    assert "editMemoryConfirmation" not in javascript


def test_web_brand_uses_selected_image_asset_and_a_distinct_user_icon():
    html = REPOSITORY_STATIC_DIR.joinpath("index.html").read_text(encoding="utf-8")
    css = REPOSITORY_STATIC_DIR.joinpath("styles.css").read_text(encoding="utf-8")
    javascript = REPOSITORY_STATIC_DIR.joinpath("app.js").read_text(encoding="utf-8")

    assert html.count('data-brand-logo="selected-a"') == 3
    assert html.count('src="/static/zhice-logo-a.png?v=20260711-clean"') == 3
    assert 'class="avatar user-avatar" id="userAvatar"' in html
    assert 'id="userAvatarPrimary"' in html
    assert 'id="userAvatarSecondary"' in html
    assert ".brand-mark img" in css
    assert ".user-avatar" in css
    assert "border-radius: 50%;" in css
    assert ".avatar-letter-primary" in css
    assert ".avatar-letter-secondary" in css
    assert "function getAvatarInitials" in javascript
    assert "Array.from" in javascript
    assert "state.currentUser?.username" in javascript
    assert ".logo-avatar" not in css
    assert "20260716-background-memory-trace" in html


def test_owner_setup_page_is_only_served_while_secret_is_configured_and_owner_missing(tmp_path):
    static_dir = tmp_path / "static"
    static_dir.mkdir()
    static_dir.joinpath("index.html").write_text(
        "<html><body>Owner Setup UI</body></html>", encoding="utf-8"
    )
    store = SQLiteAuthStore(tmp_path / "state" / "auth.sqlite3")
    store.initialize_schema()

    unavailable = TestClient(
        create_app(
            config=_config(tmp_path),
            runtime=_FakeRuntime(AuthService(store)),
            static_dir=static_dir,
        )
    )
    available = TestClient(
        create_app(
            config=_config(tmp_path),
            runtime=_FakeRuntime(AuthService(store, setup_token="setup-secret")),
            static_dir=static_dir,
        )
    )

    assert unavailable.get("/_setup").status_code == 404
    setup_page = available.get("/_setup")
    assert setup_page.status_code == 200
    assert "Owner Setup UI" in setup_page.text

    store.initialize_owner("owner", "Owner", "password-123")

    assert available.get("/_setup").status_code == 404


def test_gateway_serves_favicon(tmp_path):
    static_dir = tmp_path / "static"
    static_dir.mkdir()
    static_dir.joinpath("zhice-logo-a.png").write_bytes(b"png-logo")
    client = TestClient(
        create_app(config=_config(tmp_path), runtime=_FakeRuntime(), static_dir=static_dir)
    )

    response = client.get("/favicon.ico")

    assert response.status_code == 200
    assert "image/png" in response.headers["content-type"]
    assert response.content == b"png-logo"


def test_gateway_absorbs_chrome_devtools_workspace_probe(tmp_path):
    client = TestClient(create_app(config=_config(tmp_path), runtime=_FakeRuntime(), static_dir=tmp_path))

    response = client.get("/.well-known/appspecific/com.chrome.devtools.json")

    assert response.status_code == 204
    assert response.content == b""


def test_gateway_health_returns_workspace_and_model(tmp_path):
    config = _config(tmp_path)
    client = TestClient(create_app(config=config, runtime=_FakeRuntime(), static_dir=tmp_path))

    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "name": "ZhiCe-Agent",
        "current_model": "default/model-a",
        "auth_required": "false",
        "auth_initialized": "false",
        "owner_initialized": "false",
    }


def test_gateway_status_handles_missing_runtime(tmp_path):
    config = _config(tmp_path)

    assert gateway_status(config) == {
        "status": "ok",
        "name": "ZhiCe-Agent",
        "current_model": "unavailable",
        "auth_required": "false",
        "auth_initialized": "false",
        "owner_initialized": "false",
    }


def test_gateway_check_formats_without_starting_server(tmp_path):
    config = _config(tmp_path)

    text = format_gateway_check(config, host="127.0.0.1", port=19000)

    assert "ZhiCe-Agent gateway check ok" in text
    assert "http://127.0.0.1:19000" in text
    assert str(tmp_path) in text


def test_run_gateway_prints_trace_log_after_http_logs(tmp_path, capsys, monkeypatch):
    config = _config(tmp_path)

    monkeypatch.setattr("agent.app.gateway.build_web_runtime", lambda _config: _FakeRuntime())
    monkeypatch.setattr(
        "agent.app.gateway.configure_gateway_logging",
        lambda _options, *, logs_dir: GatewayLoggingResult(
            trace_path=logs_dir / "2026-07-08" / "trace.log"
        ),
    )
    monkeypatch.setattr("agent.app.gateway.uvicorn.run", lambda *_args, **_kwargs: None)

    run_gateway(config, log_options=GatewayLogOptions())

    lines = capsys.readouterr().out.splitlines()
    agent_index = _line_index(lines, "agent-log:")
    http_index = _line_index(lines, "http-access-log:")
    trace_index = _line_index(lines, "trace-log:")
    assert agent_index < http_index < trace_index


def test_cli_gateway_check_does_not_start_gateway(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("ZHICE_AGENT_WORKSPACE", str(tmp_path))

    def fail_if_started(*_args, **_kwargs):
        raise AssertionError("gateway should not start in --check mode")

    monkeypatch.setattr("agent.cli.run_gateway", fail_if_started)

    result = main(["gateway", "--check"])

    output = capsys.readouterr().out
    assert result == 0
    assert "ZhiCe-Agent gateway check ok" in output


def test_cli_gateway_passes_split_log_options(tmp_path, capsys, monkeypatch):
    monkeypatch.setenv("ZHICE_AGENT_WORKSPACE", str(tmp_path))
    captured = {}

    def capture_gateway(config, **kwargs):
        captured.update(kwargs)

    monkeypatch.setattr("agent.cli.run_gateway", capture_gateway)

    result = main(
        [
            "gateway",
            "--agent-log",
            "off",
            "--agent-log-level",
            "debug",
            "--trace-log",
            "off",
            "--http-access-log",
            "off",
            "--http-server-log",
            "off",
            "--http-server-log-level",
            "warning",
        ]
    )

    capsys.readouterr()
    assert result == 0
    assert captured["host"] == "127.0.0.1"
    assert captured["port"] == 10086
    assert captured["log_options"] == GatewayLogOptions(
        agent_log=False,
        agent_log_level="debug",
        trace_log=False,
        http_access_log=False,
        http_server_log=False,
        http_server_log_level="warning",
    )


def _config(tmp_path: Path) -> AppConfig:
    return AppConfig(
        workspace=tmp_path,
        config_dir=tmp_path / "config",
        prompts_dir=tmp_path / "prompts",
        contexts_dir=tmp_path / "contexts",
        sessions_dir=tmp_path / "contexts" / "sessions",
        extends_dir=tmp_path / "extends",
        logs_dir=tmp_path / "logs",
    )


def _line_index(lines: list[str], prefix: str) -> int:
    return next(index for index, line in enumerate(lines) if line.startswith(prefix))


class _FakeRuntime:
    def __init__(self, auth=None):
        self.auth = auth

    def current_model_label(self) -> str:
        return "default/model-a"
