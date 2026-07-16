from __future__ import annotations

import json

from agent.app.auth import AuthService
from agent.app.runtime import ActiveTurn, WebRuntime
from agent.auth.audit import SqliteAuditSink
from agent.auth.session_access import SessionAccessService
from agent.auth.store import SQLiteAuthStore
from agent.auth.user_context import FilesystemUserContextResolver
from agent.config import AppConfig
from agent.core.loop import CancellationToken
from agent.llm import create_llm_provider_chain
from agent.llm.selection import ConfiguredLLMProviderResolver
from agent.protocols.llm import LLMEndpoint
from agent.session import JsonlSessionStore, JsonSessionModelPreferenceStore


def test_web_runtime_model_preference_isolated_by_session_and_user_directory(tmp_path):
    store = SQLiteAuthStore(tmp_path / "state" / "auth.sqlite3")
    admin = store.initialize_owner("admin", "Admin", "password-123")
    viewer = store.create_user("viewer", "Viewer", "viewer-password")
    runtime = _runtime(tmp_path, store)
    actor = store.actor_for_user(viewer.id, channel="web")

    runtime.model_state(actor, "session-a")
    runtime.model_state(actor, "session-b")
    runtime.set_model_preference(actor, "session-a", "model-b")

    assert runtime.model_state(actor, "session-a").current_model == "model-b"
    assert runtime.model_state(actor, "session-b").current_model == "model-a"
    user_context = runtime.session_access.user_contexts.resolve(viewer.id)
    assert user_context.sessions_meta_dir.joinpath("session-a.json").is_file()
    assert not user_context.sessions_meta_dir.joinpath("session-b.json").is_file()
    assert admin.id != viewer.id


def test_same_model_preference_does_not_emit_switched_event(tmp_path, caplog):
    store = SQLiteAuthStore(tmp_path / "state" / "auth.sqlite3")
    store.initialize_schema()
    viewer = store.create_user("viewer", "Viewer", "viewer-password")
    runtime = _runtime(tmp_path, store)
    actor = store.actor_for_user(viewer.id, channel="web")
    caplog.set_level("INFO", logger="zcagent.agent.web")

    runtime.set_model_preference(actor, "session-a", "model-a")
    runtime.set_model_preference(actor, "session-a", "model-b")
    runtime.set_model_preference(actor, "session-a", "model-b")

    switched = [
        record
        for record in caplog.records
        if getattr(record, "event", "") == "model.switched"
    ]
    assert len(switched) == 1
    assert switched[0].fields["model"] == "model-b"  # type: ignore[attr-defined]


def test_background_memory_notification_is_shown_once_on_next_turn(tmp_path):
    store = SQLiteAuthStore(tmp_path / "state" / "auth.sqlite3")
    owner = store.initialize_owner("owner", "Owner", "password-123")
    runtime = _runtime(tmp_path, store)
    runtime.agent_loop = _CapturingAgentLoop()
    actor = store.actor_for_user(owner.id, channel="web")
    notification = tmp_path / "contexts" / "memory" / "extraction_state" / "pending_notification.json"
    notification.parent.mkdir(parents=True)
    notification.write_text(
        json.dumps(["回答时先给结论，最多列三点。"], ensure_ascii=False),
        encoding="utf-8",
    )

    first = runtime.run_chat_events(actor, "owner-chat", "hello")
    second = runtime.run_chat_events(actor, "owner-chat", "again")

    assert first.content == "💾 根据上次对话，我记住了：回答时先给结论，最多列三点。\n\ndone"
    assert second.content == "done"


def test_active_turn_key_prevents_other_user_stop_but_admin_can_stop_any(tmp_path):
    store = SQLiteAuthStore(tmp_path / "state" / "auth.sqlite3")
    admin = store.initialize_owner("admin", "Admin", "password-123")
    first = store.create_user("first", "First", "first-password", role_keys=["developer"])
    second = store.create_user("second", "Second", "second-password", role_keys=["developer"])
    runtime = _runtime(tmp_path, store)
    second_actor = store.actor_for_user(second.id, channel="web")
    admin_actor = store.actor_for_user(admin.id, channel="web")
    token = CancellationToken()
    runtime._register_turn((first.id, "session-a"), ActiveTurn("turn-a", token))

    denied_by_isolation = runtime.cancel_session(second_actor, "session-a")
    stopped_by_admin = runtime.cancel_session(admin_actor, "session-a")

    assert denied_by_isolation["cancelled"] == 0
    assert token.is_cancelled() is True
    assert stopped_by_admin["cancelled"] == 1


