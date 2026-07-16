from __future__ import annotations

import json
from pathlib import Path

from agent.memory.markdown_store import MarkdownMemoryStore
from agent.memory.safety import MemorySafetyPolicy
from agent.protocols.memory import MemoryContext
from agent.tools.memory import MemoryReadTool, MemoryWriteTool


def test_memory_read_tool_searches_with_concrete_query(tmp_path):
    store = _store(tmp_path)
    entry = store.add("preferences", "回答代码问题时先检查真实实现。")
    tool = MemoryReadTool(tmp_path, store=store)

    result = tool.execute(
        {"mode": "search", "query": "真实实现", "category": "preferences", "limit": 5}
    )
    payload = json.loads(result.output)

    assert result.is_error is False
    assert payload["entries"][0]["content"] == entry.content
    assert payload["entries"][0] == {
        "category": "preferences",
        "content": entry.content,
    }
    assert payload["total"] == 1
    assert result.metadata == {
        "mode": "search",
        "match_count": 1,
        "total": 1,
        "has_more": False,
        "category": "preferences",
    }


def test_memory_read_tool_lists_and_groups_all_categories(tmp_path):
    store = _store(tmp_path)
    entry = store.add("preferences", "喜欢吃西瓜")
    store.add("projects", "正在开发 ZhiCe-Agent")
    tool = MemoryReadTool(tmp_path, store=store)

    result = tool.execute({"mode": "list", "offset": 0, "limit": 1})
    payload = json.loads(result.output)

    assert payload["total"] == 2
    assert payload["returned"] == 1
    assert payload["has_more"] is True
    assert payload["categories"]["preferences"][0] == {
        "category": "preferences",
        "content": entry.content,
    }
    assert payload["categories"]["profile"] == []
    assert result.metadata["mode"] == "list"


def test_memory_read_tool_rejects_search_without_query(tmp_path):
    tool = MemoryReadTool(tmp_path, store=_store(tmp_path))

    result = tool.execute({"mode": "search"})

    assert result.is_error is True
    assert result.metadata["code"] in {"MISSING_PARAM", "INVALID_PARAM"}


def test_memory_write_tool_add_replace_delete_and_never_echoes_sensitive_content(tmp_path):
    store = _store(tmp_path)
    tool = MemoryWriteTool(
        tmp_path,
        store=store,
        safety=MemorySafetyPolicy(),
    )

    added = tool.execute(
        {
            "operation": "add",
            "category": "constraints",
            "content": "用户明确要求时直接写入长期记忆。",
            "authorization": "user_explicit",
        }
    )
    added_payload = json.loads(added.output)
    replaced = tool.execute(
        {
            "operation": "replace",
            "category": "constraints",
            "old_content": added_payload["entry"]["content"],
            "content": "用户自然语言同意后可以写入长期记忆。",
            "authorization": "user_confirmed",
        }
    )
    deleted = tool.execute(
        {
            "operation": "delete",
            "category": "constraints",
            "content": "用户自然语言同意后可以写入长期记忆。",
            "authorization": "user_explicit",
        }
    )
    rejected = tool.execute(
        {
            "operation": "add",
            "category": "profile",
            "content": "api_key=sk-secret-value-123456789",
            "authorization": "user_explicit",
        }
    )
    unauthorized = tool.execute(
        {
            "operation": "add",
            "category": "preferences",
            "content": "模型自行推断的偏好。",
            "proposal_origin": "assistant_inferred",
        }
    )

    assert (
        json.loads(replaced.output)["entry"]["content"]
        == "用户自然语言同意后可以写入长期记忆。"
    )
    assert json.loads(deleted.output)["deleted"] is True
    assert rejected.is_error is True
    assert rejected.metadata["code"] == "MEMORY_SENSITIVE_CONTENT_REJECTED"
    assert "sk-secret" not in rejected.output
    assert unauthorized.is_error is True
    assert unauthorized.metadata["code"] == "MEMORY_USER_AUTHORIZATION_REQUIRED"
    assert tool.parameters["properties"]["authorization"]["enum"] == [
        "user_explicit",
        "user_confirmed",
    ]
    assert "proposal_origin" not in tool.parameters["properties"]
    assert "entry_id" not in tool.parameters["properties"]
    assert "old_content" in tool.parameters["properties"]


def test_memory_read_tool_rejects_removed_session_summary_mode(tmp_path):
    tool = MemoryReadTool(tmp_path, store=_store(tmp_path))

    result = tool.execute({"mode": "session_summary", "session_id": "session-a"})

    assert result.is_error is True
    assert result.metadata["code"] == "INVALID_PARAM"


def _store(root: Path) -> MarkdownMemoryStore:
    memory_dir = root / "memory"
    return MarkdownMemoryStore(
        MemoryContext(
            scope="workspace",
            actor_user_id=None,
            memory_dir=memory_dir,
            durable_file=memory_dir / "MEMORY.md",
        )
    )
