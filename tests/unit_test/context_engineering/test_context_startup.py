import json

from agent.context.startup import check_context_engineering_startup
from agent.prompt_loader import PromptLoader


def _write_prompts(directory):
    directory.mkdir()
    for name in ("context_compaction", "history_query_planner", "context_query_rewrite"):
        (directory / f"{name}.md").write_text("prompt", encoding="utf-8")


def test_context_startup_reports_missing_prompts_and_embedding_config(tmp_path, caplog):
    caplog.set_level("WARNING", logger="zcagent.agent.context")

    result = check_context_engineering_startup(tmp_path / "config", PromptLoader(tmp_path / "prompts"))

    assert result.embedding_provider is None
    assert result.status.state == "degraded"
    assert result.status.code == "CONTEXT_ENGINEERING_DEGRADED"
    assert result.status.details["embedding_state"] == "not_configured"
    assert set(result.status.details["missing_prompts"]) == {
        "context_compaction.md",
        "history_query_planner.md",
        "context_query_rewrite.md",
    }
    assert any(record.event == "context.startup_degraded" for record in caplog.records)


def test_context_startup_available_with_prompts_and_embedding_endpoint(tmp_path, monkeypatch):
    prompts = tmp_path / "prompts"
    config = tmp_path / "config"
    _write_prompts(prompts)
    config.mkdir()
    monkeypatch.setenv("EMBEDDING_TEST_KEY", "secret")
    (config / "models.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "routing": {"embedding": "default/embed"},
                "embedding": {"default": {
                    "protocol": "openai",
                    "base_url": "https://example.test/v1",
                    "model": "embed",
                    "api_key": "${EMBEDDING_TEST_KEY}",
                    "dimensions": 8,
                    "supported_models": ["embed"],
                }},
                "chat": {},
            }
        ),
        encoding="utf-8",
    )

    result = check_context_engineering_startup(config, PromptLoader(prompts))

    assert result.embedding_provider is not None
    assert result.status.state == "available"
    assert result.status.code == "CONTEXT_ENGINEERING_AVAILABLE"


def test_context_startup_reports_unresolved_embedding_secret_without_leaking_name(
    tmp_path, monkeypatch
):
    prompts = tmp_path / "prompts"
    config = tmp_path / "config"
    _write_prompts(prompts)
    config.mkdir()
    monkeypatch.delenv("PRIVATE_MISSING_KEY", raising=False)
    (config / "models.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "routing": {"embedding": "default/embed"},
                "embedding": {"default": {
                    "protocol": "openai",
                    "base_url": "https://example.test/v1",
                    "model": "embed",
                    "api_key": "${PRIVATE_MISSING_KEY}",
                    "supported_models": ["embed"],
                }},
                "chat": {},
            }
        ),
        encoding="utf-8",
    )

    result = check_context_engineering_startup(config, PromptLoader(prompts))

    assert result.status.state == "degraded"
    assert result.status.details["embedding_state"] == "unavailable"
    assert "PRIVATE_MISSING_KEY" not in result.status.message
    assert "PRIVATE_MISSING_KEY" not in result.status.hint
