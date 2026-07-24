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
    channel: str = ""
    conversation_type: str = ""
    continuation_mode: str = "writable"


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

    session_id: str = ""
    model: str


class SessionRenameRequest(BaseModel):
    """Requested display title for a session."""

    title: str


class SessionMutationResponse(BaseModel):
    """Result for session metadata or delete mutations."""

    session_id: str
    status: str
    title: str = ""


class LoginRequest(BaseModel):
    """Local username/password login request."""

    username: str
    password: str


class BootstrapOwnerRequest(BaseModel):
    """One-time Owner registration request protected by a deployment secret."""

    setup_token: str
    password: str


class RegisterUserRequest(BaseModel):
    """Public self-service user registration request."""

    username: str
    password: str


class ProfileUpdateRequest(BaseModel):
    """Current-user profile fields allowed for self-service updates."""

    display_name: str


class PasswordChangeRequest(BaseModel):
    """Authenticated password rotation request."""

    current_password: str
    new_password: str


class PublicUserResponse(BaseModel):
    """User fields safe for browser and admin responses."""

    id: str
    username: str
    display_name: str
    status: str
    roles: list[str] = Field(default_factory=list)
    can_manage_admins: bool = False


class AuthMeResponse(BaseModel):
    """Current actor summary."""

    user: PublicUserResponse
    permissions: list[str]


class AuthMutationResponse(BaseModel):
    """Simple login/logout mutation status."""

    status: str
    user: PublicUserResponse | None = None


class ChannelLinkCodeResponse(BaseModel):
    """One short-lived manual external identity binding code."""

    code: str
    expires_at: str
    command: str


class ChannelAuthorizationRequest(BaseModel):
    """Opaque token returned by a channel-initiated Web authorization link."""

    token: str


class ChannelAuthorizationResponse(BaseModel):
    """Result of binding the external identity to the current Web user."""

    status: str
    channel: str


class ChannelBindingResponse(BaseModel):
    """One current user's safely presented external channel binding."""

    binding_id: str
    channel: str
    display_name: str = ""
    linked_at: str = ""


class ChannelBindingsResponse(BaseModel):
    """Current user's active external channel bindings."""

    bindings: list[ChannelBindingResponse]


class WeixinChannelStatusResponse(BaseModel):
    """Current user's Weixin account state without platform identifiers."""

    status: str
    linked_at: str = ""


class WeixinBindingAttemptResponse(BaseModel):
    """Short-lived QR binding state; responses must use no-store caching."""

    attempt_id: str
    status: str
    expires_at: str
    qr_data: str = ""
    error_code: str = ""


class AdminUsersResponse(BaseModel):
    """Admin user list response."""

    users: list[PublicUserResponse]


class AdminUserCreateRequest(BaseModel):
    """Create one local DB user."""

    username: str
    display_name: str = ""
    password: str
    roles: list[str] = Field(default_factory=lambda: ["viewer"])


class AdminUserUpdateRequest(BaseModel):
    """Update status, display name, or role assignments."""

    display_name: str | None = None
    status: str | None = None
    roles: list[str] | None = None
    can_manage_admins: bool | None = None


class RoleResponse(BaseModel):
    """Role and permission assignment shown in the admin view."""

    id: str
    key: str
    name: str
    description: str
    is_builtin: bool
    permission_keys: list[str]


class RolesResponse(BaseModel):
    """Role list response."""

    roles: list[RoleResponse]
    permissions: list[str] = Field(default_factory=list)


class RoleUpdateRequest(BaseModel):
    """Replacement permission list for one role."""

    permission_keys: list[str]


class AuditEventsResponse(BaseModel):
    """Bounded safe audit event list."""

    events: list[dict[str, Any]]


class ToolConfirmationResponse(BaseModel):
    """Pending or decided confirmation shown to the requesting actor."""

    id: str
    session_id: str
    turn_id: str
    tool_name: str
    risk_level: str
    command_preview: str = ""
    status: str
    expires_at: str


class ToolConfirmationsResponse(BaseModel):
    """List of visible confirmations."""

    confirmations: list[ToolConfirmationResponse]


class ConfirmationMutationResponse(BaseModel):
    """Approval or denial result."""

    confirmation_id: str
    status: str
