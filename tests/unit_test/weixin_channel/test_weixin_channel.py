from __future__ import annotations

import asyncio
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

import agent.channels.weixin.adapter as weixin_adapter_module
from agent.auth.session_access import SessionAccessService
from agent.auth.store import AuthStoreError, SQLiteAuthStore
from agent.auth.user_context import FilesystemUserContextResolver
from agent.channels.config import WeixinChannelConfig
from agent.channels.conversation import ChannelConversationService
from agent.channels.dedup import ChannelDedupService
from agent.channels.identity import ExternalIdentityService
from agent.channels.weixin.adapter import WeixinClawAdapter
from agent.channels.weixin.binding import WeixinBindingService
from agent.channels.weixin.notification import WeixinNotificationProvider
from agent.channels.weixin.outbound import render_chunks
from agent.channels.weixin.sidecar import WeixinSidecarError, safe_weixin_error_code


def test_weixin_error_code_rejects_numeric_dom_exception_code():
    assert safe_weixin_error_code("20", "WEIXIN_POLL_FAILED") == "WEIXIN_POLL_FAILED"


def test_channel_account_uniqueness_keeps_two_users_isolated(tmp_path):
    store, alice, _sessions = _services(tmp_path)
    bob = store.create_user("bob", "Bob", "bob-password")
    store.create_channel_account(
        channel="weixin",
        account_key="opaque-a",
        owner_user_id=alice.id,
        external_account_id="bot-a",
        external_user_id="wx-a",
        credential_ref="channels/weixin/accounts/opaque-a.json",
    )

    with pytest.raises(AuthStoreError, match="conflicts"):
        store.create_channel_account(
            channel="weixin",
            account_key="opaque-b",
            owner_user_id=bob.id,
            external_account_id="bot-a",
            external_user_id="wx-b",
            credential_ref="channels/weixin/accounts/opaque-b.json",
        )
    assert store.get_channel_account_for_user(channel="weixin", owner_user_id=bob.id) is None


def test_channel_account_status_counts_are_aggregate_and_privacy_safe(tmp_path):
    store, alice, _sessions = _services(tmp_path)
    bob = store.create_user("bob", "Bob", "bob-password")
    for account_key, owner_id, suffix in (
        ("opaque-a", alice.id, "a"),
        ("opaque-b", bob.id, "b"),
    ):
        store.create_channel_account(
            channel="weixin",
            account_key=account_key,
            owner_user_id=owner_id,
            external_account_id=f"bot-{suffix}",
            external_user_id=f"wx-{suffix}",
            credential_ref=f"channels/weixin/accounts/{account_key}.json",
        )
    store.update_channel_account_status(
        channel="weixin", account_key="opaque-b", status="reconnect_required"
    )

    counts = store.channel_account_status_counts("weixin")

    assert counts == {"active": 1, "reconnect_required": 1}
    assert "opaque" not in str(counts)


def test_binding_finalize_writes_secret_outside_database_and_status_is_safe(tmp_path):
    store, user, _sessions = _services(tmp_path)
    sidecar = _BindingSidecar()
    binding = WeixinBindingService(store, sidecar, tmp_path)
    actor = store.actor_for_user(user.id, channel="rest")

    attempt = binding.start(actor)
    binding.handle_frame(
        {
            "type": "binding.connected",
            "attempt_id": attempt.attempt_id,
            "external_account_id": "bot-secret-id",
            "external_user_id": "wx-secret-id",
            "credential": {"bot_token": "never-in-db", "base_url": "https://example.invalid"},
        }
    )

    state = binding.status(actor)
    row = store.get_channel_account_for_user(channel="weixin", owner_user_id=user.id)
    credential = binding.credentials.read(str(row["account_key"]))
    assert state == {"status": "active", "linked_at": row["linked_at"]}
    assert credential["bot_token"] == "never-in-db"
    assert "never-in-db" not in str(row)
    assert sidecar.account_started.wait(timeout=1)


