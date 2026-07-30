"""Tests for workspace paths and the two-file runtime model configuration."""

import json
import os
from pathlib import Path

import pytest

from agent.config import (
    InitConfigurationError,
    bootstrap_dotenv,
    init_runtime_files,
    load_config,
    load_llm_endpoint,
    load_llm_endpoints,
    resolve_model_route,
)
from agent.llm.runtime import (
    create_configured_llm_provider,
    create_optional_aliased_llm_provider,
)
from agent.protocols.llm import LLMConfigurationError


def _clear_zhice_env(monkeypatch):
    for name in (
        "ZHICE_AGENT_WORKSPACE",
        "ZHICE_AGENT_CONFIG_DIR",
        "ZHICE_AGENT_PROMPTS_DIR",
        "ZHICE_AGENT_CONTEXTS_DIR",
        "ZHICE_AGENT_EXTENDS_DIR",
        "ZHICE_AGENT_LOGS_DIR",
    ):
        monkeypatch.delenv(name, raising=False)


def _models(chat, *, routing=None):
    return {
        "schema_version": 1,
        "routing": routing or {"chat": next(iter(chat))},
        "chat": chat,
        "embedding": {},
    }


def _endpoint(**overrides):
    raw = {
        "protocol": "openai",
        "base_url": "https://example.test/v1",
        "api_key": "secret",
        "model": "gpt-test",
        "supported_models": ["gpt-test", "gpt-alt"],
        "context_window": 8192,
        "max_tokens": 128,
        "temperature": 0.2,
        "enabled": True,
    }
    raw.update(overrides)
    return raw


def _write_models(config_dir, payload):
    config_dir.mkdir(parents=True, exist_ok=True)
    (config_dir / "models.json").write_text(
        json.dumps(payload),
        encoding="utf-8",
    )


def test_load_config_uses_explicit_workspace(tmp_path, monkeypatch):
    _clear_zhice_env(monkeypatch)
    config = load_config(tmp_path)
    assert config.workspace == tmp_path.resolve()
    assert config.config_dir == tmp_path / "config"
    assert config.sessions_dir == tmp_path / "contexts" / "sessions"
    assert config.auth_db_path == tmp_path / "state" / "auth.sqlite3"
    assert config.channels_config_path == tmp_path / "config" / "config.yml"


def test_load_config_allows_environment_overrides(tmp_path, monkeypatch):
    monkeypatch.setenv("ZHICE_AGENT_WORKSPACE", str(tmp_path / "workspace"))
    monkeypatch.setenv("ZHICE_AGENT_CONFIG_DIR", str(tmp_path / "custom_config"))
    config = load_config()
    assert config.config_dir == (tmp_path / "custom_config").resolve()


def test_load_config_defaults_to_home_dot_zhice(tmp_path, monkeypatch):
    _clear_zhice_env(monkeypatch)
    monkeypatch.setattr("agent.config.Path.home", lambda: tmp_path)
    config = load_config()
    assert config.workspace == (tmp_path / ".zhice").resolve()


def test_public_runtime_env_template_has_required_empty_keys_and_no_workspace_locator():
    template = (Path(__file__).resolve().parents[3] / "config" / ".env.example").read_text(
        encoding="utf-8"
    )
    assignments = {
        line.split("=", 1)[0]: line.split("=", 1)[1]
        for line in template.splitlines()
        if line and not line.startswith("#") and "=" in line
    }

    assert {
        "ZHICE_LLM_OPENAI_API_KEY",
        "ZHICE_EMBEDDING_API_KEY",
        "ZHICE_AGENT_SETUP_TOKEN",
        "QQBOT_APP_ID",
        "QQBOT_APP_SECRET",
    } <= assignments.keys()
    assert "ZHICE_AGENT_WORKSPACE" not in assignments
    assert set(assignments.values()) == {""}


def test_bootstrap_dotenv_loads_workspace_env_without_allowing_workspace_rebind(
    tmp_path, monkeypatch
):
    _clear_zhice_env(monkeypatch)
    workspace = tmp_path / "workspace"
    config_dir = workspace / "config"
    config_dir.mkdir(parents=True)
    (config_dir / ".env").write_text(
        f"ZHICE_AGENT_WORKSPACE={tmp_path / 'wrong'}\nLOCAL_ONLY=value\n",
        encoding="utf-8",
    )

    assert bootstrap_dotenv(workspace=workspace, project_root=tmp_path / "project") == (
        config_dir / ".env"
    )
    assert "ZHICE_AGENT_WORKSPACE" not in os.environ
    assert os.environ["LOCAL_ONLY"] == "value"
    assert load_config(workspace).workspace == workspace.resolve()


