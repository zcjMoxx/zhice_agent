"""Weixin policy adapter built on the shared Part 14 channel runtime."""

from __future__ import annotations

import asyncio
import threading
import uuid

from agent.channels.limits import SlidingWindowRateLimiter
from agent.channels.weixin.normalize import WeixinEventError, normalize_message
from agent.channels.weixin.outbound import render_chunks
from agent.protocols.capability import CapabilityStatus
from agent.protocols.channel import ChannelCapabilities, ChannelExecutionContext

WEIXIN_C2C_CAPABILITIES = ChannelCapabilities(
    text=True,
    markdown=False,
    text_streaming=False,
    message_edit=False,
    reply_quote=False,
    inbound_media=frozenset(),
    outbound_media=frozenset(),
    interactions=False,
    typing_indicator=True,
    can_close_conversation=False,
    command_profile="weixin_c2c",
)


class WeixinClawAdapter:
    def __init__(self, config, sidecar, binding, identity, conversations, dedup, runtime):
        self.config = config
        self.sidecar = sidecar
        self.binding = binding
        self.identity = identity
        self.conversations = conversations
        self.dedup = dedup
        self.runtime = runtime
        self._state = "disabled"
        self._locks: dict[tuple[str, str], threading.Lock] = {}
        self._parallel = threading.BoundedSemaphore(config.max_parallel_conversations)
        self._sender_limit = SlidingWindowRateLimiter(limit=12, window_seconds=60)
        self._conversation_limit = SlidingWindowRateLimiter(limit=30, window_seconds=60)
        self._account_limit = SlidingWindowRateLimiter(limit=120, window_seconds=60)
        self.sidecar.set_event_handler(self._on_frame)

    @property
    def key(self) -> str:
        return "channel.weixin"

    def start(self) -> None:
        self._state = "degraded"
        self.sidecar.start()
        for account in self.identity.store.list_active_channel_accounts("weixin"):
            account_key = str(account["account_key"])
            try:
                credential = self.binding.credentials.read(account_key)
                self.sidecar.request(
                    "account.start", account_key=account_key, credential=credential
                )
            except Exception:  # noqa: BLE001 - one account must not stop other channels.
                self.identity.store.update_channel_account_status(
                    channel="weixin", account_key=account_key, status="reconnect_required"
                )
        health = self.sidecar.request("health.get")
        self._state = "available" if health.get("status") == "available" else "degraded"

    def stop(self) -> None:
        self.sidecar.stop()
        self._state = "disabled"

    def status(self) -> CapabilityStatus:
        code = f"CHANNEL_WEIXIN_{self._state.upper()}"
        details: dict[str, object] = {}
        if self.sidecar.failure:
            details["error_type"] = self.sidecar.failure
        return CapabilityStatus(
            name="channel.weixin",
            state=self._state,
            code=code,
            message=f"Weixin channel is {self._state}.",
            details=details,
        )

    def _on_frame(self, frame: dict[str, object]) -> None:
        frame_type = str(frame.get("type") or "")
        if frame_type.startswith("binding."):
            self.binding.handle_frame(frame)
            return
        if frame_type == "account.status":
            self._handle_account_status(frame)
            return
        if frame_type == "message.received":
            threading.Thread(
                target=lambda: asyncio.run(self.handle_frame(frame)),
                name="weixin-inbound",
                daemon=True,
            ).start()

    def _handle_account_status(self, frame: dict[str, object]) -> None:
        account_key = str(frame.get("account_key") or "")
        status = str(frame.get("status") or "")
        if account_key and status == "reconnect_required":
            self.identity.store.update_channel_account_status(
                channel="weixin", account_key=account_key, status=status
            )
        elif status == "degraded":
            self._state = "degraded"
        elif status == "active":
            self._state = "available"

    async def handle_frame(self, frame: dict[str, object]) -> None:
        try:
            event = normalize_message(frame)
        except WeixinEventError:
            await self._ack(frame, "rejected")
            return
        account = self.identity.store.get_channel_account(
            channel="weixin", account_key=event.account_key
        )
        if (
            account is None
            or str(account["status"]) != "active"
            or str(account["external_user_id"]) != event.external_user_id
        ):
            await self._ack(frame, "rejected")
            return
        if not self.dedup.claim("weixin", event.account_key, event.event_id, event.message_id):
            await self._ack(frame, "duplicate")
            return
        await self._ack(frame, "accepted")
        terminal_status = "done"
        terminal_error = ""
        try:
            if not (
                self._sender_limit.allow(event.external_user_id)
                and self._conversation_limit.allow(event.external_conversation_id)
                and self._account_limit.allow(event.account_key)
            ):
                await self._send(event, "消息过于频繁，请稍后再试。")
                return
            actor = self.identity.resolve("weixin", event.account_key, event.external_user_id)
            if actor is None:
                return
            context = ChannelExecutionContext(
                channel="weixin",
                account_key=event.account_key,
                conversation_type="c2c",
                external_conversation_id=event.external_conversation_id,
                capabilities=WEIXIN_C2C_CAPABILITIES,
            )
            lock_key = (event.account_key, event.external_conversation_id)
            lock = self._locks.setdefault(lock_key, threading.Lock())
            await asyncio.to_thread(self._parallel.acquire)
            await asyncio.to_thread(lock.acquire)
            try:
                await self._typing(event, True)
                deltas: list[str] = []
                result = await asyncio.to_thread(
                    self.runtime.dispatch,
                    actor,
                    event.text,
                    turn_id="turn-" + uuid.uuid4().hex,
                    on_event=lambda payload: _capture_text(payload, deltas),
                    request_id="weixin-" + uuid.uuid4().hex,
                    channel_context=context,
                )
                content = str(getattr(result, "content", "") or "".join(deltas))
                if content:
                    await self._send(event, content)
            finally:
                lock.release()
                self._parallel.release()
        except Exception as exc:
            terminal_status = "error"
            terminal_error = type(exc).__name__
            raise
        finally:
            await self._typing(event, False)
            self.dedup.finish(
                "weixin",
                event.account_key,
                event.event_id,
                status=terminal_status,
                error_code=terminal_error,
            )

    async def _ack(self, frame: dict[str, object], disposition: str) -> None:
        await asyncio.to_thread(
            self.sidecar.request,
            "message.ack",
            account_key=str(frame.get("account_key") or ""),
            event_id=str(frame.get("event_id") or ""),
            disposition=disposition,
        )

    async def _typing(self, event, active: bool) -> None:
        try:
            await asyncio.to_thread(
                self.sidecar.request,
                "typing.set",
                account_key=event.account_key,
                peer=event.external_conversation_id,
                context_token_ref=event.safe_metadata.get("context_token_ref", ""),
                active=active,
            )
        except Exception:  # noqa: BLE001 - typing is explicitly best effort.
            pass

    async def _send(self, event, content: str) -> None:
        for chunk in render_chunks(content, self.config.text_chunk_limit):
            await asyncio.to_thread(
                self.sidecar.request,
                "message.send",
                account_key=event.account_key,
                peer=event.external_conversation_id,
                context_token_ref=event.safe_metadata.get("context_token_ref", ""),
                text=chunk,
            )


def _capture_text(payload: dict[str, object], target: list[str]) -> None:
    if payload.get("type") == "text_delta" and payload.get("content"):
        target.append(str(payload["content"]))
