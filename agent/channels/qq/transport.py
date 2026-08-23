"""The only module allowed to import and operate qq-botpy."""

from __future__ import annotations

import asyncio
import hashlib
import importlib
import logging
import os
import time
from collections.abc import Awaitable, Callable
from threading import Thread
from typing import Protocol

from agent.channels.qq.normalize import normalize_c2c_message, normalize_group_message
from agent.channels.qq.outbound import (
    QQOutboundButton,
    QQOutboundMessage,
    QQSendUnconfirmedError,
)
from agent.logging_utils import DeferredConsoleHandler, log_event

qq_logger = logging.getLogger("zcagent.agent.channel.qq")


class _BotpyConsoleFormatter(logging.Formatter):
    """Match Uvicorn's colored level prefix for concise SDK messages."""

    _LEVEL_COLORS = {
        logging.DEBUG: "36",
        logging.INFO: "32",
        logging.WARNING: "33",
        logging.ERROR: "31",
        logging.CRITICAL: "91",
    }

    def __init__(self, *, use_colors: bool):
        super().__init__()
        self.use_colors = use_colors

    def format(self, record: logging.LogRecord) -> str:
        level_name = record.levelname
        color = self._LEVEL_COLORS.get(record.levelno)
        if self.use_colors and color:
            level_name = f"\033[{color}m{level_name}\033[0m"
        separator = " " * max(8 - len(record.levelname), 0)
        message = record.getMessage().replace("[botpy]", "[qq]", 1)
        rendered = f"{level_name}:{separator} {message}"
        if record.exc_info:
            rendered = f"{rendered}\n{self.formatException(record.exc_info)}"
        return rendered


class _BotpyConsoleHandler(DeferredConsoleHandler):
    """Render useful botpy messages and suppress the routine heartbeat-start line."""

    def __init__(self, stream=None):
        super().__init__(stream)
        self._install_formatter()

    def setFormatter(self, _formatter: logging.Formatter | None) -> None:  # noqa: N802
        self._install_formatter()

    def emit(self, record: logging.LogRecord) -> None:
        if record.funcName == "_send_heart":
            return
        super().emit(record)

    def _install_formatter(self) -> None:
        use_colors = bool(
            not os.environ.get("NO_COLOR")
            and hasattr(self.stream, "isatty")
            and self.stream.isatty()
        )
        logging.Handler.setFormatter(
            self,
            _BotpyConsoleFormatter(use_colors=use_colors),
        )


_BOTPY_CONSOLE_HANDLER = {
    "handler": _BotpyConsoleHandler,
    "format": "%(message)s",
    "level": logging.WARNING,
}


