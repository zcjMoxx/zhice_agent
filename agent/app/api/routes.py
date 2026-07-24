"""FastAPI routes for the local Web API."""

from __future__ import annotations

import inspect
import json
from datetime import UTC, datetime
from queue import Queue
from threading import Thread
from typing import Any

from fastapi import APIRouter, Request, Response
from fastapi.responses import StreamingResponse

from agent.app.api.schemas import (
    AdminUserCreateRequest,
    AdminUsersResponse,
    AdminUserUpdateRequest,
    AuditEventsResponse,
    AuthMeResponse,
    AuthMutationResponse,
    BootstrapOwnerRequest,
    ChannelAuthorizationRequest,
    ChannelAuthorizationResponse,
    ChannelBindingResponse,
    ChannelBindingsResponse,
    ChannelLinkCodeResponse,
    ChatMessageResponse,
    ChatRequest,
    ChatResponse,
    ConfirmationMutationResponse,
    LoginRequest,
    ModelPreferenceRequest,
    ModelsResponse,
    PasswordChangeRequest,
    ProfileUpdateRequest,
    PublicUserResponse,
    RegisterUserRequest,
    RoleResponse,
    RolesResponse,
    RoleUpdateRequest,
    SessionMutationResponse,
    SessionRenameRequest,
    SessionResponse,
    SessionsResponse,
    SessionSummaryResponse,
    ToolConfirmationResponse,
    ToolConfirmationsResponse,
)
from agent.app.auth import AuthHttpError, local_operator_actor
from agent.app.runtime import ChatTurnResult, ModelState
from agent.auth.schema import PERMISSIONS
from agent.auth.session_access import SessionAccessError
from agent.auth.store import AuthStoreError
from agent.core.turns import new_turn_id
from agent.message import Message
from agent.protocols.auth import AuditEvent
from agent.protocols.errors import ErrorCode
from agent.protocols.llm import LLMConfigurationError, LLMProviderError
from agent.protocols.runtime_event import is_runtime_event_payload

router = APIRouter(prefix="/api")


class ApiError(Exception):
    """Exception carrying a stable API error code."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        status_code: int = 500,
        details: dict[str, Any] | None = None,
    ):
        super().__init__(message)
        self.code = str(code)
        self.message = message
        self.status_code = status_code
        self.details = dict(details or {})


@router.post("/auth/bootstrap", response_model=AuthMutationResponse)
def bootstrap_owner(
    request_body: BootstrapOwnerRequest,
    request: Request,
    response: Response,
) -> AuthMutationResponse:
    """Create the unique Owner account and sign it in."""

    auth = _auth_service(request, required=True)
    try:
        login_result = auth.bootstrap_owner(
            request_body.password,
            request_body.setup_token,
            channel="web",
            user_agent_preview=request.headers.get("user-agent", ""),
            remote_addr_preview=request.client.host if request.client else "",
            request_id=_request_id(request),
            route=request.url.path,
        )
        auth.set_auth_cookie(response, login_result, secure=request.url.scheme == "https")
    except AuthHttpError as exc:
        raise _api_error_from_auth(exc) from exc
    return AuthMutationResponse(
        status="authenticated",
        user=_public_actor(login_result.actor),
    )


@router.post("/auth/login", response_model=AuthMutationResponse)
def login(request_body: LoginRequest, request: Request, response: Response) -> AuthMutationResponse:
    """Authenticate a local DB user and set the opaque HttpOnly cookie."""

    auth = _auth_service(request, required=True)
    try:
        login_result = auth.login(
            request_body.username.strip(),
            request_body.password,
            channel="web",
            user_agent_preview=request.headers.get("user-agent", ""),
            remote_addr_preview=request.client.host if request.client else "",
            request_id=_request_id(request),
            route=request.url.path,
        )
        auth.set_auth_cookie(response, login_result, secure=request.url.scheme == "https")
    except AuthHttpError as exc:
        raise _api_error_from_auth(exc) from exc
    return AuthMutationResponse(
        status="authenticated",
        user=_public_actor(login_result.actor),
    )


@router.post("/auth/register", response_model=AuthMutationResponse)
def register_user(
    request_body: RegisterUserRequest,
    request: Request,
    response: Response,
) -> AuthMutationResponse:
    """Register one public viewer account and sign it in."""

    auth = _auth_service(request, required=True)
    try:
        login_result = auth.register_user(
            request_body.username.strip(),
            request_body.password,
            channel="web",
            user_agent_preview=request.headers.get("user-agent", ""),
            remote_addr_preview=request.client.host if request.client else "",
            request_id=_request_id(request),
            route=request.url.path,
        )
        auth.set_auth_cookie(response, login_result, secure=request.url.scheme == "https")
    except AuthHttpError as exc:
        raise _api_error_from_auth(exc) from exc
    return AuthMutationResponse(
        status="authenticated",
        user=_public_actor(login_result.actor),
    )


@router.post("/auth/logout", response_model=AuthMutationResponse)
def logout(request: Request, response: Response) -> AuthMutationResponse:
    """Revoke the current auth session and clear its browser cookie."""

    auth = _auth_service(request, required=True)
    auth.logout(request)
    auth.clear_auth_cookie(response)
    return AuthMutationResponse(status="logged_out")


@router.get("/auth/me", response_model=AuthMeResponse)
def read_current_user(request: Request) -> AuthMeResponse:
    """Return the current user and explicit permission summary."""

    actor = _actor(request, channel="rest")
    return AuthMeResponse(
        user=_public_actor(actor),
        permissions=sorted(actor.permission_keys),
    )


@router.patch("/auth/profile", response_model=AuthMeResponse)
def update_current_user_profile(
    request_body: ProfileUpdateRequest,
    request: Request,
) -> AuthMeResponse:
    """Update the current user's self-service profile fields."""

    actor = _actor(request, channel="rest")
    auth = _auth_service(request, required=True)
    try:
        user = auth.update_profile(
            actor,
            request_body.display_name,
            request_id=_request_id(request),
            route=request.url.path,
        )
    except AuthHttpError as exc:
        raise _api_error_from_auth(exc) from exc
    return AuthMeResponse(
        user=_public_user(user),
        permissions=sorted(actor.permission_keys),
    )


