"""WebSocket channel for the local Web chat UI."""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import replace
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from agent.app.api.routes import _api_error_from_exception, _runtime_call, _set_model_preference
from agent.app.auth import AuthHttpError, local_operator_actor
from agent.app.runtime import EXTERNAL_COMMAND_PROFILE, WEB_COMMAND_PROFILE, ChatTurnResult
from agent.core.turns import new_turn_id
from agent.protocols.auth import AuditEvent
from agent.protocols.errors import ErrorCode

router = APIRouter()


@router.websocket("/ws")
async def websocket_chat(websocket: WebSocket) -> None:
    """Serve the bidirectional WebSocket chat channel."""

    runtime = getattr(websocket.app.state, "runtime", None)
    auth = getattr(websocket.app.state, "auth_service", None)
    await websocket.accept()
    connection_id = "ws-" + uuid.uuid4().hex
    command_profile = WEB_COMMAND_PROFILE
    send_lock = asyncio.Lock()
    active_tasks: set[asyncio.Task[None]] = set()

    async def send_event(
        event: str,
        data: Any,
        *,
        session_id: str = "",
        turn_id: str = "",
    ) -> None:
        payload: dict[str, Any] = {"event": event, "data": data}
        if session_id:
            payload["session_id"] = session_id
        if turn_id:
            payload["turn_id"] = turn_id
        async with send_lock:
            await websocket.send_json(payload)

    if runtime is None:
        await send_event(
            "channel_status",
            {
                "type": "error",
                "error": {
                    "status": 500,
                    "code": ErrorCode.CONFIG_INVALID,
                    "message": "runtime is not configured",
                    "request_id": connection_id,
                    "details": {},
                },
            },
        )
        await websocket.close(code=1011)
        return
    try:
        actor = auth.resolve_ws_actor(websocket, channel="web") if auth else local_operator_actor(channel="web")
    except AuthHttpError as exc:
        if auth and auth.audit_sink is not None:
            auth.audit_sink.record(
                AuditEvent(
                    action="auth.request_denied",
                    resource_type="websocket",
                    request_id=connection_id,
                    channel="web",
                    route="/ws",
                    status_code=401,
                    decision="deny",
                    reason_code=exc.code,
                )
            )
        await send_event(
            "channel_status",
            {
                "type": "error",
                "error": {
                    "status": exc.status_code,
                    "code": exc.code,
                    "message": exc.message,
                    "request_id": connection_id,
                    "details": exc.details,
                },
            },
        )
        await websocket.close(code=1008)
        return

    await send_event("connected", {"connection_id": connection_id})

    try:
        while True:
            frame = await websocket.receive_json()
            if not isinstance(frame, dict):
                await send_event(
                    "channel_status",
                    _request_error("invalid frame", connection_id),
                )
                continue

            frame_type = str(frame.get("type") or "message").strip().lower()
            session_id = str(frame.get("session_id") or "").strip()
            content = str(frame.get("content") or "").strip()
            if frame_type == "hello":
                try:
                    client_name, command_profile = _resolve_command_profile(frame)
                except ValueError as exc:
                    await send_event(
                        "channel_status",
                        _request_error(str(exc), connection_id),
                    )
                    continue
                actor = replace(
                    actor,
                    channel="external_ws" if command_profile == EXTERNAL_COMMAND_PROFILE else "web",
                )
                await send_event(
                    "hello",
                    {
                        "client": client_name,
                        "command_profile": command_profile,
                        "capabilities": _command_capabilities(command_profile),
                    },
                )
                continue
            if frame_type == "new_session":
                new_session_id = _new_session_id()
                create_session = getattr(runtime, "create_session", None)
                if callable(create_session):
                    _runtime_call(runtime, "create_session", actor, new_session_id, command_profile)
                await send_event("session_created", {"session_id": new_session_id}, session_id=new_session_id)
                continue
            if frame_type == "heartbeat":
                await send_event("pong", {"connection_id": connection_id}, session_id=session_id)
                continue
            if not session_id:
                if frame_type == "message" and content.lower() == "/exit" and command_profile == EXTERNAL_COMMAND_PROFILE:
                    await _close_external_connection(websocket, send_event, active_tasks, session_id="")
                    return
                await send_event(
                    "channel_status",
                    _request_error("session_id is required", connection_id, field="session_id"),
                )
                continue
            if frame_type == "stop" or (
                content.lower() == "/stop" and command_profile == EXTERNAL_COMMAND_PROFILE
            ):
                result = _runtime_call(runtime, "cancel_session", actor, session_id)
                await send_event(
                    "channel_status",
                    {"type": "stopped", **result},
                    session_id=session_id,
                    turn_id=str(result.get("turn_id") or ""),
                )
                continue
            if frame_type != "message":
                await send_event(
                    "channel_status",
                    _request_error(f"unknown frame type: {frame_type}", connection_id),
                    session_id=session_id,
                )
                continue
            if content.lower() == "/exit" and command_profile == EXTERNAL_COMMAND_PROFILE:
                await _close_external_connection(websocket, send_event, active_tasks, session_id=session_id)
                return

            task = asyncio.create_task(
                _run_message_frame(runtime, actor, frame, send_event, command_profile)
            )
            active_tasks.add(task)
            task.add_done_callback(active_tasks.discard)
    except WebSocketDisconnect:
        for task in active_tasks:
            task.cancel()


