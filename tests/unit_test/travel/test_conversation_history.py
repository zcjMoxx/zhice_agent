from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from agent.app.runtime import (
    WebRuntime,
    _persisted_candidate_research_complete,
    _persisted_candidate_research_profiles,
)
from agent.applications.travel.service import TravelApplicationError
from agent.applications.travel.source_ledger import TravelSourceLedger
from agent.config import AppConfig
from agent.message import Message
from agent.protocols.auth import ActorContext
from agent.protocols.session import SessionState
from agent.session import JsonlSessionStore


def test_runtime_persists_requirement_conversation_and_retries_idempotently(tmp_path):
    access = _SessionAccess(channel="travel")
    runtime = _runtime(tmp_path, access)
    messages = [
        {"role": "user", "content": "重庆出发去大理"},
        {"role": "assistant", "content": "哪天出发？"},
    ]

    saved = runtime.persist_travel_conversation(_actor(), "travel-a", messages)
    unchanged = runtime.persist_travel_conversation(_actor(), "travel-a", messages)

    assert saved == {"session_id": "travel-a", "message_count": 2, "status": "saved"}
    assert unchanged["status"] == "unchanged"
    assert [message.role for message in access.store.messages] == ["user", "assistant"]
    assert all(message.metadata["travel_visibility"] == "conversation" for message in access.store.messages)
    assert access.refreshes == 1


def test_runtime_updates_collecting_draft_and_restores_structured_state(tmp_path):
    access = _SessionAccess(channel="travel")
    runtime = _runtime(tmp_path, access)
    first = [{"role": "user", "content": "重庆出发去大理"}]
    updated = [*first, {"role": "assistant", "content": "几号出发？"}]
    draft = _draft()

    runtime.persist_travel_conversation(_actor(), "travel-a", first)
    result = runtime.persist_travel_conversation(
        _actor(), "travel-a", updated, draft=draft
    )
    restored = runtime.travel_draft(_actor(), "travel-a")

    assert result["status"] == "updated"
    assert restored["messages"] == updated
    assert restored["draft"]["origin"] == "重庆"
    assert restored["phase"] == "intake"
    assert access.store.metadata["title"] == "重庆 → 大理"


def test_runtime_confirms_complete_draft_and_rejects_incomplete_state(tmp_path):
    access = _SessionAccess(channel="travel")
    runtime = _runtime(tmp_path, access)

    confirmed = runtime.confirm_travel_planning(_actor(), "travel-a", _draft())

    assert confirmed == {
        "session_id": "travel-a",
        "phase": "planning",
        "status": "confirmed",
    }
    assert access.store.metadata["travel_phase"] == "planning"
    assert access.store.metadata["travel_draft"]["destinations"] == ["大理"]
    assert access.store.metadata["travel_draft"]["pace"] == "balanced"
    assert any("每间每晚上限约450元" in item for item in access.store.metadata["travel_draft"]["stay_preferences"])

    incomplete = {**_draft(), "traveller_count": None}
    with pytest.raises(TravelApplicationError) as captured:
        runtime.confirm_travel_planning(_actor(), "travel-b", incomplete)
    assert captured.value.code == "TRAVEL_REQUIREMENTS_INCOMPLETE"


def test_candidate_stage_continuation_requires_fixed_three_lane_delegation(tmp_path):
    access = _SessionAccess(channel="travel")
    access.store.update_metadata("travel-a", {"travel_phase": "planning"})
    runtime = _runtime(tmp_path, access)
    runtime.prompt_loader = SimpleNamespace(load=lambda name: f"prompt:{name}")
    runtime.travel_service = SimpleNamespace(
        get_candidate_review=lambda actor, session_id: None
    )

    message = runtime.travel_continuation_message(_actor(), "travel-a")

    assert "delegate_tasks" in message
    assert "恰好三个任务" in message
    assert "travel-transport-weather" in message
    assert "travel-stay-poi" in message
    assert "travel-guides" in message


