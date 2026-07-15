from __future__ import annotations

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
