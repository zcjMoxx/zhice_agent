"""Actor-scoped orchestration for personal SMTP connections."""

from __future__ import annotations

import secrets
from dataclasses import asdict
from typing import Any

from agent.auth.store import (
    AuthStoreError,
    NotificationEmailVerificationRateLimitError,
    SQLiteAuthStore,
)
from agent.connections.protocols import ConnectionError, EmailMessage
from agent.connections.store import SQLiteConnectionStore
from agent.integrations.email.personal_smtp import PersonalSMTPEmailProvider
from agent.protocols.auth import ActorContext


class ConnectionRuntime:
    def __init__(
        self,
        store: SQLiteConnectionStore | None,
        *,
        notification_store: SQLiteAuthStore | None = None,
        official_email_provider: Any | None = None,
    ):
        self.store = store
        self.notification_store = notification_store
        self.official_email_provider = official_email_provider

    @property
    def smtp_available(self) -> bool:
        return self.store is not None

    def _smtp_store(self) -> SQLiteConnectionStore:
        if self.store is None:
            raise ConnectionError(
                "CONNECTION_CREDENTIAL_KEY_MISSING",
                "secure SMTP connection storage is unavailable",
            )
        return self.store

    @staticmethod
    def _owner(actor: ActorContext) -> str:
        if actor.user_id is None:
            raise ConnectionError("CONNECTION_ACCESS_DENIED", "a normal logged-in user is required")
        return actor.user_id

    def list(self, actor: ActorContext) -> list[dict]:
        owner = self._owner(actor)
        items = self._smtp_store().list_for_owner(owner)
        self._adopt_existing_mailbox(owner, items)
        return [asdict(item) for item in items]

    def delete(self, actor: ActorContext, connection_id: str) -> None:
        self._smtp_store().delete(connection_id, owner_user_id=self._owner(actor))

    def create_personal_smtp(self, actor: ActorContext, *, host: str, port: int, security: str,
                             username: str, app_password: str,
                             verify: bool = True) -> dict:
        account = username.strip()
        provider = PersonalSMTPEmailProvider(host=host, port=port, security=security,
            username=account, app_password=app_password, from_address=account)
        if verify:
            provider.verify()
        item = self._smtp_store().create(owner_user_id=self._owner(actor), provider="smtp_personal",
            account_display=account, credential={"host": host, "port": port, "security": security,
            "username": account, "app_password": app_password, "from_address": account})
        self._adopt_notification_email(self._owner(actor), account)
        return asdict(item)

    def notification_email(self, actor: ActorContext) -> dict[str, Any]:
        if self.notification_store is None:
            raise ConnectionError("NOTIFICATION_EMAIL_UNAVAILABLE", "notification email storage is unavailable")
        return self.notification_store.notification_email_status(self._owner(actor))

    def request_notification_email_verification(
        self,
        actor: ActorContext,
        *,
        address: str,
    ) -> dict[str, str | int]:
        owner = self._owner(actor)
        normalized = address.strip().lower()
        if "@" not in normalized or len(normalized) > 320:
            raise ConnectionError("EMAIL_RECIPIENT_INVALID", "a valid notification email is required")
        if self.notification_store is None:
            raise ConnectionError("NOTIFICATION_EMAIL_UNAVAILABLE", "notification email storage is unavailable")
        if self.official_email_provider is None:
            raise ConnectionError("OFFICIAL_EMAIL_NOT_CONFIGURED", "official email is not configured")
        code = f"{secrets.randbelow(100_000_000):08d}"
        try:
            challenge = self.notification_store.begin_notification_email_verification(
                owner, normalized, code
            )
        except NotificationEmailVerificationRateLimitError as exc:
            raise ConnectionError(
                "NOTIFICATION_EMAIL_VERIFICATION_RATE_LIMITED",
                "notification email verification was requested too recently",
                details={"retry_after_seconds": exc.retry_after_seconds},
            ) from exc
        except AuthStoreError as exc:
            raise ConnectionError("EMAIL_RECIPIENT_INVALID", str(exc)) from exc
        self.official_email_provider.send(
            EmailMessage(
                (normalized,),
                "智策邮箱验证码",
                f"你的智策邮箱验证码是：{code}\n\n验证码 10 分钟内有效。如非本人操作，请忽略此邮件。",
            )
        )
        return challenge

    def verify_notification_email(
        self,
        actor: ActorContext,
        *,
        address: str,
        code: str,
    ) -> dict[str, Any]:
        if self.notification_store is None:
            raise ConnectionError("NOTIFICATION_EMAIL_UNAVAILABLE", "notification email storage is unavailable")
        if not self.notification_store.verify_notification_email(
            self._owner(actor), address, code
        ):
            raise ConnectionError("NOTIFICATION_EMAIL_CODE_INVALID", "notification email code is invalid or expired")
        return self.notification_store.notification_email_status(self._owner(actor))

    def send_notification_test(self, actor: ActorContext) -> dict[str, str | None]:
        if self.notification_store is None:
            raise ConnectionError("NOTIFICATION_EMAIL_UNAVAILABLE", "notification email storage is unavailable")
        if self.official_email_provider is None:
            raise ConnectionError("OFFICIAL_EMAIL_NOT_CONFIGURED", "official email is not configured")
        recipient = self.notification_store.notification_email(self._owner(actor))
        if not recipient:
            raise ConnectionError("NOTIFICATION_EMAIL_NOT_VERIFIED", "notification email is not verified")
        result = self.official_email_provider.send(
            EmailMessage(
                (recipient,),
                "智策官方通知测试",
                "这是一封由智策官方邮箱发送的测试通知。收到此邮件说明“我的邮箱”已经配置成功。",
            )
        )
        return {
            "status": result.status,
            "provider_message_id": result.provider_message_id,
            "message": result.message,
        }

    def _adopt_existing_mailbox(self, owner: str, items) -> None:
        if self.notification_store is None or self.notification_store.notification_email(owner):
            return
        for item in items:
            if item.provider == "smtp_personal" and item.status == "active":
                self._adopt_notification_email(owner, item.account_display)
                return

    def _adopt_notification_email(self, owner: str, account: str) -> None:
        if self.notification_store is None or self.notification_store.notification_email(owner):
            return
        self.notification_store.upsert_notification_email(
            owner, account, verified=True, is_default=True
        )

    def personal_email_provider(self, actor: ActorContext, connection_id: str):
        owner = self._owner(actor)
        store = self._smtp_store()
        item = store.get(connection_id, owner_user_id=owner)
        credential = store.credential(connection_id, owner_user_id=owner)
        if item.status != "active":
            raise ConnectionError("CONNECTION_PROVIDER_UNSUPPORTED", "email connection is not active")
        if item.provider != "smtp_personal":
            raise ConnectionError("CONNECTION_PROVIDER_UNSUPPORTED", "only personal SMTP is supported")
        return PersonalSMTPEmailProvider(**credential)

    def validate_email_connection(self, actor: ActorContext, connection_id: str) -> None:
        """Validate publish-time ownership and readiness without sending anything."""

        owner = self._owner(actor)
        store = self._smtp_store()
        item = store.get(connection_id, owner_user_id=owner)
        if item.status != "active" or item.provider != "smtp_personal":
            raise ConnectionError("CONNECTION_PROVIDER_UNSUPPORTED", "only active personal SMTP is supported")
        store.credential(connection_id, owner_user_id=owner)

    def send_test_email(
        self,
        actor: ActorContext,
        connection_id: str,
        *,
        recipient: str,
    ) -> dict[str, str | None]:
        """Send one explicit user-triggered test without claiming final delivery."""

        target = recipient.strip()
        if not target or "@" not in target or len(target) > 320:
            raise ConnectionError("EMAIL_RECIPIENT_INVALID", "a valid test recipient is required")
        result = self.personal_email_provider(actor, connection_id).send(
            EmailMessage(
                (target,),
                "智策工作流邮件连接测试",
                "这是一封由智策工作流发送的连接测试邮件。收到此邮件说明当前发送账号可以被工作流调用。",
            )
        )
        return {
            "status": result.status,
            "provider_message_id": result.provider_message_id,
            "message": result.message,
        }