def test_candidate_stage_continuation_reuses_completed_research_for_optimizer(tmp_path):
    access = _SessionAccess(channel="travel")
    access.store.update_metadata("travel-a", {"travel_phase": "planning"})
    runtime = _runtime(tmp_path, access)
    runtime.prompt_loader = SimpleNamespace(load=lambda name: f"prompt:{name}")
    runtime.travel_service = SimpleNamespace(
        get_candidate_review=lambda actor, session_id: None,
        source_ledger=SimpleNamespace(
            snapshot=lambda session_id: SimpleNamespace(candidate_missing_attempts=())
        ),
    )

    message = runtime.travel_continuation_message(_actor(), "travel-a")

    assert "不要再次调用 delegate_tasks" in message
    assert "一至三个" in message
    assert "立即运行 optimizer" in message


def test_persisted_complete_candidate_batch_is_durable_completion_fact():
    messages = _candidate_delegation_messages(
        statuses={
            "transport": ("completed", "OK"),
            "stay": ("completed", "OK"),
            "guides": ("completed", "OK"),
        }
    )

    assert _persisted_candidate_research_complete(messages) is True
    assert _persisted_candidate_research_profiles(messages) == frozenset(
        {
            "travel-transport-weather",
            "travel-stay-poi",
            "travel-guides",
        }
    )


@pytest.mark.parametrize(
    "statuses",
    [
        {
            "transport": ("completed", "OK"),
            "stay": ("completed", "OK"),
            "guides": ("failed", "SUBAGENT_LLM_FAILED"),
        },
        {
            "transport": ("completed", "OK"),
            "stay": ("completed", "OK"),
        },
    ],
)
def test_persisted_partial_candidate_batch_is_not_complete(statuses):
    messages = _candidate_delegation_messages(statuses=statuses)
    assert _persisted_candidate_research_complete(messages) is False
    assert _persisted_candidate_research_profiles(messages) == frozenset(
        {
            profile
            for task_id, profile in {
                "transport": "travel-transport-weather",
                "stay": "travel-stay-poi",
                "guides": "travel-guides",
            }.items()
            if statuses.get(task_id) == ("completed", "OK")
        }
    )


def test_candidate_continuation_retries_only_the_failed_persisted_lane(tmp_path):
    access = _SessionAccess(channel="travel")
    access.store.update_metadata("travel-a", {"travel_phase": "planning"})
    access.store.messages = _candidate_delegation_messages(
        statuses={
            "transport": ("completed", "OK"),
            "stay": ("completed", "OK"),
            "guides": ("failed", "SUBAGENT_LLM_FAILED"),
        }
    )
    runtime = _runtime(tmp_path, access)
    runtime.prompt_loader = SimpleNamespace(load=lambda name: f"prompt:{name}")
    ledger = TravelSourceLedger()
    runtime.travel_service = SimpleNamespace(
        get_candidate_review=lambda actor, session_id: None,
        source_ledger=ledger,
    )

    message = runtime.travel_continuation_message(_actor(), "travel-a")

    assert "禁止重跑这些已完成子任务" in message
    assert "travel-guides 只补" in message
    assert "travel-transport-weather 只补" not in message
    assert "travel-stay-poi 只补" not in message
    assert ledger.snapshot("travel-a").candidate_completed_profiles == frozenset(
        {"travel-transport-weather", "travel-stay-poi"}
    )


def test_candidate_continuation_uses_persisted_fan_in_after_ledger_loss(tmp_path):
    access = _SessionAccess(channel="travel")
    access.store.update_metadata("travel-a", {"travel_phase": "planning"})
    access.store.messages = _candidate_delegation_messages(
        statuses={
            "transport": ("completed", "OK"),
            "stay": ("completed", "OK"),
            "guides": ("completed", "OK"),
        }
    )
    runtime = _runtime(tmp_path, access)
    runtime.prompt_loader = SimpleNamespace(load=lambda name: f"prompt:{name}")
    runtime.travel_service = SimpleNamespace(
        get_candidate_review=lambda actor, session_id: None,
        source_ledger=SimpleNamespace(
            snapshot=lambda session_id: SimpleNamespace(
                candidate_missing_attempts=("maps", "social", "transport")
            )
        ),
    )

    message = runtime.travel_continuation_message(_actor(), "travel-a")

    assert "不要再次调用 delegate_tasks" in message
    assert "立即运行 optimizer" in message


