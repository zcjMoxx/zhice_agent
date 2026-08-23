from __future__ import annotations

import asyncio
import io
import logging
import sqlite3
import threading
from dataclasses import dataclass, replace
from types import SimpleNamespace

import pytest

from agent.auth.session_access import SessionAccessService
from agent.auth.store import ExternalIdentityConflictError, SQLiteAuthStore
from agent.auth.user_context import FilesystemUserContextResolver
from agent.channels.config import (
    ChannelConfigurationError,
    QQAccountConfig,
    load_channel_configuration,
)
from agent.channels.conversation import ChannelConversationService
from agent.channels.dedup import ChannelDedupService
from agent.channels.identity import ExternalIdentityService
from agent.channels.limits import SlidingWindowRateLimiter
from agent.channels.manager import ChannelManager
from agent.channels.qq.adapter import QQChannelAdapter
from agent.channels.qq.attachments import QQAttachmentError, _validate_public_http_url
from agent.channels.qq.notification import QQNotificationProvider
from agent.channels.qq.outbound import (
    QQOutboundButton,
    QQOutboundMessage,
    build_agent_markdown,
    build_binding_prompt,
    chunk_text,
)
from agent.channels.qq.startup import check_qq_startup
from agent.channels.qq.transport import (
    BotpyQQTransport,
    QQSendUnconfirmedError,
    _BotpyConsoleHandler,
)
from agent.channels.runtime_adapter import ChannelRuntimeAdapter
from agent.protocols.capability import CapabilityStatus
from agent.protocols.channel import (
    ChannelCapabilities,
    ChannelExecutionContext,
    ChannelReplyTarget,
    InboundChannelEvent,
)


def test_channel_manager_clears_start_failure_after_adapter_recovers():
    class RecoveringAdapter:
        key = "channel.weixin"

        def __init__(self):
            self.current = CapabilityStatus(
                self.key, "degraded", "CHANNEL_WEIXIN_DEGRADED"
            )

        def start(self):
            raise FileNotFoundError("credential unavailable")

        def stop(self):
            pass

        def status(self):
            return self.current

    adapter = RecoveringAdapter()
    manager = ChannelManager((adapter,))

    manager.start()

    failed = manager.statuses()[adapter.key]
    assert failed.state == "unavailable"
    assert failed.code == "CHANNEL_START_FAILED"

    adapter.current = CapabilityStatus(
        adapter.key, "available", "CHANNEL_WEIXIN_AVAILABLE"
    )

    recovered = manager.statuses()[adapter.key]
    assert recovered.state == "available"
    assert recovered.code == "CHANNEL_WEIXIN_AVAILABLE"
    assert manager.statuses()[adapter.key] == recovered


def test_missing_channel_config_is_disabled(tmp_path):
    config = load_channel_configuration(tmp_path)
    assert config.qq.enabled is False
    assert config.qq.accounts == ()


def test_channel_config_resolves_env_and_hides_secret_from_repr(tmp_path, monkeypatch):
    monkeypatch.setenv("QQ_APP", "app-123")
    monkeypatch.setenv("QQ_SECRET", "secret-456")
    (tmp_path / "config.yml").write_text(
        """
channels:
  qq:
    enabled: true
    transport: websocket
    accounts:
      - key: main
        app_id: ${QQ_APP}
        app_secret: ${QQ_SECRET}
        web_base_url: https://public.example.test
""",
        encoding="utf-8",
    )

    config = load_channel_configuration(tmp_path)

    assert config.qq.accounts[0].app_id == "app-123"
    assert config.qq.accounts[0].app_secret == "secret-456"
    assert config.qq.accounts[0].web_base_url == "https://public.example.test"
    assert config.qq.accounts[0].http_timeout_seconds == 15
    assert "secret-456" not in repr(config.qq.accounts[0])


def test_qq_account_default_web_base_url_remains_loopback():
    account = QQAccountConfig(key="main", app_id="app", app_secret="secret")

    assert account.web_base_url == "http://127.0.0.1:10086"


def test_channel_config_preserves_declared_external_channel_order(tmp_path):
    (tmp_path / "config.yml").write_text(
        """
channels:
  weixin:
    enabled: true
  qq:
    enabled: false
""",
        encoding="utf-8",
    )

    config = load_channel_configuration(tmp_path)

    assert config.order == ("weixin", "qq")


def test_channel_config_rejects_duplicate_accounts(tmp_path):
    (tmp_path / "config.yml").write_text(
        """
channels:
  qq:
    enabled: true
    accounts:
      - {key: main, app_id: a, app_secret: b}
      - {key: main, app_id: c, app_secret: d}
""",
        encoding="utf-8",
    )
    with pytest.raises(ChannelConfigurationError, match="unique"):
        load_channel_configuration(tmp_path)


@pytest.mark.parametrize("timeout", [0, 61])
def test_channel_config_rejects_unsafe_qq_http_timeout(tmp_path, timeout):
    (tmp_path / "config.yml").write_text(
        f"""
channels:
  qq:
    enabled: true
    accounts:
      - key: main
        app_id: app
        app_secret: secret
        http_timeout_seconds: {timeout}
""",
        encoding="utf-8",
    )

    with pytest.raises(ChannelConfigurationError, match="between 1 and 60"):
        load_channel_configuration(tmp_path)