def test_binding_finalize_does_not_block_sidecar_reader_on_account_start(tmp_path):
    store, user, _sessions = _services(tmp_path)
    sidecar = _BlockingAccountStartSidecar()
    binding = WeixinBindingService(store, sidecar, tmp_path)
    actor = store.actor_for_user(user.id, channel="rest")
    attempt = binding.start(actor)
    frame = {
        "type": "binding.connected",
        "attempt_id": attempt.attempt_id,
        "external_account_id": "bot-id",
        "external_user_id": "wx-id",
        "credential": {"bot_token": "token", "base_url": "https://example.invalid"},
    }

    finished = threading.Event()
    handler = threading.Thread(target=lambda: (binding.handle_frame(frame), finished.set()))
    handler.start()
    try:
        assert finished.wait(timeout=1), "binding event handler must release the sidecar reader"
        assert sidecar.account_started.wait(timeout=1)
        assert sidecar.release_account_start.is_set() is False
    finally:
        sidecar.release_account_start.set()
        handler.join(timeout=1)


def test_duplicate_and_wrong_sender_do_not_call_runtime(tmp_path, monkeypatch):
    adapter, runtime, sidecar, store = _adapter(tmp_path)
    events = _capture_adapter_events(monkeypatch)
    wrong = _frame(event_id="wrong", sender="wx-other")
    asyncio.run(adapter.handle_frame(wrong))
    valid = _frame(event_id="same", sender="wx-a")
    asyncio.run(adapter.handle_frame(valid))
    asyncio.run(adapter.handle_frame(valid))

    assert len(runtime.calls) == 1
    dispositions = [payload["disposition"] for kind, payload in sidecar.calls if kind == "message.ack"]
    assert dispositions == ["rejected", "accepted", "duplicate"]
    assert store.resolve_external_identity(
        channel="weixin", external_tenant_id="opaque-a", external_user_id="wx-a"
    ) is not None
    reasons = {
        fields.get("reason_code")
        for event, fields in events
        if event in {"channel.weixin.message_rejected", "channel.weixin.message_duplicate"}
    }
    assert reasons == {"WEIXIN_SENDER_MISMATCH", "WEIXIN_MESSAGE_DUPLICATE"}
    assert [event for event, _fields in events].count("channel.weixin.message_accepted") == 1
    assert [event for event, _fields in events].count("channel.weixin.message_done") == 1


def test_disabled_account_does_not_call_runtime(tmp_path, monkeypatch):
    adapter, runtime, sidecar, store = _adapter(tmp_path)
    events = _capture_adapter_events(monkeypatch)
    store.update_channel_account_status(
        channel="weixin", account_key="opaque-a", status="disabled"
    )
    asyncio.run(adapter.handle_frame(_frame(event_id="disabled", sender="wx-a")))
    assert runtime.calls == []
    assert sidecar.calls[-1][1]["disposition"] == "rejected"
    rejected = next(
        fields for event, fields in events if event == "channel.weixin.message_rejected"
    )
    assert rejected["reason_code"] == "WEIXIN_ACCOUNT_INACTIVE"


def test_invalid_and_unknown_account_messages_log_rejection_reasons(tmp_path, monkeypatch):
    adapter, runtime, sidecar, _store = _adapter(tmp_path)
    events = _capture_adapter_events(monkeypatch)
    invalid = _frame(event_id="invalid", sender="wx-a")
    invalid["text"] = ""

    asyncio.run(adapter.handle_frame(invalid))
    asyncio.run(
        adapter.handle_frame(
            _frame(event_id="unknown", sender="wx-a", account_key="opaque-unknown")
        )
    )

    assert runtime.calls == []
    dispositions = [
        payload["disposition"] for kind, payload in sidecar.calls if kind == "message.ack"
    ]
    assert dispositions == ["rejected", "rejected"]
    reasons = {
        fields["reason_code"]
        for event, fields in events
        if event == "channel.weixin.message_rejected"
    }
    assert reasons == {"WEIXIN_EVENT_INVALID", "WEIXIN_ACCOUNT_NOT_FOUND"}


def test_identity_resolution_failure_closes_receipt_with_safe_reason(tmp_path, monkeypatch):
    adapter, runtime, _sidecar, _store = _adapter(tmp_path)
    events = _capture_adapter_events(monkeypatch)
    monkeypatch.setattr(adapter.identity, "resolve", lambda *_args, **_kwargs: None)

    asyncio.run(adapter.handle_frame(_frame(event_id="identity-missing", sender="wx-a")))

    assert runtime.calls == []
    rejected = next(
        fields for event, fields in events if event == "channel.weixin.message_rejected"
    )
    assert rejected["reason_code"] == "WEIXIN_IDENTITY_UNRESOLVED"


