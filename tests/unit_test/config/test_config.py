"""Tests for application configuration path resolution."""

import json
import os
from pathlib import Path

import pytest

from agent.config import (
    InitConfigurationError,
    MissingWorkspaceError,
    bootstrap_dotenv,
    init_runtime_files,
    load_config,
    load_llm_endpoint,
    load_llm_endpoints,
    resolve_llm_endpoint_alias,
)
from agent.protocols.llm import LLMConfigurationError


def test_load_config_uses_explicit_workspace(tmp_path, monkeypatch):
    """An explicit workspace should be the root for all default directories."""

    _clear_zhice_env(monkeypatch)

    config = load_config(tmp_path)

    assert config.workspace == tmp_path.resolve()
    assert config.config_dir == tmp_path / "config"
    assert config.prompts_dir == tmp_path / "prompts"
    assert config.contexts_dir == tmp_path / "contexts"
    assert config.sessions_dir == tmp_path / "contexts" / "sessions"
    assert config.local_memory_dir == tmp_path / "contexts" / "memory"
    assert config.extends_dir == tmp_path / "extends"
    assert config.logs_dir == tmp_path / "logs"
    assert config.state_dir == tmp_path / "state"
    assert config.auth_db_path == tmp_path / "state" / "auth.sqlite3"
    assert config.users_contexts_dir == tmp_path / "contexts" / "users"
    assert config.shared_readonly_dir == tmp_path / "contexts" / "shared" / "readonly"


def test_load_config_allows_environment_overrides(tmp_path, monkeypatch):
    """Directory-specific environment variables should override derived paths."""

    workspace = tmp_path / "workspace"
    custom_config = tmp_path / "custom_config"
    custom_prompts = tmp_path / "custom_prompts"
    custom_contexts = tmp_path / "custom_contexts"
    custom_extends = tmp_path / "custom_extends"
    custom_logs = tmp_path / "custom_logs"

    monkeypatch.setenv("ZHICE_AGENT_WORKSPACE", str(workspace))
    monkeypatch.setenv("ZHICE_AGENT_CONFIG_DIR", str(custom_config))
    monkeypatch.setenv("ZHICE_AGENT_PROMPTS_DIR", str(custom_prompts))
    monkeypatch.setenv("ZHICE_AGENT_CONTEXTS_DIR", str(custom_contexts))
    monkeypatch.setenv("ZHICE_AGENT_EXTENDS_DIR", str(custom_extends))
    monkeypatch.setenv("ZHICE_AGENT_LOGS_DIR", str(custom_logs))

    config = load_config()

    assert config.workspace == workspace.resolve()
    assert config.config_dir == custom_config.resolve()
    assert config.prompts_dir == custom_prompts.resolve()
    assert config.contexts_dir == custom_contexts.resolve()
    assert config.sessions_dir == custom_contexts.resolve() / "sessions"
    assert config.extends_dir == custom_extends.resolve()
    assert config.logs_dir == custom_logs.resolve()


def test_load_config_requires_explicit_workspace(monkeypatch):
    """The runtime must not silently use the source repository as a workspace."""

    _clear_zhice_env(monkeypatch)

    with pytest.raises(MissingWorkspaceError, match="ZHICE_AGENT_WORKSPACE"):
        load_config()


def test_bootstrap_dotenv_loads_project_config_env_without_overriding_existing_values(
    tmp_path, monkeypatch
):
    """Project config/.env should provide defaults while shell env remains authoritative."""

    _clear_zhice_env(monkeypatch)
    workspace = tmp_path / "from_dotenv"
    override_workspace = tmp_path / "from_shell"
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / ".env").write_text(
        f"ZHICE_AGENT_WORKSPACE={workspace}\nLOCAL_ONLY=value\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("ZHICE_AGENT_WORKSPACE", str(override_workspace))

    loaded = bootstrap_dotenv(project_root=tmp_path)
    config = load_config()

    assert loaded == config_dir / ".env"
    assert config.workspace == override_workspace.resolve()
    assert os.environ["LOCAL_ONLY"] == "value"


def test_bootstrap_dotenv_accepts_windows_notepad_utf16_env(tmp_path, monkeypatch):
    """Windows Notepad may save .env as UTF-16 LE; startup should still work."""

    _clear_zhice_env(monkeypatch)
    workspace = tmp_path / "utf16_workspace"
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / ".env").write_text(
        f"ZHICE_AGENT_WORKSPACE={workspace}\n",
        encoding="utf-16",
    )

    loaded = bootstrap_dotenv(project_root=tmp_path)
    config = load_config()

    assert loaded == config_dir / ".env"
    assert config.workspace == workspace.resolve()