def _candidate_delegation_messages(*, statuses):
    profile_by_id = {
        "transport": "travel-transport-weather",
        "stay": "travel-stay-poi",
        "guides": "travel-guides",
    }
    tasks = [
        {"id": task_id, "task": f"run {profile}", "profile": profile}
        for task_id, profile in profile_by_id.items()
    ]
    results = [
        {
            "id": task_id,
            "status": status,
            "code": code,
            "child_session_id": f"child-{task_id}",
        }
        for task_id, (status, code) in statuses.items()
    ]
    return [
        Message(
            role="assistant",
            content="",
            tool_calls=[{
                "id": "candidate-batch",
                "type": "function",
                "function": {
                    "name": "delegate_tasks",
                    "arguments": json.dumps(
                        {"reason": "parallel_independent", "tasks": tasks}
                    ),
                },
            }],
        ),
        Message(
            role="tool",
            content=json.dumps(
                {
                    "status": "completed" if len(results) == 3 else "partial",
                    "results": results,
                }
            ),
            name="delegate_tasks",
            tool_call_id="candidate-batch",
            metadata={"is_error": False},
        ),
    ]


def test_runtime_rebuilds_progress_from_owned_travel_session(tmp_path):
    access = _SessionAccess(channel="travel")
    access.store.messages = [
        Message(
            role="assistant",
            content="",
            tool_calls=[{
                "id": "final",
                "type": "function",
                "function": {"name": "finalize_travel_plan", "arguments": '{"plan":{}}'},
            }],
        ),
        Message(
            role="tool",
            content='{"status":"success"}',
            name="finalize_travel_plan",
            tool_call_id="final",
            metadata={"is_error": False},
        ),
    ]
    runtime = _runtime(tmp_path, access)

    history = runtime.travel_progress_history(_actor(), "travel-a")

    assert history["session_id"] == "travel-a"
    assert history["items"][-1]["id"] == "history-complete"


def test_runtime_rebuilds_progress_from_persisted_child_sessions(tmp_path):
    access = _SessionAccess(channel="travel")
    sessions_dir = tmp_path / "contexts" / "sessions"
    access.store.sessions_dir = sessions_dir
    access.store.messages = [
        Message(
            role="assistant",
            content="",
            tool_calls=[{
                "id": "delegate",
                "type": "function",
                "function": {"name": "delegate_tasks", "arguments": '{"tasks":[]}'},
            }],
            metadata={"timestamp": 1},
        ),
        Message(
            role="tool",
            content='{"status":"completed"}',
            name="delegate_tasks",
            tool_call_id="delegate",
            metadata={"is_error": False, "timestamp": 4},
        ),
    ]
    child_store = JsonlSessionStore(sessions_dir / "_subagents" / "travel-a")
    child_store.append(
        "child-guides",
        [
            Message(
                role="assistant",
                content="",
                tool_calls=[{
                    "id": "tavily-child",
                    "type": "function",
                    "function": {
                        "name": "mcp__tavily__tavily_search",
                        "arguments": '{"query":"大理避坑"}',
                    },
                }],
                metadata={"timestamp": 2},
            ),
            Message(
                role="tool",
                content=(
                    '{"results":[{"title":"大理避坑攻略",'
                    '"content":"保留机动时间"}]}'
                ),
                name="mcp__tavily__tavily_search",
                tool_call_id="tavily-child",
                metadata={"is_error": False, "timestamp": 3},
            ),
            Message(
                role="assistant",
                content="原始 Child 总结不应直接显示",
                metadata={"timestamp": 3.5},
            ),
        ],
    )
    runtime = _runtime(tmp_path, access)

    history = runtime.travel_progress_history(_actor(), "travel-a")

    serialized = str(history["items"])
    assert "Tavily 网页检索查询完成" in serialized
    assert "大理避坑攻略" in serialized
    assert "原始 Child 总结不应直接显示" not in serialized


