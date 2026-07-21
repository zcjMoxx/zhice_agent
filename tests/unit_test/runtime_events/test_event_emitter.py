"""Tests for turn-scoped RuntimeEvent emission."""

from __future__ import annotations

from agent.core.event_emitter import RuntimeEventEmitter, callback_runtime_event_sink


def test_event_emitter_assigns_turn_scoped_sequence():
    events = []
    emitter = RuntimeEventEmitter(
        session_id="session-1",
        turn_id="turn-1",
        sink=callback_runtime_event_sink(events.append),
    )

    emitter.emit("turn.started")
    emitter.emit("context.started")

    assert [event["sequence"] for event in events] == [1, 2]
    assert {event["turn_id"] for event in events} == {"turn-1"}
    assert emitter.sequence == 2


def test_event_emitter_sink_failure_does_not_escape():
    def failing_sink(_event):
        raise RuntimeError("transport down")

    emitter = RuntimeEventEmitter(
        session_id="session-1",
        turn_id="turn-1",
        sink=callback_runtime_event_sink(failing_sink),
    )

    event = emitter.emit("turn.started")

    assert event is not None
    assert event.sequence == 1


def test_event_emitter_merges_safe_display_patch_with_core_default():
    events = []
    emitter = RuntimeEventEmitter(
        session_id="session-1",
        turn_id="turn-1",
        sink=callback_runtime_event_sink(events.append),
    )

    emitter.emit(
        "tool.completed",
        metadata={"tool_name": "read_file"},
        display={"icon": "search"},
    )

    assert events[0]["display"] == {
        "title": "read_file 执行完成",
        "icon": "search",
        "visibility": "normal",
    }
