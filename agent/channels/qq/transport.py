"""The only module allowed to import and operate qq-botpy."""

from __future__ import annotations

import asyncio
import importlib
import logging
from collections.abc import Awaitable, Callable
from threading import Thread
from typing import Protocol

from agent.channels.qq.normalize import normalize_c2c_message, normalize_group_message
from agent.channels.qq.outbound import QQOutboundButton, QQOutboundMessage

qq_logger = logging.getLogger("zcagent.agent.channel.qq")


class QQTransport(Protocol):
    def start(self, handler: Callable[[object], Awaitable[None]]) -> None: ...

    def stop(self) -> None: ...

    async def send_text(self, event, content: str, *, quote: bool = False) -> None: ...

    async def send_message(self, event, outbound: QQOutboundMessage) -> None: ...


class BotpyQQTransport:
    """qq-botpy WebSocket transport isolated from the neutral adapter."""

    def __init__(self, account):
        self.account = account
        self._client = None
        self._thread: Thread | None = None
        self._messages: dict[str, object] = {}
        self._state_handler: Callable[[str], None] | None = None

    def set_state_handler(self, handler: Callable[[str], None]) -> None:
        self._state_handler = handler

    def start(self, handler: Callable[[object], Awaitable[None]]) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        account_key = self.account.key
        transport = self

        def run_client() -> None:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                # botpy.Client captures an event loop during construction. It must
                # therefore be instantiated after its owning thread installs one.
                botpy = importlib.import_module("botpy")

                class Client(botpy.Client):
                    async def on_ready(self):
                        if transport._state_handler is not None:
                            transport._state_handler("available")
                        return None

                    async def on_error(self, event_method, *args, **kwargs):
                        if transport._state_handler is not None:
                            transport._state_handler("degraded")
                        await super().on_error(event_method, *args, **kwargs)

                    async def on_c2c_message_create(self, message):
                        event = normalize_c2c_message(account_key, message)
                        transport._messages[event.message_id] = message
                        await handler(event)

                    async def on_group_at_message_create(self, message):
                        event = normalize_group_message(account_key, message)
                        transport._messages[event.message_id] = message
                        await handler(event)

                intents = botpy.Intents(public_messages=True)
                client = Client(intents=intents)
                transport._client = client
                client.run(appid=transport.account.app_id, secret=transport.account.app_secret)
            except Exception:  # noqa: BLE001 - optional transport must degrade safely.
                if transport._state_handler is not None:
                    transport._state_handler("degraded")
                qq_logger.exception("channel.qq.transport_failed", extra={"account_key": account_key})
            finally:
                if not loop.is_running() and not loop.is_closed():
                    loop.close()

        self._thread = Thread(
            target=run_client,
            name=f"qq-{self.account.key}",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        client = self._client
        if client is None or client.is_closed():
            return
        asyncio.run_coroutine_threadsafe(client.close(), client.loop).result(timeout=5)
        if self._state_handler is not None:
            self._state_handler("disabled")

    async def send_text(self, event, content: str, *, quote: bool = False) -> None:
        message = self._messages.get(event.message_id)
        if message is None:
            raise RuntimeError("QQ reply target is no longer available")
        payload: dict[str, object] = {"content": content, "msg_type": 0}
        reference = _group_reference(event) if quote else None
        if reference is not None:
            payload["message_reference"] = reference
        await message.reply(**payload)

    async def send_message(self, event, outbound: QQOutboundMessage) -> None:
        """Send Markdown/keyboard output with progressively safer presentation fallback."""

        message = self._messages.get(event.message_id)
        if message is None:
            raise RuntimeError("QQ reply target is no longer available")
        attempts = _build_send_attempts(outbound)
        reference = _group_reference(event)
        last_error: Exception | None = None
        for payload in attempts:
            if reference is not None:
                payload["message_reference"] = reference
            try:
                await message.reply(**payload)
                return
            except Exception as exc:  # noqa: BLE001 - optional rich output must degrade safely.
                last_error = exc
                qq_logger.warning(
                    "channel.qq.rich_send_degraded",
                    extra={
                        "account_key": self.account.key,
                        "msg_type": payload["msg_type"],
                        "has_keyboard": "keyboard" in payload,
                    },
                )
        if last_error is not None:
            raise last_error


def _build_send_attempts(outbound: QQOutboundMessage) -> tuple[dict[str, object], ...]:
    keyboard = _build_keyboard(outbound.buttons)
    attempts: list[dict[str, object]] = []
    if outbound.markdown:
        rich: dict[str, object] = {
            "msg_type": 2,
            "markdown": {"content": outbound.markdown},
        }
        if keyboard is not None:
            rich["keyboard"] = keyboard
        attempts.append(rich)
        if keyboard is not None:
            attempts.append(
                {
                    "msg_type": 2,
                    "markdown": {"content": outbound.markdown},
                }
            )
    elif outbound.text:
        text_payload: dict[str, object] = {"msg_type": 0, "content": outbound.text}
        if keyboard is not None:
            text_payload["keyboard"] = keyboard
        attempts.append(text_payload)
    fallback = outbound.fallback_text or outbound.text
    if fallback and (keyboard is not None or outbound.markdown):
        attempts.append({"msg_type": 0, "content": fallback})
    return tuple(attempts)


def _group_reference(event) -> dict[str, str] | None:
    if event.conversation_type != "group" or not event.message_id:
        return None
    return {"message_id": event.message_id}


def _build_keyboard(buttons: tuple[QQOutboundButton, ...]) -> dict[str, object] | None:
    if not buttons:
        return None
    rows = []
    for button in buttons:
        action: dict[str, object] = {
            "type": 0 if button.action == "url" else 2,
            "permission": {
                "type": 2,
                "specify_role_ids": [],
                "specify_user_ids": [],
            },
            "data": button.data,
            "click_limit": 0,
            "unsupport_tips": button.unsupported_tips,
        }
        if button.action == "command":
            action["enter"] = True
        rows.append(
            {
                "buttons": [
                    {
                        "id": button.button_id,
                        "render_data": {
                            "label": button.label,
                            "visited_label": button.visited_label or button.label,
                            "style": button.style,
                        },
                        "action": action,
                    }
                ]
            }
        )
    return {"content": {"rows": rows}}
