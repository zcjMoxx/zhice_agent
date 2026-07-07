"""Tests for chat context assembly."""

from pathlib import Path

import pytest

from agent.message import Message
from agent.prompt_loader import PromptLoader


def test_build_includes_system_prompt_and_current_user_message(tmp_path):
    """ContextBuilder should combine prompts, runtime metadata, and user input."""

    from agent.core.context import ContextBuilder

    prompts_dir = _write_required_prompts(tmp_path)
    builder = ContextBuilder(PromptLoader(prompts_dir))

    messages = builder.build(
        history=[],
        user_message=Message(role="user", content="hello"),
        workspace=tmp_path,
        session_id="default",
    )

    assert [message["role"] for message in messages] == ["system", "user"]
    assert "identity prompt" in messages[0]["content"]
    assert "tool policy prompt" in messages[0]["content"]
    assert "skills intro prompt" in messages[0]["content"]
    assert "Use tools only through the provided tool schemas." in messages[0]["content"]
    assert f"workspace={tmp_path}" in messages[0]["content"]
    assert "session_id=default" in messages[0]["content"]
    assert messages[-1] == {"role": "user", "content": "hello"}


def test_build_keeps_recent_history_in_order(tmp_path):
    """Only the most recent configured history messages should be sent."""

    from agent.core.context import ContextBuilder

    prompts_dir = _write_required_prompts(tmp_path)
    builder = ContextBuilder(
        PromptLoader(prompts_dir),
        max_history_turns=None,
        max_history_messages=2,
    )
    history = [
        Message(role="user", content="old user"),
        Message(role="assistant", content="recent assistant"),
        Message(role="user", content="recent user"),
    ]

    messages = builder.build(
        history=history,
        user_message=Message(role="user", content="current"),
        workspace=tmp_path,
        session_id="default",
    )

    assert messages[1:] == [
        {"role": "assistant", "content": "recent assistant"},
        {"role": "user", "content": "recent user"},
        {"role": "user", "content": "current"},
    ]


def test_build_keeps_relevant_recent_user_turns_in_order(tmp_path):
    """Turn-based history should keep relevant recent user turns in order."""

    from agent.core.context import ContextBuilder

    prompts_dir = _write_required_prompts(tmp_path)
    builder = ContextBuilder(
        PromptLoader(prompts_dir),
        max_history_turns=2,
        max_history_messages=10,
    )
    history = [
        Message(role="user", content="u1", turn_id="turn-1", turn_index=1),
        Message(role="assistant", content="a1", turn_id="turn-1", turn_index=1),
        Message(role="user", content="u2", turn_id="turn-2", turn_index=2),
        Message(role="assistant", content="a2", turn_id="turn-2", turn_index=2),
        Message(role="user", content="u3", turn_id="turn-3", turn_index=3),
        Message(role="assistant", content="a3", turn_id="turn-3", turn_index=3),
    ]

    messages = builder.build(
        history=history,
        user_message=Message(role="user", content="u2 u3 current"),
        workspace=tmp_path,
        session_id="default",
    )

    assert [message["content"] for message in messages[1:]] == [
        "u2",
        "a2",
        "u3",
        "a3",
        "u2 u3 current",
    ]


def test_build_omits_unrelated_turn_history(tmp_path):
    """Unrelated prior turns should not enter the LLM context."""

    from agent.core.context import ContextBuilder

    prompts_dir = _write_required_prompts(tmp_path)
    builder = ContextBuilder(PromptLoader(prompts_dir), max_history_turns=2)
    history = [
        Message(role="user", content="旧 JSONL 是什么？", turn_id="turn-1", turn_index=1),
        Message(role="assistant", content="旧 JSONL 没有 turn_id。", turn_id="turn-1", turn_index=1),
    ]

    messages = builder.build(
        history=history,
        user_message=Message(role="user", content="你好"),
        workspace=tmp_path,
        session_id="default",
    )

    assert [message["role"] for message in messages] == ["system", "user"]
    assert messages[-1]["content"] == "你好"


def test_build_keeps_follow_up_turn_from_assistant_text(tmp_path):
    """A term from the previous answer should make that whole turn relevant."""

    from agent.core.context import ContextBuilder

    prompts_dir = _write_required_prompts(tmp_path)
    builder = ContextBuilder(PromptLoader(prompts_dir), max_history_turns=2)
    history = [
        Message(role="user", content="a 是什么意思？", turn_id="turn-1", turn_index=1),
        Message(role="assistant", content="a 是入口参数，b 是本地相关性分数。", turn_id="turn-1", turn_index=1),
    ]

    messages = builder.build(
        history=history,
        user_message=Message(role="user", content="讲讲 b"),
        workspace=tmp_path,
        session_id="default",
    )

    assert [message["content"] for message in messages[1:]] == [
        "a 是什么意思？",
        "a 是入口参数，b 是本地相关性分数。",
        "讲讲 b",
    ]