def test_inbound_activity_automatically_recovers_reconnect_required_account(
    tmp_path, monkeypatch
):
    adapter, runtime, sidecar, store = _adapter(tmp_path)
    events = _capture_adapter_events(monkeypatch)
    store.update_channel_account_status(
        channel="weixin", account_key="opaque-a", status="reconnect_required"
    )

    asyncio.run(adapter.handle_frame(_frame(event_id="recovered", sender="wx-a")))

    account = store.get_channel_account(channel="weixin", account_key="opaque-a")
    assert account["status"] == "active"
    assert len(runtime.calls) == 1
    dispositions = [payload["disposition"] for kind, payload in sidecar.calls if kind == "message.ack"]
    assert dispositions == ["accepted"]
    reconnected = next(fields for event, fields in events if event == "channel.weixin.reconnected")
    assert reconnected["trigger"] == "inbound_activity"
    assert "channel.weixin.message_accepted" in [event for event, _fields in events]
    assert "channel.weixin.message_done" in [event for event, _fields in events]


def test_single_poll_retry_is_debug_only_and_does_not_degrade_channel(
    tmp_path, monkeypatch
):
    adapter, _runtime, _sidecar, _store = _adapter(tmp_path)
    adapter._state = "available"
    events = _capture_adapter_events(monkeypatch)

    adapter._on_frame(
        {
            "type": "account.poll_retry",
            "account_key": "opaque-a",
            "code": "WEIXIN_POLL_DNS_FAILED",
            "consecutive_failures": 1,
        }
    )

    assert adapter.status().state == "available"
    retry = next(fields for event, fields in events if event == "channel.weixin.poll_retry")
    assert retry["reason_code"] == "WEIXIN_POLL_DNS_FAILED"
    assert retry["consecutive_failures"] == 1
    assert "channel.weixin.reconnecting" not in [event for event, _fields in events]


def test_send_failure_logs_safe_sidecar_error_code(tmp_path, monkeypatch):
    sidecar = _FailingSendSidecar()
    adapter, _runtime, _sidecar, _store = _adapter(tmp_path, sidecar=sidecar)
    events = _capture_adapter_events(monkeypatch)

    adapter._run_inbound_frame(_frame(event_id="send-failed", sender="wx-a"))

    failed = next(fields for event, fields in events if event == "channel.weixin.send_failed")
    assert failed["error_code"] == "CONTEXT_TOKEN_REFERENCE_INVALID"
    assert failed["error_type"] == "WeixinSidecarError"
    event_names = [event for event, _fields in events]
    assert "channel.weixin.message_failed" in event_names
    assert "channel.weixin.inbound_failed" in event_names


def test_send_uses_stable_client_id_long_timeout_and_marks_outbox_sent(tmp_path):
    adapter, runtime, sidecar, store = _adapter(tmp_path)
    frame = _frame(event_id="stable-delivery", sender="wx-a")

    asyncio.run(adapter.handle_frame(frame))

    sends = [payload for kind, payload in sidecar.calls if kind == "message.send"]
    assert len(runtime.calls) == 1
    assert len(sends) == 1
    assert sends[0]["timeout_seconds"] == 20.0
    assert sends[0]["client_id"].startswith("zhice-weixin-")
    assert len(sends[0]["client_id"]) == len("zhice-weixin-") + 32
    assert store.list_pending_weixin_outbound("opaque-a") == []


