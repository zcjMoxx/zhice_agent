"""Owner-scoped Weixin notification provider for background workflows."""

from __future__ import annotations

from typing import Any

from agent.channels.identity import ExternalIdentityService
from agent.channels.weixin.sidecar import safe_weixin_error_code
from agent.protocols.auth import ActorContext

_MAX_PROACTIVE_TEXT_CHARS = 8000
_TRUNCATION_NOTICE = "\n\n（内容较长，已保留前面的重点。）"
_CONTEXT_ERRORS = frozenset(
    {"CONTEXT_TOKEN_REFERENCE_INVALID", "CONTEXT_TOKEN_MISSING"}
)


class WeixinNotificationProvider:
    """Resolve the current Owner binding without exposing Weixin identifiers."""

    def __init__(self, identity: ExternalIdentityService):
        self.identity = identity
        self._adapter: Any | None = None

    def register_adapter(self, adapter: Any | None) -> None:
        self._adapter = adapter

    def capability(self, actor: ActorContext) -> dict[str, Any]:
        user_id = str(actor.user_id or "")
        if not user_id:
            return {
                "available": False,
                "bound": False,
                "code": "WORKFLOW_WEIXIN_ACCOUNT_REQUIRED",
            }
        account = self.identity.store.get_channel_account_for_user(
            channel="weixin", owner_user_id=user_id
        )
        if account is None:
            return {
                "available": False,
                "bound": False,
                "code": "WORKFLOW_WEIXIN_NOT_BOUND",
            }
        if str(account["status"]) != "active":
            return {
                "available": False,
                "bound": True,
                "code": "WORKFLOW_WEIXIN_RECONNECT_REQUIRED",
            }
        if self._adapter is None or not self._adapter.status().available:
            return {
                "available": False,
                "bound": True,
                "code": "WORKFLOW_WEIXIN_CHANNEL_UNAVAILABLE",
            }
        context = self.identity.store.get_weixin_delivery_context(
            account_key=str(account["account_key"]),
            peer=str(account["external_user_id"]),
        )
        if context is None:
            return {
                "available": False,
                "bound": True,
                "code": "WORKFLOW_WEIXIN_CONTEXT_REQUIRED",
            }
        return {"available": True, "bound": True, "code": ""}

    def validate(self, actor: ActorContext) -> None:
        capability = self.capability(actor)
        if not capability["available"]:
            raise RuntimeError(str(capability["code"]))

    def send_to_user(
        self, *, user_id: str, content: str, delivery_key: str
    ) -> dict[str, str]:
        account = self.identity.store.get_channel_account_for_user(
            channel="weixin", owner_user_id=user_id
        )
        if account is None:
            raise RuntimeError("WORKFLOW_WEIXIN_NOT_BOUND")
        if str(account["status"]) != "active":
            raise RuntimeError("WORKFLOW_WEIXIN_RECONNECT_REQUIRED")
        if self._adapter is None or not self._adapter.status().available:
            raise RuntimeError("WORKFLOW_WEIXIN_CHANNEL_UNAVAILABLE")
        account_key = str(account["account_key"])
        peer = str(account["external_user_id"])
        context = self.identity.store.get_weixin_delivery_context(
            account_key=account_key, peer=peer
        )
        if context is None:
            raise RuntimeError("WORKFLOW_WEIXIN_CONTEXT_REQUIRED")
        rendered = str(content or "").strip()
        if not rendered:
            raise RuntimeError("WORKFLOW_WEIXIN_MESSAGE_EMPTY")
        if len(rendered) > _MAX_PROACTIVE_TEXT_CHARS:
            rendered = rendered[
                : _MAX_PROACTIVE_TEXT_CHARS - len(_TRUNCATION_NOTICE)
            ].rstrip()
            rendered += _TRUNCATION_NOTICE
        try:
            return self._adapter.send_proactive_text(
                account_key=account_key,
                peer=peer,
                context_token_ref=str(context["context_token_ref"]),
                content=rendered,
                delivery_key=delivery_key,
            )
        except Exception as exc:
            code = safe_weixin_error_code(exc, type(exc).__name__)
            if code in _CONTEXT_ERRORS:
                self.identity.store.delete_weixin_delivery_context(
                    account_key=account_key, peer=peer
                )
                raise RuntimeError("WORKFLOW_WEIXIN_CONTEXT_REQUIRED") from exc
            if code in {"WEIXIN_TOKEN_STALE", "WORKFLOW_WEIXIN_RECONNECT_REQUIRED"}:
                raise RuntimeError("WORKFLOW_WEIXIN_RECONNECT_REQUIRED") from exc
            if code.startswith("WORKFLOW_WEIXIN_"):
                raise RuntimeError(code) from exc
            raise RuntimeError("WORKFLOW_ACTION_OUTCOME_UNKNOWN") from exc
