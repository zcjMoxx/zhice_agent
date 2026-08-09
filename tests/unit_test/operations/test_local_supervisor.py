from __future__ import annotations

import io
import sys
import time
from pathlib import Path

import pytest

from agent.operations.local_supervisor import (
    LocalOpsSupervisor,
    _local_ops_html,
    _ops_startup_line,
    _parse_local_command,
)


class _Process:
    pid = 4321
    stdout = None

    def poll(self):
        return None


class _Tree:
    def __init__(self):
        self.process = _Process()
        self.terminated = False

    def terminate(self, *, grace_seconds: float):
        assert grace_seconds == 3
        self.terminated = True


def test_supervisor_restart_recycles_only_owned_child(tmp_path: Path, monkeypatch):
    trees: list[_Tree] = []

    def spawn(argv, **kwargs):
        assert argv == ["python", "gateway-child"]
        assert kwargs["stdout"] is not None
        assert kwargs["stderr"] is not None
        assert kwargs["env"]["PYTHONUNBUFFERED"] == "1"
        assert kwargs["env"]["PYTHONIOENCODING"] == "utf-8"
        assert kwargs["env"]["PYTHONUTF8"] == "1"
        assert kwargs["env"]["ZHICE_FORCE_TERMINAL_COLOR"] == "1"
        tree = _Tree()
        trees.append(tree)
        return tree

    monkeypatch.setattr("agent.operations.local_supervisor.ManagedProcessTree.spawn", spawn)
    monkeypatch.setattr("agent.operations.local_supervisor._terminal_supports_color", lambda: True)
    supervisor = LocalOpsSupervisor(
        state_dir=tmp_path / "state",
        logs_dir=tmp_path / "logs",
        child_argv=["python", "gateway-child"],
    )
    supervisor._start_child()

    status = supervisor.restart()

    assert len(trees) == 2
    assert trees[0].terminated is True
    assert status["target_name"] == "zcagent-gateway"
    assert status["pid"] == 4321


def test_supervisor_binds_only_bounded_loopback_port(tmp_path: Path):
    supervisor = LocalOpsSupervisor(
        state_dir=tmp_path / "state",
        logs_dir=tmp_path / "logs",
        child_argv=["unused"],
    )

    server = supervisor._bind_server()
    try:
        host, port = server.server_address
        assert host == "127.0.0.1"
        assert 17681 <= port <= 17690
    finally:
        server.server_close()


def test_local_ops_page_follows_logs_and_pauses_when_reader_scrolls_up():
    page = _local_ops_html()

    assert "监控面板" in page
    assert "运维终端" in page
    assert "暂停跟随" in page
    assert "继续跟随" in page
    assert "setInterval(loadLogs,1000)" in page
    assert "box.scrollTop=box.scrollHeight" in page
    assert "overflow-wrap:anywhere" in page
    assert "Refresh now" not in page
    assert ".action:hover" in page
    assert ".action:active" in page
    assert ".action:focus-visible" in page
    assert ".action:disabled" in page
    assert "color-scheme:dark" in page
    assert "scrollbar-width:thin" in page
    assert "*::-webkit-scrollbar-thumb" in page
    assert "*::-webkit-scrollbar-track" in page
    assert "classList.toggle('paused',!state.following)" in page
    assert "setAttribute('aria-pressed',String(!state.following))" in page
    assert ".log-time,.log-level-info,.log-component-ws{color:#3bd18b}" in page
    assert ".log-level-warning,.log-action-tool{color:#eab308}" in page
    assert ".log-level-debug,.log-action-agent{color:#35cde2}" in page
    assert ".log-action-web{color:#d986ff}" in page
    assert ".log-action-gateway,.log-component-gateway{color:#71a7ff}" in page
    assert "document.createElement('span')" in page
    assert "box.replaceChildren(fragment)" in page
    assert "innerHTML" not in page


def test_local_ops_restart_button_reports_progress_and_result():
    page = _local_ops_html()

    assert 'id="restart-button"' in page
    assert "button.disabled=true" in page
    assert "button.textContent='重启中…'" in page
    assert "button.textContent='重启完成'" in page
    assert "button.textContent='重启失败'" in page
    assert "button.disabled=false" in page


