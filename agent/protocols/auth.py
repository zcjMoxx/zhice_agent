"""Provider-neutral identity, permission, context, and audit contracts."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol


@dataclass(frozen=True)
class ActorContext:
    """Authenticated actor data safe to pass through app and core layers."""

    actor_type: str
    user_id: str | None
    username: str
    display_name: str
    role_keys: frozenset[str]
    permission_keys: frozenset[str]
    channel: str
    auth_session_id: str | None = None

    def has_permission(self, permission_key: str) -> bool:
        """Return whether this actor has one explicit permission key."""

        return permission_key in self.permission_keys


@dataclass(frozen=True)
class UserAccount:
    """Public user fields returned by auth stores and APIs."""

    id: str
    username: str
    display_name: str
    status: str
    role_keys: tuple[str, ...] = ()
    created_at: str = ""
    updated_at: str = ""
    last_login_at: str | None = None


@dataclass(frozen=True)
class AuthLogin:
    """Successful login result containing the one-time opaque cookie token."""

    token: str
    auth_session_id: str
    expires_at: str
    actor: ActorContext


@dataclass(frozen=True)
class PermissionDecision:
    """Generic authorization decision for non-tool application actions."""

    allowed: bool
    code: str
    message: str
    require_confirmation: bool = False
    risk_level: str = "low"


@dataclass(frozen=True)
class UserContext:
    """Filesystem paths forming one internal user's context boundary."""

    user_id: str
    root_dir: Path
    files_dir: Path
    sessions_dir: Path
    sessions_meta_dir: Path
    memory_dir: Path
    shared_readonly_dir: Path


@dataclass(frozen=True)
class AuditEvent:
    """Safe structured audit event passed to an AuditSink."""

    action: str
    resource_type: str
    actor: ActorContext | None = None
    resource_id: str = ""
    request_id: str = ""
    channel: str = ""
    session_id: str = ""
    turn_id: str = ""
    tool_call_record_id: str = ""
    route: str = ""
    status_code: int | None = None
    decision: str = ""
    reason_code: str = ""
    risk_category: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


class AuthProvider(Protocol):
    """Resolve opaque login tokens into ActorContext values."""

    def resolve_token(self, token: str, *, channel: str) -> ActorContext | None:
        """Resolve one active token or return None."""


class PermissionChecker(Protocol):
    """Application authorization seam."""

    def check(self, actor: ActorContext, permission_key: str) -> PermissionDecision:
        """Return an allow or deny decision for one permission key."""


class AuditSink(Protocol):
    """Record security-relevant events without coupling core to SQLite."""

    def record(self, event: AuditEvent) -> None:
        """Persist or emit one audit event."""


class UserContextResolver(Protocol):
    """Resolve the workspace operator or one user's filesystem boundary."""

    def resolve(self, user_id: str, *, use_workspace_context: bool = False) -> UserContext:
        """Return the resolved user context."""


class ExternalIdentityResolver(Protocol):
    """Map channel identities to internal users."""

    def resolve_external_identity(
        self,
        *,
        channel: str,
        external_tenant_id: str,
        external_user_id: str,
    ) -> ActorContext | None:
        """Return the mapped actor when the identity is active."""
