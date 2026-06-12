"""Tests for chat context assembly."""

from pathlib import Path

import pytest

from agent.message import Message
from agent.prompt_loader import PromptLoader


def test_build_includes_system_prompt_and_current_user_message(tmp_path):
    """ContextBuilder should combine prompts, runtime metadata, and user input."""

    from agent.context import ContextBuilder

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

    from agent.context import ContextBuilder

    prompts_dir = _write_required_prompts(tmp_path)
    builder = ContextBuilder(PromptLoader(prompts_dir), max_history_messages=2)
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


def test_build_truncates_oversized_history_messages(tmp_path):
    """Long history items should be bounded before entering LLM messages."""

    from agent.context import ContextBuilder

    prompts_dir = _write_required_prompts(tmp_path)
    builder = ContextBuilder(PromptLoader(prompts_dir), max_message_chars=8)

    messages = builder.build(
        history=[Message(role="assistant", content="0123456789abcdef")],
        user_message=Message(role="user", content="current"),
        workspace=tmp_path,
        session_id="default",
    )

    assert messages[1]["content"] == "01234567[truncated]"


def test_build_includes_tool_messages_and_ids(tmp_path):
    """Tool results are part of session context once tool calling is enabled."""

    from agent.context import ContextBuilder

    prompts_dir = _write_required_prompts(tmp_path)
    builder = ContextBuilder(PromptLoader(prompts_dir))

    messages = builder.build(
        history=[
            Message(role="tool", content='{"status":"success"}', name="read_file", tool_call_id="call_1"),
            Message(role="assistant", content="kept"),
        ],
        user_message=Message(role="user", content="current"),
        workspace=tmp_path,
        session_id="default",
    )

    assert messages[1] == {
        "role": "tool",
        "content": '{"status":"success"}',
        "name": "read_file",
        "tool_call_id": "call_1",
    }
    assert [message["role"] for message in messages] == ["system", "tool", "assistant", "user"]


def test_build_preserves_assistant_tool_calls(tmp_path):
    """Assistant tool request messages must be replayable for the provider."""

    from agent.context import ContextBuilder

    prompts_dir = _write_required_prompts(tmp_path)
    builder = ContextBuilder(PromptLoader(prompts_dir))
    tool_calls = [{"id": "call_1", "type": "function"}]

    messages = builder.build(
        history=[Message(role="assistant", content="", tool_calls=tool_calls)],
        user_message=Message(role="user", content="current"),
        workspace=tmp_path,
        session_id="default",
    )

    assert messages[1] == {"role": "assistant", "content": "", "tool_calls": tool_calls}


def test_build_truncates_tool_messages(tmp_path):
    """Tool output should still obey the context message character limit."""

    from agent.context import ContextBuilder

    prompts_dir = _write_required_prompts(tmp_path)
    builder = ContextBuilder(PromptLoader(prompts_dir), max_message_chars=8)

    messages = builder.build(
        history=[Message(role="tool", content="0123456789abcdef", tool_call_id="call_1")],
        user_message=Message(role="user", content="current"),
        workspace=tmp_path,
        session_id="default",
    )

    assert messages[1]["content"] == "01234567[truncated]"
    assert messages[1]["tool_call_id"] == "call_1"


def test_build_raises_clear_error_when_required_prompt_is_missing(tmp_path):
    """Missing startup prompts should be visible during context construction."""

    from agent.context import ContextBuilder

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
