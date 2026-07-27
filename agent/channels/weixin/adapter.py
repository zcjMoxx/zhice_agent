"""Weixin policy adapter built on the shared Part 14 channel runtime."""

from __future__ import annotations

import asyncio
import logging
import threading
import uuid

from agent.channels.limits import SlidingWindowRateLimiter
from agent.channels.weixin.normalize import WeixinEventError, normalize_message
from agent.channels.weixin.outbound import render_chunks
from agent.channels.weixin.sidecar import (
    WEIXIN_ACCOUNT_START_TIMEOUT_SECONDS,
    WEIXIN_TOKEN_STALE,
    safe_weixin_error_code,
)
from agent.logging_utils import log_event
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
weixin_logger = logging.getLogger("zcagent.agent.channel.weixin")
WEIXIN_SEND_TIMEOUT_SECONDS = 20.0


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
        self._retry_stop = threading.Event()
        self._retry_lock = threading.Lock()
        self._retry_threads: dict[str, threading.Thread] = {}
        self._retry_requested: set[str] = set()
        self._outbox_lock = threading.Lock()
        self._outbox_threads: dict[str, threading.Thread] = {}
        self._send_locks: dict[str, threading.Lock] = {}
        self._reauth_accounts: set[str] = set()
        self.sidecar.set_event_handler(self._on_frame)
        self.binding.set_account_starter(self.start_account)

    @property
    def key(self) -> str:
        return "channel.weixin"

    def start(self) -> None:
        self._state = "degraded"
        self._retry_stop.clear()
        self.sidecar.start()
        self.identity.store.prune_weixin_outbound()
        account_states: list[str] = []
        for account in self.identity.store.list_active_channel_accounts("weixin"):
            account_key = str(account["account_key"])
            account_states.append(
                self.start_account(account_key, self.binding.credentials.read(account_key))
            )
        health = self.sidecar.request("health.get")
        self._state = (
            "available"
            if health.get("status") == "available"
            and all(status == "active" for status in account_states)
            else "degraded"
        )

    def stop(self) -> None:
        self._retry_stop.set()
        self.sidecar.stop()
        self._state = "disabled"

    def start_account(
        self,
        account_key: str,
        credential: dict[str, object],
        *,
        schedule_retry: bool = True,
    ) -> str:
        try:
            response = self.sidecar.request(
                "account.start",
                timeout_seconds=WEIXIN_ACCOUNT_START_TIMEOUT_SECONDS,
                account_key=account_key,
                credential=credential,
            )
        except Exception as exc:  # noqa: BLE001 - classify before retrying.
            code = _safe_error_code(exc)
            if code == WEIXIN_TOKEN_STALE:
                self._mark_reconnect_required(account_key, code)
                return "reconnect_required"
            self._state = "degraded"
            self._log_reconnecting(account_key, code)
            if schedule_retry:
                self._schedule_account_retry(account_key, credential)
            return "retry_pending"

        status = str(response.get("status") or "degraded")
        code = safe_weixin_error_code(response.get("code"), "WEIXIN_ACCOUNT_START_FAILED")
        if status == "active":
            self._mark_active(account_key, trigger="account_start")
            return "active"
        if status == "reconnect_required" and code == WEIXIN_TOKEN_STALE:
            self._mark_reconnect_required(account_key, code)
            return "reconnect_required"
        self._set_binding_active(account_key)
        self._state = "degraded"
        self._log_reconnecting(account_key, code)
        return "reconnecting"

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
        if frame_type == "account.poll_retry":
            self._handle_poll_retry(frame)
            return
        if frame_type == "message.received":
            threading.Thread(
                target=self._run_inbound_frame,
                args=(frame,),
                name="weixin-inbound",
                daemon=True,
            ).start()

    def _run_inbound_frame(self, frame: dict[str, object]) -> None:
        try:
            asyncio.run(self.handle_frame(frame))
        except Exception as exc:  # noqa: BLE001 - isolate one inbound worker.
            log_event(
                weixin_logger,
                logging.WARNING,
                "channel.weixin.inbound_failed",
                account_ref=_safe_account_ref(str(frame.get("account_key") or "")),
                error_type=type(exc).__name__,
                error_code=_safe_error_code(exc),
            )

    def _handle_account_status(self, frame: dict[str, object]) -> None:
        account_key = str(frame.get("account_key") or "")
        status = str(frame.get("status") or "")
        code = safe_weixin_error_code(frame.get("code"), "WEIXIN_ACCOUNT_STATUS_UNKNOWN")
        if not account_key:
            return
        if status == "reconnect_required" and code == WEIXIN_TOKEN_STALE:
            self._mark_reconnect_required(account_key, code)
        elif status == "degraded":
            self._state = "degraded"
            self._log_reconnecting(account_key, code)
        elif status == "active":
            self._mark_active(account_key, trigger="poll_recovered")
        elif status == "reconnect_required":
            self._state = "degraded"
            self._log_reconnecting(account_key, code)

    def _handle_poll_retry(self, frame: dict[str, object]) -> None:
        account_key = str(frame.get("account_key") or "")
        if not account_key:
            return
        code = safe_weixin_error_code(frame.get("code"), "WEIXIN_POLL_FAILED")
        try:
            failures = int(frame.get("consecutive_failures") or 1)
        except (TypeError, ValueError):
            failures = 1
        log_event(
            weixin_logger,
            logging.DEBUG,
            "channel.weixin.poll_retry",
            account_ref=_safe_account_ref(account_key),
            reason_code=code,
            consecutive_failures=max(1, min(failures, 999)),
        )

    async def handle_frame(self, frame: dict[str, object]) -> None:
        log_event(weixin_logger, logging.DEBUG, "channel.weixin.message_received")
        try:
            event = normalize_message(frame)
        except WeixinEventError:
            _log_message_rejected("WEIXIN_EVENT_INVALID")
            await self._ack(frame, "rejected")
            return
        account = self.identity.store.get_channel_account(
            channel="weixin", account_key=event.account_key
        )
        if account is None:
            _log_message_rejected(
                "WEIXIN_ACCOUNT_NOT_FOUND", account_key=event.account_key
            )
            await self._ack(frame, "rejected")
            return
        if str(account["external_user_id"]) != event.external_user_id:
            _log_message_rejected(
                "WEIXIN_SENDER_MISMATCH",
                account_key=event.account_key,
                level=logging.WARNING,
            )
            await self._ack(frame, "rejected")
            return
        account_status = str(account["status"])
        if account_status == "reconnect_required":
            self._mark_active(event.account_key, trigger="inbound_activity")
        elif account_status != "active":
            _log_message_rejected(
                "WEIXIN_ACCOUNT_INACTIVE", account_key=event.account_key
            )
            await self._ack(frame, "rejected")
            return
        if not self.dedup.claim("weixin", event.account_key, event.event_id, event.message_id):
            log_event(
                weixin_logger,
                logging.DEBUG,
                "channel.weixin.message_duplicate",
                account_ref=_safe_account_ref(event.account_key),
                reason_code="WEIXIN_MESSAGE_DUPLICATE",
            )
            await self._ack(frame, "duplicate")
            return
        await self._ack(frame, "accepted")
        log_event(
            weixin_logger,
            logging.DEBUG,
            "channel.weixin.message_accepted",
            account_ref=_safe_account_ref(event.account_key),
        )
        terminal_status = "done"
        terminal_error = ""
        try:
            if not (
                self._sender_limit.allow(event.external_user_id)
                and self._conversation_limit.allow(event.external_conversation_id)
                and self._account_limit.allow(event.account_key)
            ):
                log_event(
                    weixin_logger,
                    logging.WARNING,
                    "channel.weixin.message_rate_limited",
                    account_ref=_safe_account_ref(event.account_key),
                    reason_code="WEIXIN_RATE_LIMITED",
                )
                await self._send(event, "消息过于频繁，请稍后再试。")
                return
            actor = self.identity.resolve("weixin", event.account_key, event.external_user_id)
            if actor is None:
                terminal_status = "error"
                terminal_error = "WEIXIN_IDENTITY_UNRESOLVED"
                _log_message_rejected(
                    terminal_error,
                    account_key=event.account_key,
                    level=logging.WARNING,
                )
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
            terminal_error = _safe_error_code(exc)
            log_event(
                weixin_logger,
                logging.DEBUG,
                "channel.weixin.message_failed",
                account_ref=_safe_account_ref(event.account_key),
                error_type=type(exc).__name__,
                error_code=terminal_error,
            )
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
            if terminal_status == "done":
                log_event(
                    weixin_logger,
                    logging.DEBUG,
                    "channel.weixin.message_done",
                    account_ref=_safe_account_ref(event.account_key),
                )

    async def _ack(self, frame: dict[str, object], disposition: str) -> bool:
        account_key = str(frame.get("account_key") or "")
        try:
            await asyncio.to_thread(
                self.sidecar.request,
                "message.ack",
                account_key=account_key,
                event_id=str(frame.get("event_id") or ""),
                disposition=disposition,
            )
            return True
        except Exception as exc:
            log_event(
                weixin_logger,
                logging.WARNING,
                "channel.weixin.ack_failed",
                account_ref=_safe_account_ref(account_key),
                disposition=disposition,
                error_type=type(exc).__name__,
                error_code=_safe_error_code(exc),
            )
            return False

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
        rows: list[dict[str, object]] = []
        for chunk_index, chunk in enumerate(
            render_chunks(content, self.config.text_chunk_limit)
        ):
            delivery_id = _delivery_id(event.account_key, event.event_id, chunk_index)
            row = self.identity.store.enqueue_weixin_outbound(
                delivery_id=delivery_id,
                account_key=event.account_key,
                event_id=event.event_id,
                peer=event.external_conversation_id,
                context_token_ref=event.safe_metadata.get("context_token_ref", ""),
                chunk_index=chunk_index,
                text=chunk,
            )
            rows.append(row)
            log_event(
                weixin_logger,
                logging.DEBUG,
                "channel.weixin.outbox_enqueued",
                account_ref=_safe_account_ref(event.account_key),
                delivery_ref=_safe_delivery_ref(delivery_id),
                chunk_index=chunk_index,
            )
        for row in rows:
            if str(row.get("status") or "") == "sent":
                continue
            await asyncio.to_thread(self._deliver_outbox_row, row, False)

    def _deliver_outbox_row(
        self, row: dict[str, object], replay: bool
    ) -> None:
        delivery_id = str(row["delivery_id"])
        account_key = str(row["account_key"])
        with self._outbox_lock:
            send_lock = self._send_locks.setdefault(account_key, threading.Lock())
        with send_lock:
            if not self.identity.store.is_weixin_outbound_pending(delivery_id):
                return
            self._deliver_pending_outbox_row(row, replay)

    def _deliver_pending_outbox_row(
        self, row: dict[str, object], replay: bool
    ) -> None:
        delivery_id = str(row["delivery_id"])
        account_key = str(row["account_key"])
        fields = {
            "account_ref": _safe_account_ref(account_key),
            "delivery_ref": _safe_delivery_ref(delivery_id),
            "chunk_index": int(row["chunk_index"]),
            "replay": replay,
        }
        log_event(weixin_logger, logging.DEBUG, "channel.weixin.send_start", **fields)
        self.identity.store.mark_weixin_outbound_attempt(delivery_id)
        try:
            response = self.sidecar.request(
                "message.send",
                timeout_seconds=WEIXIN_SEND_TIMEOUT_SECONDS,
                account_key=account_key,
                peer=str(row["peer"]),
                context_token_ref=str(row["context_token_ref"]),
                client_id=delivery_id,
                text=str(row["text"]),
            )
            if str(response.get("status") or "") != "sent":
                raise RuntimeError("WEIXIN_SEND_NOT_CONFIRMED")
        except Exception as exc:
            code = _safe_error_code(exc)
            self.identity.store.mark_weixin_outbound_error(
                delivery_id, error_code=code
            )
            log_event(
                weixin_logger,
                logging.WARNING,
                "channel.weixin.send_failed",
                **fields,
                error_type=type(exc).__name__,
                error_code=code,
            )
            self._trigger_transport_recovery(account_key, code)
            raise
        self.identity.store.mark_weixin_outbound_sent(delivery_id)
        log_event(weixin_logger, logging.DEBUG, "channel.weixin.send_done", **fields)

    def _trigger_transport_recovery(self, account_key: str, code: str) -> None:
        if code == WEIXIN_TOKEN_STALE:
            self._mark_reconnect_required(account_key, code)
            return
        self._state = "degraded"
        self._log_reconnecting(account_key, code)
        try:
            credential = self.binding.credentials.read(account_key)
        except (OSError, ValueError):
            self._mark_reconnect_required(account_key, "WEIXIN_CREDENTIAL_UNAVAILABLE")
            return
        self._schedule_account_retry(account_key, credential)

    def _mark_active(self, account_key: str, *, trigger: str) -> None:
        changed = self._set_binding_active(account_key)
        if changed is None:
            return
        self._reauth_accounts.discard(account_key)
        self._state = "available"
        if changed or trigger != "account_start":
            log_event(
                weixin_logger,
                logging.INFO,
                "channel.weixin.reconnected",
                account_ref=_safe_account_ref(account_key),
                trigger=trigger,
            )
        self._schedule_outbox_drain(account_key)

    def _schedule_outbox_drain(self, account_key: str) -> None:
        if self._retry_stop.is_set():
            return
        with self._outbox_lock:
            current = self._outbox_threads.get(account_key)
            if current is not None and current.is_alive():
                return
            thread = threading.Thread(
                target=self._drain_outbox,
                args=(account_key,),
                name=f"weixin-outbox-{account_key[-8:]}",
                daemon=True,
            )
            self._outbox_threads[account_key] = thread
            thread.start()

    def _drain_outbox(self, account_key: str) -> None:
        try:
            rows = self.identity.store.list_pending_weixin_outbound(account_key)
            if not rows:
                return
            log_event(
                weixin_logger,
                logging.INFO,
                "channel.weixin.outbox_replay_start",
                account_ref=_safe_account_ref(account_key),
                pending_count=len(rows),
            )
            completed_events: set[str] = set()
            for row in rows:
                if self._retry_stop.is_set():
                    return
                try:
                    self._deliver_outbox_row(row, True)
                except Exception as exc:  # noqa: BLE001 - next reconnect retries pending rows.
                    log_event(
                        weixin_logger,
                        logging.WARNING,
                        "channel.weixin.outbox_replay_failed",
                        account_ref=_safe_account_ref(account_key),
                        error_type=type(exc).__name__,
                        error_code=_safe_error_code(exc),
                    )
                    return
                event_id = str(row["event_id"])
                if not self.identity.store.has_pending_weixin_outbound_event(
                    account_key=account_key, event_id=event_id
                ):
                    completed_events.add(event_id)
            for event_id in completed_events:
                self.dedup.finish(
                    "weixin", account_key, event_id, status="done"
                )
            log_event(
                weixin_logger,
                logging.INFO,
                "channel.weixin.outbox_replay_done",
                account_ref=_safe_account_ref(account_key),
                delivered_count=len(rows),
            )
        finally:
            with self._outbox_lock:
                self._outbox_threads.pop(account_key, None)

    def _set_binding_active(self, account_key: str) -> bool | None:
        account = self.identity.store.get_channel_account(
            channel="weixin", account_key=account_key
        )
        if account is None:
            return None
        changed = str(account["status"]) != "active"
        if changed:
            self.identity.store.update_channel_account_status(
                channel="weixin", account_key=account_key, status="active"
            )
        return changed

    def _mark_reconnect_required(self, account_key: str, code: str) -> None:
        account = self.identity.store.get_channel_account(
            channel="weixin", account_key=account_key
        )
        if account is not None:
            self.identity.store.update_channel_account_status(
                channel="weixin", account_key=account_key, status="reconnect_required"
            )
        self._reauth_accounts.add(account_key)
        self._state = "degraded"
        log_event(
            weixin_logger,
            logging.WARNING,
            "channel.weixin.reconnect_required",
            account_ref=_safe_account_ref(account_key),
            reason_code=code,
        )

    def _log_reconnecting(self, account_key: str, code: str) -> None:
        log_event(
            weixin_logger,
            logging.WARNING,
            "channel.weixin.reconnecting",
            account_ref=_safe_account_ref(account_key),
            reason_code=code,
        )

    def _schedule_account_retry(
        self, account_key: str, credential: dict[str, object]
    ) -> None:
        if self._retry_stop.is_set():
            return
        with self._retry_lock:
            current = self._retry_threads.get(account_key)
            if current is not None and current.is_alive():
                self._retry_requested.add(account_key)
                return
            thread = threading.Thread(
                target=self._retry_account_start,
                args=(account_key, credential),
                name=f"weixin-reconnect-{account_key[-8:]}",
                daemon=True,
            )
            self._retry_threads[account_key] = thread
            thread.start()

    def _retry_account_start(
        self, account_key: str, credential: dict[str, object]
    ) -> None:
        delay = 1.0
        try:
            while not self._retry_stop.wait(delay):
                if account_key in self._reauth_accounts:
                    return
                account = self.identity.store.get_channel_account(
                    channel="weixin", account_key=account_key
                )
                if account is None or str(account["status"]) in {
                    "disabled",
                    "cleanup_pending",
                }:
                    return
                result = self.start_account(
                    account_key, credential, schedule_retry=False
                )
                if result != "retry_pending":
                    return
                delay = min(30.0, delay * 2)
        finally:
            with self._retry_lock:
                self._retry_threads.pop(account_key, None)
                requested = account_key in self._retry_requested
                self._retry_requested.discard(account_key)
            if requested and not self._retry_stop.is_set():
                self._schedule_account_retry(account_key, credential)


def _capture_text(payload: dict[str, object], target: list[str]) -> None:
    if payload.get("type") == "text_delta" and payload.get("content"):
        target.append(str(payload["content"]))


def _safe_account_ref(account_key: str) -> str:
    return "wx-" + uuid.uuid5(uuid.NAMESPACE_URL, account_key).hex[:8]


def _delivery_id(account_key: str, event_id: str, chunk_index: int) -> str:
    value = f"{account_key}\0{event_id}\0{chunk_index}"
    return "zhice-weixin-" + uuid.uuid5(uuid.NAMESPACE_URL, value).hex


def _safe_delivery_ref(delivery_id: str) -> str:
    return "delivery-" + delivery_id[-12:]


def _safe_error_code(exc: Exception) -> str:
    return safe_weixin_error_code(exc, type(exc).__name__)


def _log_message_rejected(
    reason_code: str,
    *,
    account_key: str = "",
    level: int = logging.DEBUG,
) -> None:
    fields: dict[str, object] = {"reason_code": reason_code}
    if account_key:
        fields["account_ref"] = _safe_account_ref(account_key)
    log_event(
        weixin_logger,
        level,
        "channel.weixin.message_rejected",
        **fields,
    )
