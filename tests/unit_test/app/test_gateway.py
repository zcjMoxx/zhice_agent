from __future__ import annotations

import asyncio
import io
import json
import logging
from pathlib import Path
from types import SimpleNamespace

import uvicorn
from fastapi.testclient import TestClient

from agent.app.auth import AuthService
from agent.app.gateway import (
    _default_static_dir,
    _OrderedGatewayServer,
    _resolve_channel_startup_status,
    create_app,
    format_gateway_check,
    gateway_status,
    run_gateway,
)
from agent.app.logging import (
    GatewayLoggingResult,
    GatewayLogOptions,
    configure_gateway_logging,
)
from agent.auth.store import SQLiteAuthStore
from agent.cli import main
from agent.config import AppConfig
from agent.logging_utils import log_event
from agent.protocols.capability import CapabilityStatus

PACKAGED_STATIC_DIR = Path(__file__).resolve().parents[3] / "agent" / "web" / "static"


def test_gateway_serves_static_index(tmp_path):
    static_dir = tmp_path / "static"
    static_dir.mkdir()
    static_dir.joinpath("index.html").write_text("<html><body>Chat UI</body></html>", encoding="utf-8")
    client = TestClient(create_app(config=_config(tmp_path), runtime=_FakeRuntime(), static_dir=static_dir))

    response = client.get("/")

    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert "Chat UI" in response.text


def test_gateway_serves_dedicated_qq_binding_spa_route(tmp_path):
    static_dir = tmp_path / "static"
    static_dir.mkdir()
    static_dir.joinpath("index.html").write_text(
        "<html><body>QQ Binding UI</body></html>", encoding="utf-8"
    )
    client = TestClient(
        create_app(config=_config(tmp_path), runtime=_FakeRuntime(), static_dir=static_dir)
    )

    response = client.get("/bind/qq?token=opaque")

    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert "QQ Binding UI" in response.text


def test_gateway_logs_web_and_external_channel_lifecycle(tmp_path, caplog):
    static_dir = tmp_path / "static"
    static_dir.mkdir()
    static_dir.joinpath("index.html").write_text("<html></html>", encoding="utf-8")
    runtime = _LifecycleRuntime(
        {
            "channel.qq": CapabilityStatus(
                "channel.qq", "available", "CHANNEL_QQ_AVAILABLE"
            ),
            "channel.weixin": CapabilityStatus(
                "channel.weixin", "available", "CHANNEL_WEIXIN_AVAILABLE"
            ),
        }
    )

    with caplog.at_level(logging.INFO, logger="zcagent.gateway"):
        with TestClient(create_app(config=_config(tmp_path), runtime=runtime, static_dir=static_dir)):
            pass

    lifecycle = [
        (getattr(record, "event", ""), getattr(record, "fields", {}))
        for record in caplog.records
        if getattr(record, "event", "") == "channel.stop"
    ]
    assert [(event, fields["channel"], fields["state"]) for event, fields in lifecycle] == [
        ("channel.stop", "weixin", "stopped"),
        ("channel.stop", "qq", "stopped"),
        ("channel.stop", "web", "stopped"),
    ]
    assert runtime.manager.started is True
    assert runtime.manager.stopped is True
    summary = next(
        record for record in caplog.records if getattr(record, "event", "") == "channel.enabled"
    )
    assert summary.fields["channels"] == ["web", "qq", "weixin"]
    ready = [
        record.fields
        for record in caplog.records
        if getattr(record, "event", "") == "channel.ready"
    ]
    assert [fields["channel"] for fields in ready] == ["qq", "weixin"]
    assert ready[0]["mode"] == "shared"
    assert ready[1] == {
        "channel": "weixin",
        "mode": "per_user",
        "accounts": 3,
        "active": 2,
        "reconnect_required": 1,
    }