def test_bootstrap_dotenv_supports_utf16_and_preserves_shell_value(tmp_path, monkeypatch):
    _clear_zhice_env(monkeypatch)
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    configured = tmp_path / "configured"
    selected = tmp_path / "selected"
    (config_dir / ".env").write_text(
        f"ZHICE_AGENT_WORKSPACE={configured}\nLOCAL_ONLY=value\n",
        encoding="utf-16",
    )
    monkeypatch.setenv("ZHICE_AGENT_WORKSPACE", str(selected))
    assert bootstrap_dotenv(project_root=tmp_path) == config_dir / ".env"
    assert load_config().workspace == selected.resolve()
    assert os.environ["LOCAL_ONLY"] == "value"


def test_ensure_dirs_creates_runtime_directories(tmp_path, monkeypatch):
    _clear_zhice_env(monkeypatch)
    config = load_config(tmp_path)
    config.ensure_dirs()
    assert all(
        path.is_dir()
        for path in (
            config.config_dir,
            config.prompts_dir,
            config.sessions_dir,
            config.extends_dir,
            config.logs_dir,
        )
    )


def test_models_json_loads_chat_endpoint_and_pricing(tmp_path):
    config_dir = tmp_path / "config"
    _write_models(
        config_dir,
        _models(
            {
                "primary": _endpoint(
                    pricing={"input_per_million": 1.25, "output_per_million": 5}
                )
            },
            routing={"chat": "primary/gpt-test"},
        ),
    )
    endpoint = load_llm_endpoint(config_dir)
    assert endpoint.name == "primary"
    assert endpoint.supported_models == ("gpt-test", "gpt-alt")
    assert endpoint.input_price_per_million == 1.25
    assert endpoint.output_price_per_million == 5


def test_route_supports_endpoint_and_endpoint_model_forms(tmp_path):
    config_dir = tmp_path / "config"
    _write_models(
        config_dir,
        _models(
            {"primary": _endpoint()},
            routing={"chat": "primary/gpt-alt", "compaction": "primary"},
        ),
    )
    assert resolve_model_route(config_dir, "default") == ("primary", "gpt-alt")
    assert resolve_model_route(config_dir, "compaction") == ("primary", "")
    provider = create_configured_llm_provider(config_dir)
    assert provider.preferred_endpoint == "primary"
    assert provider.current_endpoint().model == "gpt-alt"


def test_route_rejects_model_outside_endpoint_allowlist(tmp_path):
    config_dir = tmp_path / "config"
    _write_models(
        config_dir,
        _models({"primary": _endpoint()}, routing={"chat": "primary/unknown"}),
    )
    with pytest.raises(LLMConfigurationError, match="does not support"):
        create_configured_llm_provider(config_dir)


def test_optional_compaction_route_can_be_absent_or_present(tmp_path):
    config_dir = tmp_path / "config"
    _write_models(config_dir, _models({"primary": _endpoint()}))
    assert create_optional_aliased_llm_provider(config_dir, "compaction") is None
    _write_models(
        config_dir,
        _models(
            {"primary": _endpoint()},
            routing={"chat": "primary", "compaction": "primary/gpt-alt"},
        ),
    )
    provider = create_optional_aliased_llm_provider(config_dir, "compaction")
    assert provider is not None
    assert provider.current_endpoint().model == "gpt-alt"


def test_models_json_expands_api_key_environment_placeholder(tmp_path, monkeypatch):
    monkeypatch.setenv("MODEL_TEST_KEY", "resolved")
    config_dir = tmp_path / "config"
    _write_models(
        config_dir,
        _models({"primary": _endpoint(api_key="${MODEL_TEST_KEY}")}),
    )
    assert load_llm_endpoint(config_dir).api_key == "resolved"


def test_models_json_rejects_missing_secret_without_leaking_value(tmp_path, monkeypatch):
    monkeypatch.delenv("PRIVATE_MODEL_KEY", raising=False)
    config_dir = tmp_path / "config"
    _write_models(
        config_dir,
        _models({"primary": _endpoint(api_key="${PRIVATE_MODEL_KEY}")}),
    )
    with pytest.raises(LLMConfigurationError, match="missing environment variable"):
        load_llm_endpoint(config_dir)


def test_models_json_supports_litellm_endpoint(tmp_path):
    config_dir = tmp_path / "config"
    endpoint = _endpoint(
        protocol="litellm",
        provider="anthropic",
        base_url="",
        model="claude-test",
        supported_models=["claude-test"],
    )
    _write_models(config_dir, _models({"claude": endpoint}))
    loaded = load_llm_endpoint(config_dir)
    assert loaded.protocol == "litellm"
    assert loaded.provider == "anthropic"
    assert loaded.model == "claude-test"


