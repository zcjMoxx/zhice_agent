"""External identity resolution and one-time link codes."""

from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from agent.auth.store import SQLiteAuthStore
from agent.protocols.auth import ActorContext


@dataclass(frozen=True)
class LinkCode:
    code: str
    expires_at: str


@dataclass(frozen=True)
class AuthorizationRequest:
    token: str
    expires_at: str


@dataclass(frozen=True)
class ExternalIdentityBinding:
    binding_id: str
    channel: str
    display_name: str = ""
    linked_at: str = ""


@dataclass(frozen=True)
class ExternalDeliveryTarget:
    """Server-only external destination; never serialize this through REST."""

    channel: str
    account_key: str
    external_user_id: str


class ExternalIdentityService:
    """Map platform identities to internal users without auto-registration."""

    def __init__(self, store: SQLiteAuthStore, *, ttl_seconds: int = 600):
        self.store = store
        self.ttl_seconds = max(60, int(ttl_seconds))

    def create_link_code(self, user_id: str, channel: str, account_key: str) -> LinkCode:
        code = secrets.token_urlsafe(9)
        expires = datetime.now(UTC) + timedelta(seconds=self.ttl_seconds)
        expires_at = expires.isoformat(timespec="seconds")
        self.store.create_external_link_token(
            token_hash=_hash(code),
            user_id=user_id,
            channel=channel,
            account_key=account_key,
            expires_at=expires_at,
        )
        return LinkCode(code=code, expires_at=expires_at)

    def bind(
        self,
        *,
        code: str,
        channel: str,
        account_key: str,
        external_user_id: str,
        external_display_name: str = "",
    ) -> ActorContext | None:
        now = datetime.now(UTC).isoformat(timespec="seconds")
        user_id = self.store.consume_external_link_token(
            token_hash=_hash(code.strip()),
            channel=channel,
            account_key=account_key,
            consumed_at=now,
        )
        if user_id is None:
            return None
        self.store.link_external_identity(
            user_id=user_id,
            channel=channel,
            external_tenant_id=account_key,
            external_user_id=external_user_id,
            external_display_name=external_display_name,
        )
        return self.resolve(channel, account_key, external_user_id)

    def create_authorization_request(
        self,
        *,
        channel: str,
        account_key: str,
        external_user_id: str,
        external_display_name: str = "",
    ) -> AuthorizationRequest:
        token = secrets.token_urlsafe(24)
        expires = datetime.now(UTC) + timedelta(seconds=self.ttl_seconds)
        expires_at = expires.isoformat(timespec="seconds")
        self.store.create_external_authorization_request(
            token_hash=_hash(token),
            channel=channel,
            account_key=account_key,
            external_user_id=external_user_id,
            external_display_name=external_display_name,
            expires_at=expires_at,
        )
        return AuthorizationRequest(token=token, expires_at=expires_at)

    def authorize(self, token: str, actor: ActorContext) -> bool:
        if actor.user_id is None:
            return False
        row = self.store.consume_external_authorization_request(
            token_hash=_hash(token.strip()),
            user_id=str(actor.user_id),
            consumed_at=datetime.now(UTC).isoformat(timespec="seconds"),
        )
        return row is not None

    def resolve(
        self, channel: str, account_key: str, external_user_id: str
    ) -> ActorContext | None:
        return self.store.resolve_external_identity(
            channel=channel,
            external_tenant_id=account_key,
            external_user_id=external_user_id,
        )

    def list_bindings(self, actor: ActorContext) -> tuple[ExternalIdentityBinding, ...]:
        if actor.user_id is None:
            return ()
        return tuple(
            ExternalIdentityBinding(
                binding_id=str(row["id"]),
                channel=str(row["channel"]),
                display_name=str(row["external_display_name"] or ""),
                linked_at=str(row["linked_at"]),
            )
            for row in self.store.list_external_identities_for_user(str(actor.user_id))
        )

    def delivery_target(
        self, *, user_id: str, channel: str
    ) -> ExternalDeliveryTarget | None:
        row = self.store.get_active_external_identity_for_user(
            user_id=user_id,
            channel=channel,
        )
        if row is None:
            return None
        return ExternalDeliveryTarget(
            channel=str(row["channel"]),
            account_key=str(row["external_tenant_id"]),
            external_user_id=str(row["external_user_id"]),
        )

    def unlink(self, actor: ActorContext, binding_id: str) -> bool:
        if actor.user_id is None:
            return False
        return self.store.unlink_external_identity_for_user(
            identity_id=binding_id,
            user_id=str(actor.user_id),
        )


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()
