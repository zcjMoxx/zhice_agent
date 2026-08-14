from agent.core.event_emitter import RuntimeEventEmitter


def test_travel_plan_ready_is_a_valid_non_secret_runtime_event():
    items = []
    emitter = RuntimeEventEmitter(
        session_id="session-1",
        turn_id="turn-1",
        sink=type("Sink", (), {"emit": lambda self, event: items.append(event)})(),
    )

    event = emitter.emit(
        "travel.plan_ready",
        metadata={"plan_id": "travel-plan-one"},
    )

    assert event is not None
    assert event.status == "completed"
    assert event.display["title"] == "旅行计划已生成"
    assert items[0].metadata["plan_id"] == "travel-plan-one"


def test_travel_clarification_is_a_safe_waiting_runtime_event():
    items = []
    emitter = RuntimeEventEmitter(
        session_id="session-1",
        turn_id="turn-1",
        sink=type("Sink", (), {"emit": lambda self, event: items.append(event)})(),
    )

    event = emitter.emit(
        "travel.clarification_required",
        ui_metadata={
            "detail_type": "summary",
            "detail_data": {"questions": ["一共几位出行？"]},
        },
        metadata={"question_count": 1},
    )

    assert event is not None
    assert event.status == "waiting"
    assert items[0].ui_metadata["detail_data"]["questions"] == ["一共几位出行？"]
