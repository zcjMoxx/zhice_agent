"""Helpers for runtime turn grouping and indexing."""

from __future__ import annotations

import uuid

from agent.message import Message
from agent.protocols.session import TurnGroup


def new_turn_id() -> str:
    """Return a stable id for one user turn."""

    return "turn-" + uuid.uuid4().hex


def group_messages_by_turn(messages: list[Message]) -> list[TurnGroup]:
    """Group messages by explicit ids, with deterministic legacy user boundaries."""

    groups: list[TurnGroup] = []

    legacy_index = 0
    active_legacy_id = ""
    for message in messages:
        turn_id = message.turn_id
        if not turn_id:
            if message.role == "user" or not active_legacy_id:
                legacy_index += 1
                active_legacy_id = f"legacy-turn-{legacy_index}"
            turn_id = active_legacy_id
        else:
            active_legacy_id = ""

        if groups and groups[-1].turn_id == turn_id:
            groups[-1].messages.append(message)
            if groups[-1].turn_index is None:
                groups[-1].turn_index = message.turn_index
        else:
            groups.append(
                TurnGroup(
                    turn_id=turn_id,
                    turn_index=message.turn_index or len(groups) + 1,
                    messages=[message],
                )
            )

    return groups


def next_turn_index(messages: list[Message]) -> int:
    """Return the next 1-based turn index for a session snapshot."""

    explicit_indices = [
        message.turn_index
        for message in messages
        if isinstance(message.turn_index, int) and message.turn_index > 0
    ]
    if explicit_indices:
        return max(explicit_indices) + 1

    legacy_user_turns = sum(
        1
        for group in group_messages_by_turn(messages)
        if any(message.role == "user" for message in group.messages)
    )
    return legacy_user_turns + 1


def assign_turn(
    message: Message,
    *,
    turn_id: str,
    turn_index: int,
    parent_turn_id: str | None = None,
) -> Message:
    """Attach turn metadata to a message and return it for fluent construction."""

    message.turn_id = turn_id
    message.turn_index = turn_index
    message.parent_turn_id = parent_turn_id
    return message