def test_identity_link_code_is_hashed_scoped_and_single_use(tmp_path):
    store, user, _sessions = _services(tmp_path)
    service = ExternalIdentityService(store, ttl_seconds=600)
    link = service.create_link_code(user.id, "qq", "main")

    with sqlite3.connect(store.path) as connection:
        stored = connection.execute(
            "SELECT token_hash FROM external_identity_link_tokens"
        ).fetchone()[0]
    assert stored != link.code
    assert service.bind(
        code=link.code,
        channel="qq",
        account_key="other",
        external_user_id="openid",
    ) is None
    actor = service.bind(
        code=link.code,
        channel="qq",
        account_key="main",
        external_user_id="openid",
    )

    assert actor is not None and actor.user_id == user.id
    assert service.bind(
        code=link.code,
        channel="qq",
        account_key="main",
        external_user_id="openid-2",
    ) is None


def test_web_authorization_request_binds_current_user_once(tmp_path):
    store, user, _sessions = _services(tmp_path)
    service = ExternalIdentityService(store)
    request = service.create_authorization_request(
        channel="qq",
        account_key="main",
        external_user_id="openid-web",
    )
    actor = store.actor_for_user(user.id, channel="rest")

    with sqlite3.connect(store.path) as connection:
        stored_hash = connection.execute(
            "SELECT token_hash FROM external_identity_authorization_requests"
        ).fetchone()[0]

    assert stored_hash != request.token
    assert service.authorize(request.token, actor) is True
    assert service.authorize(request.token, actor) is False
    assert service.resolve("qq", "main", "openid-web").user_id == user.id


def test_web_authorization_requires_unlink_before_binding_another_qq(tmp_path):
    store, user, _sessions = _services(tmp_path)
    service = ExternalIdentityService(store)
    store.link_external_identity(
        user_id=user.id,
        channel="qq",
        external_tenant_id="main",
        external_user_id="openid-first",
    )
    request = service.create_authorization_request(
        channel="qq",
        account_key="main",
        external_user_id="openid-second",
    )
    actor = store.actor_for_user(user.id, channel="rest")

    with pytest.raises(ExternalIdentityConflictError) as exc_info:
        service.authorize(request.token, actor)

    assert exc_info.value.reason == "user_already_bound"
    assert service.resolve("qq", "main", "openid-first").user_id == user.id
    assert service.resolve("qq", "main", "openid-second") is None
    binding = service.list_bindings(actor)[0]
    assert service.unlink(actor, binding.binding_id)
    assert service.authorize(request.token, actor) is True
    assert service.resolve("qq", "main", "openid-second").user_id == user.id


def test_web_authorization_request_expiry_fails_closed(tmp_path):
    store, user, _sessions = _services(tmp_path)
    service = ExternalIdentityService(store)
    request = service.create_authorization_request(
        channel="qq",
        account_key="main",
        external_user_id="openid-expired",
    )
    with sqlite3.connect(store.path) as connection:
        connection.execute(
            "UPDATE external_identity_authorization_requests SET expires_at=?",
            ("2000-01-01T00:00:00+00:00",),
        )

    assert service.authorize(request.token, store.actor_for_user(user.id, channel="rest")) is False
    assert service.resolve("qq", "main", "openid-expired") is None


def test_web_authorization_does_not_reassign_existing_identity(tmp_path):
    store, user, _sessions = _services(tmp_path)
    other = store.create_user("bob", "Bob", "bob-password")
    store.link_external_identity(
        user_id=other.id,
        channel="qq",
        external_tenant_id="main",
        external_user_id="openid-existing",
    )
    service = ExternalIdentityService(store)
    request = service.create_authorization_request(
        channel="qq",
        account_key="main",
        external_user_id="openid-existing",
    )

    assert service.authorize(request.token, store.actor_for_user(user.id, channel="rest")) is False
    assert service.resolve("qq", "main", "openid-existing").user_id == other.id


def test_multi_account_external_identity_does_not_collide(tmp_path):
    store, user, _sessions = _services(tmp_path)
    other = store.create_user("bob", "Bob", "bob-password")
    store.link_external_identity(
        user_id=user.id,
        channel="qq",
        external_tenant_id="main",
        external_user_id="same-openid",
    )
    store.link_external_identity(
        user_id=other.id,
        channel="qq",
        external_tenant_id="secondary",
        external_user_id="same-openid",
    )

    assert store.resolve_external_identity(
        channel="qq", external_tenant_id="main", external_user_id="same-openid"
    ).user_id == user.id
    assert store.resolve_external_identity(
        channel="qq", external_tenant_id="secondary", external_user_id="same-openid"
    ).user_id == other.id


def test_conversation_route_reuses_and_rotates_session(tmp_path):
    store, user, sessions = _services(tmp_path)
    actor = store.actor_for_user(user.id, channel="qq")
    service = ChannelConversationService(store, sessions)
    context = _context("c2c", "openid")

    first = service.resolve(actor, context)
    same = ChannelConversationService(store, sessions).resolve(actor, context)
    rotated = service.rotate(actor, context)

    assert same.session_id == first.session_id
    assert rotated.session_id != first.session_id
    assert store.session_index_get(first.session_id) is not None
    assert service.resolve(actor, context).session_id == rotated.session_id
    assert "openid" not in rotated.session_id


def test_deleting_external_session_drops_route_and_next_message_starts_fresh(tmp_path):
    store, user, sessions = _services(tmp_path)
    actor = store.actor_for_user(user.id, channel="weixin")
    service = ChannelConversationService(store, sessions)
    context = replace(
        _context("c2c", "weixin-peer"),
        channel="weixin",
        account_key="owner",
        capabilities=ChannelCapabilities(command_profile="weixin_c2c"),
    )
    first = service.resolve(actor, context)

    sessions.delete_session(actor, first.session_id)
    replacement = service.resolve(actor, context)

    assert store.session_index_get(first.session_id) is None
    assert replacement.session_id != first.session_id
    assert replacement.session_id.startswith("weixin_")