@router.post("/auth/password", response_model=AuthMutationResponse)
def change_current_user_password(
    request_body: PasswordChangeRequest,
    request: Request,
    response: Response,
) -> AuthMutationResponse:
    """Rotate password, revoke all sessions, and require a new login."""

    actor = _actor(request, channel="rest")
    auth = _auth_service(request, required=True)
    try:
        auth.change_password(
            actor,
            request_body.current_password,
            request_body.new_password,
            request_id=_request_id(request),
            route=request.url.path,
        )
    except AuthHttpError as exc:
        raise _api_error_from_auth(exc) from exc
    auth.clear_auth_cookie(response)
    return AuthMutationResponse(status="reauthentication_required")


@router.post("/channels/qq/link-code", response_model=ChannelLinkCodeResponse)
def create_qq_link_code(request: Request) -> ChannelLinkCodeResponse:
    """Create a manual QQ binding code for the current authenticated user."""

    actor = _actor(request, channel="rest")
    runtime = _runtime(request)
    identity = _channel_identity(runtime)
    account_key = _qq_account_key(runtime)
    link = identity.create_link_code(str(actor.user_id), "qq", account_key)
    auth = _auth_service(request, required=True)
    if auth.audit_sink is not None:
        auth.audit_sink.record(
            AuditEvent(
                action="external_identity.link_code_created",
                resource_type="external_identity",
                actor=actor,
                request_id=_request_id(request),
                channel="rest",
                route=request.url.path,
                decision="allow",
                metadata={"target_channel": "qq"},
            )
        )
    return ChannelLinkCodeResponse(
        code=link.code,
        expires_at=link.expires_at,
        command=f"/bind {link.code}",
    )


@router.post("/channels/qq/authorize", response_model=ChannelAuthorizationResponse)
def authorize_qq_identity(
    request_body: ChannelAuthorizationRequest,
    request: Request,
) -> ChannelAuthorizationResponse:
    """Consume a QQ-initiated authorization token for the current Web user."""

    actor = _actor(request, channel="rest")
    runtime = _runtime(request)
    identity = _channel_identity(runtime)
    if not identity.authorize(request_body.token, actor):
        raise ApiError(
            "CHANNEL_BIND_TOKEN_INVALID",
            "QQ binding link is invalid, expired, or already used",
            status_code=400,
        )
    auth = _auth_service(request, required=True)
    if auth.audit_sink is not None:
        auth.audit_sink.record(
            AuditEvent(
                action="external_identity.linked",
                resource_type="external_identity",
                actor=actor,
                request_id=_request_id(request),
                channel="rest",
                route=request.url.path,
                decision="allow",
                metadata={"target_channel": "qq", "method": "web_authorization"},
            )
        )
    return ChannelAuthorizationResponse(status="bound", channel="qq")


