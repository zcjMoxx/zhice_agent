"""Tests for chat context assembly."""

from pathlib import Path

import pytest

from agent.message import Message
from agent.prompt_loader import PromptLoader

pytestmark = pytest.mark.filterwarnings(
    "ignore:Fixed history count settings are deprecated:DeprecationWarning"
)


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


def test_build_keeps_full_history_in_order(tmp_path):
    """Deprecated fixed message limits no longer remove history that fits."""

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
        {"role": "user", "content": "old user"},
        {"role": "assistant", "content": "recent assistant"},
        {"role": "user", "content": "recent user"},
        {"role": "user", "content": "current"},
    ]


def test_build_keeps_all_budget_fitting_turns_in_order(tmp_path):
    """Full mode keeps all complete Turns in original order."""

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
        "u1",
        "a1",
        "u2",
        "a2",
        "u3",
        "a3",
        "u2 u3 current",
    ]


def test_build_keeps_unrelated_turn_when_full_history_fits(tmp_path):
    """Relevance must not delete Session state before the budget requires it."""

    from agent.core.context import ContextBuilder

    prompts_dir = _write_required_prompts(tmp_path)
    builder = ContextBuilder(
        PromptLoader(prompts_dir),
        max_history_turns=2,
        always_include_recent_turns=0,
    )
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

    assert [message["role"] for message in messages] == ["system", "user", "assistant", "user"]
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


def test_deprecated_zero_turn_limit_does_not_remove_history(tmp_path):
    """The removed fixed Turn strategy cannot override budget-first full mode."""

    from agent.core.context import ContextBuilder

    prompts_dir = _write_required_prompts(tmp_path)
    builder = ContextBuilder(PromptLoader(prompts_dir), max_history_turns=0)

    messages = builder.build(
        history=[Message(role="user", content="old")],
        user_message=Message(role="user", content="current"),
        workspace=tmp_path,
        session_id="default",
    )

    assert [message["role"] for message in messages] == ["system", "user", "user"]
    assert messages[-1]["content"] == "current"


def test_deprecated_message_cap_does_not_drop_budget_fitting_turns(tmp_path):
    """Message count is no longer a normal Session deletion policy."""

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
        "project-alpha u1",
        "project-alpha a1",
        "project-alpha u2",
        "project-alpha a2",
        "project-alpha u3",
        "project-alpha a3",
        "project-alpha current",
    ]


def test_build_lazily_backfills_messages_without_turn_id(tmp_path):
    """Legacy Session messages remain available through inferred Turn groups."""

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
        "untagged assistant",
        "untagged user",
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


def test_build_preserves_complete_legacy_tool_block_in_full_mode(tmp_path):
    """A complete tool-call/result block remains atomic in full history."""

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

    assert [message["role"] for message in messages] == [
        "system",
        "assistant",
        "tool",
        "assistant",
        "user",
    ]
    assert messages[3]["content"] == "kept"


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


def test_context_builder_includes_optional_memory_policy(tmp_path):
    prompts_dir = _write_required_prompts(tmp_path)
    (prompts_dir / "memory_policy.md").write_text(
        "Ask in conversation before writing inferred Memory.", encoding="utf-8"
    )
    from agent.core.context import ContextBuilder

    builder = ContextBuilder(PromptLoader(prompts_dir))

    messages = builder.build(
        history=[],
        user_message=Message(role="user", content="hello"),
        workspace=tmp_path,
        session_id="session-a",
    )

    assert "# Memory Policy" in messages[0]["content"]
    assert "Ask in conversation before writing inferred Memory." in messages[0]["content"]


def test_context_builder_includes_optional_diagnostics_policy(tmp_path):
    prompts_dir = _write_required_prompts(tmp_path)
    (prompts_dir / "diagnostics.md").write_text(
        "Analyze chronological trace evidence directly.", encoding="utf-8"
    )
    from agent.core.context import ContextBuilder

    messages = ContextBuilder(PromptLoader(prompts_dir)).build(
        history=[],
        user_message=Message(role="user", content="why did it fail"),
        workspace=tmp_path,
        session_id="session-a",
    )

    assert "# Diagnostics Policy" in messages[0]["content"]
    assert "Analyze chronological trace evidence directly." in messages[0]["content"]


def test_context_builder_does_not_require_diagnostics_policy(tmp_path):
    prompts_dir = _write_required_prompts(tmp_path)
    from agent.core.context import ContextBuilder

    messages = ContextBuilder(PromptLoader(prompts_dir)).build(
        history=[],
        user_message=Message(role="user", content="hello"),
        workspace=tmp_path,
        session_id="session-a",
    )

    assert "# Diagnostics Policy" not in messages[0]["content"]


def test_context_builder_includes_optional_exec_policy(tmp_path):
    prompts_dir = _write_required_prompts(tmp_path)
    (prompts_dir / "exec.md").write_text(
        "Use the smallest non-interactive command.", encoding="utf-8"
    )
    from agent.core.context import ContextBuilder

    messages = ContextBuilder(PromptLoader(prompts_dir)).build(
        history=[],
        user_message=Message(role="user", content="run tests"),
        workspace=tmp_path,
        session_id="session-a",
    )

    assert "# Exec Policy" in messages[0]["content"]
    assert "Use the smallest non-interactive command." in messages[0]["content"]