def test_workflow_notification_uses_latest_owner_context_and_is_idempotent(tmp_path):
    adapter, _runtime, sidecar, store = _adapter(
        tmp_path,
        config=WeixinChannelConfig(enabled=True, text_chunk_limit=4),
    )
    adapter._state = "available"
    account = store.get_channel_account(channel="weixin", account_key="opaque-a")
    store.upsert_weixin_delivery_context(
        account_key="opaque-a", peer="wx-a", context_token_ref="ctx-safe-ref"
    )
    provider = WeixinNotificationProvider(ExternalIdentityService(store))
    provider.register_adapter(adapter)
    actor = store.actor_for_user(str(account["owner_user_id"]), channel="workflow")

    assert provider.capability(actor) == {"available": True, "bound": True, "code": ""}
    first = provider.send_to_user(
        user_id=actor.user_id, content="今日天气晴朗", delivery_key="run-1:notify"
    )
    second = provider.send_to_user(
        user_id=actor.user_id, content="今日天气晴朗", delivery_key="run-1:notify"
    )

    assert first == second == {"status": "sent", "channel": "weixin", "chunks": "2"}
    sends = [payload for kind, payload in sidecar.calls if kind == "message.send"]
    assert [payload["text"] for payload in sends] == ["今日天气", "晴朗"]
    assert len({payload["client_id"] for payload in sends}) == 2
    assert all(payload["context_token_ref"] == "ctx-safe-ref" for payload in sends)
    assert store.list_pending_weixin_outbound("opaque-a") == []


def test_workflow_notification_requires_inbound_context_and_clears_invalid_reference(tmp_path):
    sidecar = _FailingSendSidecar()
    adapter, _runtime, _sidecar, store = _adapter(tmp_path, sidecar=sidecar)
    adapter._state = "available"
    account = store.get_channel_account(channel="weixin", account_key="opaque-a")
    provider = WeixinNotificationProvider(ExternalIdentityService(store))
    provider.register_adapter(adapter)
    actor = store.actor_for_user(str(account["owner_user_id"]), channel="workflow")

    assert provider.capability(actor)["code"] == "WORKFLOW_WEIXIN_CONTEXT_REQUIRED"
    store.upsert_weixin_delivery_context(
        account_key="opaque-a", peer="wx-a", context_token_ref="expired-safe-ref"
    )
    with pytest.raises(RuntimeError, match="WORKFLOW_WEIXIN_CONTEXT_REQUIRED"):
        provider.send_to_user(
            user_id=actor.user_id, content="测试消息", delivery_key="run-2:notify"
        )
    assert store.get_weixin_delivery_context(account_key="opaque-a", peer="wx-a") is None


def test_delivery_context_refreshes_pending_outbox_and_unlink_cleans_it(tmp_path):
    adapter, _runtime, _sidecar, store = _adapter(tmp_path)
    store.enqueue_weixin_outbound(
        delivery_id="zhice-weixin-0123456789abcdef0123456789abcdef",
        account_key="opaque-a",
        event_id="legacy-event",
        peer="wx-a",
        context_token_ref="old-safe-ref",
        chunk_index=0,
        text="pending",
    )

    store.upsert_weixin_delivery_context(
        account_key="opaque-a", peer="wx-a", context_token_ref="new-safe-ref"
    )

    pending = store.list_pending_weixin_outbound("opaque-a")
    assert pending[0]["context_token_ref"] == "new-safe-ref"
    assert store.get_weixin_delivery_context(account_key="opaque-a", peer="wx-a") == {
        "account_key": "opaque-a",
        "peer": "wx-a",
        "context_token_ref": "new-safe-ref",
        "updated_at": store.get_weixin_delivery_context(account_key="opaque-a", peer="wx-a")["updated_at"],
    }
    actor = store.actor_for_user(
        str(store.get_channel_account(channel="weixin", account_key="opaque-a")["owner_user_id"]),
        channel="rest",
    )
    adapter.binding.unlink(actor)
    assert store.get_weixin_delivery_context(account_key="opaque-a", peer="wx-a") is None