@router.get("/channels/bindings", response_model=ChannelBindingsResponse)
def list_channel_bindings(request: Request) -> ChannelBindingsResponse:
    """Return the current user's active channel bindings without raw platform ids."""

    actor = _actor(request, channel="rest")
    identity = _channel_identity(_runtime(request))
    return ChannelBindingsResponse(
        bindings=[
            ChannelBindingResponse(
                binding_id=item.binding_id,
                channel=item.channel,
                display_name=item.display_name,
                linked_at=item.linked_at,
            )
            for item in identity.list_bindings(actor)
        ]
    )


@router.delete("/channels/bindings/{binding_id}", response_model=AuthMutationResponse)
def unlink_channel_binding(binding_id: str, request: Request) -> AuthMutationResponse:
    """Disable one binding only when it belongs to the current user."""

    actor = _actor(request, channel="rest")
    identity = _channel_identity(_runtime(request))
    if not identity.unlink(actor, binding_id):
        raise ApiError(
            "CHANNEL_BINDING_NOT_FOUND",
            "Channel binding not found",
            status_code=404,
        )
    auth = _auth_service(request, required=True)
    if auth.audit_sink is not None:
        auth.audit_sink.record(
            AuditEvent(
                action="external_identity.unlinked",
                resource_type="external_identity",
                resource_id=binding_id,
                actor=actor,
                request_id=_request_id(request),
                channel="rest",
                route=request.url.path,
                decision="allow",
            )
        )
    return AuthMutationResponse(status="unbound")


@router.get("/sessions", response_model=SessionsResponse)
def list_sessions(request: Request) -> SessionsResponse:
    """Return workspace sessions ordered by recent activity."""

    runtime = _runtime(request)
    actor = _actor(request, channel="rest")
    try:
        summaries = _runtime_call(
            runtime,
            "list_sessions",
            actor,
            request_id=_request_id(request),
        )
    except Exception as exc:
        raise _api_error_from_exception(exc) from exc
    return SessionsResponse(
        sessions=[
            SessionSummaryResponse(
                session_id=summary.session_id,
                preview=summary.preview,
                updated_at=_format_timestamp(summary.updated_at),
                message_count=summary.message_count,
                title=summary.title,
                channel=summary.channel,
                conversation_type=summary.conversation_type,
                continuation_mode=summary.continuation_mode,
            )
            for summary in summaries
        ]
    )


@router.post("/sessions/{session_id}/fork", response_model=SessionSummaryResponse)
def fork_session_to_web(session_id: str, request: Request) -> SessionSummaryResponse:
    """Copy one read-only external group session into a private Web session."""

    runtime = _runtime(request)
    actor = _actor(request, channel="rest")
    try:
        summary = _runtime_call(
            runtime,
            "fork_session_to_web",
            actor,
            session_id,
            request_id=_request_id(request),
        )
    except Exception as exc:
        raise _api_error_from_exception(exc) from exc
    return SessionSummaryResponse(
        session_id=summary.session_id,
        preview=summary.preview,
        updated_at=_format_timestamp(summary.updated_at),
        message_count=summary.message_count,
        title=summary.title,
        channel=summary.channel,
        conversation_type=summary.conversation_type,
        continuation_mode=summary.continuation_mode,
    )


@router.get("/sessions/{session_id}", response_model=SessionResponse)
def read_session(session_id: str, request: Request) -> SessionResponse:
    """Return persisted messages for one session."""

    runtime = _runtime(request)
    actor = _actor(request, channel="rest")
    try:
        state = _runtime_call(
            runtime,
            "load_session",
            actor,
            session_id,
            request_id=_request_id(request),
        )
    except Exception as exc:
        raise _api_error_from_exception(exc) from exc
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
    actor = _actor(request, channel="rest")
    title = request_body.title.strip()
    if not title:
        raise ApiError(
            ErrorCode.REQUEST_VALIDATION_FAILED,
            "title is required",
            status_code=400,
            details={"field": "title"},
        )
    try:
        summary = _runtime_call(
            runtime,
            "rename_session",
            actor,
            session_id,
            title,
            request_id=_request_id(request),
        )
    except Exception as exc:
        raise _api_error_from_exception(exc) from exc
    return SessionMutationResponse(
        session_id=session_id,
        status="renamed",
        title=summary.title,
    )