def test_owner_chat_uses_workspace_tools_while_viewer_stays_in_user_files(tmp_path):
    store = SQLiteAuthStore(tmp_path / "state" / "auth.sqlite3")
    owner = store.initialize_owner("owner", "Owner", "password-123")
    viewer = store.create_user("viewer", "Viewer", "viewer-password")
    runtime = _runtime(tmp_path, store)
    capture = _CapturingAgentLoop()
    runtime.agent_loop = capture

    runtime.run_chat_events(store.actor_for_user(owner.id, channel="web"), "owner-chat", "inspect")
    owner_kwargs = capture.calls[-1]
    runtime.run_chat_events(store.actor_for_user(viewer.id, channel="web"), "viewer-chat", "inspect")
    viewer_kwargs = capture.calls[-1]

    assert owner_kwargs["workspace_override"] == tmp_path
    assert viewer_kwargs["workspace_override"] == (
        tmp_path / "contexts" / "users" / viewer.id / "files"
    )
    assert "DIR  contexts" in owner_kwargs["tools_override"].execute("list_dir", {"path": "."}).output
    assert "DIR  contexts" not in viewer_kwargs["tools_override"].execute("list_dir", {"path": "."}).output
    owner_tool_names = {
        item["function"]["name"] for item in owner_kwargs["tools_override"].definitions()
    }
    viewer_tool_names = {
        item["function"]["name"] for item in viewer_kwargs["tools_override"].definitions()
    }
    assert {"memory_read", "memory_write"} <= owner_tool_names
    assert {"memory_read", "memory_write"} <= viewer_tool_names
    owner_kwargs["tools_override"].execute("memory_read", {"mode": "list"})
    viewer_kwargs["tools_override"].execute("memory_read", {"mode": "list"})
    assert (tmp_path / "contexts" / "memory" / "MEMORY.md").is_file()
    assert (
        tmp_path / "contexts" / "users" / viewer.id / "memory" / "MEMORY.md"
    ).is_file()
    assert not (tmp_path / "contexts" / "users" / owner.id / "memory").exists()


def _runtime(tmp_path, store):
    endpoint = LLMEndpoint(
        name="primary",
        protocol="openai",
        base_url="https://example.test/v1",
        model="model-a",
        api_key="secret",
        supported_models=("model-b",),
    )
    config = AppConfig(
        workspace=tmp_path,
        config_dir=tmp_path / "config",
        prompts_dir=tmp_path / "prompts",
        contexts_dir=tmp_path / "contexts",
        sessions_dir=tmp_path / "contexts" / "sessions",
        extends_dir=tmp_path / "extends",
        logs_dir=tmp_path / "logs",
    )
    contexts = FilesystemUserContextResolver(config.contexts_dir)
    sessions = JsonlSessionStore(config.sessions_dir)
    llm = create_llm_provider_chain([endpoint], preferred_endpoint="primary")
    audit = SqliteAuditSink(store)
    return WebRuntime(
        config=config,
        sessions=sessions,
        agent_loop=_UnusedAgentLoop(),
        llm=llm,
        auth=AuthService(store, audit_sink=audit),
        session_access=SessionAccessService(store, contexts),
        model_preferences=JsonSessionModelPreferenceStore(),
        llm_resolver=ConfiguredLLMProviderResolver([endpoint], default_endpoint="primary"),
        audit_sink=audit,
    )


class _UnusedAgentLoop:
    tools = None

    def run_turn(self, *_args, **_kwargs):
        raise AssertionError("chat is not used by this test")


class _CapturingAgentLoop:
    tools = None

    def __init__(self):
        self.calls = []

    def run_turn(self, _session_id, _message, **kwargs):
        self.calls.append(kwargs)
        return "done"
