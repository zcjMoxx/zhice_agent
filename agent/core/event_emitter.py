"""Turn-scoped RuntimeEvent creation and best-effort delivery."""

from __future__ import annotations

import logging
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from agent.logging_utils import log_event
from agent.protocols.runtime_event import (
    RUNTIME_EVENT_PROTOCOL_VERSION,
    RUNTIME_EVENT_STATUS_BY_TYPE,
    RuntimeEvent,
    RuntimeEventSink,
)

event_logger = logging.getLogger("zcagent.agent.event")
RuntimeEventCallback = Callable[[dict[str, Any]], None]


class CallbackRuntimeEventSink:
    """Adapt the existing on_event callback to the RuntimeEventSink protocol."""

    def __init__(self, callback: RuntimeEventCallback):
        self._callback = callback

    def emit(self, event: RuntimeEvent) -> None:
        self._callback(event.to_dict())


class RuntimeEventEmitter:
    """Assign monotonically increasing sequence numbers within one turn."""

    def __init__(
        self,
        *,
        session_id: str,
        turn_id: str,
        request_id: str = "",
        sink: RuntimeEventSink | None = None,
        scope: dict[str, Any] | None = None,
    ):
        self.session_id = session_id
        self.turn_id = turn_id
        self.request_id = request_id
        self.sink = sink
        self.scope = dict(scope or {})
        self._sequence = 0

    @property
    def sequence(self) -> int:
        """Return the last sequence allocated by this turn emitter."""

        return self._sequence

    def emit(
        self,
        event_type: str,
        *,
        tool_call_id: str = "",
        tool_call_record_id: str = "",
        skill_run_id: str = "",
        parent_event_id: str = "",
        display: dict[str, Any] | None = None,
        ui_metadata: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> RuntimeEvent | None:
        """Create, optionally enrich, and best-effort deliver one event."""

        self._sequence += 1
        metadata = dict(metadata or {})
        try:
            resolved_display = _default_display(event_type, metadata)
            resolved_display.update(display or {})
            event = RuntimeEvent(
                protocol_version=RUNTIME_EVENT_PROTOCOL_VERSION,
                event_id="event-" + uuid.uuid4().hex,
                type=event_type,
                status=RUNTIME_EVENT_STATUS_BY_TYPE[event_type],
                timestamp=datetime.now(UTC).isoformat().replace("+00:00", "Z"),
                sequence=self._sequence,
                session_id=self.session_id,
                turn_id=self.turn_id,
                request_id=self.request_id,
                tool_call_id=tool_call_id,
                tool_call_record_id=tool_call_record_id,
                skill_run_id=skill_run_id,
                parent_event_id=parent_event_id,
                agent_id=str(self.scope.get("agent_id") or ""),
                parent_agent_id=str(self.scope.get("parent_agent_id") or ""),
                root_session_id=str(self.scope.get("root_session_id") or ""),
                root_turn_id=str(self.scope.get("root_turn_id") or ""),
                parent_session_id=str(self.scope.get("parent_session_id") or ""),
                parent_turn_id=str(self.scope.get("parent_turn_id") or ""),
                batch_id=str(self.scope.get("batch_id") or ""),
                task_id=str(self.scope.get("task_id") or ""),
                depth=int(self.scope.get("depth") or 0),
                display=resolved_display,
                ui_metadata=dict(ui_metadata or {}),
                metadata=metadata,
            )
        except Exception as exc:  # noqa: BLE001 - observability cannot break a turn.
            log_event(
                event_logger,
                logging.WARNING,
                "runtime_event.invalid",
                event_type=event_type,
                error_type=type(exc).__name__,
            )
            return None

        if self.sink is not None:
            try:
                self.sink.emit(event)
            except Exception as exc:  # noqa: BLE001 - event delivery is best effort.
                log_event(
                    event_logger,
                    logging.WARNING,
                    "runtime_event.sink_failed",
                    event_type=event_type,
                    error_type=type(exc).__name__,
                )
        return event

def callback_runtime_event_sink(
    callback: RuntimeEventCallback | None,
) -> RuntimeEventSink | None:
    """Return a sink adapter only when the legacy callback is present."""

    return CallbackRuntimeEventSink(callback) if callback is not None else None


def _default_display(event_type: str, metadata: dict[str, Any]) -> dict[str, str]:
    tool_name = str(metadata.get("tool_name") or "工具")
    skill_name = str(metadata.get("skill_name") or "Skill")
    reason = str(metadata.get("reason") or "")
    titles = {
        "turn.started": "已接收问题",
        "turn.completed": "已完成",
        "turn.failed": "处理失败",
        "turn.stopped": "已停止",
        "context.started": "正在整理上下文",
        "context.completed": "上下文整理完成",
        "context.failed": "上下文整理失败",
        "llm.started": (
            "正在根据工具结果生成回答" if reason == "tool_result" else "正在请求模型"
        ),
        "llm.completed": "模型响应完成",
        "llm.failed": "模型请求失败",
        "tool.started": "正在执行命令" if tool_name == "exec" else f"正在执行 {tool_name}",
        "tool.completed": "命令执行完成" if tool_name == "exec" else f"{tool_name} 执行完成",
        "tool.failed": "命令执行失败" if tool_name == "exec" else f"{tool_name} 执行失败",
        "tool.waiting_confirmation": "等待操作确认",
        "skill.started": f"正在运行 {skill_name}",
        "skill.progress": f"{skill_name} 运行中",
        "skill.completed": f"{skill_name} 已完成",
        "skill.failed": f"{skill_name} 运行失败",
    }
    icons = {
        "turn": "turn",
        "context": "context",
        "llm": "model",
        "tool": "tool",
        "skill": "skill",
    }
    prefix = event_type.partition(".")[0]
    return {
        "title": titles[event_type],
        "icon": icons.get(prefix, "status"),
        "visibility": "normal",
    }