@router.delete("/sessions/{session_id}", response_model=SessionMutationResponse)
def delete_session(session_id: str, request: Request) -> SessionMutationResponse:
    """Delete a session and its metadata."""

    runtime = _runtime(request)
    actor = _actor(request, channel="rest")
    try:
        _runtime_call(
            runtime,
            "delete_session",
            actor,
            session_id,
            request_id=_request_id(request),
        )
    except Exception as exc:
        raise _api_error_from_exception(exc) from exc
    return SessionMutationResponse(session_id=session_id, status="deleted")


@router.post("/chat", response_model=ChatResponse)
def chat(request_body: ChatRequest, request: Request) -> ChatResponse:
    """Run one Web chat turn through the Agent loop."""

    session_id = request_body.session_id.strip()
    message = request_body.message.strip()
    if not session_id:
        raise ApiError(
            ErrorCode.REQUEST_VALIDATION_FAILED,
            "session_id is required",
            status_code=400,
            details={"field": "session_id"},
        )
    if not message:
        raise ApiError(
            ErrorCode.REQUEST_VALIDATION_FAILED,
            "message is required",
            status_code=400,
            details={"field": "message"},
        )

    runtime = _runtime(request)
    actor = _actor(request, channel="rest")
    try:
        selected_model = (request_body.model or "").strip()
        if _should_apply_model_preference(message, selected_model):
            _set_model_preference(
                runtime,
                actor,
                session_id,
                selected_model,
                request_id=_request_id(request),
            )
        result = _run_chat_events(runtime, actor, session_id, message, request_id=_request_id(request))
    except Exception as exc:
        raise _api_error_from_exception(exc) from exc

    return ChatResponse(
        session_id=session_id,
        assistant=ChatMessageResponse(
            role="assistant",
            content=result.content,
            turn_id=result.turn_id or None,
        ),
    )


@router.post("/chat/stream")
def chat_stream(request_body: ChatRequest, request: Request) -> StreamingResponse:
    """Run one Web chat turn and stream client-friendly SSE events."""

    session_id = request_body.session_id.strip()
    message = request_body.message.strip()
    if not session_id:
        raise ApiError(
            ErrorCode.REQUEST_VALIDATION_FAILED,
            "session_id is required",
            status_code=400,
            details={"field": "session_id"},
        )
    if not message:
        raise ApiError(
            ErrorCode.REQUEST_VALIDATION_FAILED,
            "message is required",
            status_code=400,
            details={"field": "message"},
        )

    runtime = _runtime(request)
    actor = _actor(request, channel="sse")
    try:
        selected_model = (request_body.model or "").strip()
        if _should_apply_model_preference(message, selected_model):
            _set_model_preference(
                runtime,
                actor,
                session_id,
                selected_model,
                request_id=_request_id(request),
            )
    except Exception as exc:
        raise _api_error_from_exception(exc) from exc

    return StreamingResponse(
        _chat_stream_events(
            runtime,
            actor,
            session_id,
            message,
            request_id=_request_id(request),
        ),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache"},
    )


@router.get("/models", response_model=ModelsResponse)
def read_models(request: Request, session_id: str = "") -> ModelsResponse:
    """Return the current endpoint and its selectable models."""

    runtime = _runtime(request)
    actor = _actor(request, channel="rest")
    if _auth_service(request) is not None and not session_id.strip():
        raise ApiError(
            ErrorCode.REQUEST_VALIDATION_FAILED,
            "session_id is required",
            status_code=400,
            details={"field": "session_id"},
        )
    try:
        state = _runtime_model_state(
            runtime,
            actor,
            session_id.strip(),
            request_id=_request_id(request),
        )
        return _model_response(state)
    except Exception as exc:
        raise _api_error_from_exception(exc) from exc


@router.post("/model/preference", response_model=ModelsResponse)
def set_model_preference(
    request_body: ModelPreferenceRequest,
    request: Request,
) -> ModelsResponse:
    """Set the preferred model for the current endpoint."""

    model = request_body.model.strip()
    session_id = request_body.session_id.strip()
    if not model:
        raise ApiError(
            ErrorCode.REQUEST_VALIDATION_FAILED,
            "model is required",
            status_code=400,
            details={"field": "model"},
        )
    if _auth_service(request) is not None and not session_id:
        raise ApiError(
            ErrorCode.REQUEST_VALIDATION_FAILED,
            "session_id is required",
            status_code=400,
            details={"field": "session_id"},
        )
    runtime = _runtime(request)
    actor = _actor(request, channel="rest")
    try:
        return _model_response(
            _set_model_preference(
                runtime,
                actor,
                session_id,
                model,
                request_id=_request_id(request),
            )
        )
    except Exception as exc:
        raise _api_error_from_exception(exc) from exc


