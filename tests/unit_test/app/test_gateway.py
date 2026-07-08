from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from agent.app.gateway import create_app, format_gateway_check, gateway_status, run_gateway
from agent.app.logging import GatewayLoggingResult, GatewayLogOptions
from agent.cli import main
from agent.config import AppConfig


def test_gateway_serves_static_index(tmp_path):
    static_dir = tmp_path / "static"
    static_dir.mkdir()
    static_dir.joinpath("index.html").write_text("<html><body>Chat UI</body></html>", encoding="utf-8")
    client = TestClient(create_app(config=_config(tmp_path), runtime=_FakeRuntime(), static_dir=static_dir))

    response = client.get("/")

    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert "Chat UI" in response.text


def test_gateway_serves_favicon(tmp_path):
    client = TestClient(create_app(config=_config(tmp_path), runtime=_FakeRuntime(), static_dir=tmp_path))

    response = client.get("/favicon.ico")

    assert response.status_code == 200
    assert "image/svg+xml" in response.headers["content-type"]
    assert "ZC" in response.text


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
        "workspace": str(config.workspace),
        "config_dir": str(config.config_dir),
        "sessions_dir": str(config.sessions_dir),
        "current_model": "default/model-a",
    }


def test_gateway_status_handles_missing_runtime(tmp_path):
    config = _config(tmp_path)

    assert gateway_status(config) == {
        "status": "ok",
        "name": "ZhiCe-Agent",
        "workspace": str(config.workspace),
        "config_dir": str(config.config_dir),
        "sessions_dir": str(config.sessions_dir),
        "current_model": "unavailable",
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
    def current_model_label(self) -> str:
        return "default/model-a"