def test_send_failure_reconnects_and_replays_without_rerunning_agent(tmp_path, monkeypatch):
    sidecar = _RecoveringSendSidecar()
    adapter, runtime, _sidecar, store = _adapter(tmp_path, sidecar=sidecar)
    staged = adapter.binding.credentials.stage(
        "opaque-a", {"bot_token": "secret", "external_user_id": "wx-a"}
    )
    adapter.binding.credentials.promote(staged, "opaque-a")
    events = _capture_adapter_events(monkeypatch)

    adapter._run_inbound_frame(_frame(event_id="replay-after-reconnect", sender="wx-a"))

    assert sidecar.recovered.wait(timeout=3.0)
    assert _wait_for(lambda: sidecar.send_attempts == 2, timeout=3.0)
    assert _wait_for(
        lambda: store.list_pending_weixin_outbound("opaque-a") == [], timeout=1.0
    )
    assert len(runtime.calls) == 1
    assert len(set(sidecar.client_ids)) == 1
    assert sidecar.timeouts == [20.0, 20.0]
    event_names = [event for event, _fields in events]
    assert "channel.weixin.reconnecting" in event_names
    assert "channel.weixin.outbox_replay_start" in event_names
    assert _wait_for(
        lambda: any(
            event == "channel.weixin.outbox_replay_done"
            for event, _fields in events
        ),
        timeout=1.0,
    )


def test_new_adapter_process_recovers_persisted_pending_outbox(tmp_path):
    failing = _FailingSendSidecar()
    adapter, first_runtime, _sidecar, store = _adapter(tmp_path, sidecar=failing)
    adapter._run_inbound_frame(_frame(event_id="persisted-pending", sender="wx-a"))
    pending = store.list_pending_weixin_outbound("opaque-a")
    assert len(pending) == 1

    recovered_sidecar = _Sidecar()
    second_runtime = _Runtime()
    binding = WeixinBindingService(store, recovered_sidecar, tmp_path)
    restarted = WeixinClawAdapter(
        WeixinChannelConfig(enabled=True),
        recovered_sidecar,
        binding,
        ExternalIdentityService(store),
        adapter.conversations,
        ChannelDedupService(store),
        second_runtime,
    )
    restarted._mark_active("opaque-a", trigger="account_start")

    assert _wait_for(
        lambda: store.list_pending_weixin_outbound("opaque-a") == [], timeout=2.0
    )
    sends = [payload for kind, payload in recovered_sidecar.calls if kind == "message.send"]
    assert len(first_runtime.calls) == 1
    assert second_runtime.calls == []
    assert [payload["client_id"] for payload in sends] == [pending[0]["delivery_id"]]


def test_partial_chunk_failure_replays_only_unsent_chunks(tmp_path):
    sidecar = _PartialRecoveringSendSidecar()
    adapter, runtime, _sidecar, store = _adapter(
        tmp_path,
        sidecar=sidecar,
        config=WeixinChannelConfig(enabled=True, text_chunk_limit=2),
    )
    staged = adapter.binding.credentials.stage(
        "opaque-a", {"bot_token": "secret", "external_user_id": "wx-a"}
    )
    adapter.binding.credentials.promote(staged, "opaque-a")

    adapter._run_inbound_frame(_frame(event_id="partial-chunks", sender="wx-a"))

    assert sidecar.recovered.wait(timeout=3.0)
    assert _wait_for(
        lambda: store.list_pending_weixin_outbound("opaque-a") == [], timeout=3.0
    )
    assert len(runtime.calls) == 1
    counts = {
        client_id: sidecar.client_ids.count(client_id)
        for client_id in sidecar.client_ids
    }
    assert sorted(counts.values()) == [1, 1, 2]


def test_ack_failure_is_logged_without_dropping_the_turn(tmp_path, monkeypatch):
    sidecar = _AckFailingSidecar()
    adapter, runtime, _sidecar, _store = _adapter(tmp_path, sidecar=sidecar)
    events = _capture_adapter_events(monkeypatch)

    asyncio.run(adapter.handle_frame(_frame(event_id="ack-failed", sender="wx-a")))

    assert len(runtime.calls) == 1
    failed = next(fields for event, fields in events if event == "channel.weixin.ack_failed")
    assert failed["disposition"] == "accepted"
    assert failed["error_code"] == "SIDECAR_REQUEST_TIMEOUT"
    assert "channel.weixin.message_done" in [event for event, _fields in events]


