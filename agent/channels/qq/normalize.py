"""Normalize qq-botpy messages without leaking SDK objects upward."""

from __future__ import annotations

from datetime import UTC, datetime

from agent.protocols.channel import (
    ChannelAttachment,
    ChannelQuote,
    ChannelReplyTarget,
    InboundChannelEvent,
)


def normalize_c2c_message(account_key: str, message) -> InboundChannelEvent:
    user_id = str(getattr(getattr(message, "author", None), "user_openid", "") or "")
    return _normalize(account_key, message, "c2c", user_id, user_id, mentioned=True)


def normalize_group_message(account_key: str, message) -> InboundChannelEvent:
    user_id = str(getattr(getattr(message, "author", None), "member_openid", "") or "")
    conversation_id = str(getattr(message, "group_openid", "") or "")
    return _normalize(account_key, message, "group", conversation_id, user_id, mentioned=True)


def _normalize(
    account_key: str,
    message,
    conversation_type: str,
    conversation_id: str,
    user_id: str,
    *,
    mentioned: bool,
) -> InboundChannelEvent:
    message_id = str(getattr(message, "id", "") or "")
    event_id = str(getattr(message, "event_id", "") or message_id)
    reference = getattr(message, "message_reference", None)
    reference_id = str(getattr(reference, "message_id", "") or "")
    quote = ChannelQuote(reference_id, unavailable=True) if reference_id else None
    attachments = tuple(
        ChannelAttachment(
            attachment_id=str(getattr(item, "id", "") or ""),
            media_type=str(getattr(item, "content_type", "") or "application/octet-stream"),
            filename=str(getattr(item, "filename", "") or "")[:255],
            url=str(getattr(item, "url", "") or ""),
            size=_optional_int(getattr(item, "size", None)),
        )
        for item in (getattr(message, "attachments", None) or ())
    )
    return InboundChannelEvent(
        channel="qq",
        account_key=account_key,
        event_id=event_id,
        message_id=message_id,
        event_type="message",
        conversation_type=conversation_type,
        external_conversation_id=conversation_id,
        external_thread_id="",
        external_user_id=user_id,
        external_display_name="",
        text=str(getattr(message, "content", "") or "").strip(),
        quote=quote,
        attachments=attachments,
        reply_target=ChannelReplyTarget(
            channel="qq",
            account_key=account_key,
            conversation_type=conversation_type,
            external_conversation_id=conversation_id,
            reply_to_message_id=message_id,
        ),
        occurred_at=str(getattr(message, "timestamp", "") or datetime.now(UTC).isoformat()),
        safe_metadata={"mentioned": "true" if mentioned else "false"},
    )


def _optional_int(value) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None