def test_build_keeps_previous_turn_for_short_confirmation(tmp_path):
    """A short confirmation can continue an immediately previous proposal."""

    from agent.core.context import ContextBuilder

    prompts_dir = _write_required_prompts(tmp_path)
    builder = ContextBuilder(PromptLoader(prompts_dir), max_history_turns=1)
    history = [
        Message(role="user", content="先讨论方案", turn_id="turn-1", turn_index=1),
        Message(role="assistant", content="方案可以。需要我生成设计文档吗？", turn_id="turn-1", turn_index=1),
    ]

    messages = builder.build(
        history=history,
        user_message=Message(role="user", content="好的"),
        workspace=tmp_path,
        session_id="default",
    )

    assert [message["content"] for message in messages[1:]] == [
        "先讨论方案",
        "方案可以。需要我生成设计文档吗？",
        "好的",
    ]


def test_build_can_omit_all_history_by_turn_count(tmp_path):
    """A zero turn limit should leave only system and current user messages."""

    from agent.core.context import ContextBuilder

    prompts_dir = _write_required_prompts(tmp_path)
    builder = ContextBuilder(PromptLoader(prompts_dir), max_history_turns=0)

    messages = builder.build(
        history=[Message(role="user", content="old")],
        user_message=Message(role="user", content="current"),
        workspace=tmp_path,
        session_id="default",
    )

    assert [message["role"] for message in messages] == ["system", "user"]
    assert messages[-1]["content"] == "current"


def test_build_drops_old_whole_turns_for_message_cap(tmp_path):
    """The message hard cap should drop old turns rather than split them."""

    from agent.core.context import ContextBuilder

    prompts_dir = _write_required_prompts(tmp_path)
    builder = ContextBuilder(
        PromptLoader(prompts_dir),
        max_history_turns=3,
        max_history_messages=3,
    )
    history = [
        Message(role="user", content="project-alpha u1", turn_id="turn-1", turn_index=1),
        Message(role="assistant", content="project-alpha a1", turn_id="turn-1", turn_index=1),
        Message(role="user", content="project-alpha u2", turn_id="turn-2", turn_index=2),
        Message(role="assistant", content="project-alpha a2", turn_id="turn-2", turn_index=2),
        Message(role="user", content="project-alpha u3", turn_id="turn-3", turn_index=3),
        Message(role="assistant", content="project-alpha a3", turn_id="turn-3", turn_index=3),
    ]

    messages = builder.build(
        history=history,
        user_message=Message(role="user", content="project-alpha current"),
        workspace=tmp_path,
        session_id="default",
    )

    assert [message["content"] for message in messages[1:]] == [
        "project-alpha u3",
        "project-alpha a3",
        "project-alpha current",
    ]


def test_build_turn_selection_ignores_messages_without_turn_id(tmp_path):
    """Turn-based selection should ignore untagged history messages."""

    from agent.core.context import ContextBuilder

    prompts_dir = _write_required_prompts(tmp_path)
    builder = ContextBuilder(PromptLoader(prompts_dir), max_history_turns=1)

    messages = builder.build(
        history=[
            Message(role="assistant", content="untagged assistant"),
            Message(role="user", content="untagged user"),
            Message(role="user", content="kept user", turn_id="turn-kept", turn_index=1),
            Message(role="assistant", content="kept assistant", turn_id="turn-kept", turn_index=1),
        ],
        user_message=Message(role="user", content="kept"),
        workspace=tmp_path,
        session_id="default",
    )

    assert [message["content"] for message in messages[1:]] == [
        "kept user",
        "kept assistant",
        "kept",
    ]


def test_build_truncates_oversized_history_messages(tmp_path):
    """Long history items should be bounded before entering LLM messages."""

    from agent.core.context import ContextBuilder

    prompts_dir = _write_required_prompts(tmp_path)
    builder = ContextBuilder(PromptLoader(prompts_dir), max_history_turns=None, max_message_chars=8)

    messages = builder.build(
        history=[Message(role="assistant", content="0123456789abcdef")],
        user_message=Message(role="user", content="current"),
        workspace=tmp_path,
        session_id="default",
    )

    assert messages[1]["content"] == "01234567[truncated]"


def test_build_includes_complete_tool_messages_and_ids(tmp_path):
    """Complete assistant/tool blocks are part of session context."""

    from agent.core.context import ContextBuilder

    prompts_dir = _write_required_prompts(tmp_path)
    builder = ContextBuilder(PromptLoader(prompts_dir), max_history_turns=None)
    tool_calls = [{"id": "call_1", "type": "function"}]

    messages = builder.build(
        history=[
            Message(role="assistant", content="", tool_calls=tool_calls),
            Message(role="tool", content='{"status":"success"}', name="read_file", tool_call_id="call_1"),
            Message(role="assistant", content="kept"),
        ],
        user_message=Message(role="user", content="current"),
        workspace=tmp_path,
        session_id="default",
    )

    assert messages[1] == {"role": "assistant", "content": "", "tool_calls": tool_calls}
    assert messages[2] == {
        "role": "tool",
        "content": '{"status":"success"}',
        "name": "read_file",
        "tool_call_id": "call_1",
    }
    assert [message["role"] for message in messages] == [
        "system",
        "assistant",
        "tool",
        "assistant",
        "user",
    ]