@router.delete("/model/preference", response_model=ModelsResponse)
def reset_model_preference(request: Request, session_id: str) -> ModelsResponse:
    """Clear only the current session model preference."""

    runtime = _runtime(request)
    actor = _actor(request, channel="rest")
    try:
        return _model_response(
            _runtime_call(
                runtime,
                "reset_model_preference",
                actor,
                session_id,
                request_id=_request_id(request),
            )
        )
    except Exception as exc:
        raise _api_error_from_exception(exc) from exc


@router.get("/admin/users", response_model=AdminUsersResponse)
def list_users(request: Request) -> AdminUsersResponse:
    """List public user fields for authorized administrators."""

    _actor(request, "auth.users.read", channel="rest")
    auth = _auth_service(request, required=True)
    users = auth.store.list_users()
    return AdminUsersResponse(
        users=[
            _public_user(user, auth)
            for user in users
        ]
    )


@router.post("/admin/users", response_model=PublicUserResponse)
def create_user(request_body: AdminUserCreateRequest, request: Request) -> PublicUserResponse:
    """Create a local DB user with explicit role assignments."""

    actor = _actor(request, "auth.users.manage", channel="rest")
    auth = _auth_service(request, required=True)
    try:
        user = auth.create_managed_user(
            actor,
            request_body.username,
            request_body.display_name,
            request_body.password,
            request_body.roles,
        )
    except AuthHttpError as exc:
        raise _api_error_from_auth(exc) from exc
    except ValueError as exc:
        raise ApiError(ErrorCode.REQUEST_VALIDATION_FAILED, str(exc), status_code=400) from exc
    _ = actor
    if auth.audit_sink is not None:
        auth.audit_sink.record(
            AuditEvent(
                action="user.created",
                resource_type="user",
                actor=actor,
                resource_id=user.id,
                channel="rest",
                decision="allow",
            )
        )
    return _public_user(user, auth)


@router.patch("/admin/users/{user_id}", response_model=PublicUserResponse)
def update_user(
    user_id: str,
    request_body: AdminUserUpdateRequest,
    request: Request,
) -> PublicUserResponse:
    """Update user status, display name, or role assignments."""

    actor = _actor(request, "auth.users.manage", channel="rest")
    auth = _auth_service(request, required=True)
    try:
        user = auth.update_managed_user(
            actor,
            user_id,
            display_name=request_body.display_name,
            status=request_body.status,
            role_keys=request_body.roles,
            can_manage_admins=request_body.can_manage_admins,
        )
    except AuthHttpError as exc:
        raise _api_error_from_auth(exc) from exc
    if auth.audit_sink is not None:
        if request_body.can_manage_admins is not None:
            action = (
                "admin.management_delegated"
                if request_body.can_manage_admins
                else "admin.management_revoked"
            )
        elif request_body.roles is not None:
            action = "admin.role_updated" if "admin" in request_body.roles else "user.roles_updated"
        else:
            action = "user.disabled" if user.status == "disabled" else "user.updated"
        auth.audit_sink.record(
            AuditEvent(
                action=action,
                resource_type="user",
                actor=actor,
                resource_id=user.id,
                channel="rest",
                decision="allow",
                metadata={
                    "roles": list(user.role_keys),
                    "can_manage_admins": auth.can_manage_admins(user),
                },
            )
        )
    return _public_user(user, auth)


@router.get("/admin/roles", response_model=RolesResponse)
def list_roles(request: Request) -> RolesResponse:
    """Return roles and current permission assignments."""

    _actor(request, "auth.roles.read", channel="rest")
    auth = _auth_service(request, required=True)
    return RolesResponse(
        roles=[RoleResponse(**role) for role in auth.store.list_roles()],
        permissions=sorted(PERMISSIONS),
    )


