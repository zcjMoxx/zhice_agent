import json

import pytest

from agent.context.config import load_context_config
from agent.embedding.openai_compatible import (
    EmbeddingEndpoint,
    OpenAICompatibleEmbeddingProvider,
    load_embedding_provider,
)


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return None

    def read(self):
        return json.dumps(self.payload).encode("utf-8")


def test_context_config_missing_uses_safe_defaults(tmp_path):
    config = load_context_config(tmp_path)
    assert config.full_history.enabled is True
    assert config.compaction.trigger_budget_ratio == 0.85
    assert config.compaction.recent_keep_ratio == 0.15
    assert config.compaction.post_compaction_max_ratio == 0.35
    assert config.compaction.min_recent_turns == 8
    assert config.compaction.background_enabled is True
    assert config.compaction.background_trigger_budget_ratio == 0.8
    assert config.retrieval.semantic_weight == 0.45


def test_context_config_rejects_unknown_field(tmp_path):
    (tmp_path / "config.yml").write_text(
        "context:\n  retrieval:\n    unknown: 1\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="unknown context config fields"):
        load_context_config(tmp_path)


def test_context_config_rejects_compaction_post_max_at_or_above_trigger(tmp_path):
    (tmp_path / "config.yml").write_text(
        "context:\n  compaction:\n    trigger_budget_ratio: 0.8\n"
        "    post_compaction_max_ratio: 0.8\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="post_compaction_max_ratio must be below"):
        load_context_config(tmp_path)


def test_context_config_rejects_removed_legacy_target(tmp_path):
    (tmp_path / "config.yml").write_text(
        "context:\n  compaction:\n    trigger_budget_ratio: 0.8\n"
        "    target_budget_ratio: 0.6\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="unknown context config fields"):
        load_context_config(tmp_path)


def test_context_config_does_not_hide_legacy_target_beside_new_fields(tmp_path):
    (tmp_path / "config.yml").write_text(
        "context:\n  compaction:\n    target_budget_ratio: 0.6\n"
        "    recent_keep_ratio: 0.15\n"
        "    post_compaction_max_ratio: 0.35\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="unknown context config fields"):
        load_context_config(tmp_path)


def test_context_config_rejects_recent_keep_above_post_compaction_max(tmp_path):
    (tmp_path / "config.yml").write_text(
        "context:\n  compaction:\n    recent_keep_ratio: 0.6\n"
        "    post_compaction_max_ratio: 0.5\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="recent_keep_ratio must not exceed"):
        load_context_config(tmp_path)


def test_context_config_rejects_background_trigger_outside_waterlines(tmp_path):
    (tmp_path / "config.yml").write_text(
        "context:\n  compaction:\n    background_trigger_budget_ratio: 0.9\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="background_trigger_budget_ratio must be between"):
        load_context_config(tmp_path)


def test_context_config_rejects_price_because_prices_belong_to_models(tmp_path):
    (tmp_path / "config.yml").write_text(
        "context:\n  compaction:\n    input_price_per_million: -1\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="unknown context config fields"):
        load_context_config(tmp_path)




def test_openai_compatible_embedding_preserves_input_order():
    requests = []

    def urlopen(request, timeout):
        requests.append((request, timeout))
        return FakeResponse(
            {"data": [{"index": 1, "embedding": [0, 1]}, {"index": 0, "embedding": [1, 0]}]}
        )

    provider = OpenAICompatibleEmbeddingProvider(
        EmbeddingEndpoint("https://example.test/v1", "embed", "secret", dimensions=2),
        urlopen=urlopen,
    )

    assert provider.embed(["a", "b"]) == [[1.0, 0.0], [0.0, 1.0]]
    assert provider.identity.endswith(":embed:2")
    assert requests[0][1] == 30.0


def test_embedding_config_without_resolved_secret_is_degraded(tmp_path, monkeypatch):
    monkeypatch.delenv("MISSING_EMBEDDING_KEY", raising=False)
    (tmp_path / "models.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "routing": {"embedding": "default/embed"},
                "embedding": {"default": {
                    "protocol": "openai",
                    "base_url": "https://example.test/v1",
                    "model": "embed",
                    "supported_models": ["embed"],
                    "api_key": "${MISSING_EMBEDDING_KEY}",
                }},
                "chat": {},
            }
        ),
        encoding="utf-8",
    )
    assert load_embedding_provider(tmp_path) is None
