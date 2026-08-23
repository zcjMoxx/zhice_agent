"""Owner-scoped QQ notification provider for background workflows."""

from __future__ import annotations

from typing import Any, Iterable

from agent.channels.identity import ExternalIdentityService
from agent.channels.qq.outbound import QQSendUnconfirmedError
from agent.protocols.auth import ActorContext

_MAX_PROACTIVE_TEXT_CHARS = 1800
_TRUNCATION_NOTICE = "\n\n（内容较长，已保留前面的重点。）"


class QQNotificationProvider:
    """Resolve the current user's binding without exposing platform identifiers."""

    def __init__(self, identity: ExternalIdentityService):
        self.identity = identity
        self._adapters: dict[str, Any] = {}

    def register_adapters(self, adapters: Iterable[Any]) -> None:
        self._adapters = {
            str(adapter.account.key): adapter
            for adapter in adapters
            if getattr(adapter, "account", None) is not None
        }

    def capability(self, actor: ActorContext) -> dict[str, Any]:
        user_id = str(actor.user_id or "")
        if not user_id:
            return {
                "available": False,
                "bound": False,
                "code": "WORKFLOW_QQ_ACCOUNT_REQUIRED",
            }
        target = self.identity.delivery_target(user_id=user_id, channel="qq")
        if target is None:
            return {
                "available": False,
                "bound": False,
                "code": "WORKFLOW_QQ_NOT_BOUND",
            }
        adapter = self._adapters.get(target.account_key)
        if adapter is None:
            return {
                "available": False,
                "bound": True,
                "code": "WORKFLOW_QQ_CHANNEL_UNAVAILABLE",
            }
        if not bool(getattr(adapter.account, "c2c_enabled", False)):
            return {
                "available": False,
                "bound": True,
                "code": "WORKFLOW_QQ_C2C_DISABLED",
            }
        status = adapter.status()
        if not status.available:
            return {
                "available": False,
                "bound": True,
                "code": "WORKFLOW_QQ_CHANNEL_UNAVAILABLE",
            }
        return {"available": True, "bound": True, "code": ""}

    def validate(self, actor: ActorContext) -> None:
        capability = self.capability(actor)
        if not capability["available"]:
            raise RuntimeError(str(capability["code"]))

    def send_to_user(self, *, user_id: str, content: str) -> dict[str, str]:
        target = self.identity.delivery_target(user_id=user_id, channel="qq")
        if target is None:
            raise RuntimeError("WORKFLOW_QQ_NOT_BOUND")
        adapter = self._adapters.get(target.account_key)
        if adapter is None or not bool(getattr(adapter.account, "c2c_enabled", False)):
            raise RuntimeError("WORKFLOW_QQ_CHANNEL_UNAVAILABLE")
        if not adapter.status().available:
            raise RuntimeError("WORKFLOW_QQ_CHANNEL_UNAVAILABLE")
        rendered = str(content or "").strip()
        if not rendered:
            raise RuntimeError("WORKFLOW_QQ_MESSAGE_EMPTY")
        if len(rendered) > _MAX_PROACTIVE_TEXT_CHARS:
            rendered = rendered[: _MAX_PROACTIVE_TEXT_CHARS - len(_TRUNCATION_NOTICE)].rstrip()
            rendered += _TRUNCATION_NOTICE
        try:
            adapter.transport.send_proactive_text(target.external_user_id, rendered)
        except QQSendUnconfirmedError as exc:
            raise RuntimeError("WORKFLOW_ACTION_OUTCOME_UNKNOWN") from exc
        except Exception as exc:
            if isinstance(exc, TimeoutError):
                raise RuntimeError("WORKFLOW_ACTION_OUTCOME_UNKNOWN") from exc
            raise RuntimeError("WORKFLOW_QQ_SEND_FAILED") from exc
        return {"status": "accepted", "channel": "qq"}