def test_transient_account_start_failure_keeps_binding_active(tmp_path, monkeypatch):
    sidecar = _AccountStartFailingSidecar("SIDECAR_REQUEST_TIMEOUT")
    adapter, _runtime, _sidecar, store = _adapter(tmp_path, sidecar=sidecar)
    events = _capture_adapter_events(monkeypatch)

    status = adapter.start_account(
        "opaque-a", {"bot_token": "secret"}, schedule_retry=False
    )

    account = store.get_channel_account(channel="weixin", account_key="opaque-a")
    assert status == "retry_pending"
    assert account["status"] == "active"
    assert adapter.status().state == "degraded"
    reconnecting = next(fields for event, fields in events if event == "channel.weixin.reconnecting")
    assert reconnecting["reason_code"] == "SIDECAR_REQUEST_TIMEOUT"
    assert sidecar.account_start_payload["timeout_seconds"] == 15.0


def test_only_explicit_stale_token_requires_reauthentication(tmp_path, monkeypatch):
    sidecar = _AccountStartFailingSidecar("WEIXIN_TOKEN_STALE")
    adapter, _runtime, _sidecar, store = _adapter(tmp_path, sidecar=sidecar)
    events = _capture_adapter_events(monkeypatch)

    status = adapter.start_account(
        "opaque-a", {"bot_token": "secret"}, schedule_retry=False
    )

    account = store.get_channel_account(channel="weixin", account_key="opaque-a")
    assert status == "reconnect_required"
    assert account["status"] == "reconnect_required"
    assert adapter.status().state == "degraded"
    required = next(
        fields for event, fields in events if event == "channel.weixin.reconnect_required"
    )
    assert required["reason_code"] == "WEIXIN_TOKEN_STALE"


def test_transient_account_start_failure_retries_automatically(tmp_path):
    sidecar = _RecoveringAccountStartSidecar()
    adapter, _runtime, _sidecar, store = _adapter(tmp_path, sidecar=sidecar)

    status = adapter.start_account("opaque-a", {"bot_token": "secret"})

    assert status == "retry_pending"
    assert sidecar.recovered.wait(timeout=2.5)
    assert _wait_for(lambda: adapter.status().state == "available", timeout=1.0)
    account = store.get_channel_account(channel="weixin", account_key="opaque-a")
    assert account["status"] == "active"
    assert sidecar.start_attempts == 2
    assert adapter.status().state == "available"


def test_gateway_start_stays_degraded_until_account_polling_is_ready(tmp_path):
    sidecar = _DegradedAccountStartSidecar()
    adapter, _runtime, _sidecar, store = _adapter(tmp_path, sidecar=sidecar)
    staged = adapter.binding.credentials.stage(
        "opaque-a", {"bot_token": "secret", "external_user_id": "wx-a"}
    )
    adapter.binding.credentials.promote(staged, "opaque-a")

    adapter.start()
    try:
        account = store.get_channel_account(channel="weixin", account_key="opaque-a")
        assert account["status"] == "active"
        assert adapter.status().state == "degraded"
    finally:
        adapter.stop()


def test_two_accounts_dispatch_as_two_internal_actors(tmp_path):
    adapter, runtime, _sidecar, store = _adapter(tmp_path)
    bob = store.create_user("bob", "Bob", "bob-password")
    store.create_channel_account(
        channel="weixin",
        account_key="opaque-b",
        owner_user_id=bob.id,
        external_account_id="bot-b",
        external_user_id="wx-b",
        credential_ref="channels/weixin/accounts/opaque-b.json",
    )
    asyncio.run(adapter.handle_frame(_frame(event_id="a", sender="wx-a")))
    asyncio.run(
        adapter.handle_frame(
            _frame(event_id="b", sender="wx-b", account_key="opaque-b")
        )
    )
    assert [str(call[0][0].user_id) for call in runtime.calls] == [
        str(store.get_channel_account(channel="weixin", account_key="opaque-a")["owner_user_id"]),
        bob.id,
    ]


def test_plain_text_rendering_and_4000_character_boundary():
    chunks = render_chunks("**heading**\n\n" + "x" * 8001, 4000)
    assert all(len(chunk) <= 4000 for chunk in chunks)
    assert "**" not in chunks[0]
    assert "".join(chunks).replace("\n", "").startswith("heading")