def test_selected_candidate_continuation_reuses_completed_finalization_children(tmp_path):
    access = _SessionAccess(channel="travel")
    access.store.update_metadata("travel-a", {"travel_phase": "planning"})
    runtime = _runtime(tmp_path, access)
    runtime.prompt_loader = SimpleNamespace(load=lambda name: f"prompt:{name}")
    runtime.travel_service = SimpleNamespace(
        get_candidate_review=lambda actor, session_id: SimpleNamespace(status="selected"),
        source_ledger=SimpleNamespace(
            snapshot=lambda session_id: SimpleNamespace(missing_attempts=())
        ),
    )

    message = runtime.travel_continuation_message(_actor(), "travel-a")

    assert "不要再次调用 delegate_tasks" in message
    assert "立即再次调用 finalize_travel_plan" in message


def test_selected_candidate_continuation_repairs_persisted_forecast_gap(tmp_path):
    access = _SessionAccess(channel="travel")
    access.store.update_metadata("travel-a", {"travel_phase": "planning"})
    access.store.append(
        "travel-a",
        [
            Message(
                role="tool",
                content="forecast required",
                name="finalize_travel_plan",
                metadata={"is_error": True, "code": "TRAVEL_WEATHER_FORECAST_REQUIRED"},
            )
        ],
    )
    runtime = _runtime(tmp_path, access)
    runtime.prompt_loader = SimpleNamespace(load=lambda name: f"prompt:{name}")
    runtime.travel_service = SimpleNamespace(
        get_candidate_review=lambda actor, session_id: SimpleNamespace(status="selected"),
        source_ledger=SimpleNamespace(
            snapshot=lambda session_id: SimpleNamespace(
                missing_attempts=(),
                forecast_successful=False,
            )
        ),
    )

    message = runtime.travel_continuation_message(_actor(), "travel-a")

    assert "travel-final-weather" in message
    assert "只创建一个" in message
    assert "禁止查询历史天气" in message


def test_candidate_continuation_routes_persisted_route_gap_to_single_repair(tmp_path):
    access = _SessionAccess(channel="travel")
    access.store.update_metadata("travel-a", {"travel_phase": "planning"})
    access.store.append(
        "travel-a",
        [
            Message(
                role="tool",
                content="route required",
                name="finalize_travel_plan",
                metadata={"is_error": True, "code": "TRAVEL_ROUTE_EVIDENCE_MISSING"},
            )
        ],
    )
    runtime = _runtime(tmp_path, access)
    runtime.prompt_loader = SimpleNamespace(load=lambda name: f"prompt:{name}")
    runtime.travel_service = SimpleNamespace(
        get_candidate_review=lambda actor, session_id: SimpleNamespace(
            status="selected",
            selected_candidate_id="candidate-a",
            to_dict=lambda: {
                "status": "selected",
                "selected_candidate_id": "candidate-a",
            },
        ),
        source_ledger=SimpleNamespace(
            snapshot=lambda session_id: SimpleNamespace(
                missing_attempts=(),
                route_repair_attempted=False,
            )
        ),
    )

    message = runtime.travel_candidate_continuation_message(_actor(), "travel-a")

    assert "只创建一个 travel-final-route" in message
    assert "不少于 2 公里" in message
    assert "游客中心" in message
    assert "高德驾车" in message


