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


class RegistrationPolicyResponse(BaseModel):
    """Public-safe projection of the self-service registration policy."""

    registration_enabled: bool


class RegistrationPolicyUpdateRequest(BaseModel):
    """Owner-only registration policy mutation."""

    registration_enabled: bool


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


class AdminUserDeleteRequest(BaseModel):
    """Confirm permanent deletion using the target username."""

    confirmation: str


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


class SkillSummaryResponse(BaseModel):
    """Actor-visible Skill metadata safe for the administration UI."""

    qualified_name: str
    source: str
    name: str
    description: str = ""
    executable: bool = False


class SkillSourceStatusResponse(BaseModel):
    """Persistent Skill source status without paths, URLs, or raw stderr."""

    source: str
    enabled: bool = True
    sync_enabled: bool = True
    configured_target: str = ""
    current_commit: str = ""
    last_sync_started_at: str = ""
    last_sync_finished_at: str = ""
    last_success_at: str = ""
    last_status: str = "unknown"
    health: str = "unknown"
    skill_count: int = 0
    load_error_count: int = 0
    last_error_code: str = ""
    last_error_message_safe: str = ""


class SkillSourcesResponse(BaseModel):
    """Skill source management read model and actor-visible catalog."""

    status: str = "ok"
    sources: list[SkillSourceStatusResponse] = Field(default_factory=list)
    skills: list[SkillSummaryResponse] = Field(default_factory=list)


class McpServerMonitorResponse(BaseModel):
    """Credential-free aggregate health for one configured MCP Server."""

    server_id: str
    state: str
    tool_count: int = 0
    error_code: str = ""
    call_count: int = 0
    success_count: int = 0
    failure_count: int = 0
    cancelled_count: int = 0
    last_tool_error_code: str = ""
    last_connection_state: str = ""
    last_connection_at: float = 0.0
    last_connection_reason_code: str = ""
    oauth_state: str = "disabled"


class McpMonitorResponse(BaseModel):
    """Current bounded MCP runtime projection for the administration UI."""

    status: str
    catalog_version: int = 0
    generated_at: float = 0.0
    active_calls: int = 0
    catalog_refresh_count: int = 0
    list_changed_count: int = 0
    reconnect_count: int = 0
    servers: list[McpServerMonitorResponse] = Field(default_factory=list)


class XhsReadonlyAdminStatusResponse(BaseModel):
    """Credential-free Owner projection for the local Xiaohongshu MCP."""

    server_id: str = "xhs-readonly"
    state: str = "unknown"
    code: str = ""
    message: str = ""
    enabled: bool = False
    login_supported: bool = False
    login_in_progress: bool = False
    restart_supported: bool = False
    cookie_updated_at: str = ""


class OperationsTerminalResponse(BaseModel):
    """Non-secret link projection for the independently protected Ops UI."""

    enabled: bool = False
    configured: bool = False
    url: str = ""
    presentation: str = "both"
    mode: str = ""
    target_type: str = ""
    target_name: str = ""


class RoleUpdateRequest(BaseModel):
    """Replacement permission list for one role."""

    permission_keys: list[str]


class AuditEventsResponse(BaseModel):
    """Bounded safe audit event list."""

    events: list[dict[str, Any]]
    next_cursor: str = ""
    has_more: bool = False


class MonitorActivityResponse(BaseModel):
    """Existing structured Runtime Activity facts for the admin monitor."""

    recent_turns: list[dict[str, Any]] = Field(default_factory=list)
    recent_tools: list[dict[str, Any]] = Field(default_factory=list)
    summary: dict[str, int] = Field(default_factory=dict)


class AdminMonitorResponse(BaseModel):
    """Current Gateway, capability and Activity read model without diagnosis."""

    gateway: dict[str, Any] = Field(default_factory=dict)
    capabilities: dict[str, dict[str, Any]] = Field(default_factory=dict)
    activity: MonitorActivityResponse