@router.patch("/admin/roles/{role_id}", response_model=RoleResponse)
def update_role(
    role_id: str,
    request_body: RoleUpdateRequest,
    request: Request,
) -> RoleResponse:
    """Replace one role's permission assignment set."""

    actor = _actor(request, "auth.roles.manage", channel="rest")
    auth = _auth_service(request, required=True)
    try:
        role = auth.store.update_role_permissions(role_id, request_body.permission_keys)
    except AuthStoreError as exc:
        raise ApiError(ErrorCode.REQUEST_VALIDATION_FAILED, str(exc), status_code=400) from exc
    if auth.audit_sink is not None:
        auth.audit_sink.record(
            AuditEvent(
                action="role.updated",
                resource_type="role",
                actor=actor,
                resource_id=role_id,
                channel="rest",
                decision="allow",
                metadata={"permission_count": len(request_body.permission_keys)},
            )
        )
    return RoleResponse(**role)


@router.get("/audit/events", response_model=AuditEventsResponse)
def list_audit_events(
    request: Request,
    limit: int = 100,
    session_id: str = "",
    turn_id: str = "",
) -> AuditEventsResponse:
    """Return bounded audit events for actors with audit.read."""

    actor = _actor(request, "audit.read", channel="rest")
    auth = _auth_service(request, required=True)
    events = auth.store.list_audit_events(
        limit=limit,
        session_id=session_id,
        turn_id=turn_id,
    )
    if auth.audit_sink is not None:
        auth.audit_sink.record(
            AuditEvent(
                action="audit.read",
                resource_type="audit_events",
                actor=actor,
                request_id=_request_id(request),
                channel="rest",
                route=request.url.path,
                decision="allow",
                metadata={
                    "limit": limit,
                    "session_filter": bool(session_id),
                    "turn_filter": bool(turn_id),
                    "result_count": len(events),
                },
            )
        )
    return AuditEventsResponse(
        events=events
    )


@router.get("/tool-confirmations", response_model=ToolConfirmationsResponse)
def list_tool_confirmations(request: Request) -> ToolConfirmationsResponse:
    """List pending confirmations visible to the current actor."""

    actor = _actor(request, channel="rest")
    runtime = _runtime(request)
    items = _runtime_call(runtime, "list_tool_confirmations", actor)
    return ToolConfirmationsResponse(
        confirmations=[ToolConfirmationResponse(**item) for item in items]
    )


@router.post(
    "/tool-confirmations/{confirmation_id}/approve",
    response_model=ConfirmationMutationResponse,
)
def approve_tool_confirmation(
    confirmation_id: str,
    request: Request,
) -> ConfirmationMutationResponse:
    """Approve the exact pending tool call and argument hash."""

    actor = _actor(request, channel="rest")
    runtime = _runtime(request)
    status = _runtime_call(runtime, "decide_tool_confirmation", actor, confirmation_id, True)
    return ConfirmationMutationResponse(confirmation_id=confirmation_id, status=status)


@router.post(
    "/tool-confirmations/{confirmation_id}/deny",
    response_model=ConfirmationMutationResponse,
)
def deny_tool_confirmation(
    confirmation_id: str,
    request: Request,
) -> ConfirmationMutationResponse:
    """Deny a pending tool call."""

    actor = _actor(request, channel="rest")
    runtime = _runtime(request)
    status = _runtime_call(runtime, "decide_tool_confirmation", actor, confirmation_id, False)
    return ConfirmationMutationResponse(confirmation_id=confirmation_id, status=status)


def _runtime(request: Request):
    """Return the runtime dependency object stored on the FastAPI app."""

    runtime = getattr(request.app.state, "runtime", None)
    if runtime is None:
        raise ApiError(ErrorCode.CONFIG_INVALID, "runtime is not configured", status_code=500)
    return runtime


def _auth_service(request: Request, *, required: bool = False):
    """Return the app auth service, with a stable setup error when required."""

    auth = getattr(request.app.state, "auth_service", None)
    if auth is None:
        runtime = getattr(request.app.state, "runtime", None)
        auth = getattr(runtime, "auth", None)
    if auth is None and required:
        raise ApiError(
            ErrorCode.AUTH_UNAVAILABLE,
            "Authentication service is not configured",
            status_code=503,
        )
    return auth


def _channel_identity(runtime):
    identity = getattr(runtime, "channel_identity", None)
    if identity is None:
        raise ApiError(
            ErrorCode.CONFIG_INVALID,
            "External channel identity service is unavailable",
            status_code=503,
        )
    return identity


def _qq_account_key(runtime) -> str:
    config = getattr(runtime, "channel_config", None)
    accounts = tuple(getattr(getattr(config, "qq", None), "accounts", ()) or ())
    if not accounts:
        raise ApiError(
            ErrorCode.CONFIG_INVALID,
            "QQ channel account is not configured",
            status_code=503,
        )
    return "main" if any(account.key == "main" for account in accounts) else str(accounts[0].key)


