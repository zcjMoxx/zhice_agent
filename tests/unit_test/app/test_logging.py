from __future__ import annotations

import io
import json
import logging
from datetime import datetime

import pytest

from agent.app.logging import (
    DeferredGatewayTerminalLogs,
    GatewayLogOptions,
    TerminalLogFormatter,
    _format_duration_ms,
    configure_gateway_logging,
    reset_gateway_logging,
)
from agent.logging_utils import (
    DeferredConsoleHandler,
    begin_console_log_deferral,
    flush_deferred_console_logs,
    log_event,
    preview_json,
    preview_text,
    redact_mapping,
)


def teardown_function() -> None:
    flush_deferred_console_logs()
    reset_gateway_logging()


def test_deferred_console_handler_replays_startup_records_after_enabled_summary():
    stream = io.StringIO()
    handler = DeferredConsoleHandler(stream)
    handler.setFormatter(logging.Formatter("[%(levelname)s]  %(funcName)-16s %(message)s"))
    logger = logging.getLogger("test.deferred.botpy")
    logger.handlers = [handler]
    logger.propagate = False
    logger.setLevel(logging.INFO)
    begin_console_log_deferral()

    logger.info("[botpy] 登录机器人账号中...")

    assert stream.getvalue() == ""
    stream.write('gateway.channel.enabled | channels=["web","qq","weixin"]\n')
    flush_deferred_console_logs()
    lines = stream.getvalue().splitlines()
    assert lines[0].startswith("gateway.channel.enabled")
    assert lines[1] == "[INFO]  test_deferred_console_handler_replays_startup_records_after_enabled_summary [botpy] 登录机器人账号中..."


def test_terminal_formatter_uses_bracketed_seconds_and_pipe_separator():
    formatter = TerminalLogFormatter()
    record = logging.LogRecord(
        "zcagent.agent.turn",
        logging.INFO,
        __file__,
        1,
        "turn.start",
        (),
        None,
    )
    record.created = datetime(2026, 7, 7, 21, 34, 12).timestamp()
    record.event = "turn.start"  # type: ignore[attr-defined]
    record.fields = {"session": "chat-20260707", "turn": "turn-abc"}  # type: ignore[attr-defined]

    rendered = formatter.format(record)

    assert rendered == (
        "[2026-07-07 21:34:12] | INFO | "
        "agent.turn.start | session=chat-20260707 turn=turn-abc"
    )
    assert ".000" not in rendered


def test_terminal_formatter_combines_component_and_event():
    formatter = TerminalLogFormatter()

    assert _format_action(formatter, "zcagent.agent.llm", "llm.call") == "agent.llm.call"
    assert _format_action(formatter, "zcagent.agent.tool", "tool.done") == "agent.tool.done"
    assert _format_action(formatter, "zcagent.agent.session", "session.save") == "agent.session.save"
    assert _format_action(formatter, "zcagent.agent.web", "chat.done") == "web.chat.done"
    assert _format_action(formatter, "zcagent.gateway", "startup") == "gateway.startup"
    assert _format_action(formatter, "zcagent.ws", "connection.open") == "ws.connection.open"
    assert _format_action(formatter, "zcagent.other", "event") == "zcagent.event"


def test_terminal_formatter_highlights_tool_and_hides_trace_ids():
    formatter = TerminalLogFormatter()

    rendered = _format_record(
        formatter,
        "zcagent.agent.tool",
        "tool.done",
        {
            "tool": "memory_read",
            "actor_username": "user001",
            "actor_user_id": "user-internal",
            "session_id": "session-long",
            "turn_id": "turn-long",
            "turn_index": 5,
            "request_id": "ws-turn-long",
            "tool_call_id": "call-long",
            "duration_ms": 11,
            "match_count": 1,
            "total": 1,
        },
    )

    assert rendered == (
        "[2026-07-07 21:34:12] | INFO | TOOL memory_read | DONE | "
        "user=user001 turn=5 duration=11ms matches=1 total=1"
    )
    assert "session-long" not in rendered
    assert "turn-long" not in rendered
    assert "call-long" not in rendered
    assert "user-internal" not in rendered


@pytest.mark.parametrize(
    ("duration_ms", "expected"),
    [
        (57, "57ms"),
        (500, "500ms"),
        (1000, "1s"),
        (1250, "1.25s"),
        (10500, "10.5s"),
        (60000, "1m"),
        (60500, "1m1s"),
        (200000, "3m20s"),
        (3600000, "1h"),
        (3900000, "1h5m"),
        (3905000, "1h5m5s"),
    ],
)
def test_format_duration_ms_uses_natural_unit_boundaries(duration_ms, expected):
    assert _format_duration_ms(duration_ms) == expected


def test_terminal_formatter_can_color_time_and_action_segments():
    formatter = TerminalLogFormatter(color=True)

    rendered = _format_record(
        formatter,
        "zcagent.agent.turn",
        "turn.start",
        {"session": "chat-20260707"},
    )

    assert rendered.startswith("\033[32m[2026-07-07 21:34:12]\033[0m | INFO | ")
    assert "\033[36magent.turn.start\033[0m" in rendered
    assert rendered.endswith("| session=chat-20260707")


