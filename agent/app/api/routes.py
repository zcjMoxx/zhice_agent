"""FastAPI routes for the local Web API."""

from __future__ import annotations

import csv
import inspect
import io
import json
import re
from collections.abc import Mapping
from dataclasses import asdict, is_dataclass
from datetime import UTC, datetime
from queue import Queue
from threading import Thread
from typing import Any

from fastapi import APIRouter, Request, Response
from fastapi.responses import StreamingResponse

from agent.app.api.schemas import (
    AdminMonitorResponse,
    AdminUserCreateRequest,
    AdminUserDeleteRequest,
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
    HotelBrowserAdminStatusResponse,
    HotelBrowserCredentialRequest,
    LoginRequest,
    McpMonitorResponse,
    ModelPreferenceRequest,
    ModelsResponse,
    MonitorActivityResponse,
    OperationsTerminalResponse,
    PasswordChangeRequest,
    ProfileUpdateRequest,
    PublicUserResponse,
    RegisterUserRequest,
    RegistrationPolicyResponse,
    RegistrationPolicyUpdateRequest,
    RoleResponse,
    RolesResponse,
    RoleUpdateRequest,
    SessionMutationResponse,
    SessionRenameRequest,
    SessionResponse,
    SessionsResponse,
    SessionSummaryResponse,
    SkillSourcesResponse,
    SkillSourceStatusResponse,
    SkillSummaryResponse,
    SystemDiagnosticsResponse,
    ToolConfirmationResponse,
    ToolConfirmationsResponse,
    WeixinBindingAttemptResponse,
    WeixinChannelStatusResponse,
    XhsReadonlyAdminStatusResponse,
)
from agent.app.auth import AuthHttpError, local_operator_actor
from agent.app.runtime import ChatTurnResult, ModelState
from agent.applications.travel.account_credentials import CredentialStoreError
from agent.applications.travel.service import TravelApplicationError
from agent.auth.schema import PERMISSIONS
from agent.auth.session_access import SessionAccessError
from agent.auth.store import AuthStoreError, ExternalIdentityConflictError
from agent.core.turns import new_turn_id
from agent.message import Message
from agent.operations.runtime import load_operations_runtime_state, state_from_environment
from agent.protocols.auth import AuditEvent
from agent.protocols.errors import ErrorCode
from agent.protocols.llm import LLMConfigurationError, LLMProviderError
from agent.protocols.runtime_event import is_runtime_event_payload
from agent.runtime_config import RuntimeConfigurationError, load_operations_terminal_config

router = APIRouter(prefix="/api")
_SKILL_SOURCE_NAME_RE = re.compile(r"^[A-Za-z0-9_-]+$")


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
    if not auth.store.registration_enabled():
        if auth.audit_sink is not None:
            auth.audit_sink.record(
                AuditEvent(
                    action="auth.registration_failed",
                    resource_type="registration_policy",
                    request_id=_request_id(request),
                    channel="web",
                    route=request.url.path,
                    status_code=403,
                    decision="deny",
                    reason_code=ErrorCode.AUTH_REGISTRATION_DISABLED,
                )
            )
        raise ApiError(
            ErrorCode.AUTH_REGISTRATION_DISABLED,
            "Public registration is disabled",
            status_code=403,
        )
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