class SystemDiagnosticsResponse(BaseModel):
    """Bounded privileged incident and timeline read model."""

    status: str
    window_minutes: int
    filters: dict[str, Any] = Field(default_factory=dict)
    summary: dict[str, int] = Field(default_factory=dict)
    incidents: list[dict[str, Any]] = Field(default_factory=list)
    timeline: list[dict[str, Any]] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)


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


class TravelPlanSummaryResponse(BaseModel):
    """Metadata-only projection for one actor-owned travel plan."""

    plan_id: str
    owner_user_id: str
    source_session_id: str
    source_turn_id: str
    schema_version: str
    title: str
    destination_summary: str
    created_at: str
    updated_at: str


class TravelPlansResponse(BaseModel):
    """Current actor's private travel plan list."""

    plans: list[TravelPlanSummaryResponse] = Field(default_factory=list)


class TravelPlanResponse(BaseModel):
    """One complete current-actor TravelPlanV1."""

    plan: dict[str, Any]


class TravelPlanMutationResponse(BaseModel):
    """Travel plan delete mutation result."""

    plan_id: str
    status: str


class TravelRequirementExtractionRequest(BaseModel):
    """Untrusted natural-language request for a reviewable travel draft."""

    text: str = Field(min_length=1, max_length=4000)


class TravelRequirementExtractionResponse(BaseModel):
    """Strict semantic extraction projection without starting a plan."""

    draft: dict[str, Any]
    missing_fields: list[str] = Field(default_factory=list)


class TravelConversationMessageRequest(BaseModel):
    """One bounded user-visible requirement message persisted before planning."""

    role: str
    content: str = Field(min_length=1, max_length=2000)


class TravelConversationRequest(BaseModel):
    """Bounded requirement conversation for one newly created travel Session."""

    messages: list[TravelConversationMessageRequest] = Field(min_length=1, max_length=20)
    draft: dict[str, Any] = Field(default_factory=dict)


class TravelConversationResponse(BaseModel):
    """Idempotent persistence result for a travel requirement conversation."""

    session_id: str
    message_count: int
    status: str


class TravelDraftResponse(BaseModel):
    """Refresh-safe collecting state for one actor-owned travel Session."""

    session_id: str
    messages: list[TravelConversationMessageRequest] = Field(default_factory=list)
    draft: dict[str, Any] = Field(default_factory=dict)
    phase: str = "intake"
    handoff_question: str = ""


class TravelPlanningConfirmationRequest(BaseModel):
    """Final reviewed draft used to open formal planning capabilities."""

    draft: dict[str, Any]


class TravelPlanningConfirmationResponse(BaseModel):
    """Public result of the intake-to-planning phase transition."""

    session_id: str
    phase: str
    status: str


class TravelWorkItemResponse(BaseModel):
    """One unified sidebar item across the travel lifecycle."""

    session_id: str
    plan_id: str = ""
    status: str
    title: str
    preview: str
    updated_at: str
    error_code: str = ""


class TravelWorkItemsResponse(BaseModel):
    """Current actor's unified private travel work list."""

    items: list[TravelWorkItemResponse] = Field(default_factory=list)


class TravelWorkItemMutationResponse(BaseModel):
    """Delete result for an unfinished travel work item."""

    session_id: str
    status: str


class TravelGenerationStatusResponse(BaseModel):
    """Actor-owned travel generation state safe for browser recovery."""

    status: str
    session_id: str = ""
    turn_id: str = ""
    plan_id: str = ""
    error_code: str = ""


class TravelCandidateSelectionRequest(BaseModel):
    """One candidate chosen from an actor-owned pending travel review."""

    candidate_id: str = Field(min_length=1, max_length=100)


class TravelCandidateReviewResponse(BaseModel):
    """Refresh-safe bounded candidate review without source bodies."""

    session_id: str
    status: str
    recommended_candidate_id: str = ""
    selected_candidate_id: str = ""
    candidates: list[dict[str, Any]] = Field(default_factory=list)
    created_at: str = ""
    updated_at: str = ""