def test_group_routes_are_isolated_by_internal_user(tmp_path):
    store, first, sessions = _services(tmp_path)
    second = store.create_user("bob", "Bob", "bob-password")
    service = ChannelConversationService(store, sessions)
    context = _context("group", "group-1")

    first_route = service.resolve(store.actor_for_user(first.id, channel="qq"), context)
    second_route = service.resolve(store.actor_for_user(second.id, channel="qq"), context)

    assert first_route.session_id != second_route.session_id


def test_persistent_dedup_survives_service_recreation(tmp_path):
    store, _user, _sessions = _services(tmp_path)
    first = ChannelDedupService(store)
    assert first.claim("qq", "main", "event-1", "message-1") is True
    first.finish("qq", "main", "event-1")
    assert ChannelDedupService(store).claim("qq", "main", "event-1") is False


def test_channel_runtime_adapter_rotates_new_without_calling_agent(tmp_path):
    store, user, sessions = _services(tmp_path)
    actor = store.actor_for_user(user.id, channel="qq")
    fake = _FakeRuntime()
    adapter = ChannelRuntimeAdapter(fake, ChannelConversationService(store, sessions))
    events = []

    result = adapter.dispatch(
        actor,
        "/new",
        turn_id="turn-1",
        on_event=events.append,
        request_id="request-1",
        channel_context=_context("c2c", "openid"),
    )

    assert fake.calls == []
    assert result.content.startswith("New session:")
    assert events[0]["type"] == "text_delta"
    assert len(store.session_index_list(user.id)) == 1


def test_qq_unbound_message_does_not_call_runtime(tmp_path):
    store, _user, sessions = _services(tmp_path)
    runtime = _FakeChannelRuntime()
    transport = _FakeTransport()
    adapter = QQChannelAdapter(
        _Account(),
        transport,
        ExternalIdentityService(store),
        ChannelConversationService(store, sessions),
        ChannelDedupService(store),
        runtime,
    )

    asyncio.run(adapter.handle_event(_event("event-unbound")))

    assert runtime.calls == []
    assert "尚未绑定" in transport.rich[0].markdown
    assert transport.rich[0].text == ""
    assert transport.rich[0].buttons[0].label == "绑定"
    assert transport.rich[0].buttons[0].action == "command"
    assert transport.rich[0].buttons[0].data == "/bind"


def test_qq_bare_bind_returns_web_authorization_link(tmp_path):
    store, _user, sessions = _services(tmp_path)
    runtime = _FakeChannelRuntime()
    transport = _FakeTransport()
    adapter = QQChannelAdapter(
        _Account(web_base_url="https://public.example.test"),
        transport,
        ExternalIdentityService(store),
        ChannelConversationService(store, sessions),
        ChannelDedupService(store),
        runtime,
    )

    asyncio.run(adapter.handle_event(replace(_event("event-bind-link"), text="/bind")))

    assert runtime.calls == []
    outbound = transport.rich[0]
    assert "[登录并绑定智策 Agent](https://public.example.test/bind/qq?token=" in outbound.markdown
    assert outbound.buttons[0].label == "登录并绑定"
    assert outbound.buttons[0].action == "url"
    assert outbound.buttons[0].data.startswith("https://public.example.test/bind/qq?token=")


def test_qq_manual_bind_code_still_binds_directly(tmp_path):
    store, user, sessions = _services(tmp_path)
    identity = ExternalIdentityService(store)
    link = identity.create_link_code(user.id, "qq", "main")
    runtime = _FakeChannelRuntime()
    transport = _FakeTransport()
    adapter = QQChannelAdapter(
        _Account(),
        transport,
        identity,
        ChannelConversationService(store, sessions),
        ChannelDedupService(store),
        runtime,
    )

    asyncio.run(
        adapter.handle_event(
            replace(_event("event-bind-code"), text=f"/bind {link.code}")
        )
    )

    assert transport.sent == ["QQ 身份绑定成功。"]
    assert identity.resolve("qq", "main", "openid").user_id == user.id


def test_qq_manual_bind_requires_unlink_before_binding_another_qq(tmp_path):
    store, user, sessions = _services(tmp_path)
    identity = ExternalIdentityService(store)
    store.link_external_identity(
        user_id=user.id,
        channel="qq",
        external_tenant_id="main",
        external_user_id="already-bound-openid",
    )
    link = identity.create_link_code(user.id, "qq", "main")
    runtime = _FakeChannelRuntime()
    transport = _FakeTransport()
    adapter = QQChannelAdapter(
        _Account(),
        transport,
        identity,
        ChannelConversationService(store, sessions),
        ChannelDedupService(store),
        runtime,
    )

    asyncio.run(
        adapter.handle_event(
            replace(_event("event-bind-conflict"), text=f"/bind {link.code}")
        )
    )

    assert transport.sent == [
        "当前 ZhiCe-Agent 账号已经绑定其他 QQ，请先在网页渠道连接中解绑。"
    ]
    assert identity.resolve("qq", "main", "already-bound-openid").user_id == user.id
    assert identity.resolve("qq", "main", "openid") is None
    assert runtime.calls == []