@pytest.mark.parametrize(
    "change,match",
    [
        ({"max_tokens": 0}, "field must be >= 1: max_tokens"),
        ({"max_tokens": 8192}, "must be less than context_window"),
        ({"model": "openai/gpt-test"}, "unprefixed model"),
        ({"pricing": {"input_per_million": -1}}, "non-negative"),
    ],
)
def test_models_json_rejects_invalid_endpoint_values(tmp_path, change, match):
    config_dir = tmp_path / "config"
    _write_models(config_dir, _models({"primary": _endpoint(**change)}))
    with pytest.raises(LLMConfigurationError, match=match):
        load_llm_endpoints(config_dir)


def test_runtime_requires_models_json_and_ignores_old_filename(tmp_path):
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "llm_endpoints.json").write_text("{}", encoding="utf-8")
    with pytest.raises(LLMConfigurationError, match="models.json"):
        load_llm_endpoints(config_dir)


def test_init_generates_standard_config_files_and_prompts(tmp_path, monkeypatch):
    _clear_zhice_env(monkeypatch)
    config = load_config(tmp_path)
    written = init_runtime_files(
        config,
        endpoint_name="primary",
        model="gpt-test",
        context_window=8192,
        max_tokens=128,
    )
    assert config.config_dir / "models.json" in written
    assert config.config_dir / "config.yml" in written
    env_path = config.config_dir / ".env"
    assert env_path in written
    assert env_path.read_text(encoding="utf-8") == (
        Path(__file__).resolve().parents[3] / "config" / ".env.example"
    ).read_text(encoding="utf-8")
    assert "ZHICE_AGENT_WORKSPACE=" not in env_path.read_text(encoding="utf-8")
    assert not (config.config_dir / "llm_endpoints.json").exists()
    assert not (config.config_dir / "context.yml").exists()
    payload = json.loads((config.config_dir / "models.json").read_text(encoding="utf-8"))
    assert payload["routing"]["chat"] == "primary/gpt-test"
    assert payload["routing"]["compaction"] == "primary/gpt-test"
    assert payload["chat"]["primary"]["supported_models"] == ["gpt-test"]
    assert (config.prompts_dir / "context_compaction.md").is_file()


def test_init_preserves_existing_two_main_files_without_force(tmp_path, monkeypatch):
    _clear_zhice_env(monkeypatch)
    config = load_config(tmp_path)
    config.config_dir.mkdir(parents=True)
    (config.config_dir / "models.json").write_text("{}", encoding="utf-8")
    (config.config_dir / "config.yml").write_text("schema_version: 1\n", encoding="utf-8")
    (config.config_dir / ".env").write_text("EXISTING=1\n", encoding="utf-8")
    init_runtime_files(config)
    assert (config.config_dir / "models.json").read_text(encoding="utf-8") == "{}"
    assert (config.config_dir / "config.yml").read_text(encoding="utf-8") == "schema_version: 1\n"
    assert (config.config_dir / ".env").read_text(encoding="utf-8") == "EXISTING=1\n"


def test_init_force_replaces_two_main_files(tmp_path, monkeypatch):
    _clear_zhice_env(monkeypatch)
    config = load_config(tmp_path)
    config.config_dir.mkdir(parents=True)
    (config.config_dir / "models.json").write_text("{}", encoding="utf-8")
    (config.config_dir / "config.yml").write_text("bad", encoding="utf-8")
    (config.config_dir / ".env").write_text("EXISTING=1\n", encoding="utf-8")
    init_runtime_files(config, force=True)
    assert json.loads((config.config_dir / "models.json").read_text(encoding="utf-8"))["schema_version"] == 1
    assert (config.config_dir / "config.yml").read_text(encoding="utf-8").startswith("schema_version: 1")
    assert "EXISTING=1" not in (config.config_dir / ".env").read_text(encoding="utf-8")


def test_init_env_compatibility_flag_cannot_disable_standard_generation(tmp_path):
    config = load_config(tmp_path)

    written = init_runtime_files(
        config,
        create_env=False,
        create_llm_config=False,
        create_skill_sources_config=False,
        create_channels_config=False,
        create_context_config=False,
        create_embedding_config=False,
        create_prompts=False,
    )

    assert written == [config.config_dir / ".env"]


def test_init_reports_missing_public_env_template(tmp_path, monkeypatch):
    project = tmp_path / "project"
    monkeypatch.setattr("agent.config._project_root", lambda: project)
    config = load_config(tmp_path / "workspace")

    with pytest.raises(InitConfigurationError, match="Runtime env template is missing"):
        init_runtime_files(
            config,
            create_llm_config=False,
            create_skill_sources_config=False,
            create_channels_config=False,
            create_context_config=False,
            create_embedding_config=False,
            create_prompts=False,
        )


@pytest.mark.parametrize(
    "context_window,max_tokens",
    [(0, 1), (100, 0), (100, 100)],
)
def test_init_rejects_invalid_token_budgets(tmp_path, context_window, max_tokens):
    config = load_config(tmp_path)
    with pytest.raises(InitConfigurationError):
        init_runtime_files(
            config,
            context_window=context_window,
            max_tokens=max_tokens,
        )