async def _run_message_frame(
    runtime,
    actor,
    frame: dict[str, Any],
    send_event,
    command_profile: str,
) -> None:
    """Run one message frame and forward runtime events to the socket."""

    session_id = str(frame.get("session_id") or "").strip()
    content = str(frame.get("content") or "").strip()
    model = str(frame.get("model") or "").strip()
    turn_id = new_turn_id()
    if not content:
        await send_event(
            "channel_status",
            {
                **_request_error("content is required", "ws-" + turn_id, field="content"),
                "turn_id": turn_id,
            },
            session_id=session_id,
            turn_id=turn_id,
        )
        return

    await send_event(
        "channel_status",
        {"type": "accepted", "turn_id": turn_id},
        session_id=session_id,
        turn_id=turn_id,
    )
    queue: asyncio.Queue[tuple[str, Any]] = asyncio.Queue()
    loop = asyncio.get_running_loop()

    def on_event(event: dict[str, Any]) -> None:
        loop.call_soon_threadsafe(queue.put_nowait, ("event", event))

    def worker() -> None:
        try:
            if _should_apply_model_preference(content, model):
                _set_model_preference(
                    runtime,
                    actor,
                    session_id,
                    model,
                    request_id="",
                )
            result = _runtime_call(
                runtime,
                "run_chat_events",
                actor,
                session_id,
                content,
                turn_id=turn_id,
                on_event=on_event,
                command_profile=command_profile,
                request_id="",
            )
        except Exception as exc:  # noqa: BLE001 - errors must be sent over the channel.
            loop.call_soon_threadsafe(queue.put_nowait, ("error", exc))
            return
        loop.call_soon_threadsafe(queue.put_nowait, ("done", result))

    worker_task = asyncio.create_task(asyncio.to_thread(worker))
    try:
        while True:
            kind, payload = await queue.get()
            if kind == "event":
                if payload.get("type") == "text_delta":
                    await send_event(
                        "channel_text",
                        payload.get("content", ""),
                        session_id=session_id,
                        turn_id=turn_id,
                    )
                elif payload.get("type") == "tool_confirmation_required":
                    await send_event(
                        "tool_confirmation_required",
                        payload,
                        session_id=session_id,
                        turn_id=turn_id,
                    )
                continue
            if kind == "error":
                error = _api_error_from_exception(payload)
                await send_event(
                    "channel_status",
                    {
                        "type": "error",
                        "turn_id": turn_id,
                        "error": {
                            "status": error.status_code,
                            "code": error.code,
                            "message": error.message,
                            "request_id": "ws-" + turn_id,
                            "details": error.details,
                        },
                    },
                    session_id=session_id,
                    turn_id=turn_id,
                )
                break
            result: ChatTurnResult = payload
            status_type = "stopped" if result.stopped else "done"
            result_turn_id = result.turn_id or turn_id
            await send_event(
                "channel_status",
                {
                    "type": status_type,
                    "turn_id": result_turn_id,
                    "assistant": {"role": "assistant", "content": result.content},
                },
                session_id=session_id,
                turn_id=result_turn_id,
            )
            break
    finally:
        await worker_task


def _request_error(message: str, request_id: str, *, field: str = "") -> dict[str, Any]:
    details = {"field": field} if field else {}
    return {
        "type": "error",
        "error": {
            "status": 400,
            "code": ErrorCode.REQUEST_VALIDATION_FAILED,
            "message": message,
            "request_id": request_id,
            "details": details,
        },
    }


def _new_session_id() -> str:
    """Return a fresh Web session id."""

    return "session-" + uuid.uuid4().hex[:16]


def _should_apply_model_preference(content: str, model: str) -> bool:
    """Return whether a frame model should update the current endpoint model."""

    return bool(model and model != "auto" and not content.lstrip().startswith("/"))


def _resolve_command_profile(frame: dict[str, Any]) -> tuple[str, str]:
    """Resolve the command profile requested by a WS hello frame."""

    raw_client = frame.get("client")
    if raw_client is None or str(raw_client).strip() == "":
        return WEB_COMMAND_PROFILE, WEB_COMMAND_PROFILE
    client = str(raw_client).strip().lower()
    if client == WEB_COMMAND_PROFILE:
        return WEB_COMMAND_PROFILE, WEB_COMMAND_PROFILE
    if client == EXTERNAL_COMMAND_PROFILE:
        return EXTERNAL_COMMAND_PROFILE, EXTERNAL_COMMAND_PROFILE
    raise ValueError("unknown WS client; supported clients: web, external")


def _command_capabilities(command_profile: str) -> dict[str, bool]:
    """Return command capability flags advertised to the WS client."""

    external = command_profile == EXTERNAL_COMMAND_PROFILE
    return {"history_command": external, "exit_command": external}


async def _close_external_connection(websocket: WebSocket, send_event, active_tasks: set[asyncio.Task[None]], *, session_id: str) -> None:
    """Close an external WS connection for the /exit command."""

    for task in active_tasks:
        task.cancel()
    await send_event("channel_status", {"type": "closing", "reason": "exit_command"}, session_id=session_id)
    await websocket.close(code=1000)