def test_qq_group_manual_bind_code_binds_message_sender(tmp_path):
    store, user, sessions = _services(tmp_path)
    identity = ExternalIdentityService(store)
    link = identity.create_link_code(user.id, "qq", "main")
    runtime = _FakeChannelRuntime()
    transport = _FakeTransport()
    adapter = QQChannelAdapter(
        _Account(),
        transport,
        identity,
        ChannelConversationService(store, sessions),
        ChannelDedupService(store),
        runtime,
    )
    event = replace(
        _event("event-group-bind-code"),
        message_id="group-message-1",
        conversation_type="group",
        external_conversation_id="group-openid",
        external_user_id="group-member-openid",
        text=f"/bind {link.code}",
        reply_target=ChannelReplyTarget("qq", "main", "group", "group-openid"),
    )

    asyncio.run(adapter.handle_event(event))

    assert transport.sent == ["QQ 身份绑定成功。"]
    assert runtime.calls == []
    assert identity.resolve("qq", "main", "group-member-openid").user_id == user.id


def test_qq_group_bare_bind_only_redirects_to_direct_chat(tmp_path):
    store, _user, sessions = _services(tmp_path)
    identity = ExternalIdentityService(store)
    runtime = _FakeChannelRuntime()
    transport = _FakeTransport()
    adapter = QQChannelAdapter(
        _Account(),
        transport,
        identity,
        ChannelConversationService(store, sessions),
        ChannelDedupService(store),
        runtime,
    )
    event = replace(
        _event("event-group-bare-bind"),
        message_id="group-message-2",
        conversation_type="group",
        external_conversation_id="group-openid",
        external_user_id="group-member-openid",
        text="/bind",
        reply_target=ChannelReplyTarget("qq", "main", "group", "group-openid"),
    )

    asyncio.run(adapter.handle_event(event))

    assert transport.rich == []
    assert transport.sent == [
        "请在 QQ 私聊中发送 /bind 获取网页授权链接；"
        "也可以在群聊发送 /bind <绑定码> 手动绑定。"
    ]
    assert runtime.calls == []


def test_qq_group_manual_bind_code_is_single_use(tmp_path):
    store, user, sessions = _services(tmp_path)
    identity = ExternalIdentityService(store)
    link = identity.create_link_code(user.id, "qq", "main")
    runtime = _FakeChannelRuntime()
    transport = _FakeTransport()
    adapter = QQChannelAdapter(
        _Account(),
        transport,
        identity,
        ChannelConversationService(store, sessions),
        ChannelDedupService(store),
        runtime,
    )

    for index, external_user_id in enumerate(("first-member", "second-member"), start=1):
        asyncio.run(
            adapter.handle_event(
                replace(
                    _event(f"event-group-bind-replay-{index}"),
                    message_id=f"group-message-replay-{index}",
                    conversation_type="group",
                    external_conversation_id="group-openid",
                    external_user_id=external_user_id,
                    text=f"/bind {link.code}",
                    reply_target=ChannelReplyTarget(
                        "qq", "main", "group", "group-openid"
                    ),
                )
            )
        )

    assert transport.sent == [
        "QQ 身份绑定成功。",
        "绑定码无效、已过期或不属于此账号。",
    ]
    assert identity.resolve("qq", "main", "first-member").user_id == user.id
    assert identity.resolve("qq", "main", "second-member") is None
    assert runtime.calls == []


def test_qq_bound_group_bind_command_does_not_consume_code(tmp_path):
    store, user, sessions = _services(tmp_path)
    second_user = store.create_user("bob", "Bob", "bob-password")
    identity = ExternalIdentityService(store)
    store.link_external_identity(
        user_id=user.id,
        channel="qq",
        external_tenant_id="main",
        external_user_id="bound-member",
    )
    link = identity.create_link_code(second_user.id, "qq", "main")
    runtime = _FakeChannelRuntime()
    transport = _FakeTransport()
    adapter = QQChannelAdapter(
        _Account(),
        transport,
        identity,
        ChannelConversationService(store, sessions),
        ChannelDedupService(store),
        runtime,
    )
    event = replace(
        _event("event-bound-group-bind"),
        message_id="group-message-bound-bind",
        conversation_type="group",
        external_conversation_id="group-openid",
        external_user_id="bound-member",
        text=f"/bind {link.code}",
        reply_target=ChannelReplyTarget("qq", "main", "group", "group-openid"),
    )

    asyncio.run(adapter.handle_event(event))

    assert transport.sent == ["此 QQ 身份已经绑定，无需重复操作。"]
    assert runtime.calls == []
    rebound = identity.bind(
        code=link.code,
        channel="qq",
        account_key="main",
        external_user_id="unbound-member",
    )
    assert rebound.user_id == second_user.id


def test_qq_bound_event_runs_once_and_sends_result(tmp_path):
    store, user, sessions = _services(tmp_path)
    store.link_external_identity(
        user_id=user.id,
        channel="qq",
        external_tenant_id="main",
        external_user_id="openid",
    )
    runtime = _FakeChannelRuntime()
    transport = _FakeTransport()
    adapter = QQChannelAdapter(
        _Account(),
        transport,
        ExternalIdentityService(store),
        ChannelConversationService(store, sessions),
        ChannelDedupService(store),
        runtime,
    )
    event = _event("event-bound")

    asyncio.run(adapter.handle_event(event))
    asyncio.run(adapter.handle_event(event))

    assert len(runtime.calls) == 1
    assert transport.sent == ["runtime reply"]


