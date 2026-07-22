from __future__ import annotations

from agent.cli import (
    _CliModelRuntime,
    _CliSubagentRuntime,
    _handle_session_model_command,
    _handle_session_subagent_command,
)
from agent.llm.selection import ConfiguredLLMProviderResolver
from agent.protocols.capability import CapabilityStatus
from agent.protocols.llm import LLMEndpoint
from agent.protocols.session import SessionContext
from agent.session.model_preferences import JsonSessionModelPreferenceStore
from agent.session.subagent_preferences import JsonSessionSubagentPreferenceStore


def test_cli_model_command_persists_preference_per_session(tmp_path):
    resolver = ConfiguredLLMProviderResolver(
        [
            LLMEndpoint(
                name="primary",
                protocol="openai",
                base_url="https://example.test/v1",
                model="model-a",
                api_key="secret",
                context_window=32768,
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
                context_window=32768,
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


def test_model_selection_carries_failover_safe_context_budget():
    """CLI and Web selections should expose one budget valid for every fallback."""

    resolver = ConfiguredLLMProviderResolver(
        [
            LLMEndpoint(
                name="primary",
                protocol="openai",
                base_url="https://primary.test/v1",
                model="model-a",
                api_key="secret",
                context_window=32768,
                max_tokens=4096,
                supported_models=("model-b",),
            ),
            LLMEndpoint(
                name="backup",
                protocol="openai",
                base_url="https://backup.test/v1",
                model="model-c",
                api_key="secret",
                context_window=16384,
                max_tokens=2048,
            ),
        ],
        default_endpoint="primary",
    )

    default = resolver.resolve(None)
    overridden = resolver.select("primary", "model-b")

    assert default.context_budget is not None
    assert default.context_budget.input_token_limit == 14336
    assert default.context_budget.endpoint_names == ("primary", "backup")
    assert overridden.context_budget == default.context_budget
    assert resolver.context_budget(overridden) == default.context_budget


def test_cli_subagent_command_persists_mode_and_once_per_session(tmp_path):
    context = SessionContext(
        owner_user_id=None,
        sessions_dir=tmp_path / "sessions",
        sessions_meta_dir=tmp_path / "sessions_meta",
        files_dir=tmp_path,
        shared_readonly_dir=tmp_path / "shared",
    )
    runtime = _CliSubagentRuntime(
        JsonSessionSubagentPreferenceStore(),
        context,
        (("explorer", "Read and inspect"),),
        status=CapabilityStatus(
            name="subagent",
            state="available",
            code="SUBAGENT_AVAILABLE",
            message="Subagent runtime is available.",
        ),
    )

    assert "current subagent mode: auto" in _handle_session_subagent_command(runtime, "a", "")
    assert "explorer" in _handle_session_subagent_command(runtime, "a", "")
    assert "'/subagent auto'" in _handle_session_subagent_command(runtime, "a", "")
    assert "current subagent mode: off" in _handle_session_subagent_command(runtime, "a", "off")
    assert "force once: true" in _handle_session_subagent_command(runtime, "a", "once")
    assert runtime.preferences.consume_force_once(context, "a") is True
    assert runtime.preferences.get(context, "b").mode == "auto"


def test_cli_subagent_unavailable_returns_precise_cause(tmp_path):
    context = SessionContext(
        owner_user_id=None,
        sessions_dir=tmp_path / "sessions",
        sessions_meta_dir=tmp_path / "sessions_meta",
        files_dir=tmp_path,
        shared_readonly_dir=tmp_path / "shared",
    )
    runtime = _CliSubagentRuntime(
        JsonSessionSubagentPreferenceStore(),
        context,
        (),
        status=CapabilityStatus(
            name="subagent",
            state="unavailable",
            code="SUBAGENT_CONFIG_INVALID",
            message="Subagent configuration is invalid.",
            hint="Fix config/subagents.yml, then restart the process.",
        ),
    )

    output = _handle_session_subagent_command(runtime, "a", "once")

    assert output.startswith("Subagent is currently unavailable:")
    assert "Subagent configuration is invalid." in output
    assert "Fix config/subagents.yml" in output
    assert "cause_code" not in output
    assert runtime.preferences.get(context, "a").force_once is False