def test_work_item_recovers_failed_status_from_persisted_finalizer_error(tmp_path):
    access = _SessionAccess(channel="travel")
    access.store.update_metadata("travel-a", {"travel_phase": "planning"})
    access.store.append(
        "travel-a",
        [
            Message(
                role="tool",
                content="forecast required",
                name="finalize_travel_plan",
                metadata={"is_error": True, "code": "TRAVEL_WEATHER_FORECAST_REQUIRED"},
            )
        ],
    )
    runtime = _runtime(tmp_path, access)

    item = runtime.list_travel_work_items(_actor())[0]

    assert item["status"] == "failed"
    assert item["error_code"] == "TRAVEL_WEATHER_FORECAST_REQUIRED"


def test_runtime_rejects_progress_history_for_non_travel_session(tmp_path):
    runtime = _runtime(tmp_path, _SessionAccess(channel="web"))

    with pytest.raises(TravelApplicationError) as captured:
        runtime.travel_progress_history(_actor(), "web-a")

    assert captured.value.code == "TRAVEL_GENERATION_NOT_FOUND"


def test_runtime_lists_collecting_and_failed_travel_work_items(tmp_path):
    access = _SessionAccess(channel="travel")
    runtime = _runtime(tmp_path, access)
    runtime.persist_travel_conversation(
        _actor(), "travel-a", [{"role": "user", "content": "重庆去大理"}], draft=_draft()
    )

    collecting = runtime.list_travel_work_items(_actor())
    access.store.update_metadata("travel-a", {"travel_phase": "planning"})
    access.turns = [{"session_id": "travel-a", "turn_id": "turn-a", "status": "error", "error_code": "LLM_TIMEOUT"}]
    failed = runtime.list_travel_work_items(_actor())

    assert collecting[0]["status"] == "collecting"
    assert collecting[0]["title"] == "重庆 → 大理"
    assert failed[0]["status"] == "failed"
    assert failed[0]["error_code"] == "LLM_TIMEOUT"


def test_completed_intake_agent_turn_stays_collecting_in_the_work_list(tmp_path):
    access = _SessionAccess(channel="travel")
    runtime = _runtime(tmp_path, access)
    runtime.persist_travel_conversation(
        _actor(), "travel-a", [{"role": "user", "content": "你是谁"}]
    )
    access.store.update_metadata("travel-a", {"travel_phase": "intake"})
    access.turns = [{"session_id": "travel-a", "turn_id": "turn-a", "status": "success"}]

    item = runtime.list_travel_work_items(_actor())[0]

    assert item["status"] == "collecting"
    assert item["error_code"] == ""


def test_runtime_rejects_non_travel_conflict_and_invalid_messages(tmp_path):
    non_travel = _runtime(tmp_path, _SessionAccess(channel="web"))
    with pytest.raises(TravelApplicationError) as missing:
        non_travel.persist_travel_conversation(
            _actor(), "web-a", [{"role": "user", "content": "hello"}]
        )
    assert missing.value.code == "TRAVEL_GENERATION_NOT_FOUND"

    access = _SessionAccess(channel="travel")
    runtime = _runtime(tmp_path, access)
    with pytest.raises(TravelApplicationError) as invalid:
        runtime.persist_travel_conversation(
            _actor(), "travel-a", [{"role": "tool", "content": "hidden"}]
        )
    assert invalid.value.code == "TRAVEL_CONVERSATION_INVALID"

    runtime.persist_travel_conversation(
        _actor(), "travel-a", [{"role": "user", "content": "first"}]
    )
    access.turns = [{"turn_id": "turn-a", "status": "error"}]
    with pytest.raises(TravelApplicationError) as conflict:
        runtime.persist_travel_conversation(
            _actor(), "travel-a", [{"role": "user", "content": "replacement"}]
        )
    assert conflict.value.code == "TRAVEL_CONVERSATION_CONFLICT"