def test_qq_structured_agent_reply_uses_markdown(tmp_path):
    store, user, sessions = _services(tmp_path)
    store.link_external_identity(
        user_id=user.id,
        channel="qq",
        external_tenant_id="main",
        external_user_id="openid",
    )
    transport = _FakeTransport()
    adapter = QQChannelAdapter(
        _Account(),
        transport,
        ExternalIdentityService(store),
        ChannelConversationService(store, sessions),
        ChannelDedupService(store),
        _FakeChannelRuntime("处理方式：\n- 重新上传图片\n- 描述图片内容"),
    )

    asyncio.run(adapter.handle_event(_event("event-markdown")))

    assert transport.sent == []
    assert transport.rich[0].markdown.startswith("处理方式：")
    assert transport.rich[0].fallback_text == "处理方式：\n• 重新上传图片\n• 描述图片内容"


def test_qq_group_structured_agent_reply_uses_quoted_text(tmp_path):
    store, user, sessions = _services(tmp_path)
    store.link_external_identity(
        user_id=user.id,
        channel="qq",
        external_tenant_id="main",
        external_user_id="group-member",
    )
    transport = _FakeTransport()
    adapter = QQChannelAdapter(
        _Account(),
        transport,
        ExternalIdentityService(store),
        ChannelConversationService(store, sessions),
        ChannelDedupService(store),
        _FakeChannelRuntime("处理方式：\n- 第一项\n- 第二项"),
    )
    event = replace(
        _event("event-group-markdown-as-text"),
        conversation_type="group",
        external_conversation_id="group-openid",
        external_user_id="group-member",
        reply_target=ChannelReplyTarget("qq", "main", "group", "group-openid"),
    )

    asyncio.run(adapter.handle_event(event))

    assert transport.rich == []
    assert transport.sent == ["处理方式：\n• 第一项\n• 第二项"]
    assert transport.quotes == [True]
    assert transport.sequences == [1]


def test_agent_markdown_detection_keeps_plain_and_long_content_as_text():
    assert build_agent_markdown("普通短句回复。") is None
    assert build_agent_markdown("- " + "x" * 1800) is None
    assert build_agent_markdown("建议：\n- 第一项\n- 第二项") is not None


def test_chunk_text_preserves_long_content():
    text = "第一段\n\n" + "x" * 2200
    chunks = chunk_text(text, limit=500)
    assert len(chunks) > 1
    assert "".join(chunks).replace("\n", "") == text.replace("\n", "")


def test_rate_limiter_rejects_burst_and_recovers_after_window():
    limiter = SlidingWindowRateLimiter(limit=2, window_seconds=10)
    assert limiter.allow("sender", now=0) is True
    assert limiter.allow("sender", now=1) is True
    assert limiter.allow("sender", now=2) is False
    assert limiter.allow("sender", now=11) is True


def test_attachment_guard_rejects_loopback_before_download():
    with pytest.raises(QQAttachmentError, match="not public"):
        _validate_public_http_url("http://127.0.0.1/private.txt")


def test_qq_startup_missing_credentials_is_unavailable():
    config = type("Config", (), {"enabled": True, "accounts": (_Account(app_id=""),)})()
    status = check_qq_startup(config)
    assert status.state == "unavailable"
    assert status.code == "CHANNEL_QQ_CREDENTIALS_MISSING"


def test_botpy_client_is_constructed_inside_worker_with_default_sdk_logging(monkeypatch, caplog):
    constructed_thread_ids = []
    client_options = []

    class FakeClient:
        def __init__(self, *, intents, timeout, bot_log, ext_handlers):
            del intents
            constructed_thread_ids.append(threading.get_ident())
            client_options.append((timeout, bot_log, ext_handlers))
            assert asyncio.get_event_loop() is not None

        def run(self, *, appid, secret):
            assert appid == "app"
            assert secret == "secret"
            asyncio.get_event_loop().run_until_complete(self.on_ready())

    fake_botpy = SimpleNamespace(
        Client=FakeClient,
        Intents=lambda **_kwargs: object(),
    )
    monkeypatch.setattr(
        "agent.channels.qq.transport.importlib.import_module",
        lambda name: fake_botpy if name == "botpy" else None,
    )
    transport = BotpyQQTransport(_Account())
    caller_thread_id = threading.get_ident()

    async def start_from_running_loop():
        transport.start(lambda _event: asyncio.sleep(0))

    with caplog.at_level(logging.INFO, logger="zcagent.agent.channel.qq"):
        asyncio.run(start_from_running_loop())
        transport._thread.join(timeout=2)

    assert constructed_thread_ids
    assert constructed_thread_ids[0] != caller_thread_id
    assert client_options[0][0] == 15
    assert client_options[0][1] is True
    assert client_options[0][2]["handler"] is _BotpyConsoleHandler
    assert client_options[0][2]["format"] == "%(message)s"
    assert [
        getattr(record, "event", "")
        for record in caplog.records
        if getattr(record, "event", "").startswith("channel.qq.")
    ] == []


def test_qq_late_ready_and_degraded_transitions_use_channel_events(tmp_path, caplog):
    store, _user, sessions = _services(tmp_path)
    adapter = QQChannelAdapter(
        _Account(),
        _FakeTransport(),
        ExternalIdentityService(store),
        ChannelConversationService(store, sessions),
        ChannelDedupService(store),
        _FakeChannelRuntime(),
    )
    adapter.mark_startup_readiness_reported()

    with caplog.at_level(logging.INFO, logger="zcagent.agent.channel.qq"):
        adapter._set_state("available")
        adapter._set_state("available")
        adapter._set_state("degraded")

    events = [
        getattr(record, "event", "")
        for record in caplog.records
        if getattr(record, "event", "").startswith("channel.qq.")
    ]
    assert events == ["channel.qq.ready", "channel.qq.degraded"]