def test_web_settings_exposes_binding_status_cancel_reconnect_and_unlink():
    root = Path(__file__).resolve().parents[3]
    settings = (root / "web" / "frontend" / "src" / "components" / "SettingsCenter.vue").read_text(
        encoding="utf-8"
    )
    channels = (root / "web" / "frontend" / "src" / "stores" / "channels.ts").read_text(
        encoding="utf-8"
    )
    client = (root / "web" / "frontend" / "src" / "api" / "client.ts").read_text(
        encoding="utf-8"
    )

    assert all(
        action in settings
        for action in ("startWeixin", "cancelWeixin", "reconnectWeixin", "unlinkWeixin")
    )
    assert "weixinAttempt?.qr_data" in settings
    assert "schedulePoll" in channels
    assert "/api/channels/weixin/reconnect" in client
    assert 'cache: "no-store"' in client


def test_unlink_removes_live_binding_but_keeps_session_index(tmp_path):
    adapter, _runtime, _sidecar, store = _adapter(tmp_path)
    actor = store.actor_for_user(
        store.get_channel_account(channel="weixin", account_key="opaque-a")["owner_user_id"],
        channel="rest",
    )
    store.session_index_create(
        session_id="weixin_history",
        owner_user_id=str(actor.user_id),
        channel="weixin",
        conversation_type="c2c",
        external_chat_id="safe-chat",
    )
    store.enqueue_weixin_outbound(
        delivery_id="zhice-weixin-0123456789abcdef0123456789abcdef",
        account_key="opaque-a",
        event_id="pending-before-unlink",
        peer="wx-a",
        context_token_ref="ctx-safe-ref",
        chunk_index=0,
        text="pending reply",
    )
    assert adapter.binding.unlink(actor) == "unbound"
    assert store.get_channel_account(channel="weixin", account_key="opaque-a") is None
    assert store.list_pending_weixin_outbound("opaque-a") == []
    assert store.session_index_get("weixin_history") is not None


def _services(tmp_path):
    store = SQLiteAuthStore(tmp_path / "state" / "auth.sqlite3")
    store.initialize_schema()
    user = store.create_user("alice", "Alice", "alice-password")
    sessions = SessionAccessService(
        store,
        FilesystemUserContextResolver(tmp_path / "contexts", workspace_dir=tmp_path),
    )
    return store, user, sessions


def _adapter(tmp_path, *, sidecar=None, config=None):
    store, user, sessions = _services(tmp_path)
    store.create_channel_account(
        channel="weixin",
        account_key="opaque-a",
        owner_user_id=user.id,
        external_account_id="bot-a",
        external_user_id="wx-a",
        credential_ref="channels/weixin/accounts/opaque-a.json",
    )
    sidecar = sidecar or _Sidecar()
    runtime = _Runtime()
    binding = WeixinBindingService(store, sidecar, tmp_path)
    adapter = WeixinClawAdapter(
        config or WeixinChannelConfig(enabled=True),
        sidecar,
        binding,
        ExternalIdentityService(store),
        ChannelConversationService(store, sessions),
        ChannelDedupService(store),
        runtime,
    )
    return adapter, runtime, sidecar, store


def _frame(*, event_id: str, sender: str, account_key: str = "opaque-a"):
    return {
        "protocol_version": "1",
        "type": "message.received",
        "account_key": account_key,
        "event_id": event_id,
        "message_id": "message-1",
        "conversation_type": "c2c",
        "external_user_id": sender,
        "text": "hello",
        "context_token_ref": "ctx-safe-ref",
    }


class _Sidecar:
    failure = ""

    def __init__(self):
        self.calls = []

    def set_event_handler(self, handler):
        self.handler = handler

    def request(self, frame_type, **payload):
        self.calls.append((frame_type, payload))
        if frame_type == "message.send":
            return {"type": "message.send_result", "status": "sent"}
        return {"type": f"{frame_type}_result", "status": "ok"}

    def stop(self):
        pass


class _FailingSendSidecar(_Sidecar):
    def request(self, frame_type, **payload):
        if frame_type == "message.send":
            raise WeixinSidecarError("CONTEXT_TOKEN_REFERENCE_INVALID")
        return super().request(frame_type, **payload)


class _AckFailingSidecar(_Sidecar):
    def request(self, frame_type, **payload):
        if frame_type == "message.ack":
            raise WeixinSidecarError("SIDECAR_REQUEST_TIMEOUT")
        return super().request(frame_type, **payload)