def test_ensure_dirs_creates_runtime_directories(tmp_path, monkeypatch):
    """ensure_dirs should create the directories needed by the first-stage CLI."""

    _clear_zhice_env(monkeypatch)
    config = load_config(tmp_path)

    config.ensure_dirs()

    assert config.config_dir.is_dir()
    assert config.prompts_dir.is_dir()
    assert config.contexts_dir.is_dir()
    assert config.sessions_dir.is_dir()
    assert config.local_memory_dir.is_dir()
    assert config.extends_dir.is_dir()
    assert config.logs_dir.is_dir()


def test_load_llm_endpoint_reads_openai_endpoint(tmp_path):
    """OpenAI-compatible endpoints should load into the shared LLMEndpoint shape."""

    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "llm_endpoints.json").write_text(
        json.dumps(
            {
                "default": {
                    "protocol": "openai",
                    "base_url": "https://example.test/v1",
                    "api_key": "local-json-secret",
                    "model": "gpt-test",
                    "supported_models": ["gpt-test", "gpt-alt", "gpt-*"],
                    "max_tokens": 128,
                    "context_window": 8192,
                    "temperature": 0.2,
                }
            }
        ),
        encoding="utf-8",
    )

    endpoint = load_llm_endpoint(config_dir)

    assert endpoint.protocol == "openai"
    assert endpoint.base_url == "https://example.test/v1"
    assert endpoint.api_key == "local-json-secret"
    assert endpoint.model == "gpt-test"
    assert endpoint.provider == ""
    assert endpoint.supported_models == ("gpt-test", "gpt-alt", "gpt-*")
    assert endpoint.max_tokens == 128
    assert endpoint.context_window == 8192
    assert endpoint.temperature == 0.2
    assert endpoint.priority == 1
    assert endpoint.enabled is True
    assert endpoint.role == "default"


def test_load_llm_endpoints_reads_priority_and_enabled_keyed_config(tmp_path):
    """Endpoint failover should be able to load every configured endpoint."""

    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "llm_endpoints.json").write_text(
        json.dumps(
            {
                "_comment": "ignored",
                "default": {
                    "protocol": "openai",
                    "base_url": "https://a.test/v1",
                    "api_key": "a-key",
                    "model": "model-a",
                    "context_window": 8192,
                    "priority": 2,
                },
                "backup": {
                    "protocol": "openai",
                    "base_url": "https://b.test/v1",
                    "api_key": "b-key",
                    "model": "model-b",
                    "context_window": 8192,
                    "priority": 1,
                    "enabled": False,
                    "role": "default",
                },
            }
        ),
        encoding="utf-8",
    )

    endpoints = load_llm_endpoints(config_dir)

    assert [endpoint.name for endpoint in endpoints] == ["default", "backup"]
    assert endpoints[0].priority == 2
    assert endpoints[1].priority == 1
    assert endpoints[1].enabled is False


def test_load_llm_endpoint_allows_default_string_alias(tmp_path):
    """A top-level default alias should avoid duplicating endpoint config."""

    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "llm_endpoints.json").write_text(
        json.dumps(
            {
                "default": "litellm_anthropic",
                "litellm_anthropic": {
                    "protocol": "litellm",
                    "provider": "anthropic",
                    "api_key": "anthropic-key",
                    "model": "claude-sonnet-4",
                    "context_window": 200000,
                    "priority": 1,
                },
            }
        ),
        encoding="utf-8",
    )

    endpoints = load_llm_endpoints(config_dir)
    endpoint = load_llm_endpoint(config_dir)

    assert [item.name for item in endpoints] == ["litellm_anthropic"]
    assert endpoint.name == "litellm_anthropic"
    assert endpoint.provider == "anthropic"
    assert endpoint.model == "claude-sonnet-4"
    assert resolve_llm_endpoint_alias(config_dir, "default") == "litellm_anthropic"


def test_load_llm_endpoint_keeps_litellm_models_unprefixed(tmp_path):
    """LiteLLM endpoints should keep local model names separate from provider prefixes."""

    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "llm_endpoints.json").write_text(
        json.dumps(
            {
                "default": {
                    "protocol": "litellm",
                    "provider": "anthropic",
                    "api_key": "anthropic-key",
                    "model": "claude-sonnet-4",
                    "context_window": 200000,
                    "supported_models": ["claude-sonnet-4", "claude-opus-4"],
                }
            }
        ),
        encoding="utf-8",
    )

    endpoint = load_llm_endpoint(config_dir)

    assert endpoint.provider == "anthropic"
    assert endpoint.model == "claude-sonnet-4"
    assert endpoint.supported_models == (
        "claude-sonnet-4",
        "claude-opus-4",
    )