def test_botpy_console_handler_matches_uvicorn_prefix_and_suppresses_heartbeat():
    stream = io.StringIO()
    handler = _BotpyConsoleHandler(stream)
    logger = logging.getLogger("test.botpy.console")
    logger.handlers = [handler]
    logger.propagate = False
    logger.setLevel(logging.INFO)

    logger.info("[botpy] 登录机器人账号中...", stacklevel=1)
    heartbeat = logging.LogRecord(
        logger.name,
        logging.INFO,
        __file__,
        1,
        "[botpy] 心跳维持启动...",
        (),
        None,
        func="_send_heart",
    )
    logger.handle(heartbeat)

    output = stream.getvalue()
    assert output.startswith("INFO:     [qq] 登录机器人账号中...")
    assert "test_botpy_console_handler" not in output
    assert "心跳维持启动" not in output


def test_botpy_rich_message_drops_keyboard_before_plain_text_fallback():
    transport = BotpyQQTransport(_Account())
    reply = _FakeReplyMessage(fail_keyboard=True)
    transport._messages["message-1"] = reply
    outbound = QQOutboundMessage(
        markdown="[登录并绑定](https://example.com/bind)",
        buttons=(
            QQOutboundButton(
                button_id="bind-login",
                label="登录并绑定",
                action="url",
                data="https://example.com/bind",
            ),
        ),
        fallback_text="https://example.com/bind",
    )

    asyncio.run(transport.send_message(_event("event-rich-fallback"), outbound))

    assert reply.calls[0]["msg_type"] == 2
    assert "keyboard" in reply.calls[0]
    assert reply.calls[1] == {
        "msg_type": 2,
        "markdown": {"content": "[登录并绑定](https://example.com/bind)"},
        "msg_seq": 1,
    }


def test_binding_prompt_sends_markdown_with_command_keyboard():
    transport = BotpyQQTransport(_Account())
    reply = _FakeReplyMessage()
    transport._messages["message-1"] = reply

    asyncio.run(
        transport.send_message(
            _event("event-binding-keyboard"),
            build_binding_prompt(),
        )
    )

    payload = reply.calls[0]
    assert payload["msg_type"] == 2
    assert "尚未绑定" in payload["markdown"]["content"]
    button = payload["keyboard"]["content"]["rows"][0]["buttons"][0]
    assert button["render_data"]["label"] == "绑定"
    assert button["action"]["type"] == 2
    assert button["action"]["data"] == "/bind"
    assert button["action"]["enter"] is True


def test_botpy_rich_message_falls_back_to_plain_text():
    transport = BotpyQQTransport(_Account())
    reply = _FakeReplyMessage(fail_rich=True)
    transport._messages["message-1"] = reply
    outbound = QQOutboundMessage(
        markdown="[登录并绑定](https://example.com/bind)",
        buttons=(
            QQOutboundButton(
                button_id="bind-login",
                label="登录并绑定",
                action="url",
                data="https://example.com/bind",
            ),
        ),
        fallback_text="登录并绑定：https://example.com/bind",
    )

    asyncio.run(transport.send_message(_event("event-text-fallback"), outbound))

    assert [call["msg_type"] for call in reply.calls] == [2, 2, 0]
    assert reply.calls[-1]["content"] == "登录并绑定：https://example.com/bind"


def test_botpy_group_text_reply_quotes_trigger_message():
    transport = BotpyQQTransport(_Account())
    reply = _FakeReplyMessage()
    transport._messages["message-1"] = reply
    event = replace(
        _event("event-group-text-reference"),
        conversation_type="group",
        external_conversation_id="group-openid",
        reply_target=ChannelReplyTarget("qq", "main", "group", "group-openid"),
    )

    asyncio.run(transport.send_text(event, "群聊回答", quote=True))

    assert reply.calls == [
        {
            "content": "群聊回答",
            "msg_type": 0,
            "msg_seq": 1,
            "message_reference": {"message_id": "message-1"},
        }
    ]


def test_botpy_direct_text_reply_does_not_add_group_reference():
    transport = BotpyQQTransport(_Account())
    reply = _FakeReplyMessage()
    transport._messages["message-1"] = reply

    asyncio.run(transport.send_text(_event("event-direct-text"), "私聊回答", quote=True))

    assert reply.calls == [{"content": "私聊回答", "msg_type": 0, "msg_seq": 1}]


def test_botpy_text_reply_logs_confirmed_delivery(caplog):
    transport = BotpyQQTransport(_Account())
    reply = _FakeReplyMessage()
    transport._messages["message-1"] = reply

    with caplog.at_level(logging.DEBUG, logger="zcagent.agent.channel.qq"):
        asyncio.run(transport.send_text(_event("event-confirmed-text"), "私聊回答"))

    records = [
        record
        for record in caplog.records
        if getattr(record, "event", "").startswith("channel.qq.send_")
    ]
    assert [record.event for record in records] == [
        "channel.qq.send_start",
        "channel.qq.send_done",
    ]
    assert records[-1].fields["response_message_id_hash"]
    assert records[-1].fields["source_message_id_hash"] != "message-1"