@pytest.mark.parametrize(
    "command",
    ["status", "logs", "logs 500", "logs-follow", "diagnose", "restart", "help", "exit"],
)
def test_local_ops_command_parser_accepts_only_common_fixed_commands(command: str):
    assert _parse_local_command(command)


@pytest.mark.parametrize(
    "command",
    [
        "bash",
        "sudo -i",
        "docker ps",
        "config view .env",
        "status extra",
        "logs 0",
        "logs 501",
        "logs --follow",
        "restart now",
        "status; whoami",
    ],
)
def test_local_ops_command_parser_rejects_shell_config_and_extra_arguments(command: str):
    with pytest.raises(ValueError):
        _parse_local_command(command)


def test_ops_startup_line_reuses_bold_title_and_cyan_address(monkeypatch):
    monkeypatch.setattr("agent.console._COLOR_ENABLED", True)

    line = _ops_startup_line("http://127.0.0.1:17681")

    assert line == (
        "\x1b[1mZhiCe-Agent Ops\x1b[0m listening on "
        "\x1b[36mhttp://127.0.0.1:17681\x1b[0m (local process)"
    )


def test_supervisor_tees_human_gateway_output_and_strips_ansi_for_browser(
    tmp_path: Path,
    capsys,
):
    supervisor = LocalOpsSupervisor(
        state_dir=tmp_path / "state",
        logs_dir=tmp_path / "logs",
        child_argv=["unused"],
    )

    class Process:
        stdout = io.BytesIO(
            b"\x1b[32mINFO:\x1b[0m     Started server process [123]\n"
            b"[2026-08-09 17:54:26] | INFO | agent.turn.done | duration=442ms\n"
            + "[2026-08-09 22:24:25] | INFO | agent.turn.done | output_preview=你好，我是智策助手\n".encode()
        )

    supervisor._read_child_output(Process())

    assert supervisor.logs(10) == [
        "INFO:     Started server process [123]",
        "[2026-08-09 17:54:26] | INFO | agent.turn.done | duration=442ms",
        "[2026-08-09 22:24:25] | INFO | agent.turn.done | output_preview=你好，我是智策助手",
    ]
    terminal = capsys.readouterr().out
    assert "\x1b[32mINFO:\x1b[0m" in terminal
    assert "Started server process [123]" in terminal
    assert "agent.turn.done" in terminal
    assert "你好，我是智策助手" in terminal
    assert all("\x1b[" not in line for line in supervisor.logs(10))


def test_supervisor_captures_real_child_terminal_stream(tmp_path: Path):
    supervisor = LocalOpsSupervisor(
        state_dir=tmp_path / "state",
        logs_dir=tmp_path / "logs",
        child_argv=[
            sys.executable,
            "-c",
            "print('INFO:     Uvicorn running'); print('WARNING:  [gateway] degraded')",
        ],
    )

    supervisor._start_child()
    deadline = time.monotonic() + 5
    while len(supervisor.logs(10)) < 2 and time.monotonic() < deadline:
        time.sleep(0.02)
    supervisor._stop_child()

    assert supervisor.logs(10) == [
        "INFO:     Uvicorn running",
        "WARNING:  [gateway] degraded",
    ]


def test_supervisor_captures_real_child_chinese_as_utf8(tmp_path: Path):
    supervisor = LocalOpsSupervisor(
        state_dir=tmp_path / "state",
        logs_dir=tmp_path / "logs",
        child_argv=[
            sys.executable,
            "-c",
            "print('INFO | agent.turn.done | input_preview=你是谁 output_preview=我是智策助手')",
        ],
    )

    supervisor._start_child()
    deadline = time.monotonic() + 5
    while not supervisor.logs(1) and time.monotonic() < deadline:
        time.sleep(0.02)
    supervisor._stop_child()

    assert supervisor.logs(1) == [
        "INFO | agent.turn.done | input_preview=你是谁 output_preview=我是智策助手"
    ]