@router.get("/auth/registration-policy", response_model=RegistrationPolicyResponse)
def read_public_registration_policy(request: Request) -> RegistrationPolicyResponse:
    """Return the anonymous-safe public registration policy."""

    auth = _auth_service(request, required=True)
    return RegistrationPolicyResponse(
        registration_enabled=auth.store.registration_enabled()
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
    try:
        authorized = identity.authorize(request_body.token, actor)
    except ExternalIdentityConflictError as exc:
        raise ApiError(
            "CHANNEL_QQ_USER_ALREADY_BOUND",
            "当前账号已经绑定其他 QQ，请先在渠道连接中解绑。",
            status_code=409,
        ) from exc
    if not authorized:
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


@router.get("/channels/weixin", response_model=WeixinChannelStatusResponse)
def get_weixin_status(request: Request) -> WeixinChannelStatusResponse:
    actor = _actor(request, channel="rest")
    binding = _weixin_binding(_runtime(request))
    return WeixinChannelStatusResponse(**binding.status(actor))


@router.post(
    "/channels/weixin/binding-attempts",
    response_model=WeixinBindingAttemptResponse,
)
def create_weixin_binding_attempt(
    request: Request, response: Response
) -> WeixinBindingAttemptResponse:
    actor = _actor(request, channel="rest")
    binding = _weixin_binding(_runtime(request))
    response.headers["Cache-Control"] = "no-store"
    try:
        return _weixin_attempt_response(binding.start(actor))
    except AuthStoreError as exc:
        raise ApiError("WEIXIN_ALREADY_BOUND", str(exc), status_code=409) from exc


@router.get(
    "/channels/weixin/binding-attempts/{attempt_id}",
    response_model=WeixinBindingAttemptResponse,
)
def get_weixin_binding_attempt(
    attempt_id: str, request: Request, response: Response
) -> WeixinBindingAttemptResponse:
    actor = _actor(request, channel="rest")
    response.headers["Cache-Control"] = "no-store"
    try:
        return _weixin_attempt_response(_weixin_binding(_runtime(request)).get(actor, attempt_id))
    except KeyError as exc:
        raise ApiError("WEIXIN_BINDING_ATTEMPT_NOT_FOUND", "Binding attempt not found", status_code=404) from exc


@router.delete(
    "/channels/weixin/binding-attempts/{attempt_id}",
    response_model=WeixinBindingAttemptResponse,
)
def cancel_weixin_binding_attempt(
    attempt_id: str, request: Request, response: Response
) -> WeixinBindingAttemptResponse:
    actor = _actor(request, channel="rest")
    response.headers["Cache-Control"] = "no-store"
    try:
        attempt = _weixin_binding(_runtime(request)).cancel(actor, attempt_id)
    except KeyError as exc:
        raise ApiError("WEIXIN_BINDING_ATTEMPT_NOT_FOUND", "Binding attempt not found", status_code=404) from exc
    return _weixin_attempt_response(attempt)


@router.delete("/channels/weixin/binding", response_model=AuthMutationResponse)
def unlink_weixin_binding(request: Request) -> AuthMutationResponse:
    actor = _actor(request, channel="rest")
    try:
        status = _weixin_binding(_runtime(request)).unlink(actor)
    except KeyError as exc:
        raise ApiError("WEIXIN_BINDING_NOT_FOUND", "Weixin binding not found", status_code=404) from exc
    return AuthMutationResponse(status=status)


@router.post("/channels/weixin/reconnect", response_model=AuthMutationResponse)
def reconnect_weixin_binding(request: Request) -> AuthMutationResponse:
    actor = _actor(request, channel="rest")
    try:
        status = _weixin_binding(_runtime(request)).reconnect(actor)
    except KeyError as exc:
        raise ApiError("WEIXIN_BINDING_NOT_FOUND", "Weixin binding not found", status_code=404) from exc
    return AuthMutationResponse(status=status)


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


@router.get(
    "/admin/auth/registration-policy",
    response_model=RegistrationPolicyResponse,
)
def read_owner_registration_policy(request: Request) -> RegistrationPolicyResponse:
    """Return the registration policy to the unique Owner."""

    actor = _actor(request, channel="rest")
    auth = _auth_service(request, required=True)
    if "owner" not in actor.role_keys:
        raise ApiError(
            ErrorCode.AUTH_PERMISSION_DENIED,
            "Only Owner can manage public registration",
            status_code=403,
            details={"required_role": "owner"},
        )
    return RegistrationPolicyResponse(
        registration_enabled=auth.store.registration_enabled()
    )


@router.patch(
    "/admin/auth/registration-policy",
    response_model=RegistrationPolicyResponse,
)
def update_owner_registration_policy(
    request_body: RegistrationPolicyUpdateRequest,
    request: Request,
) -> RegistrationPolicyResponse:
    """Update public registration as the unique Owner."""

    actor = _actor(request, channel="rest")
    auth = _auth_service(request, required=True)
    if "owner" not in actor.role_keys:
        if auth.audit_sink is not None:
            auth.audit_sink.record(
                AuditEvent(
                    action="auth.registration_policy_updated",
                    resource_type="registration_policy",
                    actor=actor,
                    request_id=_request_id(request),
                    channel="rest",
                    route=request.url.path,
                    status_code=403,
                    decision="deny",
                    reason_code=ErrorCode.AUTH_PERMISSION_DENIED,
                )
            )
        raise ApiError(
            ErrorCode.AUTH_PERMISSION_DENIED,
            "Only Owner can manage public registration",
            status_code=403,
            details={"required_role": "owner"},
        )
    try:
        enabled = auth.store.set_registration_enabled(
            request_body.registration_enabled,
            actor_user_id=actor.user_id,
        )
    except AuthStoreError as exc:
        raise ApiError(
            ErrorCode.AUTH_UNAVAILABLE,
            "Registration policy is unavailable",
            status_code=503,
        ) from exc
    if auth.audit_sink is not None:
        auth.audit_sink.record(
            AuditEvent(
                action="auth.registration_policy_updated",
                resource_type="registration_policy",
                actor=actor,
                request_id=_request_id(request),
                channel="rest",
                route=request.url.path,
                status_code=200,
                decision="allow",
                metadata={"registration_enabled": enabled},
            )
        )
    return RegistrationPolicyResponse(registration_enabled=enabled)


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


@router.delete("/admin/users/{user_id}", response_model=AuthMutationResponse)
def delete_user(
    user_id: str,
    request_body: AdminUserDeleteRequest,
    request: Request,
) -> AuthMutationResponse:
    """Permanently delete one disabled non-Owner local account."""

    actor = _actor(request, "auth.users.manage", channel="rest")
    runtime = _runtime(request)
    auth = _auth_service(request, required=True)
    session_access = getattr(runtime, "session_access", None)
    user_contexts = getattr(session_access, "user_contexts", None)
    if user_contexts is None:
        raise ApiError(ErrorCode.AUTH_UNAVAILABLE, "User context storage is unavailable", 503)
    try:
        deleted = auth.delete_managed_user(
            actor,
            user_id,
            request_body.confirmation,
            user_contexts,
        )
    except AuthHttpError as exc:
        raise _api_error_from_auth(exc) from exc
    if auth.audit_sink is not None:
        auth.audit_sink.record(
            AuditEvent(
                action="user.deleted",
                resource_type="user",
                actor=actor,
                resource_id=deleted.id,
                channel="rest",
                decision="allow",
            )
        )
    return AuthMutationResponse(status="deleted")


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
    target_role = next((role for role in auth.store.list_roles() if role["id"] == role_id), None)
    if target_role is not None and target_role["key"] == "admin" and "owner" not in actor.role_keys:
        raise ApiError(
            ErrorCode.AUTH_PERMISSION_DENIED,
            "Only Owner can update administrator role permissions",
            status_code=403,
            details={"required_role": "owner"},
        )
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


@router.get("/admin/skills/sources", response_model=SkillSourcesResponse)
def read_skill_sources(request: Request) -> SkillSourcesResponse:
    """Return persistent source health and the actor-visible Skill catalog."""

    actor = _actor(request, "skill.sources.read", channel="rest")
    return _skill_sources_response(_runtime(request), actor)


@router.get("/admin/mcp/status", response_model=McpMonitorResponse)
def read_mcp_status(request: Request) -> McpMonitorResponse:
    """Return credential-free MCP runtime health beside the Skill catalog."""

    _actor(request, "skill.sources.read", channel="rest")
    mcp = getattr(_runtime(request), "mcp_runtime", None)
    if mcp is None:
        return McpMonitorResponse(status="disabled")
    catalog = mcp.snapshot()
    stats = mcp.stats_snapshot()
    tool_totals: dict[str, dict[str, object]] = {}
    for tool in stats.tools:
        totals = tool_totals.setdefault(
            tool.server_id,
            {"calls": 0, "success": 0, "failure": 0, "cancelled": 0, "last_error": ""},
        )
        totals["calls"] = int(totals["calls"]) + tool.call_count
        totals["success"] = int(totals["success"]) + tool.success_count
        totals["failure"] = int(totals["failure"]) + tool.error_count
        totals["cancelled"] = int(totals["cancelled"]) + tool.cancelled_count
        if tool.last_error_code:
            totals["last_error"] = tool.last_error_code
    latest_connections = {}
    for event in stats.connection_history:
        latest_connections[event.server_id] = event
    oauth_states = {item.server_id: item.state for item in stats.oauth}
    servers = []
    for server in catalog.servers:
        totals = tool_totals.get(server.server_id, {})
        latest = latest_connections.get(server.server_id)
        servers.append(
            {
                "server_id": server.server_id,
                "state": server.state,
                "tool_count": server.tool_count,
                "error_code": server.error_code,
                "call_count": int(totals.get("calls", 0)),
                "success_count": int(totals.get("success", 0)),
                "failure_count": int(totals.get("failure", 0)),
                "cancelled_count": int(totals.get("cancelled", 0)),
                "last_tool_error_code": str(totals.get("last_error", "")),
                "last_connection_state": str(getattr(latest, "state", "")),
                "last_connection_at": float(getattr(latest, "timestamp", 0.0)),
                "last_connection_reason_code": str(getattr(latest, "reason_code", "")),
                "oauth_state": str(oauth_states.get(server.server_id, "disabled")),
            }
        )
    return McpMonitorResponse(
        status="ok",
        catalog_version=catalog.version,
        generated_at=catalog.generated_at,
        active_calls=stats.active_calls,
        catalog_refresh_count=stats.catalog_refresh_count,
        list_changed_count=stats.list_changed_count,
        reconnect_count=stats.reconnect_count,
        servers=servers,
    )


@router.get(
    "/admin/external-platforms/xhs/status",
    response_model=XhsReadonlyAdminStatusResponse,
)
def read_xhs_admin_status(request: Request) -> XhsReadonlyAdminStatusResponse:
    """Return local login-management availability without touching credentials."""

    _owner_xhs_actor(request)
    supervisor = _xhs_supervisor(request)
    snapshot = supervisor.admin_snapshot()
    return _xhs_admin_response(
        snapshot,
        state=str(snapshot.get("state") or ("unknown" if snapshot["enabled"] else "unavailable")),
        code=str(
            snapshot.get("code")
            or ("XHS_AUTH_NOT_CHECKED" if snapshot["enabled"] else "XHS_SIDECAR_UNAVAILABLE")
        ),
        message=str(
            snapshot.get("message")
            or (
                "Login status has not been checked."
                if snapshot["enabled"]
                else "The local Xiaohongshu sidecar is unavailable."
            )
        ),
    )


@router.post(
    "/admin/external-platforms/xhs/check-login",
    response_model=XhsReadonlyAdminStatusResponse,
)
def check_xhs_admin_login(request: Request) -> XhsReadonlyAdminStatusResponse:
    """Check the isolated account through the same MCP Runtime used by travel."""

    actor = _owner_xhs_actor(request)
    runtime = _runtime(request)
    supervisor = _xhs_supervisor(request)
    state, code, message = _check_xhs_login(runtime, actor)
    record = getattr(supervisor, "record_login_status", None)
    if callable(record):
        record(state, code, message)
    _audit_xhs_admin_action(
        request,
        actor,
        action="external_platform.xhs.login_checked",
        decision="allow" if state == "authenticated" else "error",
        code=code,
    )
    return _xhs_admin_response(
        supervisor.admin_snapshot(),
        state=state,
        code=code,
        message=message,
    )


@router.post(
    "/admin/external-platforms/xhs/login",
    response_model=XhsReadonlyAdminStatusResponse,
)
def start_xhs_admin_login(request: Request) -> XhsReadonlyAdminStatusResponse:
    """Open the fixed local login helper for the Owner's desktop session."""

    actor = _owner_xhs_actor(request)
    supervisor = _xhs_supervisor(request)
    code = supervisor.start_login()
    successful = code in {"XHS_LOGIN_STARTED", "XHS_LOGIN_ALREADY_RUNNING"}
    _audit_xhs_admin_action(
        request,
        actor,
        action="external_platform.xhs.login_started",
        decision="allow" if successful else "error",
        code=code,
    )
    messages = {
        "XHS_LOGIN_STARTED": "The Xiaohongshu login window was opened.",
        "XHS_LOGIN_ALREADY_RUNNING": "The Xiaohongshu login window is already open.",
        "XHS_LOGIN_UNSUPPORTED": "This runtime cannot open a local login window.",
        "XHS_LOGIN_START_FAILED": "The Xiaohongshu login window could not be opened.",
    }
    return _xhs_admin_response(
        supervisor.admin_snapshot(),
        state="login_pending" if successful else "unavailable",
        code=code,
        message=messages.get(code, "The Xiaohongshu login action failed."),
    )


@router.post(
    "/admin/mcp/xhs-readonly/restart",
    response_model=XhsReadonlyAdminStatusResponse,
)
def restart_xhs_admin_sidecar(request: Request) -> XhsReadonlyAdminStatusResponse:
    """Restart only the local sidecar process owned by this Gateway."""

    actor = _owner_xhs_actor(request)
    supervisor = _xhs_supervisor(request)
    code = supervisor.restart()
    successful = code == "XHS_RESTARTED"
    _audit_xhs_admin_action(
        request,
        actor,
        action="mcp.xhs.restarted",
        decision="allow" if successful else "error",
        code=code,
    )
    messages = {
        "XHS_RESTARTED": "The Xiaohongshu sidecar was restarted.",
        "XHS_RESTART_NOT_OWNED": "The current Xiaohongshu process is externally managed.",
        "XHS_RESTART_UNAVAILABLE": "The local Xiaohongshu sidecar is unavailable.",
        "XHS_RESTART_FAILED": "The Xiaohongshu sidecar could not be restarted.",
    }
    return _xhs_admin_response(
        supervisor.admin_snapshot(),
        state="unknown" if successful else "unavailable",
        code=code,
        message=messages.get(code, "The Xiaohongshu restart action failed."),
    )


@router.get(
    "/admin/external-platforms/ctrip/status",
    response_model=HotelBrowserAdminStatusResponse,
)
def read_hotel_browser_admin_status(request: Request) -> HotelBrowserAdminStatusResponse:
    """Return the safe Ctrip credential and login capability projection."""

    _owner_platform_actor(request)
    return _hotel_admin_response(_hotel_account_supervisor(request).admin_snapshot())


@router.put(
    "/admin/external-platforms/ctrip/credentials",
    response_model=HotelBrowserAdminStatusResponse,
)
def save_hotel_browser_credentials(
    request_body: HotelBrowserCredentialRequest,
    request: Request,
) -> HotelBrowserAdminStatusResponse:
    """Persist one Ctrip password in runtime .env, then start the login helper."""

    actor = _owner_platform_actor(request)
    supervisor = _hotel_account_supervisor(request)
    try:
        supervisor.save_credentials(request_body.username, request_body.password)
    except CredentialStoreError as exc:
        _audit_hotel_admin_action(
            request,
            actor,
            action="external_platform.ctrip.credentials_saved",
            decision="error",
            code="HOTEL_CREDENTIAL_STORE_UNAVAILABLE",
        )
        raise ApiError(
            "HOTEL_CREDENTIAL_STORE_UNAVAILABLE",
            "Runtime environment credential storage is unavailable",
            status_code=503,
        ) from exc
    code = supervisor.start_login()
    successful = code in {"HOTEL_LOGIN_STARTED", "HOTEL_LOGIN_ALREADY_RUNNING"}
    _audit_hotel_admin_action(
        request,
        actor,
        action="external_platform.ctrip.credentials_saved",
        decision="allow" if successful else "error",
        code=code,
    )
    return _hotel_admin_response(supervisor.admin_snapshot())


@router.delete(
    "/admin/external-platforms/ctrip/credentials",
    response_model=HotelBrowserAdminStatusResponse,
)
def delete_hotel_browser_credentials(request: Request) -> HotelBrowserAdminStatusResponse:
    """Delete stored Ctrip credentials without exposing or returning them."""

    actor = _owner_platform_actor(request)
    supervisor = _hotel_account_supervisor(request)
    code = supervisor.delete_credentials()
    _audit_hotel_admin_action(
        request,
        actor,
        action="external_platform.ctrip.credentials_deleted",
        decision="allow",
        code=code,
    )
    return _hotel_admin_response(supervisor.admin_snapshot())


@router.post(
    "/admin/external-platforms/ctrip/login",
    response_model=HotelBrowserAdminStatusResponse,
)
def start_hotel_browser_login(request: Request) -> HotelBrowserAdminStatusResponse:
    """Start automatic Ctrip password login with visible verification fallback."""

    actor = _owner_platform_actor(request)
    supervisor = _hotel_account_supervisor(request)
    code = supervisor.start_login()
    successful = code in {"HOTEL_LOGIN_STARTED", "HOTEL_LOGIN_ALREADY_RUNNING"}
    _audit_hotel_admin_action(
        request,
        actor,
        action="external_platform.ctrip.login_started",
        decision="allow" if successful else "error",
        code=code,
    )
    return _hotel_admin_response(supervisor.admin_snapshot())


@router.post(
    "/admin/skills/sources/{source}/sync",
    response_model=AuthMutationResponse,
)
def sync_skill_source(source: str, request: Request) -> AuthMutationResponse:
    """Synchronize one configured source and record only safe audit facts."""

    source = _validated_skill_source_name(source)
    actor = _actor(request, "skill.sync", channel="rest")
    runtime = _runtime(request)
    skill_sync = getattr(runtime, "skill_sync", None)
    if skill_sync is None:
        raise ApiError("SKILL_SYNC_UNAVAILABLE", "Skill synchronization is unavailable", 503)
    _ensure_configured_skill_source(skill_sync, source)
    try:
        result = skill_sync.sync(source_names=[source])
        errors = tuple(getattr(result, "errors", ()) or ())
    except Exception as exc:  # noqa: BLE001 - raw source errors must not cross the API.
        _audit_skill_source_action(
            request,
            actor,
            action="skill.source.sync_failed",
            source=source,
            decision="error",
            reason_code="SKILL_SYNC_FAILED",
            error_type=type(exc).__name__,
        )
        raise ApiError(
            "SKILL_SYNC_FAILED",
            "Skill source synchronization failed",
            status_code=502,
        ) from exc
    if errors:
        _audit_skill_source_action(
            request,
            actor,
            action="skill.source.sync_failed",
            source=source,
            decision="error",
            reason_code="SKILL_SYNC_FAILED",
        )
        raise ApiError(
            "SKILL_SYNC_FAILED",
            "Skill source synchronization failed",
            status_code=502,
        )
    loader = getattr(runtime, "skill_loader", None)
    invalidate = getattr(loader, "invalidate", None)
    if callable(invalidate):
        invalidate(source=source)
    _audit_skill_source_action(
        request,
        actor,
        action="skill.source.sync_completed",
        source=source,
        decision="allow",
    )
    return AuthMutationResponse(status="synchronized")


@router.post(
    "/admin/skills/sources/{source}/refresh-index",
    response_model=AuthMutationResponse,
)
def refresh_skill_source_index(source: str, request: Request) -> AuthMutationResponse:
    """Invalidate one derived Skill index without changing source files."""

    source = _validated_skill_source_name(source)
    actor = _actor(request, "skill.sources.read", channel="rest")
    runtime = _runtime(request)
    _ensure_configured_skill_source(getattr(runtime, "skill_sync", None), source)
    loader = getattr(runtime, "skill_loader", None)
    invalidate = getattr(loader, "invalidate", None)
    if not callable(invalidate):
        raise ApiError("SKILL_INDEX_UNAVAILABLE", "Skill index is unavailable", 503)
    try:
        invalidate(source=source)
    except Exception as exc:  # noqa: BLE001 - keep cache implementation details private.
        raise ApiError(
            "SKILL_INDEX_REFRESH_FAILED",
            "Skill index refresh failed",
            status_code=500,
        ) from exc
    _audit_skill_source_action(
        request,
        actor,
        action="skill.index.refreshed",
        source=source,
        decision="allow",
    )
    return AuthMutationResponse(status="refreshed")


@router.get("/admin/operations/terminal", response_model=OperationsTerminalResponse)
def read_operations_terminal(request: Request) -> OperationsTerminalResponse:
    """Project the configured independent Ops URL to the unique Owner only."""

    actor = _actor(request, channel="rest")
    if "owner" not in actor.role_keys:
        _audit_operations_projection(
            request,
            actor,
            action="server.operations.access_denied",
            decision="deny",
            reason_code="OWNER_REQUIRED",
        )
        raise ApiError(
            ErrorCode.AUTH_PERMISSION_DENIED,
            "Only Owner can access server operations",
            status_code=403,
            details={"required_role": "owner"},
        )
    config = getattr(request.app.state, "config", None)
    config_dir = getattr(config, "config_dir", None)
    state_dir = getattr(config, "state_dir", None)
    if config_dir is None or state_dir is None:
        raise ApiError(ErrorCode.CONFIG_INVALID, "runtime config is unavailable", 503)
    runtime_state = load_operations_runtime_state(state_dir) or state_from_environment()
    if runtime_state is not None:
        response = OperationsTerminalResponse(
            enabled=True,
            configured=True,
            url=runtime_state.url,
            presentation=runtime_state.presentation,
            mode=runtime_state.mode,
            target_type=runtime_state.target_type,
            target_name=runtime_state.target_name,
        )
        _audit_operations_projection(
            request,
            actor,
            action="server.operations.entry_read",
            decision="allow",
        )
        return response
    try:
        terminal = load_operations_terminal_config(config_dir)
    except RuntimeConfigurationError as exc:
        raise ApiError(
            ErrorCode.CONFIG_INVALID,
            "Operations terminal configuration is invalid",
            status_code=503,
        ) from exc
    response = OperationsTerminalResponse(
        enabled=terminal.enabled,
        configured=bool(terminal.enabled and terminal.url),
        url=terminal.url if terminal.enabled else "",
        presentation=terminal.presentation,
        mode="server_docker" if terminal.enabled else "",
        target_type="container" if terminal.enabled else "",
        target_name="zhice-agent" if terminal.enabled else "",
    )
    _audit_operations_projection(
        request,
        actor,
        action="server.operations.entry_read",
        decision="allow",
    )
    return response


@router.get("/admin/monitor", response_model=AdminMonitorResponse)
def read_admin_monitor(
    request: Request,
    limit: int = 50,
    status: str = "",
) -> AdminMonitorResponse:
    """Return current health/capability/Activity truth without inferring causes."""

    _actor(request, "turn.read.any", channel="rest")
    runtime = _runtime(request)
    auth = _auth_service(request, required=True)
    activity = auth.store.list_monitor_activity(limit=limit, status=status)
    statuses_method = getattr(runtime, "capability_statuses", None)
    try:
        statuses = statuses_method() if callable(statuses_method) else {}
    except Exception:  # noqa: BLE001 - the monitor must report partial truth.
        statuses = {}
    capabilities = {
        str(name): _public_monitor_capability(str(name), status)
        for name, status in statuses.items()
    }
    current_model_method = getattr(runtime, "current_model_label", None)
    try:
        current_model = str(current_model_method()) if callable(current_model_method) else "unavailable"
    except Exception:  # noqa: BLE001 - current model is optional monitor context.
        current_model = "unavailable"
    return AdminMonitorResponse(
        gateway={
            "status": "ok",
            "name": "ZhiCe-Agent",
            "current_model": current_model,
            "auth_initialized": auth.store.has_users(),
            "owner_initialized": auth.store.has_owner(),
        },
        capabilities=capabilities,
        activity=MonitorActivityResponse(**activity),
    )


@router.get("/admin/diagnostics", response_model=SystemDiagnosticsResponse)
def read_system_diagnostics(
    request: Request,
    minutes: int = 60,
    limit: int = 100,
    actor_user_id: str = "",
    session_id: str = "",
    turn_id: str = "",
    request_id: str = "",
    channel: str = "",
    component: str = "",
    endpoint: str = "",
    model: str = "",
    tool_name: str = "",
    mcp_server: str = "",
    status: str = "",
    error_code: str = "",
    incident_id: str = "",
) -> SystemDiagnosticsResponse:
    """Return deterministic incidents and a safe cross-component timeline."""

    actor = _actor(request, "diagnostics.system.use", channel="rest")
    diagnostics = getattr(_runtime(request), "system_diagnostics", None)
    if diagnostics is None:
        raise ApiError(
            "DIAGNOSTICS_UNAVAILABLE",
            "System diagnostics is unavailable",
            status_code=503,
        )
    payload = diagnostics.diagnose(
        {
            "minutes": minutes,
            "limit": limit,
            "actor_user_id": actor_user_id,
            "session_id": session_id,
            "turn_id": turn_id,
            "request_id": request_id,
            "channel": channel,
            "component": component,
            "endpoint": endpoint,
            "model": model,
            "tool_name": tool_name,
            "mcp_server": mcp_server,
            "status": status,
            "error_code": error_code,
            "incident_id": incident_id,
        }
    )
    auth = _auth_service(request, required=True)
    if auth.audit_sink is not None:
        auth.audit_sink.record(
            AuditEvent(
                action="diagnostics.system.read",
                resource_type="runtime_diagnostics",
                actor=actor,
                request_id=_request_id(request),
                channel="rest",
                route=request.url.path,
                decision="allow",
                metadata={
                    "window_minutes": int(payload.get("window_minutes") or 0),
                    "incident_count": int(payload.get("summary", {}).get("incidents", 0)),
                },
            )
        )
    return SystemDiagnosticsResponse(**payload)


@router.get("/audit/events", response_model=AuditEventsResponse)
def list_audit_events(
    request: Request,
    limit: int = 100,
    session_id: str = "",
    turn_id: str = "",
    action: str = "",
    event_type: str = "",
    actor_user_id: str = "",
    decision: str = "",
    outcome: str = "",
    from_ts: str = "",
    to_ts: str = "",
    cursor: str = "",
) -> AuditEventsResponse:
    """Return bounded audit events for actors with audit.read."""

    actor = _actor(request, "audit.read", channel="rest")
    auth = _auth_service(request, required=True)
    bounded_limit = max(1, min(int(limit), 500))
    events = auth.store.list_audit_events(
        limit=bounded_limit + 1,
        session_id=session_id,
        turn_id=turn_id,
        action=action,
        event_type=event_type,
        actor_user_id=actor_user_id or None,
        decision=decision,
        outcome=outcome,
        from_ts=from_ts,
        to_ts=to_ts,
        cursor=cursor,
    )
    has_more = len(events) > bounded_limit
    visible_events = events[:bounded_limit]
    next_cursor = _audit_cursor(visible_events[-1]) if has_more and visible_events else ""
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
                    "event_type_filter": bool(event_type),
                    "outcome_filter": bool(outcome),
                    "result_count": len(visible_events),
                },
            )
        )
    return AuditEventsResponse(
        events=visible_events,
        next_cursor=next_cursor,
        has_more=has_more,
    )