def test_botpy_none_reply_is_unconfirmed_and_not_retried(caplog):
    transport = BotpyQQTransport(_Account())
    reply = _FakeReplyMessage(unconfirmed=True)
    transport._messages["message-1"] = reply
    outbound = QQOutboundMessage(
        markdown="**回答**",
        fallback_text="回答",
    )

    with caplog.at_level(logging.DEBUG, logger="zcagent.agent.channel.qq"):
        with pytest.raises(QQSendUnconfirmedError, match="no delivery confirmation"):
            asyncio.run(transport.send_message(_event("event-unconfirmed"), outbound))

    assert len(reply.calls) == 1
    assert [
        record.event
        for record in caplog.records
        if getattr(record, "event", "").startswith("channel.qq.send_")
    ] == ["channel.qq.send_start", "channel.qq.send_unconfirmed"]


def test_botpy_proactive_text_uses_c2c_api_without_source_message_id(monkeypatch):
    calls = []

    class FakeAPI:
        async def post_c2c_message(self, **kwargs):
            calls.append(kwargs)
            return {"id": "accepted-message"}

    class FakeFuture:
        def __init__(self, coroutine):
            self.coroutine = coroutine

        def result(self, *, timeout):
            assert timeout == 15
            return asyncio.run(self.coroutine)

    loop = SimpleNamespace(is_running=lambda: True)
    monkeypatch.setattr(
        "agent.channels.qq.transport.asyncio.run_coroutine_threadsafe",
        lambda coroutine, current_loop: FakeFuture(coroutine)
        if current_loop is loop
        else None,
    )
    transport = BotpyQQTransport(_Account())
    transport._client = SimpleNamespace(
        api=FakeAPI(),
        loop=loop,
        is_closed=lambda: False,
    )
    transport._thread = SimpleNamespace(is_alive=lambda: True)

    assert transport.send_proactive_text("private-openid", "今日天气晴") == {
        "id": "accepted-message"
    }
    assert calls == [
        {"openid": "private-openid", "msg_type": 0, "content": "今日天气晴"}
    ]
    assert "msg_id" not in calls[0]


def test_qq_notification_provider_uses_current_binding_and_returns_safe_result(tmp_path):
    store = SQLiteAuthStore(tmp_path / "auth.sqlite3")
    user = store.initialize_owner("owner", "Owner", "password-123")
    store.link_external_identity(
        user_id=user.id,
        channel="qq",
        external_tenant_id="main",
        external_user_id="private-openid",
    )
    transport = SimpleNamespace(calls=[])
    transport.send_proactive_text = lambda target, content: transport.calls.append(
        (target, content)
    ) or {"id": "platform-message"}
    adapter = SimpleNamespace(
        account=_Account(),
        transport=transport,
        status=lambda: CapabilityStatus("qq.main", "available"),
    )
    provider = QQNotificationProvider(ExternalIdentityService(store))
    provider.register_adapters((adapter,))
    actor = store.actor_for_user(user.id, channel="workflow")

    assert provider.capability(actor) == {
        "available": True,
        "bound": True,
        "code": "",
    }
    assert provider.send_to_user(user_id=user.id, content="**今日建议**") == {
        "status": "accepted",
        "channel": "qq",
    }
    assert transport.calls == [("private-openid", "**今日建议**")]


def test_botpy_group_markdown_fallback_keeps_trigger_reference():
    transport = BotpyQQTransport(_Account())
    reply = _FakeReplyMessage(fail_rich=True)
    transport._messages["message-1"] = reply
    event = replace(
        _event("event-group-markdown-reference"),
        conversation_type="group",
        external_conversation_id="group-openid",
        reply_target=ChannelReplyTarget("qq", "main", "group", "group-openid"),
    )
    outbound = QQOutboundMessage(
        markdown="回答：\n- 第一项\n- 第二项",
        fallback_text="回答：\n- 第一项\n- 第二项",
    )

    asyncio.run(transport.send_message(event, outbound))

    assert [call["msg_type"] for call in reply.calls] == [2, 0]
    assert all(
        call["message_reference"] == {"message_id": "message-1"}
        for call in reply.calls
    )


def test_qq_group_long_text_quotes_only_first_chunk(tmp_path):
    store, user, sessions = _services(tmp_path)
    store.link_external_identity(
        user_id=user.id,
        channel="qq",
        external_tenant_id="main",
        external_user_id="group-member",
    )
    transport = _FakeTransport()
    adapter = QQChannelAdapter(
        _Account(),
        transport,
        ExternalIdentityService(store),
        ChannelConversationService(store, sessions),
        ChannelDedupService(store),
        _FakeChannelRuntime("x" * 1900),
    )
    event = replace(
        _event("event-group-long-reply"),
        conversation_type="group",
        external_conversation_id="group-openid",
        external_user_id="group-member",
        reply_target=ChannelReplyTarget("qq", "main", "group", "group-openid"),
    )

    asyncio.run(adapter.handle_event(event))

    assert len(transport.sent) == 2
    assert transport.quotes == [True, False]
    assert transport.sequences == [1, 2]


def test_qq_group_reply_caps_five_chunks_and_marks_truncation(tmp_path):
    store, user, sessions = _services(tmp_path)
    store.link_external_identity(
        user_id=user.id,
        channel="qq",
        external_tenant_id="main",
        external_user_id="group-member",
    )
    transport = _FakeTransport()
    adapter = QQChannelAdapter(
        _Account(),
        transport,
        ExternalIdentityService(store),
        ChannelConversationService(store, sessions),
        ChannelDedupService(store),
        _FakeChannelRuntime("x" * 11000),
    )
    event = replace(
        _event("event-group-reply-cap"),
        conversation_type="group",
        external_conversation_id="group-openid",
        external_user_id="group-member",
        reply_target=ChannelReplyTarget("qq", "main", "group", "group-openid"),
    )

    asyncio.run(adapter.handle_event(event))

    assert len(transport.sent) == 5
    assert transport.sequences == [1, 2, 3, 4, 5]
    assert transport.quotes == [True, False, False, False, False]
    assert transport.sent[-1].endswith("[回答过长，剩余内容请在私聊或 Web 查看。]")