def test_context_builder_does_not_require_exec_policy(tmp_path):
    prompts_dir = _write_required_prompts(tmp_path)
    from agent.core.context import ContextBuilder

    messages = ContextBuilder(PromptLoader(prompts_dir)).build(
        history=[],
        user_message=Message(role="user", content="hello"),
        workspace=tmp_path,
        session_id="session-a",
    )

    assert "# Exec Policy" not in messages[0]["content"]


def test_repository_memory_policy_uses_conversational_authorization():
    policy = (
        Path(__file__).resolve().parents[3] / "prompts" / "memory_policy.md"
    ).read_text(encoding="utf-8")

    assert "authorization=user_explicit" in policy
    assert "authorization=user_confirmed" in policy
    assert "独立的 idle-session Extractor" in policy
    assert "不要增加隐藏 review 调用" in policy
    assert "assistant_inferred" not in policy
    assert "confirmation broker" not in policy


def _write_required_prompts(tmp_path: Path) -> Path:
    prompts_dir = tmp_path / "prompts"
    prompts_dir.mkdir()
    (prompts_dir / "identity.md").write_text("identity prompt", encoding="utf-8")
    (prompts_dir / "tool_use_policy.md").write_text("tool policy prompt", encoding="utf-8")
    (prompts_dir / "skills_intro.md").write_text("skills intro prompt", encoding="utf-8")
    return prompts_dir


def test_context_builder_defaults_use_recent_three_plus_three_relevant_turns(tmp_path):
    from agent.core.context import ContextBuilder

    builder = ContextBuilder(PromptLoader(_write_required_prompts(tmp_path)))

    assert builder.max_history_turns == 50
    assert builder.always_include_recent_turns == 3
    assert builder.max_relevant_turns == 3


def test_build_keeps_all_turns_when_full_history_fits(tmp_path):
    from agent.core.context import ContextBuilder

    builder = ContextBuilder(PromptLoader(_write_required_prompts(tmp_path)))
    history = []
    for index, content in enumerate(
        [
            "project-alpha old one",
            "project-alpha old two",
            "project-alpha old three",
            "unrelated old four",
            "unrelated old five",
            "recent six",
            "recent seven",
            "recent eight",
        ],
        start=1,
    ):
        turn_id = f"turn-{index}"
        history.extend(
            [
                Message(role="user", content=content, turn_id=turn_id, turn_index=index),
                Message(
                    role="assistant",
                    content=f"answer {index}",
                    turn_id=turn_id,
                    turn_index=index,
                ),
            ]
        )

    messages = builder.build(
        history=history,
        user_message=Message(role="user", content="continue project-alpha"),
        workspace=tmp_path,
        session_id="default",
    )

    contents = [message["content"] for message in messages]
    assert contents[1::2][:-1] == [
        "project-alpha old one",
        "project-alpha old two",
        "project-alpha old three",
        "unrelated old four",
        "unrelated old five",
        "recent six",
        "recent seven",
        "recent eight",
    ]


def test_context_budget_drops_retrieved_turns_before_recent_three(tmp_path):
    from agent.core.context import ContextBuilder, estimate_llm_tokens
    from agent.protocols.llm import ContextBudget

    builder = ContextBuilder(PromptLoader(_write_required_prompts(tmp_path)))
    history = []
    for index in range(1, 7):
        turn_id = f"turn-{index}"
        history.extend(
            [
                Message(
                    role="user",
                    content=f"project-alpha question {index} " + "x" * 160,
                    turn_id=turn_id,
                    turn_index=index,
                ),
                Message(
                    role="assistant",
                    content=f"project-alpha answer {index} " + "y" * 160,
                    turn_id=turn_id,
                    turn_index=index,
                ),
            ]
        )
    unbounded = builder.build(
        history=history,
        user_message=Message(role="user", content="continue project-alpha"),
        workspace=tmp_path,
        session_id="default",
    )
    recent_only = [unbounded[0], *unbounded[-7:]]
    budget = ContextBudget(input_token_limit=estimate_llm_tokens(recent_only))

    messages = builder.build(
        history=history,
        user_message=Message(role="user", content="continue project-alpha"),
        workspace=tmp_path,
        session_id="default",
        context_budget=budget,
    )

    contents = [message["content"] for message in messages]
    assert not any("question 1" in content for content in contents)
    assert not any("question 2" in content for content in contents)
    assert not any("question 3" in content for content in contents)
    assert all(any(f"question {index}" in content for content in contents) for index in (4, 5, 6))
    assert estimate_llm_tokens(messages) <= budget.input_token_limit


def test_context_budget_rejects_required_content_that_cannot_fit(tmp_path):
    from agent.core.context import ContextBuilder
    from agent.protocols.llm import ContextBudget, LLMContextBudgetError

    builder = ContextBuilder(PromptLoader(_write_required_prompts(tmp_path)))

    with pytest.raises(LLMContextBudgetError, match="Required system/current-turn content"):
        builder.build(
            history=[],
            user_message=Message(role="user", content="current request"),
            workspace=tmp_path,
            session_id="default",
            context_budget=ContextBudget(input_token_limit=1),
        )
