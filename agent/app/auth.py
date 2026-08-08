"""FastAPI-shell authentication helpers for cookies, bearer tokens, and RBAC."""

from __future__ import annotations

import hmac
from datetime import UTC, datetime

from fastapi import Request, Response, WebSocket

from agent.auth.store import AuthSetupError, AuthStoreError, SQLiteAuthStore
from agent.protocols.auth import ActorContext, AuditEvent, AuthLogin, UserAccount
from agent.protocols.errors import ErrorCode

AUTH_COOKIE_NAME = "zcagent_session"


class AuthHttpError(RuntimeError):
    """Stable auth failure mapped by the API layer."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        status_code: int,
        details: dict[str, object] | None = None,
    ):
        super().__init__(message)
        self.code = str(code)
        self.message = message
        self.status_code = status_code
        self.details = dict(details or {})


class AuthService:
    """Application service wrapping SQLite auth state for HTTP and WS shells."""

    def __init__(self, store: SQLiteAuthStore, audit_sink=None, *, setup_token: str = ""):
        self.store = store
        self.audit_sink = audit_sink
        self.setup_token = str(setup_token or "")

    def login(
        self,
        username: str,
        password: str,
        *,
        channel: str = "web",
        user_agent_preview: str = "",
        remote_addr_preview: str = "",
        request_id: str = "",
        route: str = "/api/auth/login",
    ) -> AuthLogin:
        """Create a login session or return a precise local failure reason."""

        user = self.store.get_user_by_username(username)
        if user is None:
            self._audit_login_failure(
                username, request_id=request_id, channel=channel, route=route,
                status_code=404, reason_code=ErrorCode.AUTH_USER_NOT_FOUND,
            )
            raise AuthHttpError(
                ErrorCode.AUTH_INVALID_CREDENTIALS,
                "Invalid username or password",
                status_code=401,
            )
        if user.status != "active":
            self._audit_login_failure(
                username, request_id=request_id, channel=channel, route=route,
                status_code=403, reason_code=ErrorCode.AUTH_ACCOUNT_DISABLED,
            )
            raise AuthHttpError(ErrorCode.AUTH_ACCOUNT_DISABLED, "Account is disabled", status_code=403)
        try:
            login = self.store.login(
                username,
                password,
                channel=channel,
                user_agent_preview=user_agent_preview,
                remote_addr_preview=remote_addr_preview,
            )
            self._audit(
                AuditEvent(
                    action="auth.login_success",
                    resource_type="auth_session",
                    actor=login.actor,
                    resource_id=login.auth_session_id,
                    request_id=request_id,
                    channel=channel,
                    route=route,
                    status_code=200,
                    decision="allow",
                )
            )
            return login
        except AuthStoreError as exc:
            self._audit_login_failure(
                username, request_id=request_id, channel=channel, route=route,
                status_code=401, reason_code=ErrorCode.AUTH_INVALID_PASSWORD,
            )
            raise AuthHttpError(
                ErrorCode.AUTH_INVALID_CREDENTIALS,
                "Invalid username or password",
                status_code=401,
            ) from exc

    def _audit_login_failure(
        self, username: str, *, request_id: str, channel: str, route: str,
        status_code: int, reason_code: str,
    ) -> None:
        """Record a login denial without retaining submitted credentials."""

        self._audit(
            AuditEvent(
                action="auth.login_failed",
                resource_type="auth_session",
                request_id=request_id,
                channel=channel,
                route=route,
                status_code=status_code,
                decision="deny",
                reason_code=reason_code,
                metadata={"username_preview": str(username)[:64]},
            )
        )

    def bootstrap_owner(
        self,
        password: str,
        setup_token: str,
        *,
        channel: str = "web",
        user_agent_preview: str = "",
        remote_addr_preview: str = "",
        request_id: str = "",
        route: str = "/api/auth/bootstrap",
    ) -> AuthLogin:
        """Create and authenticate the unique permanent Owner account."""

        if not self.setup_token:
            raise AuthHttpError(
                ErrorCode.AUTH_OWNER_SETUP_DISABLED,
                "Web Owner setup is disabled. Run zcagent auth init-owner on the server.",
                status_code=503,
            )
        if not hmac.compare_digest(self.setup_token, str(setup_token or "")):
            raise AuthHttpError(
                ErrorCode.AUTH_INVALID_SETUP_CREDENTIAL,
                "Invalid setup credential",
                status_code=401,
            )

        try:
            user = self.store.initialize_owner("owner", "owner", password)
        except AuthSetupError as exc:
            raise AuthHttpError(
                ErrorCode.AUTH_OWNER_ALREADY_INITIALIZED,
                "Owner is already initialized",
                status_code=409,
            ) from exc
        except (AuthStoreError, TypeError, ValueError) as exc:
            raise AuthHttpError(ErrorCode.REQUEST_VALIDATION_FAILED, str(exc), status_code=400) from exc
        login = self.login(
            user.username,
            password,
            channel=channel,
            user_agent_preview=user_agent_preview,
            remote_addr_preview=remote_addr_preview,
            request_id=request_id,
            route=route,
        )
        self._audit(
            AuditEvent(
                action="auth.bootstrap_completed",
                resource_type="user",
                actor=login.actor,
                resource_id=user.id,
                request_id=request_id,
                channel=channel,
                route=route,
                status_code=200,
                decision="allow",
            )
        )
        return login

    def register_user(
        self,
        username: str,
        password: str,
        *,
        channel: str = "web",
        user_agent_preview: str = "",
        remote_addr_preview: str = "",
        request_id: str = "",
        route: str = "/api/auth/register",
    ) -> AuthLogin:
        """Register one self-service viewer account and create its login state."""

        if not self.store.is_initialized():
            self.store.initialize_schema()
        try:
            user = self.store.create_user(
                username,
                username,
                password,
                role_keys=["viewer"],
            )
        except AuthStoreError as exc:
            code = (
                ErrorCode.USER_USERNAME_ALREADY_EXISTS
                if str(exc) == "username already exists"
                else ErrorCode.REQUEST_VALIDATION_FAILED
            )
            status_code = 409 if code == ErrorCode.USER_USERNAME_ALREADY_EXISTS else 400
            self._audit_registration_failure(
                username,
                request_id=request_id,
                route=route,
                reason_code=code,
                status_code=status_code,
            )
            raise AuthHttpError(code, str(exc), status_code=status_code) from exc
        except (TypeError, ValueError) as exc:
            self._audit_registration_failure(
                username,
                request_id=request_id,
                route=route,
                reason_code=ErrorCode.REQUEST_VALIDATION_FAILED,
                status_code=400,
            )
            raise AuthHttpError(ErrorCode.REQUEST_VALIDATION_FAILED, str(exc), status_code=400) from exc
        login = self.login(
            user.username,
            password,
            channel=channel,
            user_agent_preview=user_agent_preview,
            remote_addr_preview=remote_addr_preview,
            request_id=request_id,
            route=route,
        )
        self._audit(
            AuditEvent(
                action="auth.user_registered",
                resource_type="user",
                actor=login.actor,
                resource_id=user.id,
                request_id=request_id,
                channel=channel,
                route=route,
                status_code=200,
                decision="allow",
                metadata={"role": "viewer"},
            )
        )
        return login

    def create_managed_user(
        self,
        actor: ActorContext,
        username: str,
        display_name: str,
        password: str,
        role_keys,
    ) -> UserAccount:
        """Create a user while enforcing protected Owner/Admin boundaries."""

        roles = self._validated_managed_roles(actor, role_keys)
        try:
            return self.store.create_user(
                username,
                display_name or username,
                password,
                role_keys=roles,
            )
        except AuthStoreError as exc:
            code = (
                ErrorCode.USER_USERNAME_ALREADY_EXISTS
                if str(exc) == "username already exists"
                else ErrorCode.REQUEST_VALIDATION_FAILED
            )
            status_code = 409 if code == ErrorCode.USER_USERNAME_ALREADY_EXISTS else 400
            raise AuthHttpError(code, str(exc), status_code=status_code) from exc

    def update_managed_user(
        self,
        actor: ActorContext,
        user_id: str,
        *,
        display_name: str | None = None,
        status: str | None = None,
        role_keys=None,
        can_manage_admins: bool | None = None,
    ) -> UserAccount:
        """Update one account without allowing Admin or Owner privilege bypasses."""

        try:
            target = self.store.get_user(user_id)
        except AuthStoreError as exc:
            raise AuthHttpError(ErrorCode.REQUEST_VALIDATION_FAILED, str(exc), status_code=400) from exc

        target_is_owner = "owner" in target.role_keys
        target_is_admin = "admin" in target.role_keys
        requested_roles = None
        if role_keys is not None:
            requested_roles = self._validated_managed_roles(actor, role_keys)
        requested_admin = requested_roles is not None and "admin" in requested_roles
        admin_security_change = target_is_admin or requested_admin

        if target_is_owner and (status is not None or role_keys is not None or can_manage_admins is not None):
            raise AuthHttpError(
                ErrorCode.AUTH_OWNER_ACCOUNT_PROTECTED,
                "Owner account is protected",
                status_code=403,
            )
        if admin_security_change and not actor.has_permission("auth.admin.manage"):
            raise AuthHttpError(
                ErrorCode.AUTH_ADMIN_MANAGEMENT_NOT_DELEGATED,
                "Administrator management is not delegated",
                status_code=403,
                details={"required_permission": "auth.admin.manage"},
            )
        if can_manage_admins is not None:
            if "owner" not in actor.role_keys:
                raise AuthHttpError(
                    ErrorCode.AUTH_PERMISSION_DENIED,
                    "Only the Owner can delegate administrator management",
                    status_code=403,
                    details={"required_role": "owner"},
                )
            effective_roles = requested_roles if requested_roles is not None else target.role_keys
            if "admin" not in effective_roles:
                raise AuthHttpError(
                    ErrorCode.REQUEST_VALIDATION_FAILED,
                    "Administrator management can only be delegated to an admin",
                    status_code=400,
                )

        try:
            self.store.update_user(
                user_id,
                display_name=display_name,
                status=status,
                role_keys=requested_roles,
                direct_permission=("auth.admin.manage", can_manage_admins)
                if can_manage_admins is not None
                else None,
            )
            return self.store.get_user(user_id)
        except AuthStoreError as exc:
            raise AuthHttpError(ErrorCode.REQUEST_VALIDATION_FAILED, str(exc), status_code=400) from exc

    def delete_managed_user(
        self,
        actor: ActorContext,
        user_id: str,
        confirmation: str,
        user_contexts,
    ) -> UserAccount:
        """Delete one disabled local account with filesystem rollback on DB failure."""

        if "owner" not in actor.role_keys:
            raise AuthHttpError(
                ErrorCode.AUTH_PERMISSION_DENIED,
                "Only Owner can permanently delete users",
                status_code=403,
                details={"required_role": "owner"},
            )
        try:
            target = self.store.get_user(user_id)
        except AuthStoreError as exc:
            raise AuthHttpError(
                ErrorCode.AUTH_USER_NOT_FOUND, "User not found", status_code=404
            ) from exc
        if "owner" in target.role_keys or actor.user_id == target.id:
            raise AuthHttpError(
                ErrorCode.AUTH_OWNER_ACCOUNT_PROTECTED,
                "Owner account is protected",
                status_code=403,
            )
        if str(confirmation) != target.username:
            raise AuthHttpError(
                ErrorCode.AUTH_USER_DELETE_CONFIRMATION_INVALID,
                "输入的用户名与目标账号不一致",
                status_code=400,
            )

        root_dir, quarantine = user_contexts.quarantine_for_delete(user_id)
        try:
            deleted = self.store.delete_user(user_id, expected_username=target.username)
        except AuthStoreError as exc:
            user_contexts.restore_quarantine(root_dir, quarantine)
            message = str(exc)
            if message == "user must be disabled before deletion":
                raise AuthHttpError(
                    ErrorCode.AUTH_USER_DELETE_REQUIRES_DISABLED,
                    "请先停用该账号，再执行永久删除",
                    status_code=409,
                ) from exc
            if message == "user channel accounts must be unlinked before deletion":
                raise AuthHttpError(
                    ErrorCode.AUTH_USER_DELETE_CHANNELS_BOUND,
                    "该账号仍绑定微信，请先恢复账号并完成微信解绑",
                    status_code=409,
                ) from exc
            raise AuthHttpError(
                ErrorCode.REQUEST_VALIDATION_FAILED, message, status_code=400
            ) from exc
        except Exception:
            user_contexts.restore_quarantine(root_dir, quarantine)
            raise
        user_contexts.purge_quarantine(quarantine)
        return deleted

    def can_manage_admins(self, user: UserAccount) -> bool:
        """Return the effective admin-management flag shown by management APIs."""

        if "owner" in user.role_keys:
            return True
        return self.store.user_has_direct_permission(user.id, "auth.admin.manage")

    @staticmethod
    def _validated_managed_roles(actor: ActorContext, role_keys) -> tuple[str, ...]:
        roles = tuple(dict.fromkeys(str(key).strip() for key in role_keys if str(key).strip()))
        if not roles:
            raise AuthHttpError(
                ErrorCode.REQUEST_VALIDATION_FAILED,
                "At least one role is required",
                status_code=400,
                details={"field": "roles"},
            )
        if "owner" in roles:
            raise AuthHttpError(
                ErrorCode.AUTH_OWNER_ROLE_ASSIGNMENT_FORBIDDEN,
                "Owner role cannot be assigned",
                status_code=403,
            )
        if "admin" in roles and not actor.has_permission("auth.admin.manage"):
            raise AuthHttpError(
                ErrorCode.AUTH_ADMIN_MANAGEMENT_NOT_DELEGATED,
                "Administrator management is not delegated",
                status_code=403,
                details={"required_permission": "auth.admin.manage"},
            )
        return roles

    def resolve_request_actor(self, request: Request, *, channel: str = "rest") -> ActorContext:
        """Resolve a cookie or bearer token from one HTTP request."""

        self._require_setup()
        token = _bearer_token(request.headers.get("authorization", ""))
        token = token or request.cookies.get(AUTH_COOKIE_NAME, "")
        actor = self.store.resolve_token(token, channel=channel)
        if actor is None:
            raise AuthHttpError(ErrorCode.AUTH_REQUIRED, "Authentication required", status_code=401)
        return actor

    def resolve_ws_actor(self, websocket: WebSocket, *, channel: str = "web") -> ActorContext:
        """Resolve same-origin cookie or explicit bearer token for WebSocket."""

        self._require_setup()
        token = _bearer_token(websocket.headers.get("authorization", ""))
        token = token or websocket.cookies.get(AUTH_COOKIE_NAME, "")
        actor = self.store.resolve_token(token, channel=channel)
        if actor is None:
            raise AuthHttpError(ErrorCode.AUTH_REQUIRED, "Authentication required", status_code=401)
        return actor

    def logout(self, request: Request) -> None:
        """Revoke the request's current opaque token when present."""

        token = _bearer_token(request.headers.get("authorization", ""))
        token = token or request.cookies.get(AUTH_COOKIE_NAME, "")
        actor = self.store.resolve_token(token, channel="rest")
        self.store.revoke_token(token)
        self._audit(
            AuditEvent(
                action="auth.logout",
                resource_type="auth_session",
                actor=actor,
                request_id=str(getattr(request.state, "request_id", "")),
                channel="rest",
                route=request.url.path,
                status_code=200,
                decision="revoked",
            )
        )

    def update_profile(
        self,
        actor: ActorContext,
        display_name: str,
        *,
        request_id: str = "",
        route: str = "/api/auth/profile",
    ) -> UserAccount:
        """Update the current DB user's self-service profile fields."""

        if not actor.user_id:
            raise AuthHttpError(
                ErrorCode.AUTH_ACCOUNT_REQUIRED,
                "A database-backed user account is required",
                status_code=400,
            )
        try:
            user = self.store.update_user(actor.user_id, display_name=display_name)
        except AuthStoreError as exc:
            raise AuthHttpError(ErrorCode.REQUEST_VALIDATION_FAILED, str(exc), status_code=400) from exc
        self._audit(
            AuditEvent(
                action="auth.profile_updated",
                resource_type="user",
                actor=actor,
                resource_id=user.id,
                request_id=request_id,
                channel=actor.channel,
                route=route,
                status_code=200,
                decision="allow",
            )
        )
        return user

    def change_password(
        self,
        actor: ActorContext,
        current_password: str,
        new_password: str,
        *,
        request_id: str = "",
        route: str = "/api/auth/password",
    ) -> UserAccount:
        """Rotate credentials and revoke every active login session."""

        if not actor.user_id or not actor.auth_session_id:
            raise AuthHttpError(
                ErrorCode.AUTH_SESSION_REQUIRED,
                "An authenticated database session is required",
                status_code=401,
            )
        try:
            self.store.change_password(
                actor.user_id,
                current_password,
                new_password,
            )
        except AuthStoreError as exc:
            code = (
                ErrorCode.AUTH_INVALID_CURRENT_PASSWORD
                if str(exc) == "invalid current password"
                else ErrorCode.REQUEST_VALIDATION_FAILED
            )
            self._audit_password_failure(actor, request_id=request_id, route=route, reason_code=code)
            raise AuthHttpError(code, str(exc), status_code=400) from exc
        except (TypeError, ValueError) as exc:
            self._audit_password_failure(
                actor,
                request_id=request_id,
                route=route,
                reason_code=ErrorCode.REQUEST_VALIDATION_FAILED,
            )
            raise AuthHttpError(ErrorCode.REQUEST_VALIDATION_FAILED, str(exc), status_code=400) from exc
        user = self.store.get_user(actor.user_id)
        self._audit(
            AuditEvent(
                action="auth.password_changed",
                resource_type="user",
                actor=actor,
                resource_id=user.id,
                request_id=request_id,
                channel=actor.channel,
                route=route,
                status_code=200,
                decision="allow",
                metadata={"all_sessions_revoked": True},
            )
        )
        return user

    @staticmethod
    def require_permission(actor: ActorContext, permission_key: str) -> None:
        """Raise a stable 403 when an actor lacks a permission."""

        if not actor.has_permission(permission_key):
            raise AuthHttpError(
                ErrorCode.AUTH_PERMISSION_DENIED,
                "Permission denied",
                status_code=403,
                details={"required_permission": permission_key},
            )

    @staticmethod
    def set_auth_cookie(response: Response, login: AuthLogin, *, secure: bool = False) -> None:
        """Set the HttpOnly login cookie without exposing user ids."""

        expiry = datetime.fromisoformat(login.expires_at)
        if expiry.tzinfo is None:
            expiry = expiry.replace(tzinfo=UTC)
        max_age = max(0, int((expiry - datetime.now(UTC)).total_seconds()))
        response.set_cookie(
            AUTH_COOKIE_NAME,
            login.token,
            max_age=max_age,
            expires=expiry,
            httponly=True,
            samesite="lax",
            secure=secure,
            path="/",
        )

    @staticmethod
    def clear_auth_cookie(response: Response) -> None:
        """Expire the browser cookie after server-side revocation."""

        response.delete_cookie(AUTH_COOKIE_NAME, path="/", httponly=True, samesite="lax")

    def _require_setup(self) -> None:
        if not self.store.has_users():
            raise AuthHttpError(
                "AUTH_SETUP_REQUIRED",
                "Authentication has no users. Register an account first.",
                status_code=503,
            )

    def _audit_password_failure(
        self,
        actor: ActorContext,
        *,
        request_id: str,
        route: str,
        reason_code: str,
    ) -> None:
        self._audit(
            AuditEvent(
                action="auth.password_change_failed",
                resource_type="user",
                actor=actor,
                resource_id=str(actor.user_id or ""),
                request_id=request_id,
                channel=actor.channel,
                route=route,
                status_code=400,
                decision="deny",
                reason_code=reason_code,
            )
        )

    def _audit_registration_failure(
        self,
        username: str,
        *,
        request_id: str,
        route: str,
        reason_code: str,
        status_code: int,
    ) -> None:
        self._audit(
            AuditEvent(
                action="auth.registration_failed",
                resource_type="user",
                request_id=request_id,
                channel="web",
                route=route,
                status_code=status_code,
                decision="deny",
                reason_code=reason_code,
                metadata={"username_preview": str(username)[:64]},
            )
        )

    def _audit(self, event: AuditEvent) -> None:
        if self.audit_sink is not None:
            self.audit_sink.record(event)


def local_operator_actor(*, channel: str = "cli") -> ActorContext:
    """Return the trusted local no-login operator profile used by CLI and legacy tests."""

    permissions = {
        "auth.users.read",
        "auth.users.manage",
        "auth.admin.manage",
        "auth.roles.read",
        "auth.roles.manage",
        "session.manage.any",
        "chat.stop.any",
        "turn.read.any",
        "tool.exec.dangerous",
        "skill.sync",
        "audit.read",
        "audit.export",
    }
    return ActorContext(
        actor_type="local_operator",
        user_id=None,
        username="local-operator",
        display_name="Local operator",
        role_keys=frozenset({"local_operator"}),
        permission_keys=frozenset(permissions),
        channel=channel,
    )


def _bearer_token(authorization: str) -> str:
    prefix = "bearer "
    normalized = str(authorization or "").strip()
    if normalized.lower().startswith(prefix):
        return normalized[len(prefix) :].strip()
    return ""