class _AccountStartFailingSidecar(_Sidecar):
    def __init__(self, code):
        super().__init__()
        self.code = code

    def request(self, frame_type, **payload):
        if frame_type == "account.start":
            self.account_start_payload = payload
            raise WeixinSidecarError(self.code)
        return super().request(frame_type, **payload)


class _RecoveringAccountStartSidecar(_Sidecar):
    def __init__(self):
        super().__init__()
        self.start_attempts = 0
        self.recovered = threading.Event()

    def request(self, frame_type, **payload):
        if frame_type == "account.start":
            self.start_attempts += 1
            if self.start_attempts == 1:
                raise WeixinSidecarError("SIDECAR_REQUEST_TIMEOUT")
            self.recovered.set()
            return {"type": "account.status", "status": "active", "code": "OK"}
        return super().request(frame_type, **payload)


class _RecoveringSendSidecar(_Sidecar):
    def __init__(self):
        super().__init__()
        self.send_attempts = 0
        self.client_ids = []
        self.timeouts = []
        self.recovered = threading.Event()

    def request(self, frame_type, **payload):
        self.calls.append((frame_type, payload))
        if frame_type == "message.send":
            self.send_attempts += 1
            self.client_ids.append(payload["client_id"])
            self.timeouts.append(payload["timeout_seconds"])
            if self.send_attempts == 1:
                raise WeixinSidecarError("SIDECAR_REQUEST_TIMEOUT")
            return {"type": "message.send_result", "status": "sent"}
        if frame_type == "account.start":
            self.recovered.set()
            return {"type": "account.status", "status": "active", "code": "OK"}
        return {"type": f"{frame_type}_result", "status": "ok"}


class _PartialRecoveringSendSidecar(_RecoveringSendSidecar):
    def request(self, frame_type, **payload):
        if frame_type != "message.send":
            return super().request(frame_type, **payload)
        self.calls.append((frame_type, payload))
        self.send_attempts += 1
        self.client_ids.append(payload["client_id"])
        self.timeouts.append(payload["timeout_seconds"])
        if self.send_attempts == 2:
            raise WeixinSidecarError("SIDECAR_REQUEST_TIMEOUT")
        return {"type": "message.send_result", "status": "sent"}


class _DegradedAccountStartSidecar(_Sidecar):
    def start(self):
        pass

    def request(self, frame_type, **payload):
        if frame_type == "account.start":
            return {
                "type": "account.status",
                "status": "degraded",
                "code": "WEIXIN_NOTIFY_START_FAILED",
            }
        if frame_type == "health.get":
            return {"type": "health.status", "status": "available", "code": "OK"}
        return super().request(frame_type, **payload)


class _BindingSidecar(_Sidecar):
    def __init__(self):
        super().__init__()
        self.account_started = threading.Event()

    def request(self, frame_type, **payload):
        self.calls.append((frame_type, payload))
        if frame_type == "binding.start":
            return {
                "type": "binding.qr",
                "attempt_id": payload["attempt_id"],
                "qr_data": "data:image/png;base64,safe",
            }
        if frame_type == "account.start":
            self.account_started.set()
            return {"type": "account.status", "status": "active"}
        return {"type": f"{frame_type}_result", "status": "ok"}


class _BlockingAccountStartSidecar(_BindingSidecar):
    def __init__(self):
        super().__init__()
        self.release_account_start = threading.Event()

    def request(self, frame_type, **payload):
        if frame_type != "account.start":
            return super().request(frame_type, **payload)
        self.calls.append((frame_type, payload))
        self.account_started.set()
        self.release_account_start.wait(timeout=3)
        return {"type": "account.status", "status": "active"}


class _Runtime:
    def __init__(self):
        self.calls = []

    def dispatch(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        return SimpleNamespace(content="**reply**")


def _capture_adapter_events(monkeypatch):
    events = []
    monkeypatch.setattr(
        weixin_adapter_module,
        "log_event",
        lambda _logger, _level, event, **fields: events.append((event, fields)),
    )
    return events


def _wait_for(predicate, *, timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return predicate()
