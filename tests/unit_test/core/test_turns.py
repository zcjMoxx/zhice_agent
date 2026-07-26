"""Tests for runtime turn grouping helpers."""

from agent.core.turns import group_messages_by_turn, next_turn_index
from agent.message import Message


def test_group_messages_by_explicit_turn_id():
    """Messages with the same explicit turn id form one runtime group."""

    groups = group_messages_by_turn(
        [
            Message(role="user", content="hello", turn_id="turn-a", turn_index=1),
            Message(role="assistant", content="hi", turn_id="turn-a", turn_index=1),
        ],
    )

    assert len(groups) == 1
    assert groups[0].turn_id == "turn-a"
    assert groups[0].turn_index == 1
    assert [message.content for message in groups[0].messages] == ["hello", "hi"]


def test_group_messages_backfills_messages_without_turn_id():
    """Legacy messages receive deterministic in-memory Turn boundaries."""

    groups = group_messages_by_turn(
        [
            Message(role="user", content="first"),
            Message(role="assistant", content="one"),
            Message(role="user", content="second", turn_id="turn-2", turn_index=2),
            Message(role="assistant", content="two", turn_id="turn-2", turn_index=2),
        ],
    )

    assert [group.turn_id for group in groups] == ["legacy-turn-1", "turn-2"]
    assert [message.content for message in groups[0].messages] == ["first", "one"]
    assert [[message.content for message in group.messages] for group in groups] == [
        ["first", "one"],
        ["second", "two"],
    ]


def test_group_multiple_explicit_turns_keeps_file_order():
    """Explicit turn groups should appear in file order."""

    groups = group_messages_by_turn(
        [
            Message(role="user", content="first", turn_id="turn-a", turn_index=1),
            Message(role="assistant", content="one", turn_id="turn-a", turn_index=1),
            Message(role="user", content="second", turn_id="turn-b", turn_index=2),
            Message(role="assistant", content="two", turn_id="turn-b", turn_index=2),
        ],
    )

    assert [group.turn_id for group in groups] == ["turn-a", "turn-b"]
    assert [[message.content for message in group.messages] for group in groups] == [
        ["first", "one"],
        ["second", "two"],
    ]


def test_group_non_adjacent_same_turn_keeps_file_order_without_merging_back():
    """Grouping follows persisted order and does not reorder older fragments."""

    groups = group_messages_by_turn(
        [
            Message(role="user", content="first", turn_id="turn-a", turn_index=1),
            Message(role="assistant", content="middle", turn_id="turn-b", turn_index=2),
            Message(role="assistant", content="late", turn_id="turn-a", turn_index=1),
        ],
    )

    assert [group.turn_id for group in groups] == ["turn-a", "turn-b", "turn-a"]


def test_next_turn_index_uses_explicit_indices_first():
    """Explicit persisted indices are the strongest source for the next index."""

    assert (
        next_turn_index(
            [
                Message(role="user", content="old", turn_id="turn-a", turn_index=3),
                Message(role="user", content="ignored"),
            ],
        )
        == 4
    )


def test_next_turn_index_counts_legacy_user_turns_without_explicit_indices():
    """New writes continue after lazily inferred legacy Turn boundaries."""

    assert (
        next_turn_index(
            [
                Message(role="user", content="first"),
                Message(role="assistant", content="one"),
                Message(role="user", content="second"),
            ],
        )
        == 3
    )
