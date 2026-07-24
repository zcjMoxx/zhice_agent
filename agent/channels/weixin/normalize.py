"""Allowlist normalization for frames emitted by the Weixin sidecar."""

from __future__ import annotations

from datetime import UTC, datetime

from agent.protocols.channel import ChannelReplyTarget, InboundChannelEvent


class WeixinEventError(ValueError):
    """Raised when a sidecar message cannot be treated as direct text."""


def normalize_message(frame: dict[str, object]) -> InboundChannelEvent:
    if frame.get("type") != "message.received":
        raise WeixinEventError("unsupported frame type")
    account_key = _required(frame, "account_key")
    event_id = _required(frame, "event_id")
    message_id = _required(frame, "message_id")
    sender = _required(frame, "external_user_id")
    text = str(frame.get("text") or "")
    if str(frame.get("conversation_type") or "c2c") != "c2c" or not text.strip():
        raise WeixinEventError("only non-empty direct text is supported")
    occurred_at = str(frame.get("occurred_at") or datetime.now(UTC).isoformat(timespec="seconds"))
    context_ref = str(frame.get("context_token_ref") or "")
    return InboundChannelEvent(
        channel="weixin",
        account_key=account_key,
        event_id=event_id,
        message_id=message_id,
        event_type="message",
        conversation_type="c2c",
        external_conversation_id=sender,
        external_thread_id="",
        external_user_id=sender,
        external_display_name="",
        text=text,
        quote=None,
        attachments=(),
        reply_target=ChannelReplyTarget(
            channel="weixin",
            account_key=account_key,
            conversation_type="c2c",
            external_conversation_id=sender,
            reply_to_message_id=message_id,
        ),
        occurred_at=occurred_at,
        safe_metadata={
            "protocol_version": str(frame.get("protocol_version") or ""),
            "context_token_ref": context_ref,
        },
    )


def _required(frame: dict[str, object], key: str) -> str:
    value = str(frame.get(key) or "").strip()
    if not value:
        raise WeixinEventError(f"missing {key}")
    return value
