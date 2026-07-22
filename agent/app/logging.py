"""Gateway logging configuration and formatters."""

from __future__ import annotations

import json
import logging
import os
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import TextIO

from agent.logging_utils import redact_mapping

_AGENT_LOGGER_NAME = "zcagent.agent"
_OUR_HANDLER_ATTR = "_zhice_gateway_logging_handler"
_COLORAMA_FIXED = False
_COLOR_RESET = "\033[0m"
_TIME_COLOR = "32"
_TOOL_COLOR = "33"
_WARNING_COLOR = "1;91"
_ERROR_COLOR = "1;31"
_COMPONENT_COLORS = {
    "agent": "36",
    "web": "35",
    "gateway": "34",
    "ws": "32",
    "zcagent": "37",
}


@dataclass(frozen=True)
class GatewayLogOptions:
    """Runtime logging switches for the local gateway."""

    agent_log: bool = True
    agent_log_level: str = "info"
    trace_log: bool = True
    http_access_log: bool = True
    http_server_log: bool = True
    http_server_log_level: str = "info"


@dataclass(frozen=True)
class GatewayLoggingResult:
    """Resolved logging paths after configuration."""

    trace_path: Path | None = None


class TerminalLogFormatter(logging.Formatter):
    """Render compact human-readable Agent lifecycle lines."""

    def __init__(self, *, color: bool = False):
        super().__init__()
        self.color = color

    def format(self, record: logging.LogRecord) -> str:
        timestamp = datetime.fromtimestamp(record.created).strftime("[%Y-%m-%d %H:%M:%S]")
        component = _component_for_logger(record.name)
        event = str(getattr(record, "event", record.getMessage()))
        action, phase, terminal_fields = _terminal_view(
            component,
            event,
            getattr(record, "fields", {}),
        )
        fields = _format_fields(terminal_fields)
        level = record.levelname
        severity_color = _severity_color(record.levelno) if self.color else ""
        if severity_color:
            fixed = f"{timestamp} | {level} | {action}"
            if phase:
                fixed = f"{fixed} | {phase}"
            if fields:
                fixed = f"{fixed} | {fields}"
            return _style(fixed, severity_color)
        if self.color:
            timestamp = _style(timestamp, _TIME_COLOR)
            action_color = (
                _TOOL_COLOR
                if action.startswith("TOOL ")
                else _COMPONENT_COLORS.get(component, _COMPONENT_COLORS["zcagent"])
            )
            action = _style(action, action_color)
        fixed = f"{timestamp} | {level} | {action}"
        if phase:
            fixed = f"{fixed} | {phase}"
        if fields:
            return f"{fixed} | {fields}"
        return fixed


class JsonlTraceFormatter(logging.Formatter):
    """Render structured trace events as one JSON object per line."""

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": datetime.fromtimestamp(record.created).astimezone().isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "component": _component_for_logger(record.name),
            "event": str(getattr(record, "event", record.getMessage())),
        }
        fields = getattr(record, "fields", {})
        if isinstance(fields, dict):
            payload.update(redact_mapping(fields))
        return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


class DailyTraceFileHandler(logging.Handler):
    """Append JSONL trace events to logs/YYYY-MM-DD/trace.log."""

    def __init__(self, logs_dir: Path):
        super().__init__()
        self.logs_dir = Path(logs_dir).expanduser().resolve()
        self.setFormatter(JsonlTraceFormatter())

    def current_path(self) -> Path:
        """Return today's trace log path."""

        return self.logs_dir / datetime.now().strftime("%Y-%m-%d") / "trace.log"

    def emit(self, record: logging.LogRecord) -> None:
        """Write one formatted log record to the current daily trace file."""

        try:
            path = self.current_path()
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as handle:
                handle.write(self.format(record))
                handle.write("\n")
        except Exception:
            self.handleError(record)