def test_gateway_channel_summary_and_ready_events_follow_config_order(tmp_path, caplog):
    static_dir = tmp_path / "static"
    static_dir.mkdir()
    static_dir.joinpath("index.html").write_text("<html></html>", encoding="utf-8")
    runtime = _LifecycleRuntime(
        {
            "channel.qq": CapabilityStatus("channel.qq", "available", "CHANNEL_QQ_AVAILABLE"),
            "channel.weixin": CapabilityStatus(
                "channel.weixin", "available", "CHANNEL_WEIXIN_AVAILABLE"
            ),
        },
        order=("weixin", "qq"),
    )

    with caplog.at_level(logging.INFO, logger="zcagent.gateway"):
        with TestClient(create_app(config=_config(tmp_path), runtime=runtime, static_dir=static_dir)):
            pass

    summary = next(
        record for record in caplog.records if getattr(record, "event", "") == "channel.enabled"
    )
    ready = [
        record.fields["channel"]
        for record in caplog.records
        if getattr(record, "event", "") == "channel.ready"
    ]
    assert summary.fields["channels"] == ["web", "weixin", "qq"]
    assert ready == ["weixin", "qq"]


def test_gateway_logs_disabled_and_failed_channels_without_blocking_web(tmp_path, caplog):
    static_dir = tmp_path / "static"
    static_dir.mkdir()
    static_dir.joinpath("index.html").write_text("<html></html>", encoding="utf-8")
    runtime = _LifecycleRuntime(
        {
            "channel.qq": CapabilityStatus(
                "channel.qq", "disabled", "CHANNEL_QQ_DISABLED"
            ),
            "channel.weixin": CapabilityStatus(
                "channel.weixin",
                "unavailable",
                "CHANNEL_START_FAILED",
                details={"error_type": "WeixinSidecarError"},
            ),
        },
        adapter_keys=(),
    )

    with caplog.at_level(logging.INFO, logger="zcagent.gateway"):
        with TestClient(create_app(config=_config(tmp_path), runtime=runtime, static_dir=static_dir)) as client:
            assert client.get("/health").status_code == 200

    records = {
        getattr(record, "fields", {}).get("channel"): record
        for record in caplog.records
        if getattr(record, "event", "") in {"channel.skip", "channel.start_failed"}
    }
    assert getattr(records["qq"], "event") == "channel.skip"
    assert getattr(records["weixin"], "event") == "channel.start_failed"
    assert records["weixin"].levelno == logging.WARNING
    assert records["weixin"].fields["code"] == "CHANNEL_START_FAILED"
    assert records["weixin"].fields["error_type"] == "WeixinSidecarError"


def test_qq_startup_readiness_timeout_is_bounded(monkeypatch):
    adapter = SimpleNamespace(
        status=lambda: CapabilityStatus(
            "qq.main", "degraded", "CHANNEL_QQ_DEGRADED"
        )
    )
    runtime = SimpleNamespace(
        channel_manager=SimpleNamespace(adapters={"qq.main": adapter})
    )
    ticks = iter((0.0, 11.0))
    monkeypatch.setattr("agent.app.gateway.time.monotonic", lambda: next(ticks))

    status = _resolve_channel_startup_status(
        runtime,
        {"channel.qq": CapabilityStatus("channel.qq", "available", "CHANNEL_QQ_AVAILABLE")},
        "qq",
    )

    assert status.state == "degraded"


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


def test_default_static_dir_is_the_packaged_vue_build():
    assert _default_static_dir() == PACKAGED_STATIC_DIR.resolve()
    assert _default_static_dir().joinpath("index.html").is_file()


def test_packaged_vue_entry_serves_home_admin_and_static_assets(tmp_path):
    client = TestClient(
        create_app(config=_config(tmp_path), runtime=_FakeRuntime(), static_dir=PACKAGED_STATIC_DIR)
    )
    home = client.get("/")
    admin = client.get("/admin")
    logo = client.get("/static/zhice-logo-a.png")

    assert home.status_code == 200
    assert admin.status_code == 200
    assert '<div id="app"></div>' in home.text
    assert home.text == admin.text
    assert 'type="module"' in home.text
    assert logo.status_code == 200


