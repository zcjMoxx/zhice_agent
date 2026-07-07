"""Tests for local turn relevance selection."""

from agent.message import Message
from agent.protocols.session import TurnGroup


def test_relevance_omits_unrelated_greeting():
    """Small talk should not inject an unrelated previous topic."""

    from agent.core.context_relevance import select_relevant_turns

    turns = [
        _turn(
            "turn-jsonl",
            1,
            [
                Message(role="user", content="旧 JSONL 和 metadata fallback 有什么区别？"),
                Message(role="assistant", content="新格式使用顶层 turn_id，不再从 metadata 回退。"),
            ],
        )
    ]

    assert select_relevant_turns("你好", turns) == []


def test_relevance_keeps_direct_follow_up_from_full_turn_text():
    """A follow-up to a term introduced by the assistant should keep that turn."""

    from agent.core.context_relevance import select_relevant_turns

    turn = _turn(
        "turn-answer",
        1,
        [
            Message(role="user", content="a 是什么意思？"),
            Message(role="assistant", content="a 是入口参数，b 是本地相关性分数。"),
        ],
    )

    assert select_relevant_turns("讲讲 b", [turn]) == [turn]


def test_relevance_weights_code_and_error_anchors():
    """Code-like anchors should connect a short debugging follow-up."""

    from agent.core.context_relevance import select_relevant_turns

    unrelated = _turn(
        "turn-doc",
        1,
        [
            Message(role="user", content="补一下设计文档"),
            Message(role="assistant", content="我会更新 docs_design 下的设计记录。"),
        ],
    )
    related = _turn(
        "turn-pytest",
        2,
        [
            Message(role="user", content="跑 pytest 报错"),
            Message(role="assistant", content="PermissionError 来自 .tmp/pytest_basetemp 被占用。"),
        ],
    )

    assert select_relevant_turns("pytest 这个 PermissionError 怎么处理", [unrelated, related]) == [
        related
    ]


def test_relevance_allows_short_confirmation_after_assistant_question():
    """A short confirmation can attach to the immediately previous proposal."""

    from agent.core.context_relevance import select_relevant_turns

    turn = _turn(
        "turn-proposal",
        1,
        [
            Message(role="user", content="先讨论方案"),
            Message(role="assistant", content="方案可以。需要我生成设计文档吗？"),
        ],
    )

    assert select_relevant_turns("好的", [turn]) == [turn]


def test_relevance_preserves_chronological_order_after_scoring():
    """Selected turns should be returned in their original order for context replay."""

    from agent.core.context_relevance import select_relevant_turns

    old = _turn(
        "turn-old",
        1,
        [
            Message(role="user", content="ContextBuilder 怎么取历史？"),
            Message(role="assistant", content="ContextBuilder 先组装 prompt。"),
        ],
    )
    new = _turn(
        "turn-new",
        2,
        [
            Message(role="user", content="turn_id 存在哪里？"),
            Message(role="assistant", content="turn_id 写在 JSONL 顶层字段。"),
        ],
    )

    assert select_relevant_turns("ContextBuilder 和 turn_id", [old, new]) == [old, new]


def _turn(turn_id: str, turn_index: int, messages: list[Message]) -> TurnGroup:
    for message in messages:
        message.turn_id = turn_id
        message.turn_index = turn_index
    return TurnGroup(turn_id=turn_id, turn_index=turn_index, messages=messages)
