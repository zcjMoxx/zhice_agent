"""Helpers for runtime turn grouping and indexing."""

from __future__ import annotations

import uuid

from agent.message import Message
from agent.protocols.session import TurnGroup


def new_turn_id() -> str:
    """Return a stable id for one user turn."""

    return "turn-" + uuid.uuid4().hex


def group_messages_by_turn(messages: list[Message]) -> list[TurnGroup]:
    """Group messages that already carry explicit turn ids."""

    groups: list[TurnGroup] = []

    for message in messages:
        if not message.turn_id:
            continue

        if groups and groups[-1].turn_id == message.turn_id:
            groups[-1].messages.append(message)
            if groups[-1].turn_index is None:
                groups[-1].turn_index = message.turn_index
        else:
            groups.append(
                TurnGroup(
                    turn_id=message.turn_id,
                    turn_index=message.turn_index,
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

    return 1


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