def test_qq_direct_reply_caps_four_chunks_with_unique_sequences(tmp_path):
    store, user, sessions = _services(tmp_path)
    store.link_external_identity(
        user_id=user.id,
        channel="qq",
        external_tenant_id="main",
        external_user_id="openid",
    )
    transport = _FakeTransport()
    adapter = QQChannelAdapter(
        _Account(),
        transport,
        ExternalIdentityService(store),
        ChannelConversationService(store, sessions),
        ChannelDedupService(store),
        _FakeChannelRuntime("x" * 9000),
    )

    asyncio.run(adapter.handle_event(_event("event-direct-reply-cap")))

    assert len(transport.sent) == 4
    assert transport.sequences == [1, 2, 3, 4]
    assert transport.quotes == [False, False, False, False]
    assert transport.sent[-1].endswith("[回答过长，剩余内容请在私聊或 Web 查看。]")


def test_qq_send_error_marks_persistent_receipt_as_error(tmp_path):
    store, user, sessions = _services(tmp_path)
    store.link_external_identity(
        user_id=user.id,
        channel="qq",
        external_tenant_id="main",
        external_user_id="openid",
    )
    transport = _FakeTransport(
        send_error=QQSendUnconfirmedError("QQ delivery was not confirmed")
    )
    adapter = QQChannelAdapter(
        _Account(),
        transport,
        ExternalIdentityService(store),
        ChannelConversationService(store, sessions),
        ChannelDedupService(store),
        _FakeChannelRuntime("runtime reply"),
    )
    event = _event("event-unconfirmed-receipt")

    asyncio.run(adapter.handle_event(event))

    with sqlite3.connect(tmp_path / "state" / "auth.sqlite3") as connection:
        row = connection.execute(
            "SELECT status, error_code FROM channel_event_receipts WHERE event_id=?",
            (event.event_id,),
        ).fetchone()
    assert row == ("error", "QQSendUnconfirmedError")


def _services(tmp_path):
    store = SQLiteAuthStore(tmp_path / "state" / "auth.sqlite3")
    store.initialize_schema()
    user = store.create_user("alice", "Alice", "alice-password")
    sessions = SessionAccessService(
        store,
        FilesystemUserContextResolver(tmp_path / "contexts", workspace_dir=tmp_path),
    )
    return store, user, sessions


def _context(conversation_type: str, conversation_id: str) -> ChannelExecutionContext:
    return ChannelExecutionContext(
        channel="qq",
        account_key="main",
        conversation_type=conversation_type,
        external_conversation_id=conversation_id,
        capabilities=ChannelCapabilities(command_profile=f"qq_{conversation_type}"),
    )


def _event(event_id: str) -> InboundChannelEvent:
    return InboundChannelEvent(
        channel="qq",
        account_key="main",
        event_id=event_id,
        message_id="message-1",
        event_type="message",
        conversation_type="c2c",
        external_conversation_id="openid",
        external_thread_id="",
        external_user_id="openid",
        external_display_name="",
        text="hello",
        quote=None,
        attachments=(),
        reply_target=ChannelReplyTarget("qq", "main", "c2c", "openid"),
        occurred_at="2026-07-23T00:00:00+00:00",
        safe_metadata={"mentioned": "true"},
    )


@dataclass(frozen=True)
class _Account:
    key: str = "main"
    app_id: str = "app"
    app_secret: str = "secret"
    web_base_url: str = "http://127.0.0.1:10086"
    c2c_enabled: bool = True
    group_enabled: bool = True
    group_require_mention: bool = True
    http_timeout_seconds: int = 15
    max_parallel_conversations: int = 2
    max_attachment_bytes: int = 1024


class _FakeRuntime:
    def __init__(self):
        self.calls = []

    def run_chat_events(self, *args, **kwargs):
        self.calls.append((args, kwargs))


class _Result:
    content = "runtime reply"


class _FakeChannelRuntime:
    def __init__(self, content="runtime reply"):
        self.calls = []
        self.content = content

    def dispatch(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        return SimpleNamespace(content=self.content)


class _FakeTransport:
    def __init__(self, *, send_error=None):
        self.sent = []
        self.quotes = []
        self.sequences = []
        self.rich = []
        self.send_error = send_error

    def start(self, handler):
        self.handler = handler

    def stop(self):
        return None

    async def send_text(self, event, content, *, quote=False, msg_seq=1):
        self.sent.append(content)
        self.quotes.append(quote)
        self.sequences.append(msg_seq)
        if self.send_error is not None:
            raise self.send_error

    async def send_message(self, event, outbound):
        self.rich.append(outbound)


class _FakeReplyMessage:
    def __init__(self, *, fail_keyboard=False, fail_rich=False, unconfirmed=False):
        self.fail_keyboard = fail_keyboard
        self.fail_rich = fail_rich
        self.unconfirmed = unconfirmed
        self.calls = []

    async def reply(self, **kwargs):
        self.calls.append(kwargs)
        if self.fail_keyboard and "keyboard" in kwargs:
            raise RuntimeError("keyboard rejected")
        if self.fail_rich and kwargs.get("msg_type") == 2:
            raise RuntimeError("rich message rejected")
        if self.unconfirmed:
            return None
        return SimpleNamespace(id=f"reply-{len(self.calls)}")