def test_runtime_deletes_plan_and_associated_travel_session(tmp_path):
    access = _SessionAccess(channel="travel")
    runtime = _runtime(tmp_path, access)
    runtime.travel_service = SimpleNamespace(
        delete_plan=lambda actor, plan_id: "travel-a"
    )

    runtime.delete_travel_plan(_actor(), "plan-a")

    assert access.deleted == ["travel-a"]


def test_runtime_does_not_delete_a_non_travel_session_returned_by_a_bad_plan_link(tmp_path):
    access = _SessionAccess(channel="web")
    runtime = _runtime(tmp_path, access)
    runtime.travel_service = SimpleNamespace(
        delete_plan=lambda actor, plan_id: "web-a"
    )

    runtime.delete_travel_plan(_actor(), "plan-a")

    assert access.deleted == []


def _runtime(tmp_path, session_access):
    return WebRuntime(
        config=AppConfig(
            workspace=tmp_path,
            config_dir=tmp_path / "config",
            prompts_dir=tmp_path / "prompts",
            contexts_dir=tmp_path / "contexts",
            sessions_dir=tmp_path / "contexts" / "sessions",
            extends_dir=tmp_path / "extends",
            logs_dir=tmp_path / "logs",
        ),
        sessions=SimpleNamespace(),
        agent_loop=SimpleNamespace(),
        llm=SimpleNamespace(),
        auth=SimpleNamespace(store=session_access),
        session_access=session_access,
    )


def _actor():
    return ActorContext(
        actor_type="user",
        user_id="user-a",
        username="user-a",
        display_name="User A",
        role_keys=frozenset({"viewer"}),
        permission_keys=frozenset(),
        channel="rest",
    )


class _MessageStore:
    def __init__(self):
        self.messages = []
        self.metadata = {}

    def load(self, session_id):
        return SessionState(
            session_id=session_id,
            messages=list(self.messages),
            metadata=dict(self.metadata),
        )

    def append(self, session_id, messages):
        del session_id
        self.messages.extend(messages)

    def replace(self, session_id, messages):
        del session_id
        self.messages = list(messages)

    def update_metadata(self, session_id, values):
        del session_id
        self.metadata.update(values)


class _SessionAccess:
    def __init__(self, *, channel):
        self.channel = channel
        self.store = _MessageStore()
        self.refreshes = 0
        self.deleted = []
        self.turns = []
        self.owner_user_id = "user-a"

    def resolve_session(self, actor, session_id, *, write=False, delete=False):
        del actor, write, delete
        return SimpleNamespace(
            session_id=session_id,
            owner_user_id=self.owner_user_id,
            channel=self.channel,
            store=self.store,
        )

    def refresh_index(self, actor, session_id):
        del actor, session_id
        self.refreshes += 1

    def delete_session(self, actor, session_id):
        del actor
        self.deleted.append(session_id)

    def list_turn_runs(self, *, actor_user_id, session_id="", limit=1):
        del actor_user_id, session_id, limit
        return list(self.turns)

    def session_index_list(self, owner_user_id):
        if owner_user_id != "user-a":
            return []
        return [{
            "session_id": "travel-a",
            "owner_user_id": "user-a",
            "channel": self.channel,
            "title": self.store.metadata.get("title", ""),
            "preview": "重庆去大理",
            "updated_at": "2026-08-13T00:00:00Z",
        }]

    def session_index_get(self, session_id):
        if session_id != "travel-a":
            return None
        return {
            "session_id": session_id,
            "owner_user_id": self.owner_user_id,
            "channel": self.channel,
        }


def _draft():
    return {
        "intent": "travel_requirement",
        "intent_topic": "",
        "origin": "重庆",
        "destinations": ["大理"],
        "start_date": "2026-10-01",
        "end_date": "2026-10-03",
        "traveller_type": "",
        "traveller_count": 2,
        "budget_total_cny": None,
        "budget_level": "balanced",
        "transport_preferences": [],
        "stay_preferences": [],
        "interest_tags": [],
        "pace": "",
        "planning_mode": "",
        "hard_constraints": [],
    }