class QQTransport(Protocol):
    def start(self, handler: Callable[[object], Awaitable[None]]) -> None: ...

    def stop(self) -> None: ...

    async def send_text(
        self,
        event,
        content: str,
        *,
        quote: bool = False,
        msg_seq: int = 1,
    ) -> None: ...

    async def send_message(self, event, outbound: QQOutboundMessage) -> None: ...

    def send_proactive_text(self, external_user_id: str, content: str) -> object: ...


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
                        log_event(
                            qq_logger,
                            logging.WARNING,
                            "channel.qq.degraded",
                            account_key=account_key,
                            event_method=str(event_method or "unknown")[:80],
                        )
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
                client = Client(
                    intents=intents,
                    timeout=int(getattr(transport.account, "http_timeout_seconds", 15)),
                    bot_log=True,
                    ext_handlers=_BOTPY_CONSOLE_HANDLER,
                )
                logging.getLogger("botpy").propagate = False
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

    async def send_text(
        self,
        event,
        content: str,
        *,
        quote: bool = False,
        msg_seq: int = 1,
    ) -> None:
        message = self._messages.get(event.message_id)
        if message is None:
            raise RuntimeError("QQ reply target is no longer available")
        payload: dict[str, object] = {
            "content": content,
            "msg_type": 0,
            "msg_seq": msg_seq,
        }
        reference = _group_reference(event) if quote else None
        if reference is not None:
            payload["message_reference"] = reference
        await self._reply(event, message, payload)

    async def send_message(self, event, outbound: QQOutboundMessage) -> None:
        """Send Markdown/keyboard output with progressively safer presentation fallback."""

        message = self._messages.get(event.message_id)
        if message is None:
            raise RuntimeError("QQ reply target is no longer available")
        attempts = _build_send_attempts(outbound)
        reference = _group_reference(event)
        last_error: Exception | None = None
        for payload in attempts:
            payload["msg_seq"] = 1
            if reference is not None:
                payload["message_reference"] = reference
            try:
                await self._reply(event, message, payload)
                return
            except QQSendUnconfirmedError:
                # QQ may already have accepted this msg_id + msg_seq. Sending a
                # fallback here could duplicate the same logical reply.
                raise
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

    def send_proactive_text(self, external_user_id: str, content: str) -> object:
        """Send one C2C text without a source msg_id and without retrying."""

        client = self._client
        thread = self._thread
        if (
            client is None
            or thread is None
            or not thread.is_alive()
            or client.is_closed()
            or not client.loop.is_running()
        ):
            raise RuntimeError("QQ proactive transport is unavailable")
        fields = {
            "account_key": self.account.key,
            "external_user_id_hash": _safe_hash(external_user_id),
            "content_chars": len(content),
            "msg_type": 0,
        }
        log_event(qq_logger, logging.DEBUG, "channel.qq.proactive_send_start", **fields)
        started = time.perf_counter()

        async def send() -> object:
            return await client.api.post_c2c_message(
                openid=external_user_id,
                msg_type=0,
                content=content,
            )

        future = asyncio.run_coroutine_threadsafe(send(), client.loop)
        try:
            response = future.result(
                timeout=max(1, int(getattr(self.account, "http_timeout_seconds", 15)))
            )
        except TimeoutError:
            future.cancel()
            log_event(
                qq_logger,
                logging.WARNING,
                "channel.qq.proactive_send_unconfirmed",
                **fields,
                error_code="QQ_SEND_TIMEOUT",
                duration_ms=_elapsed_ms(started),
            )
            raise
        except Exception as exc:
            log_event(
                qq_logger,
                logging.WARNING,
                "channel.qq.proactive_send_failed",
                **fields,
                error_code=type(exc).__name__,
                duration_ms=_elapsed_ms(started),
            )
            raise
        if response is None:
            log_event(
                qq_logger,
                logging.WARNING,
                "channel.qq.proactive_send_unconfirmed",
                **fields,
                error_code="QQ_SEND_UNCONFIRMED",
                duration_ms=_elapsed_ms(started),
            )
            raise QQSendUnconfirmedError(
                "QQ SDK returned no acceptance confirmation; the message was not retried"
            )
        log_event(
            qq_logger,
            logging.INFO,
            "channel.qq.proactive_send_accepted",
            **fields,
            response_message_id_hash=_safe_hash(_response_message_id(response)),
            duration_ms=_elapsed_ms(started),
        )
        return response

    async def _reply(self, event, message, payload: dict[str, object]) -> object:
        fields = _send_log_fields(self.account.key, event, payload)
        log_event(qq_logger, logging.DEBUG, "channel.qq.send_start", **fields)
        started = time.perf_counter()
        try:
            response = await message.reply(**payload)
        except Exception as exc:  # noqa: BLE001 - log the SDK boundary before propagation.
            log_event(
                qq_logger,
                logging.WARNING,
                "channel.qq.send_failed",
                **fields,
                error_code=type(exc).__name__,
                duration_ms=_elapsed_ms(started),
            )
            raise
        if response is None:
            log_event(
                qq_logger,
                logging.WARNING,
                "channel.qq.send_unconfirmed",
                **fields,
                error_code="QQ_SEND_UNCONFIRMED",
                duration_ms=_elapsed_ms(started),
            )
            raise QQSendUnconfirmedError(
                "QQ SDK returned no delivery confirmation; the reply was not retried"
            )
        log_event(
            qq_logger,
            logging.INFO,
            "channel.qq.send_done",
            **fields,
            response_message_id_hash=_safe_hash(_response_message_id(response)),
            duration_ms=_elapsed_ms(started),
        )
        return response


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


def _send_log_fields(account_key: str, event, payload: dict[str, object]) -> dict[str, object]:
    content = payload.get("content")
    markdown = payload.get("markdown")
    if content is None and isinstance(markdown, dict):
        content = markdown.get("content", "")
    return {
        "account_key": account_key,
        "conversation_type": event.conversation_type,
        "event_id_hash": _safe_hash(event.event_id),
        "source_message_id_hash": _safe_hash(event.message_id),
        "msg_type": payload.get("msg_type", 0),
        "msg_seq": payload.get("msg_seq", 1),
        "has_reference": "message_reference" in payload,
        "has_keyboard": "keyboard" in payload,
        "content_chars": len(str(content or "")),
    }


def _response_message_id(response: object) -> str:
    if isinstance(response, dict):
        return str(response.get("id", ""))
    return str(getattr(response, "id", "") or "")


def _safe_hash(value: object) -> str:
    text = str(value or "")
    if not text:
        return ""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]


def _elapsed_ms(started: float) -> int:
    return max(0, int((time.perf_counter() - started) * 1000))


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
