from __future__ import annotations

from pathlib import Path

import pytest

from agent.memory.markdown_store import MarkdownMemoryStore, MemoryStoreError, _replace_with_retry
from agent.protocols.memory import MemoryContext


def test_markdown_store_initializes_and_supports_crud_without_losing_manual_text(tmp_path):
    context = _context(tmp_path)
    context.durable_file.parent.mkdir(parents=True)
    context.durable_file.write_text(
        "Manual header\n\n<!-- zhice-memory:start -->\n\n"
        "## profile\n\n## preferences\n\n## projects\n\n"
        "## constraints\n\n## decisions\n\n<!-- zhice-memory:end -->\n\nManual footer\n",
        encoding="utf-8",
    )
    store = MarkdownMemoryStore(context)

    created = store.add("preferences", "回答代码问题时先检查真实代码路径。")
    duplicate = store.add("preferences", " 回答代码问题时先检查真实代码路径。 ")
    replaced = store.replace(
        "preferences",
        created.content,
        "回答代码问题时先检查真实代码和运行状态。",
    )

    assert duplicate == created
    assert store.search("真实代码", category="preferences") == [replaced]

    assert store.delete("preferences", replaced.content) is True
    assert store.search(category="preferences") == []
    text = context.durable_file.read_text(encoding="utf-8")
    assert text.startswith("Manual header")
    assert text.endswith("Manual footer\n")
    assert "created_at" not in text
    assert "updated_at" not in text
    assert "source:" not in text
    assert "mem_" not in text


def test_markdown_store_rejects_malformed_managed_content_without_overwriting(tmp_path):
    context = _context(tmp_path)
    context.durable_file.parent.mkdir(parents=True)
    original = "# Memory\n\n<!-- zhice-memory:start -->\ninvalid\n<!-- zhice-memory:end -->\n"
    context.durable_file.write_text(original, encoding="utf-8")
    store = MarkdownMemoryStore(context)

    with pytest.raises(MemoryStoreError) as exc_info:
        store.add("preferences", "keep this")

    assert exc_info.value.code == "MEMORY_FORMAT_INVALID"
    assert context.durable_file.read_text(encoding="utf-8") == original


def test_markdown_store_searches_chinese_and_bounds_results(tmp_path):
    store = MarkdownMemoryStore(_context(tmp_path), max_read_chars=70)
    first = store.add("preferences", "回答项目问题时优先检查真实代码路径。")
    store.add("preferences", "解释问题时给出完整调用链和运行状态。")
    store.add("constraints", "不要访问工作区之外的文件。")

    matches = store.search("代码路径", limit=20)

    assert matches[0] == first
    assert sum(len(entry.content) for entry in matches) <= 70
    assert store.search("完全不存在的关键词") == []


def test_markdown_store_lists_with_offset_and_counts_matches(tmp_path):
    store = MarkdownMemoryStore(_context(tmp_path))
    first = store.add("preferences", "喜欢吃西瓜。")
    second = store.add("projects", "正在开发 ZhiCe-Agent。")

    assert store.count() == 2
    assert store.count("西瓜") == 1
    assert store.search(offset=1, limit=1) == [second]
    assert store.search(limit=1) == [first]


def test_markdown_store_rejects_invalid_category_and_missing_entry(tmp_path):
    store = MarkdownMemoryStore(_context(tmp_path))

    with pytest.raises(MemoryStoreError) as category_error:
        store.add("unknown", "value")
    with pytest.raises(MemoryStoreError) as missing_error:
        store.replace("preferences", "missing", "value")

    assert category_error.value.code == "MEMORY_INVALID_CATEGORY"
    assert missing_error.value.code == "MEMORY_ENTRY_NOT_FOUND"


def test_atomic_replace_retries_transient_permission_error(tmp_path, monkeypatch):
    source = tmp_path / "source.tmp"
    target = tmp_path / "target.md"
    source.write_text("new", encoding="utf-8")
    target.write_text("old", encoding="utf-8")
    real_replace = __import__("os").replace
    attempts = 0

    def flaky_replace(source_path, target_path):
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise PermissionError("transient Windows file lock")
        real_replace(source_path, target_path)

    monkeypatch.setattr("agent.memory.markdown_store.os.replace", flaky_replace)
    monkeypatch.setattr("agent.memory.markdown_store.time.sleep", lambda _delay: None)

    _replace_with_retry(source, target)

    assert attempts == 3
    assert target.read_text(encoding="utf-8") == "new"


def _context(root: Path) -> MemoryContext:
    memory_dir = root / "memory"
    return MemoryContext(
        scope="workspace",
        actor_user_id=None,
        memory_dir=memory_dir,
        durable_file=memory_dir / "MEMORY.md",
    )
