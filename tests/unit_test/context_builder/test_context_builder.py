"""Tests for second-stage no-tool chat context assembly."""

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
    assert "当前 CLI 不提供任何工具调用或 Skill 执行能力。" in messages[0]["content"]
    assert "<zhi-ce_use_file_system_tool>" in messages[0]["content"]
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


def test_build_skips_tool_messages_for_no_tool_chat(tmp_path):
    """The second-stage chat path should only send system/user/assistant roles."""

    from agent.context import ContextBuilder

    prompts_dir = _write_required_prompts(tmp_path)
    builder = ContextBuilder(PromptLoader(prompts_dir))

    messages = builder.build(
        history=[
            Message(role="tool", content="old tool result", tool_call_id="call_1"),
            Message(role="assistant", content="kept"),
        ],
        user_message=Message(role="user", content="current"),
        workspace=tmp_path,
        session_id="default",
    )

    assert [message["role"] for message in messages] == ["system", "assistant", "user"]
    assert "old tool result" not in str(messages)


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