def test_load_llm_endpoint_allows_default_ref_alias(tmp_path):
    """Alias objects should be accepted for more explicit configs."""

    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "llm_endpoints.json").write_text(
        json.dumps(
            {
                "default": {"ref": "backup"},
                "backup": {
                    "protocol": "openai",
                    "base_url": "https://backup.test/v1",
                    "api_key": "backup-key",
                    "model": "backup-model",
                    "context_window": 8192,
                },
            }
        ),
        encoding="utf-8",
    )

    endpoint = load_llm_endpoint(config_dir)

    assert endpoint.name == "backup"


def test_load_llm_endpoints_accepts_reference_endpoints_list_config(tmp_path):
    """List configs use explicit endpoint names from each entry."""

    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "llm_endpoints.json").write_text(
        json.dumps(
            {
                "max_outer_retries": 2,
                "endpoints": [
                    {
                        "name": "cpa",
                        "role": "default",
                        "priority": 1,
                        "protocol": "openai",
                        "base_url": "https://cpa.test/v1",
                        "api_key": "cpa-key",
                        "model": "gpt-5.5",
                        "context_window": 131072,
                        "enabled": True,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    endpoints = load_llm_endpoints(config_dir)

    assert len(endpoints) == 1
    assert endpoints[0].name == "cpa"
    assert endpoints[0].protocol == "openai"
    assert endpoints[0].base_url == "https://cpa.test/v1"
    assert endpoints[0].priority == 1


def test_load_llm_endpoints_requires_names_in_list_config(tmp_path):
    """Only keyed configs can infer endpoint names from outer object keys."""

    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "llm_endpoints.json").write_text(
        json.dumps(
            {
                "endpoints": [
                    {
                        "protocol": "openai",
                        "base_url": "https://cpa.test/v1",
                        "api_key": "cpa-key",
                        "model": "gpt-5.5",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(LLMConfigurationError, match=r"endpoints\[0\]\.name"):
        load_llm_endpoints(config_dir)


def test_load_llm_endpoint_resolves_api_key_placeholder_from_environment(tmp_path, monkeypatch):
    """api_key may reference an environment variable with ${ENV_VAR} syntax."""

    config_dir = tmp_path / "config"
    config_dir.mkdir()
    monkeypatch.setenv("ZHICE_LLM_OPENAI_API_KEY", "env-placeholder-secret")
    (config_dir / "llm_endpoints.json").write_text(
        json.dumps(
            {
                "default": {
                    "protocol": "openai",
                    "base_url": "https://example.test/v1",
                    "api_key": "${ZHICE_LLM_OPENAI_API_KEY}",
                    "model": "gpt-test",
                    "context_window": 8192,
                }
            }
        ),
        encoding="utf-8",
    )

    endpoint = load_llm_endpoint(config_dir)

    assert endpoint.api_key == "env-placeholder-secret"


def test_load_llm_endpoint_rejects_missing_api_key_placeholder_environment_variable(
    tmp_path, monkeypatch
):
    """Missing placeholder variables should fail with a precise configuration error."""

    config_dir = tmp_path / "config"
    config_dir.mkdir()
    monkeypatch.delenv("ZHICE_LLM_OPENAI_API_KEY", raising=False)
    (config_dir / "llm_endpoints.json").write_text(
        json.dumps(
            {
                "default": {
                    "protocol": "openai",
                    "base_url": "https://example.test/v1",
                    "api_key": "${ZHICE_LLM_OPENAI_API_KEY}",
                    "model": "gpt-test",
                    "context_window": 8192,
                }
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(LLMConfigurationError, match="ZHICE_LLM_OPENAI_API_KEY"):
        load_llm_endpoint(config_dir)


def test_load_llm_endpoint_accepts_litellm_provider(tmp_path):
    """LiteLLM endpoints can use the in-process SDK without a base_url."""

    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "llm_endpoints.json").write_text(
        json.dumps(
            {
                "claude": {
                    "protocol": "litellm",
                    "provider": "anthropic",
                    "api_key": "litellm-local-key",
                    "model": "claude-sonnet-4",
                    "context_window": 200000,
                }
            }
        ),
        encoding="utf-8",
    )

    endpoint = load_llm_endpoint(config_dir, "claude")

    assert endpoint.protocol == "litellm"
    assert endpoint.provider == "anthropic"
    assert endpoint.base_url == ""
    assert endpoint.model == "claude-sonnet-4"
    assert endpoint.api_key == "litellm-local-key"


def test_load_llm_endpoint_rejects_prefixed_model_names(tmp_path):
    """Local config keeps provider and model separate."""

    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "llm_endpoints.json").write_text(
        json.dumps(
            {
                "claude": {
                    "protocol": "litellm",
                    "provider": "anthropic",
                    "api_key": "litellm-local-key",
                    "model": "anthropic/claude-sonnet-4",
                    "context_window": 200000,
                }
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(LLMConfigurationError, match="unprefixed model name"):
        load_llm_endpoint(config_dir, "claude")


def test_load_llm_endpoint_rejects_direct_anthropic_protocol(tmp_path):
    """Anthropic direct calls are intentionally reserved for future LiteLLM routing."""

    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "llm_endpoints.json").write_text(
        json.dumps(
            {
                "claude": {
                    "protocol": "anthropic",
                    "base_url": "https://api.anthropic.com/v1",
                    "api_key": "anthropic-local-key",
                    "model": "claude-sonnet-4",
                    "context_window": 200000,
                }
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(LLMConfigurationError, match="unsupported protocol"):
        load_llm_endpoint(config_dir, "claude")


def test_load_llm_endpoint_defaults_context_window(tmp_path):
    """Endpoints may omit context_window and use the lightweight default."""

    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "llm_endpoints.json").write_text(
        json.dumps(
            {
                "default": {
                    "protocol": "openai",
                    "base_url": "https://example.test/v1",
                    "api_key": "secret",
                    "model": "gpt-test",
                }
            }
        ),
        encoding="utf-8",
    )

    assert load_llm_endpoint(config_dir).context_window == 131072


@pytest.mark.parametrize(
    ("max_tokens", "error"),
    [
        (8192, "max_tokens must be less than context_window"),
        (0, "field must be >= 1: max_tokens"),
    ],
)
def test_load_llm_endpoint_rejects_invalid_context_budgets(
    tmp_path,
    max_tokens,
    error,
):
    """Configured input/output limits must describe a usable model context window."""

    config_dir = tmp_path / "config"
    config_dir.mkdir()
    endpoint = {
        "protocol": "openai",
        "base_url": "https://example.test/v1",
        "api_key": "secret",
        "model": "gpt-test",
        "context_window": 8192,
        "max_tokens": max_tokens,
    }
    (config_dir / "llm_endpoints.json").write_text(
        json.dumps({"default": endpoint}),
        encoding="utf-8",
    )

    with pytest.raises(LLMConfigurationError, match=error):
        load_llm_endpoint(config_dir)


def test_init_runtime_files_generates_local_env_and_llm_config(tmp_path, monkeypatch):
    """The second-stage init path should create runnable local config files."""

    _clear_zhice_env(monkeypatch)
    config = load_config(tmp_path)

    written = init_runtime_files(
        config,
        create_env=True,
        endpoint_name="local",
        base_url="https://gateway.test/v1",
        api_key="local-json-secret",
        model="test-model",
    )

    assert tmp_path / ".env" in written
    assert tmp_path / "config" / "llm_endpoints.json" in written
    assert tmp_path / "config" / "skill_sources.yml" in written
    assert tmp_path / "config" / "channels.yml" in written
    assert (tmp_path / "prompts" / "identity.md").is_file()
    assert (tmp_path / "config" / "skill_sources.yml").is_file()
    assert (tmp_path / "config" / "channels.yml").read_text(encoding="utf-8") == (
        Path("config/channels.example.yml").resolve().read_text(encoding="utf-8")
    )
    env_text = (tmp_path / ".env").read_text(encoding="utf-8")
    assert env_text == f"ZHICE_AGENT_WORKSPACE={tmp_path.resolve()}\n"

    endpoint = load_llm_endpoint(tmp_path / "config", "local")
    assert endpoint.provider == ""
    assert endpoint.base_url == "https://gateway.test/v1"
    assert endpoint.api_key == "local-json-secret"
    assert endpoint.model == "test-model"
    assert endpoint.context_window == 131072
    assert endpoint.max_tokens == 16384
    raw_endpoint = json.loads(
        (tmp_path / "config" / "llm_endpoints.json").read_text(encoding="utf-8")
    )["local"]
    assert raw_endpoint["max_tokens"] == 16384
    assert "max_input_tokens" not in raw_endpoint


def test_init_runtime_files_rejects_invalid_token_budget(tmp_path, monkeypatch):
    """Init must not write a config whose output reservation consumes the window."""

    _clear_zhice_env(monkeypatch)
    config = load_config(tmp_path)

    with pytest.raises(InitConfigurationError, match="less than context_window"):
        init_runtime_files(
            config,
            context_window=4096,
            max_tokens=4096,
        )

    assert not (tmp_path / "config" / "llm_endpoints.json").exists()


def test_init_runtime_files_preserves_existing_files_without_force(tmp_path, monkeypatch):
    """Init should skip user-owned local runtime files unless force is enabled."""

    _clear_zhice_env(monkeypatch)
    config = load_config(tmp_path)
    (tmp_path / ".env").write_text("EXISTING=1\n", encoding="utf-8")
    config.config_dir.mkdir(parents=True, exist_ok=True)
    config.channels_config_path.write_text("channels: existing\n", encoding="utf-8")

    written = init_runtime_files(config, create_env=True)

    assert (tmp_path / ".env").read_text(encoding="utf-8") == "EXISTING=1\n"
    assert tmp_path / ".env" not in written
    assert tmp_path / "config" / "llm_endpoints.json" in written
    assert config.channels_config_path.read_text(encoding="utf-8") == "channels: existing\n"
    assert config.channels_config_path not in written


def test_init_runtime_files_fills_missing_files_when_config_exists(tmp_path, monkeypatch):
    """A pre-existing config file should be preserved while missing files are created."""

    _clear_zhice_env(monkeypatch)
    config = load_config(tmp_path)
    config.config_dir.mkdir()
    (config.config_dir / "llm_endpoints.json").write_text("{}", encoding="utf-8")

    written = init_runtime_files(config, create_env=True)

    assert (config.config_dir / "llm_endpoints.json").read_text(encoding="utf-8") == "{}"
    assert tmp_path / ".env" in written
    assert (tmp_path / ".env").exists()
    assert config.config_dir / "skill_sources.yml" in written
    assert config.channels_config_path in written


def test_init_runtime_files_can_skip_skill_source_config(tmp_path, monkeypatch):
    """Init should allow minimal configs without a Skill source template."""

    _clear_zhice_env(monkeypatch)
    config = load_config(tmp_path)

    written = init_runtime_files(config, create_skill_sources_config=False)

    assert tmp_path / "config" / "llm_endpoints.json" in written
    assert tmp_path / "config" / "skill_sources.yml" not in written
    assert not (tmp_path / "config" / "skill_sources.yml").exists()


def test_init_runtime_files_force_replaces_existing_channel_config(tmp_path, monkeypatch):
    _clear_zhice_env(monkeypatch)
    config = load_config(tmp_path)
    config.config_dir.mkdir(parents=True)
    config.channels_config_path.write_text("channels: existing\n", encoding="utf-8")

    written = init_runtime_files(config, force=True)

    assert config.channels_config_path in written
    assert config.channels_config_path.read_text(encoding="utf-8") == (
        Path("config/channels.example.yml").resolve().read_text(encoding="utf-8")
    )


def test_init_runtime_files_reports_missing_channel_template(tmp_path, monkeypatch):
    _clear_zhice_env(monkeypatch)
    config = load_config(tmp_path / "runtime")
    source_root = tmp_path / "source"
    monkeypatch.setattr("agent.config._default_workspace", lambda: source_root)

    with pytest.raises(InitConfigurationError, match="Channel config template is missing"):
        init_runtime_files(
            config,
            create_llm_config=False,
            create_skill_sources_config=False,
            create_prompts=False,
        )


def _clear_zhice_env(monkeypatch) -> None:
    """Remove ZhiCe-Agent path variables so tests are deterministic."""

    for key in [
        "ZHICE_AGENT_WORKSPACE",
        "ZHICE_AGENT_CONFIG_DIR",
        "ZHICE_AGENT_PROMPTS_DIR",
        "ZHICE_AGENT_CONTEXTS_DIR",
        "ZHICE_AGENT_EXTENDS_DIR",
        "ZHICE_AGENT_LOGS_DIR",
        "LOCAL_ONLY",
    ]:
        monkeypatch.delenv(key, raising=False)