def _actor(request: Request, permission_key: str | None = None, *, channel: str):
    """Resolve an authenticated actor and optionally check one privileged action."""

    auth = _auth_service(request)
    if auth is None:
        actor = local_operator_actor(channel=channel)
    else:
        actor = None
        try:
            actor = getattr(request.state, "actor", None) or auth.resolve_request_actor(
                request, channel=channel
            )
            if actor.channel != channel:
                actor = actor.__class__(
                    actor_type=actor.actor_type,
                    user_id=actor.user_id,
                    username=actor.username,
                    display_name=actor.display_name,
                    role_keys=actor.role_keys,
                    permission_keys=actor.permission_keys,
                    channel=channel,
                    auth_session_id=actor.auth_session_id,
                )
            if permission_key:
                auth.require_permission(actor, permission_key)
        except AuthHttpError as exc:
            if auth.audit_sink is not None:
                auth.audit_sink.record(
                    AuditEvent(
                        action="auth.request_denied",
                        resource_type="http_request",
                        actor=actor,
                        request_id=_request_id(request),
                        channel=channel,
                        route=request.url.path,
                        status_code=exc.status_code,
                        decision="deny",
                        reason_code=exc.code,
                        metadata={"permission_key": permission_key or ""},
                    )
                )
            raise _api_error_from_auth(exc) from exc
    request.state.actor = actor
    return actor


def _runtime_call(runtime, method_name: str, actor, *args, **kwargs):
    """Call actor-aware runtime methods while preserving old fake-runtime tests."""

    method = getattr(runtime, method_name)
    signature = inspect.signature(method)
    parameters = signature.parameters
    accepts_kwargs = any(
        parameter.kind == inspect.Parameter.VAR_KEYWORD for parameter in parameters.values()
    )
    filtered_kwargs = (
        kwargs
        if accepts_kwargs
        else {key: value for key, value in kwargs.items() if key in parameters}
    )
    positional = list(parameters.values())
    if positional and positional[0].name == "actor":
        return method(actor, *args, **filtered_kwargs)
    return method(*args, **filtered_kwargs)


def _runtime_model_state(
    runtime,
    actor,
    session_id: str,
    *,
    request_id: str = "",
) -> ModelState:
    """Read actor/session-aware model state with legacy fake compatibility."""

    method = getattr(runtime, "model_state")
    parameters = list(inspect.signature(method).parameters.values())
    if parameters and parameters[0].name == "actor":
        kwargs = {"request_id": request_id} if "request_id" in inspect.signature(method).parameters else {}
        return method(actor, session_id, **kwargs)
    if parameters and parameters[0].name == "session_id":
        return method(session_id)
    return method()


def _set_model_preference(
    runtime,
    actor,
    session_id: str,
    model: str,
    *,
    request_id: str = "",
) -> ModelState:
    """Update one session preference without touching a shared provider."""

    method = getattr(runtime, "set_model_preference")
    parameters = list(inspect.signature(method).parameters.values())
    if parameters and parameters[0].name == "actor":
        kwargs = {"request_id": request_id} if "request_id" in inspect.signature(method).parameters else {}
        return method(actor, session_id, model, **kwargs)
    return method(model)


def _public_actor(actor) -> PublicUserResponse:
    """Convert ActorContext into the public user response shape."""

    return PublicUserResponse(
        id=str(actor.user_id or "local-operator"),
        username=actor.username,
        display_name=actor.display_name,
        status="active",
        roles=sorted(actor.role_keys),
        can_manage_admins=actor.has_permission("auth.admin.manage"),
    )


def _public_user(user, auth=None) -> PublicUserResponse:
    return PublicUserResponse(
        id=user.id,
        username=user.username,
        display_name=user.display_name,
        status=user.status,
        roles=list(user.role_keys),
        can_manage_admins=auth.can_manage_admins(user) if auth is not None else False,
    )


def _api_error_from_auth(exc: AuthHttpError) -> ApiError:
    return ApiError(
        exc.code,
        exc.message,
        status_code=exc.status_code,
        details=exc.details,
    )


def _request_id(request: Request) -> str:
    return str(getattr(request.state, "request_id", ""))


