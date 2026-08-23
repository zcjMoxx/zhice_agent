"""Actor-scoped orchestration for personal SMTP connections."""

from __future__ import annotations

from dataclasses import asdict

from agent.connections.protocols import ConnectionError, EmailMessage
from agent.connections.store import SQLiteConnectionStore
from agent.integrations.email.personal_smtp import PersonalSMTPEmailProvider
from agent.protocols.auth import ActorContext


class ConnectionRuntime:
    def __init__(self, store: SQLiteConnectionStore):
        self.store = store

    @staticmethod
    def _owner(actor: ActorContext) -> str:
        if actor.user_id is None:
            raise ConnectionError("CONNECTION_ACCESS_DENIED", "a normal logged-in user is required")
        return actor.user_id

    def list(self, actor: ActorContext) -> list[dict]:
        return [asdict(item) for item in self.store.list_for_owner(self._owner(actor))]

    def delete(self, actor: ActorContext, connection_id: str) -> None:
        self.store.delete(connection_id, owner_user_id=self._owner(actor))

    def create_personal_smtp(self, actor: ActorContext, *, host: str, port: int, security: str,
                             username: str, app_password: str,
                             verify: bool = True) -> dict:
        account = username.strip()
        provider = PersonalSMTPEmailProvider(host=host, port=port, security=security,
            username=account, app_password=app_password, from_address=account)
        if verify:
            provider.verify()
        item = self.store.create(owner_user_id=self._owner(actor), provider="smtp_personal",
            account_display=account, credential={"host": host, "port": port, "security": security,
            "username": account, "app_password": app_password, "from_address": account})
        return asdict(item)

    def personal_email_provider(self, actor: ActorContext, connection_id: str):
        owner = self._owner(actor)
        item = self.store.get(connection_id, owner_user_id=owner)
        credential = self.store.credential(connection_id, owner_user_id=owner)
        if item.status != "active":
            raise ConnectionError("CONNECTION_PROVIDER_UNSUPPORTED", "email connection is not active")
        if item.provider != "smtp_personal":
            raise ConnectionError("CONNECTION_PROVIDER_UNSUPPORTED", "only personal SMTP is supported")
        return PersonalSMTPEmailProvider(**credential)

    def validate_email_connection(self, actor: ActorContext, connection_id: str) -> None:
        """Validate publish-time ownership and readiness without sending anything."""

        owner = self._owner(actor)
        item = self.store.get(connection_id, owner_user_id=owner)
        if item.status != "active" or item.provider != "smtp_personal":
            raise ConnectionError("CONNECTION_PROVIDER_UNSUPPORTED", "only active personal SMTP is supported")
        self.store.credential(connection_id, owner_user_id=owner)

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
