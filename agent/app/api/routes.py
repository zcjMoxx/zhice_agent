"""FastAPI routes for the local Web API."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from queue import Queue
from threading import Thread
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

from agent.app.api.schemas import (
    ChatMessageResponse,
    ChatRequest,
    ChatResponse,
    ModelPreferenceRequest,
    ModelsResponse,
    SessionMutationResponse,
    SessionRenameRequest,
    SessionResponse,
    SessionsResponse,
    SessionSummaryResponse,
)
from agent.app.runtime import ChatTurnResult, ModelState
from agent.message import Message
from agent.protocols.llm import LLMConfigurationError, LLMProviderError

router = APIRouter(prefix="/api")


class ApiError(Exception):
    """Exception carrying a stable API error code."""

    def __init__(self, code: str, message: str, *, status_code: int = 500):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


@router.get("/sessions", response_model=SessionsResponse)
def list_sessions(request: Request) -> SessionsResponse:
    """Return workspace sessions ordered by recent activity."""

    runtime = _runtime(request)
    try:
        summaries = runtime.list_sessions()
    except ValueError as exc:
        raise ApiError("INVALID_REQUEST", str(exc), status_code=400) from exc
    return SessionsResponse(
        sessions=[
            SessionSummaryResponse(
                session_id=summary.session_id,
                preview=summary.preview,
                updated_at=_format_timestamp(summary.updated_at),
                message_count=summary.message_count,
                title=summary.title,
            )
            for summary in summaries
        ]
    )


@router.get("/sessions/{session_id}", response_model=SessionResponse)
def read_session(session_id: str, request: Request) -> SessionResponse:
    """Return persisted messages for one session."""

    runtime = _runtime(request)
    try:
        state = runtime.load_session(session_id)
    except ValueError as exc:
        raise ApiError("INVALID_REQUEST", str(exc), status_code=400) from exc
    return SessionResponse(
        session_id=state.session_id,
        messages=[_message_response(message) for message in state.messages],
        metadata=dict(state.metadata),
    )


@router.patch("/sessions/{session_id}", response_model=SessionMutationResponse)
def rename_session(
    session_id: str,
    request_body: SessionRenameRequest,
    request: Request,
) -> SessionMutationResponse:
    """Rename a session display title."""

    runtime = _runtime(request)
    title = request_body.title.strip()
    if not title:
        raise ApiError("INVALID_REQUEST", "title is required", status_code=400)
    try:
        summary = runtime.rename_session(session_id, title)
    except ValueError as exc:
        raise ApiError("INVALID_REQUEST", str(exc), status_code=400) from exc
    return SessionMutationResponse(
        session_id=session_id,
        status="renamed",
        title=summary.title,
    )


@router.delete("/sessions/{session_id}", response_model=SessionMutationResponse)
def delete_session(session_id: str, request: Request) -> SessionMutationResponse:
    """Delete a session and its metadata."""

    runtime = _runtime(request)
    try:
        runtime.delete_session(session_id)
    except ValueError as exc:
        raise ApiError("INVALID_REQUEST", str(exc), status_code=400) from exc
    return SessionMutationResponse(session_id=session_id, status="deleted")


@router.post("/chat", response_model=ChatResponse)
def chat(request_body: ChatRequest, request: Request) -> ChatResponse:
    """Run one Web chat turn through the Agent loop."""

    session_id = request_body.session_id.strip()
    message = request_body.message.strip()
    if not session_id:
        raise ApiError("INVALID_REQUEST", "session_id is required", status_code=400)
    if not message:
        raise ApiError("INVALID_REQUEST", "message is required", status_code=400)

    runtime = _runtime(request)
    try:
        selected_model = (request_body.model or "").strip()
        if _should_apply_model_preference(message, selected_model):
            runtime.set_model_preference(selected_model)
        assistant_text = _run_chat_events(runtime, session_id, message).content
    except Exception as exc:
        raise _api_error_from_exception(exc) from exc

    return ChatResponse(
        session_id=session_id,
        assistant=ChatMessageResponse(role="assistant", content=assistant_text),
    )


@router.post("/chat/stream")
def chat_stream(request_body: ChatRequest, request: Request) -> StreamingResponse:
    """Run one Web chat turn and stream client-friendly SSE events."""

    session_id = request_body.session_id.strip()
    message = request_body.message.strip()
    if not session_id:
        raise ApiError("INVALID_REQUEST", "session_id is required", status_code=400)
    if not message:
        raise ApiError("INVALID_REQUEST", "message is required", status_code=400)

    runtime = _runtime(request)
    try:
        selected_model = (request_body.model or "").strip()
        if _should_apply_model_preference(message, selected_model):
            runtime.set_model_preference(selected_model)
    except Exception as exc:
        raise _api_error_from_exception(exc) from exc

    return StreamingResponse(
        _chat_stream_events(runtime, session_id, message),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache"},
    )


@router.get("/models", response_model=ModelsResponse)
def read_models(request: Request) -> ModelsResponse:
    """Return the current endpoint and its selectable models."""

    runtime = _runtime(request)
    try:
        return _model_response(runtime.model_state())
    except ValueError as exc:
        raise ApiError("INVALID_REQUEST", str(exc), status_code=400) from exc


@router.post("/model/preference", response_model=ModelsResponse)
def set_model_preference(
    request_body: ModelPreferenceRequest,
    request: Request,
) -> ModelsResponse:
    """Set the preferred model for the current endpoint."""

    model = request_body.model.strip()
    if not model:
        raise ApiError("INVALID_REQUEST", "model is required", status_code=400)
    runtime = _runtime(request)
    try:
        return _model_response(runtime.set_model_preference(model))
    except ValueError as exc:
        raise ApiError("INVALID_REQUEST", str(exc), status_code=400) from exc


def _runtime(request: Request):
    """Return the runtime dependency object stored on the FastAPI app."""

    runtime = getattr(request.app.state, "runtime", None)
    if runtime is None:
        raise ApiError("CONFIG_ERROR", "runtime is not configured", status_code=500)
    return runtime


def _run_chat_events(runtime, session_id: str, message: str, on_event=None) -> ChatTurnResult:
    """Run a chat turn through the required Web runtime event path."""

    return runtime.run_chat_events(session_id, message, on_event=on_event)


def _should_apply_model_preference(message: str, model: str) -> bool:
    """Return whether a request model should update the current endpoint model."""

    return bool(model and model != "auto" and not message.lstrip().startswith("/"))


def _chat_stream_events(runtime, session_id: str, message: str):
    """Yield one chat result as SSE events."""

    yield _sse("status", {"phase": "accepted"})
    events: Queue[tuple[str, Any]] = Queue()

    def on_event(event: dict[str, Any]) -> None:
        events.put(("event", event))

    def worker() -> None:
        try:
            result = _run_chat_events(runtime, session_id, message, on_event=on_event)
        except Exception as exc:  # noqa: BLE001 - streaming responses must encode errors.
            events.put(("error", exc))
            return
        events.put(("done", result))

    thread = Thread(target=worker, daemon=True)
    thread.start()

    while True:
        kind, payload = events.get()
        if kind == "event":
            if payload.get("type") == "text_delta":
                yield _sse("delta", {"content": payload.get("content", "")})
            continue
        if kind == "error":
            error = _api_error_from_exception(payload)
            yield _sse("error", {"error": {"code": error.code, "message": error.message}})
            break
        result = payload
        assistant = ChatMessageResponse(role="assistant", content=result.content)
        done_event = "stopped" if result.stopped else "done"
        yield _sse(
            done_event,
            {
                "session_id": session_id,
                "assistant": _model_dump(assistant),
            },
        )
        break
    thread.join(timeout=0.1)


def _sse(event: str, payload: dict[str, Any]) -> str:
    """Format one Server-Sent Event."""

    data = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    return f"event: {event}\ndata: {data}\n\n"


def _api_error_from_exception(exc: Exception) -> ApiError:
    """Map runtime exceptions into the stable API error shape."""

    if isinstance(exc, ApiError):
        return exc
    if isinstance(exc, ValueError):
        return ApiError("INVALID_REQUEST", str(exc), status_code=400)
    if isinstance(exc, LLMConfigurationError):
        return ApiError("CONFIG_ERROR", _safe_message(str(exc)), status_code=500)
    if isinstance(exc, LLMProviderError):
        return ApiError("LLM_ERROR", _safe_message(str(exc)), status_code=502)
    return ApiError("INTERNAL_ERROR", "Unexpected server error", status_code=500)


def _model_dump(model) -> dict[str, Any]:
    """Return a plain dict across supported Pydantic versions."""

    if hasattr(model, "model_dump"):
        return model.model_dump()
    return model.dict()


def _model_response(model_state: ModelState) -> ModelsResponse:
    """Convert runtime model state to an API response."""

    return ModelsResponse(
        endpoint=str(model_state.endpoint),
        current_model=str(model_state.current_model),
        models=[str(model) for model in model_state.models],
    )


def _message_response(message: Message) -> ChatMessageResponse:
    """Convert an internal Message to the API response shape."""

    return ChatMessageResponse(
        role=message.role,
        content=message.content,
        name=message.name,
        tool_call_id=message.tool_call_id,
        tool_calls=list(message.tool_calls),
        metadata=dict(message.metadata),
    )


def _format_timestamp(timestamp: float) -> str:
    """Format session timestamps as stable UTC ISO 8601 strings."""

    if timestamp <= 0:
        return ""
    return datetime.fromtimestamp(timestamp, UTC).isoformat(timespec="seconds")


def _safe_message(message: str) -> str:
    """Bound provider/config error text before returning it over HTTP."""

    return message[:500] if message else "request failed"
