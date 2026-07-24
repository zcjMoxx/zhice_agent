"""Transport-neutral contracts for external chat channels."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Protocol

from agent.protocols.auth import ActorContext


@dataclass(frozen=True)
class ChannelCapabilities:
    """Features honestly supported by one channel conversation profile."""

    text: bool = True
    markdown: bool = False
    text_streaming: bool = False
    message_edit: bool = False
    reply_quote: bool = False
    inbound_media: frozenset[str] = frozenset()
    outbound_media: frozenset[str] = frozenset()
    interactions: bool = False
    typing_indicator: bool = False
    can_close_conversation: bool = False
    command_profile: str = "external"


@dataclass(frozen=True)
class ChannelQuote:
    """Safe, bounded description of one quoted platform message."""

    message_id: str
    sender_display_name: str = ""
    text: str = ""
    unavailable: bool = False


@dataclass(frozen=True)
class ChannelAttachment:
    """Safe metadata for one inbound attachment before guarded download."""

    attachment_id: str
    media_type: str
    filename: str = ""
    url: str = ""
    size: int | None = None


@dataclass(frozen=True)
class ChannelReplyTarget:
    """Platform target retained by the adapter, never by AgentLoop."""

    channel: str
    account_key: str
    conversation_type: str
    external_conversation_id: str
    external_thread_id: str = ""
    reply_to_message_id: str = ""


@dataclass(frozen=True)
class InboundChannelEvent:
    """Normalized inbound event with allowlisted metadata only."""

    channel: str
    account_key: str
    event_id: str
    message_id: str
    event_type: str
    conversation_type: str
    external_conversation_id: str
    external_thread_id: str
    external_user_id: str
    external_display_name: str
    text: str
    quote: ChannelQuote | None
    attachments: tuple[ChannelAttachment, ...]
    reply_target: ChannelReplyTarget
    occurred_at: str
    safe_metadata: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class ChannelExecutionContext:
    """Controlled channel context passed to the application runtime."""

    channel: str
    account_key: str
    conversation_type: str
    external_conversation_id: str
    external_thread_id: str = ""
    capabilities: ChannelCapabilities = ChannelCapabilities()


RuntimeEventCallback = Callable[[dict[str, Any]], None]


class ChannelChatRuntime(Protocol):
    """Narrow application-runtime surface consumed by channel adapters."""

    def run_chat_events(
        self,
        actor: ActorContext,
        session_id: str,
        message: str,
        *,
        turn_id: str,
        on_event: RuntimeEventCallback,
        command_profile: str,
        request_id: str,
        channel_context: ChannelExecutionContext,
    ) -> Any: ...

    def stop_turn(
        self,
        actor: ActorContext,
        session_id: str,
        turn_id: str | None = None,
    ) -> bool: ...

    def resume_confirmation(
        self,
        actor: ActorContext,
        confirmation_id: str,
        decision: str,
    ) -> object: ...


class ChannelAdapter(Protocol):
    """Lifecycle contract managed by ChannelManager."""

    @property
    def key(self) -> str: ...

    def start(self) -> None: ...

    def stop(self) -> None: ...

    def status(self) -> Any: ...
