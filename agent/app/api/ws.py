"""WebSocket channel for the local Web chat UI."""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from agent.app.api.routes import _api_error_from_exception
from agent.app.runtime import EXTERNAL_COMMAND_PROFILE, WEB_COMMAND_PROFILE, ChatTurnResult

router = APIRouter()


@router.websocket("/ws")
async def websocket_chat(websocket: WebSocket) -> None:
    """Serve the bidirectional WebSocket chat channel."""

    runtime = getattr(websocket.app.state, "runtime", None)
    await websocket.accept()
    connection_id = "ws-" + uuid.uuid4().hex
    command_profile = WEB_COMMAND_PROFILE
    send_lock = asyncio.Lock()
    active_tasks: set[asyncio.Task[None]] = set()

    async def send_event(event: str, data: Any, *, session_id: str = "") -> None:
        payload: dict[str, Any] = {"event": event, "data": data}
        if session_id:
            payload["session_id"] = session_id
        async with send_lock:
            await websocket.send_json(payload)

    await send_event("connected", {"connection_id": connection_id})
    if runtime is None:
        await send_event(
            "channel_status",
            {"type": "error", "error": {"code": "CONFIG_ERROR", "message": "runtime is not configured"}},
        )
        await websocket.close(code=1011)
        return

    try:
        while True:
            frame = await websocket.receive_json()
            if not isinstance(frame, dict):
                await send_event(
                    "channel_status",
                    {"type": "error", "error": {"code": "INVALID_REQUEST", "message": "invalid frame"}},
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
                        {"type": "error", "error": {"code": "INVALID_REQUEST", "message": str(exc)}},
                    )
                    continue
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
                    {"type": "error", "error": {"code": "INVALID_REQUEST", "message": "session_id is required"}},
                )
                continue
            if frame_type == "stop" or content.lower() == "/stop":
                result = runtime.cancel_session(session_id)
                await send_event("channel_status", {"type": "stopped", **result}, session_id=session_id)
                continue
            if frame_type != "message":
                await send_event(
                    "channel_status",
                    {
                        "type": "error",
                        "error": {"code": "INVALID_REQUEST", "message": f"unknown frame type: {frame_type}"},
                    },
                    session_id=session_id,
                )
                continue
            if content.lower() == "/exit" and command_profile == EXTERNAL_COMMAND_PROFILE:
                await _close_external_connection(websocket, send_event, active_tasks, session_id=session_id)
                return

            task = asyncio.create_task(_run_message_frame(runtime, frame, send_event, command_profile))
            active_tasks.add(task)
            task.add_done_callback(active_tasks.discard)
    except WebSocketDisconnect:
        for task in active_tasks:
            task.cancel()


async def _run_message_frame(runtime, frame: dict[str, Any], send_event, command_profile: str) -> None:
    """Run one message frame and forward runtime events to the socket."""

    session_id = str(frame.get("session_id") or "").strip()
    content = str(frame.get("content") or "").strip()
    model = str(frame.get("model") or "").strip()
    turn_id = "turn-" + uuid.uuid4().hex
    if not content:
        await send_event(
            "channel_status",
            {"type": "error", "turn_id": turn_id, "error": {"code": "INVALID_REQUEST", "message": "content is required"}},
            session_id=session_id,
        )
        return

    await send_event("channel_status", {"type": "accepted", "turn_id": turn_id}, session_id=session_id)
    queue: asyncio.Queue[tuple[str, Any]] = asyncio.Queue()
    loop = asyncio.get_running_loop()

    def on_event(event: dict[str, Any]) -> None:
        loop.call_soon_threadsafe(queue.put_nowait, ("event", event))

    def worker() -> None:
        try:
            if _should_apply_model_preference(content, model):
                runtime.set_model_preference(model)
            result = runtime.run_chat_events(
                session_id,
                content,
                on_event=on_event,
                command_profile=command_profile,
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
                    await send_event("channel_text", payload.get("content", ""), session_id=session_id)
                continue
            if kind == "error":
                error = _api_error_from_exception(payload)
                await send_event(
                    "channel_status",
                    {
                        "type": "error",
                        "turn_id": turn_id,
                        "error": {"code": error.code, "message": error.message},
                    },
                    session_id=session_id,
                )
                break
            result: ChatTurnResult = payload
            status_type = "stopped" if result.stopped else "done"
            await send_event(
                "channel_status",
                {
                    "type": status_type,
                    "turn_id": result.turn_id or turn_id,
                    "assistant": {"role": "assistant", "content": result.content},
                },
                session_id=session_id,
            )
            break
    finally:
        await worker_task


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