def test_vue_source_uses_single_initials_node_and_part16_surfaces():
    root = Path(__file__).resolve().parents[3] / "web" / "frontend" / "src"
    avatar = root.joinpath("components/UserAvatar.vue").read_text(encoding="utf-8")
    settings = root.joinpath("components/SettingsCenter.vue").read_text(encoding="utf-8")
    admin = root.joinpath("layouts/AdminLayout.vue").read_text(encoding="utf-8")

    assert '<span class="user-avatar" aria-hidden="true">{{ initials }}</span>' in avatar
    assert all(name in settings for name in ("常规", "个性化", "个人资料", "账号与安全", "渠道连接"))
    assert all(
        name in admin
        for name in (
            "概览",
            "账号管理",
            "角色与权限",
            "Skills",
            "运行诊断",
            "服务器运维",
            "高级设置",
        )
    )
    assert "近期运行记录" in admin
    assert "普通运行错误请到运行诊断查看" in admin


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
        "capabilities": {
            "subagent": {
                "name": "subagent",
                "state": "available",
                "code": "SUBAGENT_AVAILABLE",
                "message": "subagent is available.",
                "hint": "",
                "details": {},
            }
        },
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
        "capabilities": {
            "subagent": {
                "name": "subagent",
                "state": "unavailable",
                "code": "SUBAGENT_UNAVAILABLE",
                "message": "subagent is temporarily unavailable.",
                "hint": "Contact an administrator.",
                "details": {},
            }
        },
    }


def test_gateway_health_uses_generic_capability_provider(tmp_path):
    runtime = _FakeRuntime()
    runtime.capability_statuses = lambda: {
        "subagent": runtime.subagent_status,
        "mcp": CapabilityStatus(
            name="mcp",
            state="degraded",
            code="MCP_PARTIAL",
            message="One MCP server is unavailable.",
            hint="Check config/mcp.json.",
        ),
    }

    payload = gateway_status(_config(tmp_path), runtime=runtime)

    assert payload["status"] == "ok"
    assert payload["capabilities"]["mcp"]["state"] == "degraded"
    assert payload["capabilities"]["mcp"]["code"] == "MCP_DEGRADED"
    assert payload["capabilities"]["mcp"]["message"] == "mcp is temporarily limited."
    assert payload["capabilities"]["mcp"]["hint"] == "Contact an administrator."
    assert "mcp.json" not in json.dumps(payload["capabilities"]["mcp"])


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
            trace_path=logs_dir / "log-2026-07-08.jsonl"
        ),
    )
    monkeypatch.setattr("agent.app.gateway._OrderedGatewayServer.run", lambda _self: None)

    run_gateway(config, log_options=GatewayLogOptions())

    lines = capsys.readouterr().out.splitlines()
    agent_index = _line_index(lines, "agent-log:")
    http_index = _line_index(lines, "http-access-log:")
    trace_index = _line_index(lines, "trace-log:")
    assert agent_index < http_index < trace_index


def test_run_gateway_forwards_supervisor_color_override_to_uvicorn(tmp_path, monkeypatch):
    config = _config(tmp_path)
    captured = {}

    monkeypatch.setenv("ZHICE_FORCE_TERMINAL_COLOR", "1")
    monkeypatch.setattr("agent.app.gateway.build_web_runtime", lambda _config: _FakeRuntime())
    monkeypatch.setattr(
        "agent.app.gateway.configure_gateway_logging",
        lambda _options, *, logs_dir: GatewayLoggingResult(
            trace_path=logs_dir / "log-2026-08-09.jsonl"
        ),
    )

    def capture(server):
        captured["use_colors"] = server.config.use_colors

    monkeypatch.setattr("agent.app.gateway._OrderedGatewayServer.run", capture)

    run_gateway(config, log_options=GatewayLogOptions())

    assert captured["use_colors"] is True


def test_run_gateway_swallows_keyboard_interrupt_during_server_shutdown(tmp_path, monkeypatch):
    config = _config(tmp_path)

    monkeypatch.setattr("agent.app.gateway.build_web_runtime", lambda _config: _FakeRuntime())
    monkeypatch.setattr(
        "agent.app.gateway.configure_gateway_logging",
        lambda _options, *, logs_dir: GatewayLoggingResult(
            trace_path=logs_dir / "log-2026-07-08.jsonl"
        ),
    )

    def interrupt(_self):
        raise KeyboardInterrupt

    monkeypatch.setattr("agent.app.gateway._OrderedGatewayServer.run", interrupt)

    run_gateway(config, log_options=GatewayLogOptions())