def configure_gateway_logging(
    options: GatewayLogOptions,
    *,
    logs_dir: Path,
    terminal_stream: TextIO | None = None,
) -> GatewayLoggingResult:
    """Configure Agent terminal and workspace trace handlers idempotently."""

    agent_logger = logging.getLogger(_AGENT_LOGGER_NAME)
    _remove_our_handlers(agent_logger)
    agent_logger.propagate = False

    levels = [_level_number(options.agent_log_level)]
    if options.trace_log:
        levels.append(logging.DEBUG)
    agent_logger.setLevel(min(levels))

    if options.agent_log:
        stream = terminal_stream or sys.stderr
        terminal_handler = logging.StreamHandler(stream)
        terminal_handler.setLevel(_level_number(options.agent_log_level))
        terminal_handler.setFormatter(TerminalLogFormatter(color=_stream_supports_color(stream)))
        setattr(terminal_handler, _OUR_HANDLER_ATTR, True)
        agent_logger.addHandler(terminal_handler)

    trace_path: Path | None = None
    if options.trace_log:
        trace_handler = DailyTraceFileHandler(logs_dir)
        trace_handler.setLevel(logging.DEBUG)
        trace_path = trace_handler.current_path()
        trace_path.parent.mkdir(parents=True, exist_ok=True)
        setattr(trace_handler, _OUR_HANDLER_ATTR, True)
        agent_logger.addHandler(trace_handler)

    _configure_uvicorn_logging(options)
    return GatewayLoggingResult(trace_path=trace_path)


def reset_gateway_logging() -> None:
    """Remove gateway-owned logging handlers and restore propagation."""

    agent_logger = logging.getLogger(_AGENT_LOGGER_NAME)
    _remove_our_handlers(agent_logger)
    agent_logger.setLevel(logging.NOTSET)
    agent_logger.propagate = True


def _remove_our_handlers(logger: logging.Logger) -> None:
    """Close and detach handlers previously installed by this module."""

    for handler in list(logger.handlers):
        if getattr(handler, _OUR_HANDLER_ATTR, False):
            logger.removeHandler(handler)
            handler.close()


def _configure_uvicorn_logging(options: GatewayLogOptions) -> None:
    """Apply coarse server logger levels without owning uvicorn handlers."""

    server_level = _level_number(options.http_server_log_level)
    if not options.http_server_log:
        server_level = logging.CRITICAL + 1
    for name in ("uvicorn", "uvicorn.error"):
        logging.getLogger(name).setLevel(server_level)
    access_logger = logging.getLogger("uvicorn.access")
    access_logger.setLevel(logging.INFO if options.http_access_log else logging.CRITICAL + 1)


def _level_number(level: str) -> int:
    """Convert a CLI logging level into a logging module constant."""

    return getattr(logging, level.upper())


def _format_fields(raw_fields: object) -> str:
    """Format structured fields as key=value pairs for terminal output."""

    if not isinstance(raw_fields, dict):
        return ""
    fields = redact_mapping(raw_fields)
    parts = []
    for key, value in fields.items():
        parts.append(f"{key}={_format_field_value(value)}")
    return " ".join(parts)


def _terminal_view(
    component: str,
    event: str,
    raw_fields: object,
) -> tuple[str, str, dict[str, object]]:
    """Return an event-specific human view without flattening trace identifiers."""

    fields = redact_mapping(raw_fields) if isinstance(raw_fields, dict) else {}
    fields = _humanize_duration_fields(fields)
    if component == "agent" and event.startswith("tool.") and fields.get("tool"):
        phase = {
            "tool.start": "START",
            "tool.done": "DONE",
            "tool.error": "FAILED",
        }.get(event, event.removeprefix("tool.").upper())
        selected = _human_context_fields(fields)
        if event in {"tool.done", "tool.error"}:
            if fields.get("duration") is not None:
                selected["duration"] = fields["duration"]
            for source_key, terminal_key in (
                ("match_count", "matches"),
                ("total", "total"),
                ("category", "category"),
                ("operation", "operation"),
                ("code", "code"),
            ):
                value = fields.get(source_key)
                if value not in (None, ""):
                    selected[terminal_key] = value
        return f"TOOL {fields['tool']}", phase, selected

    selected = {
        key: value
        for key, value in fields.items()
        if key
        not in {
            "actor_user_id",
            "actor_username",
            "auth_session_id",
            "request_id",
            "tool_call_id",
            "tool_call_record_id",
            "turn_id",
            "turn_index",
        }
    }
    if not event.startswith("session."):
        selected.pop("session_id", None)
    context = _human_context_fields(fields)
    return f"{component}.{event}", "", {**context, **selected}


