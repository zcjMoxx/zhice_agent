from __future__ import annotations

from agent.cli import _CliModelRuntime, _handle_session_model_command
from agent.llm.selection import ConfiguredLLMProviderResolver
from agent.protocols.llm import LLMEndpoint
from agent.protocols.session import SessionContext
from agent.session.model_preferences import JsonSessionModelPreferenceStore


def test_cli_model_command_persists_preference_per_session(tmp_path):
    resolver = ConfiguredLLMProviderResolver(
        [
            LLMEndpoint(
                name="primary",
                protocol="openai",
                base_url="https://example.test/v1",
                model="model-a",
                api_key="secret",
                supported_models=("model-b",),
            )
        ],
        default_endpoint="primary",
    )
    context = SessionContext(
        owner_user_id=None,
        sessions_dir=tmp_path / "contexts" / "sessions",
        sessions_meta_dir=tmp_path / "contexts" / "sessions_meta",
        files_dir=tmp_path,
        shared_readonly_dir=tmp_path / "contexts" / "shared" / "readonly",
    )
    runtime = _CliModelRuntime(resolver, JsonSessionModelPreferenceStore(), context)

    _handle_session_model_command(runtime, "session-a", "primary/model-b")

    assert runtime.preferences.get(context, "session-a").model_name == "model-b"
    assert runtime.preferences.get(context, "session-b") is None
    assert "primary/model-b" in _handle_session_model_command(runtime, "session-a", "")
    assert "primary/model-a" in _handle_session_model_command(runtime, "session-b", "")


def test_cli_model_reset_only_clears_current_session(tmp_path):
    resolver = ConfiguredLLMProviderResolver(
        [
            LLMEndpoint(
                name="primary",
                protocol="openai",
                base_url="https://example.test/v1",
                model="model-a",
                api_key="secret",
                supported_models=("model-b",),
            )
        ],
        default_endpoint="primary",
    )
    context = SessionContext(
        owner_user_id=None,
        sessions_dir=tmp_path / "sessions",
        sessions_meta_dir=tmp_path / "sessions_meta",
        files_dir=tmp_path,
        shared_readonly_dir=tmp_path / "shared",
    )
    runtime = _CliModelRuntime(resolver, JsonSessionModelPreferenceStore(), context)
    _handle_session_model_command(runtime, "session-a", "primary/model-b")
    _handle_session_model_command(runtime, "session-b", "primary/model-b")

    _handle_session_model_command(runtime, "session-a", "reset")

    assert runtime.preferences.get(context, "session-a") is None
    assert runtime.preferences.get(context, "session-b").model_name == "model-b"