def test_build_drops_orphan_tool_messages_after_history_trimming(tmp_path):
    """OpenAI-compatible providers reject tool messages without tool_calls."""

    from agent.core.context import ContextBuilder

    prompts_dir = _write_required_prompts(tmp_path)
    builder = ContextBuilder(
        PromptLoader(prompts_dir),
        max_history_turns=None,
        max_history_messages=2,
    )

    messages = builder.build(
        history=[
            Message(
                role="assistant",
                content="",
                tool_calls=[{"id": "call_1", "type": "function"}],
            ),
            Message(role="tool", content='{"status":"success"}', name="read_file", tool_call_id="call_1"),
            Message(role="assistant", content="kept"),
        ],
        user_message=Message(role="user", content="current"),
        workspace=tmp_path,
        session_id="default",
    )

    assert [message["role"] for message in messages] == ["system", "assistant", "user"]
    assert messages[1]["content"] == "kept"


def test_build_drops_incomplete_assistant_tool_call_blocks(tmp_path):
    """Assistant tool_calls are replayed only with their matching tool results."""

    from agent.core.context import ContextBuilder

    prompts_dir = _write_required_prompts(tmp_path)
    builder = ContextBuilder(PromptLoader(prompts_dir), max_history_turns=None)

    messages = builder.build(
        history=[
            Message(
                role="assistant",
                content="",
                tool_calls=[{"id": "call_1", "type": "function"}],
            ),
            Message(role="assistant", content="kept"),
        ],
        user_message=Message(role="user", content="current"),
        workspace=tmp_path,
        session_id="default",
    )

    assert [message["role"] for message in messages] == ["system", "assistant", "user"]
    assert messages[1]["content"] == "kept"


def test_build_preserves_complete_assistant_tool_calls(tmp_path):
    """Assistant tool request messages are replayed with matching tool output."""

    from agent.core.context import ContextBuilder

    prompts_dir = _write_required_prompts(tmp_path)
    builder = ContextBuilder(PromptLoader(prompts_dir), max_history_turns=None)
    tool_calls = [{"id": "call_1", "type": "function"}]

    messages = builder.build(
        history=[
            Message(role="assistant", content="", tool_calls=tool_calls),
            Message(role="tool", content='{"status":"success"}', tool_call_id="call_1"),
        ],
        user_message=Message(role="user", content="current"),
        workspace=tmp_path,
        session_id="default",
    )

    assert messages[1] == {"role": "assistant", "content": "", "tool_calls": tool_calls}


def test_build_truncates_tool_messages(tmp_path):
    """Tool output should still obey the context message character limit."""

    from agent.core.context import ContextBuilder

    prompts_dir = _write_required_prompts(tmp_path)
    builder = ContextBuilder(PromptLoader(prompts_dir), max_history_turns=None, max_message_chars=8)
    tool_calls = [{"id": "call_1", "type": "function"}]

    messages = builder.build(
        history=[
            Message(role="assistant", content="", tool_calls=tool_calls),
            Message(role="tool", content="0123456789abcdef", tool_call_id="call_1"),
        ],
        user_message=Message(role="user", content="current"),
        workspace=tmp_path,
        session_id="default",
    )

    assert messages[2]["content"] == "01234567[truncated]"
    assert messages[2]["tool_call_id"] == "call_1"


def test_build_raises_clear_error_when_required_prompt_is_missing(tmp_path):
    """Missing startup prompts should be visible during context construction."""

    from agent.core.context import ContextBuilder

    prompts_dir = tmp_path / "prompts"
    prompts_dir.mkdir()
    (prompts_dir / "identity.md").write_text("identity prompt", encoding="utf-8")
    (prompts_dir / "tool_use_policy.md").write_text("tool policy prompt", encoding="utf-8")
    builder = ContextBuilder(PromptLoader(prompts_dir))

    with pytest.raises(Exception, match="skills_intro"):
        builder.build(
            history=[],
            user_message=Message(role="user", content="current"),
            workspace=tmp_path,
            session_id="default",
        )


def _write_required_prompts(tmp_path: Path) -> Path:
    prompts_dir = tmp_path / "prompts"
    prompts_dir.mkdir()
    (prompts_dir / "identity.md").write_text("identity prompt", encoding="utf-8")
    (prompts_dir / "tool_use_policy.md").write_text("tool policy prompt", encoding="utf-8")
    (prompts_dir / "skills_intro.md").write_text("skills intro prompt", encoding="utf-8")
    return prompts_dir