@router.get("/audit/events/export")
def export_audit_events(
    request: Request,
    session_id: str = "",
    turn_id: str = "",
    action: str = "",
    event_type: str = "",
    actor_user_id: str = "",
    decision: str = "",
    outcome: str = "",
    from_ts: str = "",
    to_ts: str = "",
) -> Response:
    """Export a bounded filtered security-audit CSV for authorized actors."""

    actor = _actor(request, "audit.export", channel="rest")
    auth = _auth_service(request, required=True)
    events = auth.store.list_audit_events(
        limit=500,
        session_id=session_id,
        turn_id=turn_id,
        action=action,
        event_type=event_type,
        actor_user_id=actor_user_id or None,
        decision=decision,
        outcome=outcome,
        from_ts=from_ts,
        to_ts=to_ts,
    )
    output = io.StringIO(newline="")
    fields = [
        "id", "ts", "actor_user_id", "channel", "action", "resource_type",
        "resource_id", "session_id", "turn_id", "route", "status_code",
        "decision", "reason_code", "risk_category", "metadata",
    ]
    writer = csv.DictWriter(output, fieldnames=fields, extrasaction="ignore")
    writer.writeheader()
    for event in events:
        writer.writerow({**event, "metadata": json.dumps(event.get("metadata", {}), ensure_ascii=False)})
    if auth.audit_sink is not None:
        auth.audit_sink.record(
            AuditEvent(
                action="audit.export",
                resource_type="audit_events",
                actor=actor,
                request_id=_request_id(request),
                channel="rest",
                route=request.url.path,
                decision="allow",
                metadata={"result_count": len(events), "filtered": any((session_id, turn_id, action, event_type, actor_user_id, decision, outcome, from_ts, to_ts))},
            )
        )
    return Response(
        content="\ufeff" + output.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="zhice-security-audit.csv"'},
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


def _skill_sources_response(runtime, actor) -> SkillSourcesResponse:
    status_service = getattr(runtime, "skill_status", None)
    skill_loader = getattr(runtime, "skill_loader", None)
    skill_sync = getattr(runtime, "skill_sync", None)
    list_statuses = getattr(status_service, "list_statuses", None)
    list_skills = getattr(skill_loader, "list_skills_for_actor", None)
    if not callable(list_statuses) or not callable(list_skills):
        raise ApiError(
            "SKILL_STATUS_UNAVAILABLE",
            "Skill source status is unavailable",
            status_code=503,
        )
    try:
        raw_statuses = list_statuses(skill_loader=skill_loader, skill_sync=skill_sync)
        raw_skills = list_skills(actor)
    except Exception as exc:  # noqa: BLE001 - management APIs return only safe summaries.
        raise ApiError(
            "SKILL_STATUS_UNAVAILABLE",
            "Skill source status is unavailable",
            status_code=503,
        ) from exc
    if isinstance(raw_statuses, Mapping):
        raw_statuses = raw_statuses.get("sources", ())
    sources = sorted(
        (_public_skill_source_status(item) for item in (raw_statuses or ())),
        key=lambda item: item.source,
    )
    skills = sorted(
        (_public_skill_summary(item) for item in (raw_skills or ())),
        key=lambda item: item.qualified_name,
    )
    return SkillSourcesResponse(sources=sources, skills=skills)


def _public_skill_source_status(value: object) -> SkillSourceStatusResponse:
    raw = _object_mapping(value)
    source = _bounded_text(raw.get("source") or raw.get("name"), 100)
    if not _SKILL_SOURCE_NAME_RE.fullmatch(source):
        source = "unknown"
    last_status = _bounded_text(raw.get("last_status") or raw.get("status"), 40) or "unknown"
    health = _bounded_text(raw.get("health"), 40)
    if not health:
        health = {
            "synced": "healthy",
            "up_to_date": "healthy",
            "success": "healthy",
            "failed": "error",
            "skipped": "disabled",
        }.get(last_status, "unknown")
    return SkillSourceStatusResponse(
        source=source,
        enabled=bool(raw.get("enabled", True)),
        sync_enabled=bool(raw.get("sync_enabled", raw.get("sync", True))),
        configured_target=_bounded_text(raw.get("configured_target") or raw.get("target"), 160),
        current_commit=_bounded_text(raw.get("current_commit") or raw.get("commit"), 80),
        last_sync_started_at=_bounded_text(raw.get("last_sync_started_at"), 64),
        last_sync_finished_at=_bounded_text(raw.get("last_sync_finished_at"), 64),
        last_success_at=_bounded_text(raw.get("last_success_at"), 64),
        last_status=last_status,
        health=health,
        skill_count=_non_negative_int(raw.get("skill_count", raw.get("skills", 0))),
        load_error_count=_non_negative_int(raw.get("load_error_count", 0)),
        last_error_code=_bounded_text(raw.get("last_error_code"), 100),
        last_error_message_safe=_bounded_text(raw.get("last_error_message_safe"), 500),
    )


def _public_skill_summary(value: object) -> SkillSummaryResponse:
    raw = _object_mapping(value)
    source = _bounded_text(raw.get("source"), 100)
    name = _bounded_text(raw.get("name"), 100)
    qualified_name = _bounded_text(raw.get("qualified_name"), 220)
    if not qualified_name and source and name:
        qualified_name = f"{source}/{name}"
    runtime = raw.get("runtime")
    metadata = raw.get("metadata")
    metadata_runtime = metadata.get("runtime") if isinstance(metadata, Mapping) else None
    return SkillSummaryResponse(
        qualified_name=qualified_name,
        source=source,
        name=name,
        description=_bounded_text(raw.get("description") or raw.get("summary"), 500),
        executable=bool(raw.get("executable", runtime is not None or metadata_runtime is not None)),
    )


def _object_mapping(value: object) -> Mapping[str, Any]:
    if isinstance(value, Mapping):
        return value
    as_dict_method = getattr(value, "as_dict", None)
    if callable(as_dict_method):
        result = as_dict_method()
        return result if isinstance(result, Mapping) else {}
    if is_dataclass(value):
        result = asdict(value)
        return result if isinstance(result, Mapping) else {}
    attributes = getattr(value, "__dict__", None)
    return attributes if isinstance(attributes, Mapping) else {}


def _bounded_text(value: object, limit: int) -> str:
    text = str(value or "").replace("\r", " ").replace("\n", " ").strip()
    return text[:limit]


def _non_negative_int(value: object) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def _validated_skill_source_name(source: str) -> str:
    normalized = str(source or "").strip()
    if not _SKILL_SOURCE_NAME_RE.fullmatch(normalized):
        raise ApiError(
            ErrorCode.REQUEST_VALIDATION_FAILED,
            "Invalid Skill source name",
            status_code=400,
        )
    return normalized


def _ensure_configured_skill_source(skill_sync, source: str) -> None:
    load = getattr(skill_sync, "load", None)
    if not callable(load):
        raise ApiError("SKILL_SYNC_UNAVAILABLE", "Skill synchronization is unavailable", 503)
    try:
        _settings, sources = load()
    except Exception as exc:  # noqa: BLE001 - configuration details stay server-side.
        raise ApiError(
            "SKILL_SOURCE_CONFIG_INVALID",
            "Skill source configuration is invalid",
            status_code=503,
        ) from exc
    if source not in {str(getattr(item, "name", "")) for item in sources}:
        raise ApiError(
            "SKILL_SOURCE_NOT_CONFIGURED",
            "Skill source is not configured",
            status_code=404,
        )


def _audit_skill_source_action(
    request: Request,
    actor,
    *,
    action: str,
    source: str,
    decision: str,
    reason_code: str = "",
    error_type: str = "",
) -> None:
    auth = _auth_service(request)
    if auth is None or auth.audit_sink is None:
        return
    metadata = {"source": source}
    if error_type:
        metadata["error_type"] = _bounded_text(error_type, 100)
    auth.audit_sink.record(
        AuditEvent(
            action=action,
            resource_type="skill_source",
            actor=actor,
            resource_id=source,
            request_id=_request_id(request),
            channel="rest",
            route=request.url.path,
            decision=decision,
            reason_code=reason_code,
            metadata=metadata,
        )
    )


def _audit_operations_projection(
    request: Request,
    actor,
    *,
    action: str,
    decision: str,
    reason_code: str = "",
) -> None:
    auth = _auth_service(request)
    if auth is None or auth.audit_sink is None:
        return
    auth.audit_sink.record(
        AuditEvent(
            action=action,
            resource_type="server_operations",
            actor=actor,
            request_id=_request_id(request),
            channel="rest",
            route=request.url.path,
            decision=decision,
            reason_code=reason_code,
        )
    )


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


def _weixin_binding(runtime):
    binding = getattr(runtime, "channel_weixin_binding", None)
    if binding is None:
        raise ApiError(
            "CHANNEL_WEIXIN_UNAVAILABLE",
            "Weixin channel is disabled or unavailable",
            status_code=503,
        )
    return binding


def _weixin_attempt_response(attempt) -> WeixinBindingAttemptResponse:
    return WeixinBindingAttemptResponse(
        attempt_id=attempt.attempt_id,
        status=attempt.status,
        expires_at=attempt.expires_at,
        qr_data=attempt.qr_data,
        error_code=attempt.error_code,
    )


def _owner_xhs_actor(request: Request):
    actor = _actor(request, "skill.sources.read", channel="rest")
    if "owner" not in actor.role_keys:
        raise ApiError(
            ErrorCode.AUTH_PERMISSION_DENIED,
            "Only Owner can manage the Xiaohongshu MCP login",
            status_code=403,
            details={"required_role": "owner"},
        )
    return actor


def _owner_platform_actor(request: Request):
    actor = _actor(request, "skill.sources.read", channel="rest")
    if "owner" not in actor.role_keys:
        raise ApiError(
            ErrorCode.AUTH_PERMISSION_DENIED,
            "Only Owner can manage platform account credentials",
            status_code=403,
            details={"required_role": "owner"},
        )
    return actor


def _hotel_account_supervisor(request: Request):
    supervisor = getattr(_runtime(request), "hotel_accounts", None)
    if supervisor is None:
        raise ApiError(
            "HOTEL_ACCOUNT_MANAGER_UNAVAILABLE",
            "The local hotel account manager is unavailable",
            status_code=503,
        )
    return supervisor


def _hotel_admin_response(snapshot: dict[str, object]) -> HotelBrowserAdminStatusResponse:
    return HotelBrowserAdminStatusResponse(
        state=str(snapshot.get("state") or "unknown"),
        code=str(snapshot.get("code") or "HOTEL_AUTH_NOT_CHECKED"),
        message=str(snapshot.get("message") or "Hotel login has not been checked."),
        credential_store_supported=bool(snapshot.get("credential_store_supported")),
        credential_configured=bool(snapshot.get("credential_configured")),
        account_hint=str(snapshot.get("account_hint") or ""),
        credential_source=str(snapshot.get("credential_source") or ""),
        credentials_updated_at=str(snapshot.get("credentials_updated_at") or ""),
        browser_supported=bool(snapshot.get("browser_supported")),
        login_supported=bool(snapshot.get("login_supported")),
        login_in_progress=bool(snapshot.get("login_in_progress")),
        login_mode=str(
            snapshot.get("login_mode") or "password_with_manual_verification_fallback"
        ),
        last_checked_at=str(snapshot.get("last_checked_at") or ""),
    )


def _audit_hotel_admin_action(
    request: Request,
    actor,
    *,
    action: str,
    decision: str,
    code: str,
) -> None:
    auth = _auth_service(request)
    if auth is None or auth.audit_sink is None:
        return
    auth.audit_sink.record(
        AuditEvent(
            action=action,
            resource_type="external_platform_account",
            actor=actor,
            resource_id="ctrip",
            channel="rest",
            decision=decision,
            reason_code=code,
            metadata={},
        )
    )


def _xhs_supervisor(request: Request):
    supervisor = getattr(_runtime(request), "xhs_sidecar", None)
    if supervisor is None:
        raise ApiError(
            "XHS_SIDECAR_UNAVAILABLE",
            "The local Xiaohongshu sidecar is unavailable",
            status_code=503,
        )
    return supervisor


def _xhs_admin_response(
    snapshot: dict[str, object],
    *,
    state: str,
    code: str,
    message: str,
) -> XhsReadonlyAdminStatusResponse:
    return XhsReadonlyAdminStatusResponse(
        state=state,
        code=code,
        message=message,
        enabled=bool(snapshot.get("enabled")),
        login_supported=bool(snapshot.get("login_supported")),
        login_in_progress=bool(snapshot.get("login_in_progress")),
        restart_supported=bool(snapshot.get("restart_supported")),
        cookie_updated_at=str(snapshot.get("cookie_updated_at") or ""),
    )


def _check_xhs_login(runtime, actor) -> tuple[str, str, str]:
    mcp = getattr(runtime, "mcp_runtime", None)
    bind = getattr(mcp, "tools_for_actor", None)
    if not callable(bind):
        return "unavailable", "XHS_MCP_UNAVAILABLE", "The Xiaohongshu MCP is unavailable."
    tools = bind(actor, runtime.config.workspace)
    tool = next(
        (
            item
            for item in tools
            if "xhs-readonly" in str(getattr(item, "name", ""))
            and str(getattr(item, "name", "")).endswith("check_login_status")
        ),
        None,
    )
    if tool is None:
        return (
            "unavailable",
            "XHS_LOGIN_CHECK_UNAVAILABLE",
            "The Xiaohongshu login check is unavailable.",
        )
    result = tool.execute({})

    return _classify_xhs_login_result(result)


def _classify_xhs_login_result(result) -> tuple[str, str, str]:
    """Classify one normalized ToolResult without exposing its raw payload."""

    objects = _xhs_result_objects(str(getattr(result, "output", "")))
    codes = {
        str(getattr(result, "metadata", {}).get("code") or "").upper(),
        *(str(item.get("code") or "").upper() for item in objects),
    }
    if "TRAVEL_SOURCE_AUTH_REQUIRED" in codes:
        return (
            "auth_required",
            "TRAVEL_SOURCE_AUTH_REQUIRED",
            "The isolated Xiaohongshu account needs login.",
        )
    if bool(getattr(result, "is_error", False)):
        code = next((item for item in codes if item), "XHS_LOGIN_CHECK_FAILED")
        return "unavailable", code, "The Xiaohongshu login check failed."
    if any(
        str(item.get("status") or "").casefold() == "success"
        and str(item.get("code") or "OK").upper() in {"OK", "MCP_OK"}
        for item in objects
    ):
        return "authenticated", "OK", "The isolated Xiaohongshu account is logged in."
    code = next((item for item in codes if item not in {"", "OK", "MCP_OK"}), "XHS_LOGIN_CHECK_FAILED")
    return "unavailable", code, "The Xiaohongshu login state could not be confirmed."


def _xhs_result_objects(value: str) -> list[dict[str, Any]]:
    if not value.strip() or len(value) > 20_000:
        return []
    queue: list[Any] = list(_xhs_json_documents(value))
    result: list[dict[str, Any]] = []
    while queue and len(result) < 12:
        current = queue.pop(0)
        if isinstance(current, list):
            queue.extend(current[:12])
            continue
        if not isinstance(current, dict):
            continue
        result.append(current)
        for key in (
            "data",
            "output",
            "result",
            "text",
            "structuredContent",
            "structured_content",
            "content",
        ):
            nested = current.get(key)
            if isinstance(nested, dict | list):
                queue.append(nested)
            elif isinstance(nested, str) and len(nested) <= 16_000:
                queue.extend(_xhs_json_documents(nested))
    return result


def _xhs_json_documents(value: str) -> list[Any]:
    """Decode bounded adjacent JSON documents emitted by MCP structured + text content."""

    decoder = json.JSONDecoder()
    documents: list[Any] = []
    index = 0
    while index < len(value) and len(documents) < 12:
        while index < len(value) and value[index].isspace():
            index += 1
        if index >= len(value):
            break
        try:
            document, end = decoder.raw_decode(value, index)
        except (TypeError, ValueError):
            break
        documents.append(document)
        index = end
    return documents


def _audit_xhs_admin_action(
    request: Request,
    actor,
    *,
    action: str,
    decision: str,
    code: str,
) -> None:
    auth = _auth_service(request)
    if auth is None or auth.audit_sink is None:
        return
    auth.audit_sink.record(
        AuditEvent(
            action=action,
            resource_type=(
                "external_platform_account"
                if action.startswith("external_platform.")
                else "mcp_server"
            ),
            actor=actor,
            resource_id=("xhs" if action.startswith("external_platform.") else "xhs-readonly"),
            channel="rest",
            decision=decision,
            reason_code=code,
            metadata={},
        )
    )


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
    if isinstance(exc, TravelApplicationError):
        details = {"field": exc.field} if exc.field else {}
        return ApiError(exc.code, exc.message, status_code=exc.status_code, details=details)
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


def _public_monitor_capability(name: str, status: object) -> dict[str, Any]:
    """Expose only stable public capability state to the Part 16 monitor."""

    state = str(getattr(status, "state", "unavailable"))
    messages = {
        "available": f"{name} is available.",
        "disabled": f"{name} is not enabled.",
        "degraded": f"{name} is temporarily limited.",
        "unavailable": f"{name} is temporarily unavailable.",
    }
    return {
        "name": name,
        "state": state,
        "code": f"{name.upper().replace('.', '_')}_{state.upper()}",
        "message": messages.get(state, f"{name} status is {state}."),
        "hint": "Contact an administrator." if state in {"degraded", "unavailable"} else "",
        "details": {},
    }


def _audit_cursor(event: dict[str, Any]) -> str:
    """Build an opaque-enough stable cursor from the last ordered audit row."""

    timestamp = str(event.get("ts") or "")
    event_id = str(event.get("id") or "")
    return f"{timestamp}|{event_id}" if timestamp and event_id else ""


def _safe_message(message: str) -> str:
    """Bound provider/config error text before returning it over HTTP."""

    return message[:500] if message else "request failed"
