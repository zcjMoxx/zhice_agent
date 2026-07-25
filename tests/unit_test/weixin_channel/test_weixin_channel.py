from __future__ import annotations

import asyncio
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

from agent.auth.session_access import SessionAccessService
from agent.auth.store import AuthStoreError, SQLiteAuthStore
from agent.auth.user_context import FilesystemUserContextResolver
from agent.channels.config import WeixinChannelConfig
from agent.channels.conversation import ChannelConversationService
from agent.channels.dedup import ChannelDedupService
from agent.channels.identity import ExternalIdentityService
from agent.channels.weixin.adapter import WeixinClawAdapter
from agent.channels.weixin.binding import WeixinBindingService
from agent.channels.weixin.outbound import render_chunks


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


def test_duplicate_and_wrong_sender_do_not_call_runtime(tmp_path):
    adapter, runtime, sidecar, store = _adapter(tmp_path)
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


def test_disabled_account_does_not_call_runtime(tmp_path):
    adapter, runtime, sidecar, store = _adapter(tmp_path)
    store.update_channel_account_status(
        channel="weixin", account_key="opaque-a", status="disabled"
    )
    asyncio.run(adapter.handle_frame(_frame(event_id="disabled", sender="wx-a")))
    assert runtime.calls == []
    assert sidecar.calls[-1][1]["disposition"] == "rejected"


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
    index = (root / "web" / "static" / "index.html").read_text(encoding="utf-8")
    script = (root / "web" / "static" / "app.js").read_text(encoding="utf-8")
    for element_id in (
        "weixinBindingState",
        "weixinQr",
        "weixinBindButton",
        "weixinCancelButton",
        "weixinReconnectButton",
        "weixinUnbindButton",
    ):
        assert f'id="{element_id}"' in index
    assert 'fetch("/api/channels/weixin/reconnect"' in script
    assert 'cache: "no-store"' in script


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
    assert adapter.binding.unlink(actor) == "unbound"
    assert store.get_channel_account(channel="weixin", account_key="opaque-a") is None
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


def _adapter(tmp_path):
    store, user, sessions = _services(tmp_path)
    store.create_channel_account(
        channel="weixin",
        account_key="opaque-a",
        owner_user_id=user.id,
        external_account_id="bot-a",
        external_user_id="wx-a",
        credential_ref="channels/weixin/accounts/opaque-a.json",
    )
    sidecar = _Sidecar()
    runtime = _Runtime()
    binding = WeixinBindingService(store, sidecar, tmp_path)
    adapter = WeixinClawAdapter(
        WeixinChannelConfig(enabled=True),
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
        return {"type": f"{frame_type}_result", "status": "ok"}

    def stop(self):
        pass


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
