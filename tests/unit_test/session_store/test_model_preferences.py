from __future__ import annotations

import json

from agent.llm.selection import ConfiguredLLMProviderResolver
from agent.protocols.llm import LLMEndpoint
from agent.protocols.session import SessionContext, SessionModelPreference
from agent.session.model_preferences import JsonSessionModelPreferenceStore


def test_session_model_preference_preserves_title_and_reset_only_removes_model_fields(tmp_path):
    context = _context(tmp_path, "user-1")
    context.sessions_meta_dir.mkdir(parents=True)
    metadata_path = context.sessions_meta_dir / "session-a.json"
    metadata_path.write_text('{"title":"Important"}\n', encoding="utf-8")
    store = JsonSessionModelPreferenceStore()

    store.set(
        context,
        "session-a",
        SessionModelPreference(endpoint_name="primary", model_name="model-b"),
    )
    saved = json.loads(metadata_path.read_text(encoding="utf-8"))

    assert saved == {
        "preferred_endpoint_name": "primary",
        "preferred_model_name": "model-b",
        "title": "Important",
    }
    assert store.get(context, "session-a") == SessionModelPreference("primary", "model-b")

    store.reset(context, "session-a")

    assert json.loads(metadata_path.read_text(encoding="utf-8")) == {"title": "Important"}


def test_model_resolver_is_call_scoped_and_stale_preference_falls_back():
    endpoints = [
        _endpoint("primary", "model-a", supported=("model-b",), priority=1),
        _endpoint("backup", "model-c", priority=2),
    ]
    resolver = ConfiguredLLMProviderResolver(endpoints, default_endpoint="primary")

    first = resolver.bind(resolver.resolve(SessionModelPreference("primary", "model-b")))
    second = resolver.bind(resolver.resolve(None))
    stale = resolver.resolve(SessionModelPreference("removed", "missing"))

    assert first.current_endpoint().model == "model-b"
    assert second.current_endpoint().model == "model-a"
    assert stale.endpoint_name == "primary"
    assert stale.model_name == "model-a"
    assert stale.source == "fallback"
    assert stale.reason_code == "STALE_MODEL_PREFERENCE"


def _context(tmp_path, user_id: str) -> SessionContext:
    root = tmp_path / "contexts" / "users" / user_id
    return SessionContext(
        owner_user_id=user_id,
        sessions_dir=root / "sessions",
        sessions_meta_dir=root / "sessions_meta",
        files_dir=root / "files",
        shared_readonly_dir=tmp_path / "contexts" / "shared" / "readonly",
    )


def _endpoint(name: str, model: str, *, supported=(), priority=1) -> LLMEndpoint:
    return LLMEndpoint(
        name=name,
        protocol="openai",
        base_url="https://example.test/v1",
        model=model,
        api_key="secret",
        priority=priority,
        supported_models=tuple(supported),
    )