def test_terminal_formatter_marks_entire_warning_line_in_bright_red():
    formatter = TerminalLogFormatter(color=True)

    rendered = _format_record(
        formatter,
        "zcagent.agent.subagent",
        "subagent.runtime_unavailable",
        {"code": "SUBAGENT_PROMPT_NOT_FOUND", "missing_prompt": "subagent.md"},
        level=logging.WARNING,
    )

    assert rendered.startswith("\033[1;91m[2026-07-07 21:34:12] | WARNING | ")
    assert "agent.subagent.runtime_unavailable" in rendered
    assert "code=SUBAGENT_PROMPT_NOT_FOUND missing_prompt=subagent.md" in rendered
    assert rendered.endswith("\033[0m")
    assert rendered.count("\033[") == 2


def test_preview_text_redacts_multiline_and_truncates():
    text = "OPENAI_API_KEY=sk-testsecret123456\n" + "x" * 80

    preview = preview_text(text, limit=48)

    assert "\n" not in preview
    assert "sk-testsecret123456" not in preview
    assert "OPENAI_API_KEY=<redacted>" in preview
    assert preview.endswith("...")
    assert len(preview) <= 48


def test_preview_json_redacts_nested_sensitive_fields():
    payload = {
        "api_key": "abc",
        "nested": {"authorization": "Bearer secret-token"},
        "items": [{"refresh_token": "r"}],
    }

    preview = preview_json(payload, limit=200)

    assert "abc" not in preview
    assert "secret-token" not in preview
    assert '"api_key":"***"' in preview
    assert '"authorization":"***"' in preview
    assert '"refresh_token":"***"' in preview


def test_redact_mapping_keeps_original_object_untouched():
    payload = {"api_key": "abc", "name": "demo"}

    redacted = redact_mapping(payload)

    assert redacted == {"api_key": "***", "name": "demo"}
    assert payload == {"api_key": "abc", "name": "demo"}


def test_configure_gateway_logging_writes_date_partitioned_trace_jsonl(tmp_path):
    stream = io.StringIO()
    result = configure_gateway_logging(
        GatewayLogOptions(trace_log=True),
        logs_dir=tmp_path / "logs",
        terminal_stream=stream,
    )

    log_event(
        logging.getLogger("zcagent.agent.turn"),
        logging.INFO,
        "turn.start",
        session_id="chat-20260707",
        turn_id="turn-abc",
        api_key="secret",
    )

    assert result.trace_path is not None
    assert result.trace_path.name == "trace.log"
    assert result.trace_path.parent.name == datetime.now().strftime("%Y-%m-%d")
    payload = json.loads(result.trace_path.read_text(encoding="utf-8").splitlines()[-1])
    assert payload["level"] == "INFO"
    assert payload["component"] == "agent"
    assert "logger" not in payload
    assert payload["event"] == "turn.start"
    assert payload["session_id"] == "chat-20260707"
    assert payload["turn_id"] == "turn-abc"
    assert payload["api_key"] == "***"
    assert "secret" not in result.trace_path.read_text(encoding="utf-8")
    assert "[20" in stream.getvalue()


def test_configure_gateway_logging_emits_gateway_channel_events_to_terminal_and_trace(tmp_path):
    stream = io.StringIO()
    result = configure_gateway_logging(
        GatewayLogOptions(trace_log=True),
        logs_dir=tmp_path / "logs",
        terminal_stream=stream,
    )

    log_event(
        logging.getLogger("zcagent.gateway"),
        logging.INFO,
        "channel.enabled",
        channels=["web", "qq", "weixin"],
    )

    terminal = stream.getvalue()
    assert 'INFO:     [gateway] channels enabled | channels=["web","qq","weixin"]' in terminal
    assert 'channels=["web","qq","weixin"]' in terminal
    assert result.trace_path is not None
    payload = json.loads(result.trace_path.read_text(encoding="utf-8").splitlines()[-1])
    assert payload == {
        "ts": payload["ts"],
        "level": "INFO",
        "component": "gateway",
        "event": "channel.enabled",
        "channels": ["web", "qq", "weixin"],
    }


def test_external_channel_events_use_server_style_while_agent_turn_keeps_timestamp(tmp_path):
    stream = io.StringIO()
    configure_gateway_logging(
        GatewayLogOptions(trace_log=False),
        logs_dir=tmp_path / "logs",
        terminal_stream=stream,
    )

    log_event(
        logging.getLogger("zcagent.agent.channel.weixin"),
        logging.WARNING,
        "channel.weixin.send_failed",
        account_ref="wx-a31f",
        error_type="WeixinSidecarError",
    )
    log_event(
        logging.getLogger("zcagent.agent.turn"),
        logging.INFO,
        "turn.start",
        channel="weixin",
    )

    lines = stream.getvalue().splitlines()
    assert lines[0] == (
        "WARNING:  [weixin] send failed | "
        "account_ref=wx-a31f error_type=WeixinSidecarError"
    )
    assert lines[1].startswith("[20")
    assert "agent.turn.start" in lines[1]


