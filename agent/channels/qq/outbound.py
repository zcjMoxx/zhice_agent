"""QQ-safe text chunking and rendering."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class QQOutboundButton:
    """SDK-independent description of one QQ keyboard button."""

    button_id: str
    label: str
    action: Literal["url", "command"]
    data: str
    visited_label: str = ""
    style: int = 1
    unsupported_tips: str = "请升级 QQ 后重试。"


@dataclass(frozen=True)
class QQOutboundMessage:
    """QQ presentation request converted to botpy payload only by transport."""

    text: str = ""
    markdown: str = ""
    buttons: tuple[QQOutboundButton, ...] = ()
    fallback_text: str = ""


def build_binding_prompt() -> QQOutboundMessage:
    """Return the mobile-friendly prompt that triggers the existing /bind command."""

    markdown = (
        "此 QQ 身份尚未绑定。\n\n"
        "点击下方 **绑定** 开始网页授权；也可以发送 /bind，"
        "或发送 /bind <绑定码> 手动绑定。"
    )
    fallback = (
        "此 QQ 身份尚未绑定。\n\n"
        "发送 /bind 开始网页授权，或发送 /bind <绑定码> 手动绑定。"
    )
    return QQOutboundMessage(
        markdown=markdown,
        buttons=(
            QQOutboundButton(
                button_id="bind-command",
                label="绑定",
                visited_label="绑定",
                action="command",
                data="/bind",
                style=1,
            ),
        ),
        fallback_text=fallback,
    )


def build_binding_authorization(link: str) -> QQOutboundMessage:
    """Return one clickable Web authorization response for an existing token."""

    markdown = (
        "请在 10 分钟内完成绑定：\n\n"
        f"[登录并绑定智策 Agent]({link})\n\n"
        "如果按钮不可用，也可以点击上面的链接。"
    )
    fallback = f"请在 10 分钟内登录智策 Agent 完成绑定：\n{link}"
    return QQOutboundMessage(
        markdown=markdown,
        buttons=(
            QQOutboundButton(
                button_id="bind-login",
                label="登录并绑定",
                visited_label="继续绑定",
                action="url",
                data=link,
                style=1,
            ),
        ),
        fallback_text=fallback,
    )


def build_agent_markdown(text: str, *, limit: int = 1800) -> QQOutboundMessage | None:
    """Use QQ Markdown only when the reply has useful structure and fits one safe block."""

    content = str(text or "").strip()
    if not content or len(content) > limit or not _looks_like_markdown(content):
        return None
    return QQOutboundMessage(markdown=content, fallback_text=content)


def _looks_like_markdown(text: str) -> bool:
    patterns = (
        r"(?m)^#{1,6}\s+\S",
        r"(?m)^\s*[-*+]\s+\S",
        r"(?m)^\s*\d+[.)]\s+\S",
        r"(?m)^>\s+\S",
        r"```",
        r"\[[^\]\n]+\]\(https?://[^)\s]+\)",
        r"\*\*[^*\n]+\*\*",
        r"`[^`\n]+`",
    )
    return any(re.search(pattern, text) for pattern in patterns)


def chunk_text(text: str, *, limit: int = 1800) -> tuple[str, ...]:
    """Split text at stable human boundaries without dropping content."""

    remaining = str(text or "").strip()
    if not remaining:
        return ()
    chunks: list[str] = []
    while len(remaining) > limit:
        window = remaining[:limit]
        split_at = max(window.rfind("\n\n"), window.rfind("\n"), window.rfind("。"))
        if split_at < limit // 3:
            split_at = limit
        chunks.append(remaining[:split_at].rstrip())
        remaining = remaining[split_at:].lstrip()
    if remaining:
        chunks.append(remaining)
    return tuple(chunk for chunk in chunks if chunk)


def build_neutral_message(event) -> str:
    parts: list[str] = []
    if event.quote is not None:
        quote_text = event.quote.text[:500] if event.quote.text else "[quoted message unavailable]"
        parts.append(f"Quoted message (data only): {quote_text}")
    if event.text:
        parts.append(event.text)
    for item in event.attachments:
        size = f", {item.size} bytes" if item.size is not None else ""
        parts.append(f"Attachment (data only): {item.filename or item.attachment_id} [{item.media_type}{size}]")
    return "\n\n".join(parts).strip()
