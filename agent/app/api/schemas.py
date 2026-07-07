"""Pydantic schemas for the local Web API."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class ErrorDetail(BaseModel):
    """Stable API error detail."""

    code: str
    message: str


class ErrorResponse(BaseModel):
    """Stable API error response."""

    error: ErrorDetail


class SessionSummaryResponse(BaseModel):
    """Compact session metadata used by the sidebar."""

    session_id: str
    preview: str
    updated_at: str
    message_count: int
    title: str = ""


class SessionsResponse(BaseModel):
    """Session list response."""

    sessions: list[SessionSummaryResponse]


class ChatMessageResponse(BaseModel):
    """One persisted chat message returned to the Web UI."""

    role: str
    content: str
    name: str | None = None
    tool_call_id: str | None = None
    tool_calls: list[dict[str, Any]] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    turn_id: str | None = None
    turn_index: int | None = None
    parent_turn_id: str | None = None


class SessionResponse(BaseModel):
    """Full session message history response."""

    session_id: str
    messages: list[ChatMessageResponse]
    metadata: dict[str, Any] = Field(default_factory=dict)


class ChatRequest(BaseModel):
    """One Web chat request."""

    session_id: str
    message: str
    model: str | None = None


class ChatResponse(BaseModel):
    """Assistant result for one Web chat request."""

    session_id: str
    assistant: ChatMessageResponse


class ModelsResponse(BaseModel):
    """Current endpoint and selectable models for the Web UI."""

    endpoint: str
    current_model: str
    models: list[str]


class ModelPreferenceRequest(BaseModel):
    """Requested model preference for the current endpoint."""

    model: str


class SessionRenameRequest(BaseModel):
    """Requested display title for a session."""

    title: str


class SessionMutationResponse(BaseModel):
    """Result for session metadata or delete mutations."""

    session_id: str
    status: str
    title: str = ""