def test_deferred_gateway_terminal_logs_replay_after_server_status_without_delaying_trace(
    tmp_path,
):
    stream = io.StringIO()
    result = configure_gateway_logging(
        GatewayLogOptions(trace_log=True),
        logs_dir=tmp_path / "logs",
        terminal_stream=stream,
    )
    deferred = DeferredGatewayTerminalLogs()
    deferred.start()

    log_event(
        logging.getLogger("zcagent.gateway"),
        logging.INFO,
        "channel.start",
        channel="web",
        state="available",
    )

    assert stream.getvalue() == ""
    assert result.trace_path is not None
    assert "channel.start" in result.trace_path.read_text(encoding="utf-8")

    stream.write("INFO:     Application startup complete.\n")
    stream.write("INFO:     Uvicorn running on http://127.0.0.1:10086\n")
    deferred.flush()

    lines = stream.getvalue().splitlines()
    assert lines[0] == "INFO:     Application startup complete."
    assert lines[1].startswith("INFO:     Uvicorn running")
    assert lines[2] == "INFO:     [web] start | state=available"


def test_tool_terminal_is_compact_while_trace_keeps_full_ids(tmp_path):
    stream = io.StringIO()
    result = configure_gateway_logging(
        GatewayLogOptions(trace_log=True),
        logs_dir=tmp_path / "logs",
        terminal_stream=stream,
    )

    log_event(
        logging.getLogger("zcagent.agent.tool"),
        logging.INFO,
        "tool.start",
        tool="memory_read",
        actor_username="user001",
        actor_user_id="user-internal",
        session_id="session-full",
        turn_id="turn-full",
        turn_index=5,
        request_id="request-full",
        tool_call_id="call-full",
    )

    terminal = stream.getvalue()
    assert "TOOL memory_read | START | user=user001 turn=5" in terminal
    assert "session-full" not in terminal
    assert "request-full" not in terminal
    assert "call-full" not in terminal
    assert result.trace_path is not None
    payload = json.loads(result.trace_path.read_text(encoding="utf-8").splitlines()[-1])
    assert payload["session_id"] == "session-full"
    assert payload["turn_id"] == "turn-full"
    assert payload["request_id"] == "request-full"
    assert payload["tool_call_id"] == "call-full"


def test_turn_done_output_preview_is_visible_in_terminal_and_trace(tmp_path):
    stream = io.StringIO()
    result = configure_gateway_logging(
        GatewayLogOptions(trace_log=True),
        logs_dir=tmp_path / "logs",
        terminal_stream=stream,
    )

    log_event(
        logging.getLogger("zcagent.agent.turn"),
        logging.INFO,
        "turn.done",
        session_id="session-full",
        turn_id="turn-full",
        turn_index=6,
        duration_ms=9969,
        output_preview="结论：抽象是提取多个对象的共同特征。",
    )

    assert "output_preview=结论：抽象是提取多个对象的共同特征。" in stream.getvalue()
    assert "duration=9.97s" in stream.getvalue()
    assert result.trace_path is not None
    payload = json.loads(result.trace_path.read_text(encoding="utf-8").splitlines()[-1])
    assert payload["output_preview"] == "结论：抽象是提取多个对象的共同特征。"
    assert payload["duration_ms"] == 9969
    assert "duration" not in payload


def test_configure_gateway_logging_is_idempotent_for_terminal_handlers(tmp_path):
    stream = io.StringIO()
    options = GatewayLogOptions(trace_log=False)
    configure_gateway_logging(options, logs_dir=tmp_path / "logs", terminal_stream=stream)
    configure_gateway_logging(options, logs_dir=tmp_path / "logs", terminal_stream=stream)

    log_event(logging.getLogger("zcagent.agent.turn"), logging.INFO, "turn.start", session_id="s")

    lines = [line for line in stream.getvalue().splitlines() if "turn.start" in line]
    assert len(lines) == 1


def test_agent_log_can_be_disabled_while_trace_stays_on(tmp_path):
    stream = io.StringIO()
    result = configure_gateway_logging(
        GatewayLogOptions(agent_log=False, trace_log=True),
        logs_dir=tmp_path / "logs",
        terminal_stream=stream,
    )

    log_event(logging.getLogger("zcagent.agent.turn"), logging.INFO, "turn.start", session_id="s")

    assert stream.getvalue() == ""
    assert result.trace_path is not None
    assert "turn.start" in result.trace_path.read_text(encoding="utf-8")


def _format_action(formatter: TerminalLogFormatter, logger_name: str, event: str) -> str:
    return _format_record(formatter, logger_name, event).split(" | ")[2]


def _format_record(
    formatter: TerminalLogFormatter,
    logger_name: str,
    event: str,
    fields: dict[str, object] | None = None,
    *,
    level: int = logging.INFO,
) -> str:
    record = logging.LogRecord(logger_name, level, __file__, 1, event, (), None)
    record.created = datetime(2026, 7, 7, 21, 34, 12).timestamp()
    record.event = event  # type: ignore[attr-defined]
    if fields is not None:
        record.fields = fields  # type: ignore[attr-defined]
    return formatter.format(record)
