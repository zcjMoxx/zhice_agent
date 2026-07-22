import logging

from agent.memory.startup import check_memory_extraction_startup
from agent.prompt_loader import PromptLoader


def test_disabled_memory_extraction_does_not_require_prompt(tmp_path):
    result = check_memory_extraction_startup(PromptLoader(tmp_path), enabled=False)

    assert result.enabled is False
    assert result.status.state == "disabled"
    assert result.status.code == "MEMORY_EXTRACTION_DISABLED"


def test_missing_memory_extraction_prompt_is_unavailable(tmp_path, caplog):
    with caplog.at_level(logging.WARNING, logger="zcagent.agent.memory"):
        result = check_memory_extraction_startup(PromptLoader(tmp_path))

    assert result.enabled is False
    assert result.status.state == "unavailable"
    assert result.status.code == "MEMORY_EXTRACTION_PROMPT_NOT_FOUND"
    assert result.status.message == (
        "Required built-in Memory extraction prompt is missing: memory_extraction.md"
    )
    assert result.status.details["prompt_file"] == "memory_extraction.md"
    assert str(tmp_path) not in caplog.text


def test_empty_memory_extraction_prompt_is_unavailable(tmp_path):
    (tmp_path / "memory_extraction.md").write_text("  \n", encoding="utf-8")

    result = check_memory_extraction_startup(PromptLoader(tmp_path))

    assert result.enabled is False
    assert result.status.state == "unavailable"
    assert result.status.code == "MEMORY_EXTRACTION_PROMPT_INVALID"


def test_valid_memory_extraction_prompt_is_available(tmp_path):
    (tmp_path / "memory_extraction.md").write_text("Extract durable memory.", encoding="utf-8")

    result = check_memory_extraction_startup(PromptLoader(tmp_path))

    assert result.enabled is True
    assert result.status.state == "available"
    assert result.status.code == "MEMORY_EXTRACTION_AVAILABLE"
