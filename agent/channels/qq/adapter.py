"""QQ policy adapter built on neutral channel services."""

from __future__ import annotations

import asyncio
import uuid
from urllib.parse import quote

from agent.channels.limits import SlidingWindowRateLimiter
from agent.channels.qq.attachments import QQAttachmentService
from agent.channels.qq.outbound import (
    QQOutboundMessage,
    build_agent_markdown,
    build_binding_authorization,
    build_binding_prompt,
    build_neutral_message,
    chunk_text,
)
from agent.protocols.capability import CapabilityStatus
from agent.protocols.channel import ChannelCapabilities, ChannelExecutionContext

QQ_C2C_CAPABILITIES = ChannelCapabilities(
    markdown=True,
    reply_quote=True,
    inbound_media=frozenset({"image", "file"}),
    interactions=True,
    command_profile="qq_c2c",
)
QQ_GROUP_CAPABILITIES = ChannelCapabilities(
    markdown=True,
    reply_quote=True,
    inbound_media=frozenset({"image", "file"}),
    command_profile="qq_group",
)


class QQChannelAdapter:
    def __init__(self, account, transport, identity, conversations, dedup, runtime):
        self.account = account
        self.transport = transport
        self.identity = identity
        self.conversations = conversations
        self.dedup = dedup
        self.runtime = runtime
        self._state = "disabled"
        self._locks: dict[tuple[str, str], asyncio.Lock] = {}
        self._parallel = asyncio.Semaphore(account.max_parallel_conversations)
        self._sender_limit = SlidingWindowRateLimiter(limit=12, window_seconds=60)
        self._conversation_limit = SlidingWindowRateLimiter(limit=30, window_seconds=60)
        self._account_limit = SlidingWindowRateLimiter(limit=120, window_seconds=60)
        self._attachments = QQAttachmentService(max_bytes=account.max_attachment_bytes)
        set_state_handler = getattr(transport, "set_state_handler", None)
        if callable(set_state_handler):
            set_state_handler(self._set_state)

    @property
    def key(self) -> str:
        return f"qq.{self.account.key}"

    def start(self) -> None:
        self._state = "degraded"
        self.transport.start(self.handle_event)

    def stop(self) -> None:
        self.transport.stop()
        self._state = "disabled"

    def status(self) -> CapabilityStatus:
        return CapabilityStatus(
            name=self.key,
            state=self._state,
            code=f"CHANNEL_QQ_{self._state.upper()}",
            message=f"QQ account {self.account.key} is {self._state}.",
            details={"account_key": self.account.key},
        )

    async def handle_event(self, event) -> None:
        if event.channel != "qq" or event.account_key != self.account.key:
            return
        if not self.dedup.claim("qq", self.account.key, event.event_id, event.message_id):
            return
        terminal_status = "done"
        terminal_error = ""
        try:
            if event.conversation_type == "group":
                if not self.account.group_enabled:
                    return
                if self.account.group_require_mention and event.safe_metadata.get("mentioned") != "true":
                    return
            elif not self.account.c2c_enabled:
                return
            if not (
                self._sender_limit.allow(event.external_user_id)
                and self._conversation_limit.allow(event.external_conversation_id)
                and self._account_limit.allow(self.account.key)
            ):
                await self._send(event, "消息过于频繁，请稍后再试。")
                return
            actor = self.identity.resolve("qq", self.account.key, event.external_user_id)
            if actor is None:
                await self._handle_unbound(event)
                return
            if event.text.strip().lower().startswith("/bind"):
                await self._send(event, "此 QQ 身份已经绑定，无需重复操作。")
                return
            context = ChannelExecutionContext(
                channel="qq",
                account_key=self.account.key,
                conversation_type=event.conversation_type,
                external_conversation_id=event.external_conversation_id,
                external_thread_id=event.external_thread_id,
                capabilities=(
                    QQ_C2C_CAPABILITIES
                    if event.conversation_type == "c2c"
                    else QQ_GROUP_CAPABILITIES
                ),
            )
            message = build_neutral_message(event)
            if event.attachments:
                user = self.identity.store.get_user(str(actor.user_id))
                user_context = self.conversations.sessions.user_contexts.resolve(
                    str(actor.user_id),
                    use_workspace_context="owner" in user.role_keys,
                )
                attachment_text = await asyncio.to_thread(
                    self._attachments.download_all,
                    event.attachments,
                    user_context.files_dir,
                )
                message = "\n\n".join(part for part in (event.text, *attachment_text) if part)
            if not message:
                await self._send(event, "消息内容为空，未创建会话。")
                return
            lock_key = (event.external_conversation_id, event.external_user_id)
            lock = self._locks.setdefault(lock_key, asyncio.Lock())
            async with self._parallel, lock:
                deltas: list[str] = []
                result = await asyncio.to_thread(
                    self.runtime.dispatch,
                    actor,
                    message,
                    turn_id="turn-" + uuid.uuid4().hex,
                    on_event=lambda payload: _capture_text(payload, deltas),
                    request_id="qq-" + uuid.uuid4().hex,
                    channel_context=context,
                )
                content = str(getattr(result, "content", "") or "".join(deltas))
                if content:
                    await self._send_runtime_content(event, content)
        except Exception as exc:
            terminal_status = "error"
            terminal_error = type(exc).__name__
            raise
        finally:
            self.dedup.finish(
                "qq",
                self.account.key,
                event.event_id,
                status=terminal_status,
                error_code=terminal_error,
            )

    async def _handle_unbound(self, event) -> None:
        text = event.text.strip()
        if text.lower() == "/bind":
            if event.conversation_type != "c2c":
                await self._send(
                    event,
                    "请在 QQ 私聊中发送 /bind 获取网页授权链接；"
                    "也可以在群聊发送 /bind <绑定码> 手动绑定。",
                )
                return
            request = self.identity.create_authorization_request(
                channel="qq",
                account_key=self.account.key,
                external_user_id=event.external_user_id,
                external_display_name=event.external_display_name,
            )
            link = f"{self.account.web_base_url}/?channel_bind={quote(request.token, safe='')}"
            await self._send_message(event, build_binding_authorization(link))
            return
        if text.lower().startswith("/bind "):
            actor = self.identity.bind(
                code=text.split(maxsplit=1)[1],
                channel="qq",
                account_key=self.account.key,
                external_user_id=event.external_user_id,
                external_display_name=event.external_display_name,
            )
            await self._send(event, "QQ 身份绑定成功。" if actor else "绑定码无效、已过期或不属于此账号。")
            return
        if event.conversation_type != "c2c":
            await self._send(
                event,
                "此 QQ 身份尚未绑定。请在私聊发送 /bind，"
                "或在群聊发送 /bind <绑定码> 手动绑定。",
            )
            return
        await self._send_message(event, build_binding_prompt())

    async def _send(self, event, content: str) -> None:
        for index, chunk in enumerate(chunk_text(content)):
            await self.transport.send_text(
                event,
                chunk,
                quote=event.conversation_type == "group" and index == 0,
            )

    async def _send_message(self, event, outbound: QQOutboundMessage) -> None:
        await self.transport.send_message(event, outbound)

    async def _send_runtime_content(self, event, content: str) -> None:
        markdown = build_agent_markdown(content)
        if markdown is not None:
            await self._send_message(event, markdown)
            return
        await self._send(event, content)

    def _set_state(self, state: str) -> None:
        if state in {"available", "degraded", "disabled"}:
            self._state = state


def _capture_text(payload: dict[str, object], target: list[str]) -> None:
    if payload.get("type") == "text_delta" and payload.get("content"):
        target.append(str(payload["content"]))