def test_ordered_gateway_server_groups_channel_logs_outside_uvicorn_status(
    tmp_path, monkeypatch
):
    stream = io.StringIO()
    configure_gateway_logging(
        GatewayLogOptions(trace_log=False),
        logs_dir=tmp_path / "logs",
        terminal_stream=stream,
    )
    server_logger = logging.getLogger("uvicorn.error")
    server_handler = logging.StreamHandler(stream)
    server_handler.setFormatter(logging.Formatter("INFO:     %(message)s"))
    original_handlers = list(server_logger.handlers)
    original_propagate = server_logger.propagate
    server_logger.handlers = [server_handler]
    server_logger.propagate = False

    async def fake_startup(_server, sockets=None):
        del sockets
        log_event(
            logging.getLogger("zcagent.gateway"),
            logging.INFO,
            "channel.start",
            channel="web",
            state="available",
        )
        server_logger.info("Application startup complete.")
        server_logger.info("Uvicorn running on http://127.0.0.1:10086")

    async def fake_shutdown(_server, sockets=None):
        del sockets
        server_logger.info("Shutting down")
        server_logger.info("Waiting for application shutdown.")
        log_event(
            logging.getLogger("zcagent.gateway"),
            logging.INFO,
            "channel.stop",
            channel="web",
            state="stopped",
        )
        server_logger.info("Application shutdown complete.")

    monkeypatch.setattr(uvicorn.Server, "startup", fake_startup)
    monkeypatch.setattr(uvicorn.Server, "shutdown", fake_shutdown)
    server = _OrderedGatewayServer(uvicorn.Config(lambda _scope, _receive, _send: None, log_config=None))
    try:
        asyncio.run(server.startup())
        asyncio.run(server.shutdown())
    finally:
        server_logger.handlers = original_handlers
        server_logger.propagate = original_propagate

    lines = stream.getvalue().splitlines()
    assert [line.startswith("INFO:     [") for line in lines] == [
        False,
        False,
        True,
        True,
        False,
        False,
        False,
    ]
    assert "Application startup complete." in lines[0]
    assert "Uvicorn running" in lines[1]
    assert lines[2] == "INFO:     [web] start | state=available"
    assert lines[3] == "INFO:     [web] channel stopped | state=stopped"
    assert "Shutting down" in lines[4]
    assert "Waiting for application shutdown." in lines[5]
    assert "Application shutdown complete." in lines[6]


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
    monkeypatch.setenv("ZHICE_OPS_MODE", "local_docker")
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

    output = capsys.readouterr().out
    assert result == 0
    assert "skills sync skipped" not in output
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
        self.subagent_status = CapabilityStatus(
            name="subagent",
            state="available",
            code="SUBAGENT_AVAILABLE",
            message="Subagent runtime is available.",
        )

    def current_model_label(self) -> str:
        return "default/model-a"


class _LifecycleManager:
    def __init__(self, adapter_keys):
        self.adapters = {key: object() for key in adapter_keys}
        self.started = False
        self.stopped = False

    def start(self):
        self.started = True

    def stop(self):
        self.stopped = True


class _LifecycleRuntime(_FakeRuntime):
    def __init__(
        self,
        statuses,
        adapter_keys=("channel.qq", "channel.weixin"),
        order=("qq", "weixin"),
    ):
        super().__init__()
        self._statuses = statuses
        self.manager = _LifecycleManager(adapter_keys)
        self.channel_manager = self.manager
        self.channel_config = SimpleNamespace(order=order)
        self.channel_identity = SimpleNamespace(
            store=SimpleNamespace(
                channel_account_status_counts=lambda _channel: {
                    "active": 2,
                    "reconnect_required": 1,
                }
            )
        )

    def capability_statuses(self):
        return dict(self._statuses)

    def shutdown(self):
        self.manager.stop()