def _human_context_fields(fields: dict[str, object]) -> dict[str, object]:
    selected: dict[str, object] = {}
    username = fields.get("actor_username")
    turn_index = fields.get("turn_index")
    if username:
        selected["user"] = username
    if turn_index not in (None, ""):
        selected["turn"] = turn_index
    return selected


def _humanize_duration_fields(fields: dict[str, object]) -> dict[str, object]:
    """Replace terminal duration_ms with a compact human-readable duration."""

    result: dict[str, object] = {}
    for key, value in fields.items():
        if key == "duration_ms" and _is_duration_number(value):
            result["duration"] = _format_duration_ms(float(value))
        else:
            result[key] = value
    return result


def _format_duration_ms(duration_ms: float) -> str:
    """Format non-negative milliseconds using natural unit boundaries."""

    milliseconds = max(0.0, duration_ms)
    if milliseconds < 1000:
        return f"{int(round(milliseconds))}ms"
    if milliseconds < 60000:
        seconds = round(milliseconds / 1000, 2)
        return f"{seconds:g}s"
    total_seconds = int(milliseconds / 1000 + 0.5)
    hours, remainder = divmod(total_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    parts: list[str] = []
    if hours:
        parts.append(f"{hours}h")
    if minutes:
        parts.append(f"{minutes}m")
    if seconds:
        parts.append(f"{seconds}s")
    return "".join(parts) or "0s"


def _is_duration_number(value: object) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool)


def _stream_supports_color(stream: TextIO) -> bool:
    """Return whether ANSI color should be emitted to the terminal stream."""

    if os.getenv("NO_COLOR"):
        return False
    isatty = getattr(stream, "isatty", None)
    if not callable(isatty) or not isatty():
        return False
    if os.name != "nt":
        return True
    if _fix_windows_console_with_colorama():
        return True
    return _enable_windows_virtual_terminal(stream)


def _fix_windows_console_with_colorama() -> bool:
    """Let colorama enable ANSI handling on Windows when it is installed."""

    global _COLORAMA_FIXED
    if _COLORAMA_FIXED:
        return True
    try:
        from colorama import just_fix_windows_console
    except ImportError:
        return False
    just_fix_windows_console()
    _COLORAMA_FIXED = True
    return True


def _enable_windows_virtual_terminal(stream: TextIO) -> bool:
    """Enable native Windows virtual terminal processing for stdout/stderr."""

    if stream is sys.stdout:
        std_handle = -11
    elif stream is sys.stderr:
        std_handle = -12
    else:
        return False
    try:
        import ctypes
    except ImportError:
        return False
    kernel32 = ctypes.windll.kernel32
    handle = kernel32.GetStdHandle(std_handle)
    if handle in (0, -1):
        return False
    mode = ctypes.c_uint32()
    if not kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
        return False
    enable_virtual_terminal_processing = 0x0004
    return bool(kernel32.SetConsoleMode(handle, mode.value | enable_virtual_terminal_processing))


def _style(text: str, color_code: str) -> str:
    """Apply one ANSI color code to a terminal segment."""

    return f"\033[{color_code}m{text}{_COLOR_RESET}"


def _severity_color(level: int) -> str:
    """Return an attention color for warning and error terminal records."""

    if level >= logging.ERROR:
        return _ERROR_COLOR
    if level >= logging.WARNING:
        return _WARNING_COLOR
    return ""


def _component_for_logger(logger_name: str) -> str:
    """Map internal logger names to short user-facing components."""

    if logger_name == "zcagent.agent.web" or logger_name.startswith("zcagent.agent.web."):
        return "web"
    if logger_name.startswith("zcagent.agent."):
        return "agent"
    if logger_name == "zcagent.gateway" or logger_name.startswith("zcagent.gateway."):
        return "gateway"
    if logger_name == "zcagent.ws" or logger_name.startswith("zcagent.ws."):
        return "ws"
    return "zcagent"


def _format_field_value(value: object) -> str:
    """Render one terminal field value compactly."""

    if isinstance(value, str):
        if not value or any(char.isspace() for char in value):
            return json.dumps(value, ensure_ascii=False)
        return value
    if isinstance(value, bool):
        return "true" if value else "false"
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
