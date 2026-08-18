from agent.core.event_emitter import RuntimeEventEmitter


def test_travel_intake_events_are_registered_and_delivered_to_the_runtime_sink():
    items = []
    emitter = RuntimeEventEmitter(
        session_id="session-1",
        turn_id="turn-1",
        sink=type("Sink", (), {"emit": lambda self, event: items.append(event)})(),
    )

    draft_event = emitter.emit(
        "travel.intake_draft_updated",
        display={"visibility": "internal"},
        ui_metadata={
            "detail_type": "travel_intake_draft",
            "detail_data": {"draft": {"origin": "北京"}, "ready": True},
        },
        metadata={"ready": True},
    )
    handoff_event = emitter.emit(
        "travel.main_chat_handoff",
        display={"visibility": "internal"},
        ui_metadata={
            "detail_type": "travel_main_chat_handoff",
            "detail_data": {"question": "帮我写代码", "topic": "编程"},
        },
        metadata={"topic": "编程"},
    )
    confirmed_event = emitter.emit(
        "travel.planning_confirmed",
        display={"visibility": "internal"},
        ui_metadata={
            "detail_type": "travel_planning_confirmed",
            "detail_data": {"phase": "planning"},
        },
        metadata={"phase": "planning"},
    )

    assert draft_event is not None
    assert handoff_event is not None
    assert confirmed_event is not None
    assert [event.type for event in items] == [
        "travel.intake_draft_updated",
        "travel.main_chat_handoff",
        "travel.planning_confirmed",
    ]


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


def test_travel_candidate_auto_selection_is_a_valid_completed_runtime_event():
    items = []
    emitter = RuntimeEventEmitter(
        session_id="session-1",
        turn_id="turn-1",
        sink=type("Sink", (), {"emit": lambda self, event: items.append(event)})(),
    )

    event = emitter.emit(
        "travel.candidate_review_auto_selected",
        ui_metadata={
            "detail_type": "travel_candidates",
            "detail_data": {
                "status": "selected",
                "selected_candidate_id": "candidate-a",
                "candidates": [{"candidate_id": "candidate-a"}],
            },
        },
        metadata={"candidate_count": 1},
    )

    assert event is not None
    assert event.status == "completed"
    assert event.display["title"] == "行程方向已确定"
    assert items == [event]