def _run_chat_events(
    runtime,
    actor,
    session_id: str,
    message: str,
    on_event=None,
    *,
    turn_id: str | None = None,
    request_id: str = "",
) -> ChatTurnResult:
    """Run a chat turn through the required Web runtime event path."""

    return _runtime_call(
        runtime,
        "run_chat_events",
        actor,
        session_id,
        message,
        turn_id=turn_id,
        on_event=on_event,
        request_id=request_id,
    )


def _should_apply_model_preference(message: str, model: str) -> bool:
    """Return whether a request model should update the current endpoint model."""

    return bool(model and model != "auto" and not message.lstrip().startswith("/"))


def _chat_stream_events(runtime, actor, session_id: str, message: str, *, request_id: str = ""):
    """Yield one chat result as SSE events."""

    turn_id = new_turn_id()
    yield _sse("status", {"phase": "accepted", "turn_id": turn_id})
    events: Queue[tuple[str, Any]] = Queue()

    def on_event(event: dict[str, Any]) -> None:
        events.put(("event", event))

    def worker() -> None:
        try:
            result = _run_chat_events(
                runtime,
                actor,
                session_id,
                message,
                on_event=on_event,
                turn_id=turn_id,
                request_id=request_id,
            )
        except Exception as exc:  # noqa: BLE001 - streaming responses must encode errors.
            events.put(("error", exc))
            return
        events.put(("done", result))

    thread = Thread(target=worker, daemon=True)
    thread.start()

    while True:
        kind, payload = events.get()
        if kind == "event":
            if is_runtime_event_payload(payload):
                yield _sse("runtime", payload)
            elif payload.get("type") == "text_delta":
                yield _sse("delta", {"content": payload.get("content", ""), "turn_id": turn_id})
            elif payload.get("type") in {
                "tool_confirmation_required",
                "mcp_elicitation_requested",
            }:
                interaction = dict(payload)
                interaction.setdefault("turn_id", turn_id)
                yield _sse(str(payload["type"]), interaction)
            continue
        if kind == "error":
            error = _api_error_from_exception(payload)
            yield _sse(
                "error",
                {
                    "turn_id": turn_id,
                    "error": {
                        "status": error.status_code,
                        "code": error.code,
                        "message": error.message,
                        "request_id": request_id,
                        "details": error.details,
                    },
                },
            )
            break
        result = payload
        result_turn_id = result.turn_id or turn_id
        assistant = ChatMessageResponse(
            role="assistant",
            content=result.content,
            turn_id=result_turn_id,
        )
        done_event = "stopped" if result.stopped else "done"
        yield _sse(
            done_event,
            {
                "session_id": session_id,
                "turn_id": result_turn_id,
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
    if isinstance(exc, AuthHttpError):
        return _api_error_from_auth(exc)
    if isinstance(exc, SessionAccessError):
        return ApiError(
            exc.code,
            exc.message,
            status_code=exc.status_code,
            details=exc.details,
        )
    if isinstance(exc, AuthStoreError):
        return ApiError(ErrorCode.REQUEST_VALIDATION_FAILED, str(exc), status_code=400)
    if isinstance(exc, PermissionError):
        permission = str(exc).partition(":")[2].strip()
        return ApiError(
            ErrorCode.AUTH_PERMISSION_DENIED,
            "Permission denied",
            status_code=403,
            details={"required_permission": permission} if permission else {},
        )
    if isinstance(exc, ValueError):
        return ApiError(ErrorCode.REQUEST_VALIDATION_FAILED, str(exc), status_code=400)
    if isinstance(exc, LLMConfigurationError):
        return ApiError(ErrorCode.CONFIG_INVALID, _safe_message(str(exc)), status_code=500)
    if isinstance(exc, LLMProviderError):
        return ApiError(ErrorCode.LLM_ERROR, _safe_message(str(exc)), status_code=502)
    return ApiError(ErrorCode.INTERNAL_ERROR, "Unexpected server error", status_code=500)


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
        turn_id=message.turn_id,
        turn_index=message.turn_index,
        parent_turn_id=message.parent_turn_id,
    )


def _format_timestamp(timestamp: float) -> str:
    """Format session timestamps as stable UTC ISO 8601 strings."""

    if timestamp <= 0:
        return ""
    return datetime.fromtimestamp(timestamp, UTC).isoformat(timespec="seconds")


def _safe_message(message: str) -> str:
    """Bound provider/config error text before returning it over HTTP."""

    return message[:500] if message else "request failed"
    PublicUserResponse,
    RoleResponse,
    RoleUpdateRequest,
    RolesResponse,
