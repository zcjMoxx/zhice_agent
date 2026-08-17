from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from agent.app.runtime import (
    WebRuntime,
    _persisted_finalization_child_ids,
    _persisted_finalization_completed_categories,
    _travel_finalization_repair_profiles,
    _travel_forecast_window_context,
)
from agent.applications.travel.config import TravelConfig
from agent.applications.travel.service import TravelApplicationService
from agent.applications.travel.subagents import (
    TRAVEL_FINAL_ROUTE_PROFILE,
    TRAVEL_FINAL_STAY_PROFILE,
    TRAVEL_FINAL_WEATHER_PROFILE,
)
from agent.auth.user_context import FilesystemUserContextResolver
from agent.config import AppConfig
from agent.message import Message
from agent.prompt_loader import PromptLoader
from agent.protocols.auth import ActorContext
from agent.protocols.tool import ToolResult
from agent.session import JsonlSessionStore
from agent.tools.base import BaseTool


def test_persisted_child_phase_mapping_uses_delegate_profiles_and_result_ids():
    call_id = "call-final"
    messages = [
        Message(
            role="assistant",
            content="",
            tool_calls=[{
                "id": call_id,
                "type": "function",
                "function": {
                    "name": "delegate_tasks",
                    "arguments": json.dumps({
                        "tasks": [
                            {"id": "stay", "profile": "travel-final-stay"},
                            {"id": "route", "profile": "travel-final-route"},
                            {"id": "weather", "profile": "travel-final-weather"},
                        ]
                    }),
                },
            }],
        ),
        Message(
            role="tool",
            content=json.dumps({
                "status": "success",
                "output": json.dumps({
                    "results": [
                        {"id": "stay", "child_session_id": "child-final-stay"},
                        {"id": "route", "child_session_id": "child-final-route"},
                        {"id": "weather", "child_session_id": "child-final-weather"},
                    ]
                }),
            }),
            name="delegate_tasks",
            tool_call_id=call_id,
        ),
    ]

    assert _persisted_finalization_child_ids(messages) == {
        "child-final-stay",
        "child-final-route",
        "child-final-weather",
    }


def test_persisted_finalization_recovers_only_completed_ok_lanes():
    call_id = "call-final"
    messages = [
        Message(
            role="assistant",
            content="",
            tool_calls=[{
                "id": call_id,
                "type": "function",
                "function": {
                    "name": "delegate_tasks",
                    "arguments": json.dumps({
                        "tasks": [
                            {"id": "stay", "profile": TRAVEL_FINAL_STAY_PROFILE},
                            {"id": "route", "profile": TRAVEL_FINAL_ROUTE_PROFILE},
                            {"id": "weather", "profile": TRAVEL_FINAL_WEATHER_PROFILE},
                        ]
                    }),
                },
            }],
        ),
        Message(
            role="tool",
            content=json.dumps({
                "status": "success",
                "output": json.dumps({
                    "status": "partial",
                    "results": [
                        {"id": "stay", "status": "completed", "code": "OK"},
                        {"id": "route", "status": "failed", "code": "TIMEOUT"},
                        {"id": "weather", "status": "completed", "code": "OK"},
                    ],
                }),
            }),
            name="delegate_tasks",
            tool_call_id=call_id,
        ),
    ]

    assert _persisted_finalization_completed_categories(messages) == frozenset(
        {"lodging", "weather"}
    )


def test_finalizer_forecast_error_routes_to_one_weather_repair_profile():
    messages = [
        Message(
            role="tool",
            content="forecast required",
            name="finalize_travel_plan",
            metadata={"is_error": True, "code": "TRAVEL_WEATHER_FORECAST_REQUIRED"},
        )
    ]

    assert _travel_finalization_repair_profiles(
        messages,
        SimpleNamespace(forecast_successful=False),
    ) == frozenset({TRAVEL_FINAL_WEATHER_PROFILE})
    assert _travel_finalization_repair_profiles(
        messages,
        SimpleNamespace(forecast_successful=True),
    ) == frozenset()

    route_messages = [
        Message(
            role="tool",
            content="route required",
            name="finalize_travel_plan",
            metadata={"is_error": True, "code": "TRAVEL_ROUTE_EVIDENCE_MISSING"},
        )
    ]
    assert _travel_finalization_repair_profiles(
        route_messages,
        SimpleNamespace(route_repair_attempted=False),
    ) == frozenset({TRAVEL_FINAL_ROUTE_PROFILE})


def test_server_weather_window_marks_near_dates_as_forecast_required():
    today = datetime.now(ZoneInfo("Asia/Shanghai")).date()
    payload = json.loads(
        _travel_forecast_window_context(
            {
                "destinations": ["郑州"],
                "start_date": (today + timedelta(days=2)).isoformat(),
                "end_date": (today + timedelta(days=7)).isoformat(),
            }
        )
    )

    assert payload["inside_forecast_window"] is True
    assert payload["required_weather_tool"] == "get_forecast"
    assert payload["destination"] == "郑州"


def test_runtime_uses_same_llm_and_only_intake_tools_before_confirmation(tmp_path):
    resolver = FilesystemUserContextResolver(tmp_path / "contexts", workspace_dir=tmp_path)
    context = resolver.resolve("user-a")
    sessions = JsonlSessionStore(context.sessions_dir)
    sessions.update_metadata(
        "travel-a",
        {"travel_phase": "intake", "travel_draft": _complete_draft()},
    )
    access = _SessionAccess(context, sessions)
    loop = _CapturingLoop()
    llm = object()
    mcp = _McpRuntime()
    runtime = WebRuntime(
        config=_config(tmp_path),
        sessions=sessions,
        agent_loop=loop,
        llm=llm,
        auth=SimpleNamespace(store=None),
        session_access=access,
        prompt_loader=PromptLoader(_project_prompts()),
        travel_service=TravelApplicationService(TravelConfig(enabled=True), resolver),
        mcp_runtime=mcp,
    )

    result = runtime.run_chat_events(_actor(), "travel-a", "你是谁")

    assert result.content == "自然接待回复"
    assert loop.tool_names == [
        "update_travel_draft",
        "offer_main_chat_handoff",
        "confirm_and_start_travel_planning",
    ]
    assert loop.llm_override is llm
    assert "智策旅行助手接待规则" in loop.system_prompt_addendum
    assert "服务端当前旅行草稿" in loop.system_prompt_addendum
    assert '"origin":"重庆"' in loop.system_prompt_addendum
    assert "智能旅行规划规则" not in loop.system_prompt_addendum
    assert mcp.calls == 0


def test_runtime_uses_fast_candidate_model_and_opens_planning_tools_after_confirmation(
    tmp_path,
    monkeypatch,
):
    resolver = FilesystemUserContextResolver(tmp_path / "contexts", workspace_dir=tmp_path)
    context = resolver.resolve("user-a")
    sessions = JsonlSessionStore(context.sessions_dir)
    draft = _complete_draft()
    sessions.update_metadata(
        "travel-a",
        {"travel_phase": "planning", "travel_draft": draft},
    )
    access = _SessionAccess(context, sessions)
    loop = _CapturingLoop()
    mcp = _McpRuntime()
    main_llm = object()
    candidate_llm = object()
    monkeypatch.setattr(
        "agent.app.runtime.create_optional_aliased_llm_provider",
        lambda config_dir, alias: candidate_llm if alias == "compaction" else None,
    )
    runtime = WebRuntime(
        config=_config(tmp_path),
        sessions=sessions,
        agent_loop=loop,
        llm=main_llm,
        auth=SimpleNamespace(store=None),
        session_access=access,
        prompt_loader=PromptLoader(_project_prompts()),
        travel_service=TravelApplicationService(TravelConfig(enabled=True), resolver),
        mcp_runtime=mcp,
    )

    runtime.run_chat_events(_actor(), "travel-a", "我已确认，请开始规划")

    assert "finalize_travel_plan" in loop.tool_names
    assert "search_travel_hotels" in loop.tool_names
    assert "mcp__amap__maps_text_search" in loop.tool_names
    assert "update_travel_draft" not in loop.tool_names
    assert "智能旅行规划规则" in loop.system_prompt_addendum
    assert "服务端已确认的旅行草稿" in loop.system_prompt_addendum
    assert '"origin":"重庆"' in loop.system_prompt_addendum
    assert loop.llm_override is candidate_llm
    assert mcp.calls == 1


def test_runtime_keeps_main_model_for_selected_candidate_finalization(tmp_path, monkeypatch):
    resolver = FilesystemUserContextResolver(tmp_path / "contexts", workspace_dir=tmp_path)
    context = resolver.resolve("user-a")
    sessions = JsonlSessionStore(context.sessions_dir)
    sessions.update_metadata(
        "travel-a",
        {"travel_phase": "planning", "travel_draft": _complete_draft()},
    )
    access = _SessionAccess(context, sessions)
    loop = _CapturingLoop()
    mcp = _McpRuntime()
    main_llm = object()
    service = TravelApplicationService(TravelConfig(enabled=True), resolver)
    service.save_candidate_review(
        _actor(),
        session_id="travel-a",
        turn_id="turn-candidates",
        candidates=[{"candidate_id": "candidate-a"}, {"candidate_id": "candidate-b"}],
        recommended_candidate_id="candidate-a",
    )
    service.select_candidate(_actor(), "travel-a", "candidate-a")
    monkeypatch.setattr(
        "agent.app.runtime.create_optional_aliased_llm_provider",
        lambda config_dir, alias: None,
    )
    runtime = WebRuntime(
        config=_config(tmp_path),
        sessions=sessions,
        agent_loop=loop,
        llm=main_llm,
        auth=SimpleNamespace(store=None),
        session_access=access,
        prompt_loader=PromptLoader(_project_prompts()),
        travel_service=service,
        mcp_runtime=mcp,
    )

    runtime.run_chat_events(_actor(), "travel-a", "继续生成我选择的方案")

    assert loop.llm_override is main_llm


def test_intake_setup_failure_does_not_leave_a_phantom_active_turn(tmp_path):
    resolver = FilesystemUserContextResolver(tmp_path / "contexts", workspace_dir=tmp_path)
    context = resolver.resolve("user-a")
    sessions = JsonlSessionStore(context.sessions_dir)
    sessions.update_metadata("travel-a", {"travel_phase": "intake"})
    runtime = WebRuntime(
        config=_config(tmp_path),
        sessions=sessions,
        agent_loop=_CapturingLoop(),
        llm=object(),
        auth=SimpleNamespace(store=None),
        session_access=_SessionAccess(context, sessions),
        prompt_loader=PromptLoader(tmp_path / "missing-prompts"),
        travel_service=TravelApplicationService(TravelConfig(enabled=True), resolver),
        mcp_runtime=_McpRuntime(),
    )

    with pytest.raises(FileNotFoundError):
        runtime.run_chat_events(_actor(), "travel-a", "你是谁")

    assert runtime._active_turns == {}


class _CapturingLoop:
    hook_runtime = None

    def __init__(self):
        self.tool_names = []
        self.llm_override = None
        self.system_prompt_addendum = ""

    def run_turn(self, session_id, message, **kwargs):
        del session_id, message
        provider = kwargs["tools_override"]
        self.tool_names = [item["function"]["name"] for item in provider.definitions()]
        self.llm_override = kwargs["llm_override"]
        self.system_prompt_addendum = kwargs["system_prompt_addendum"]
        return "自然接待回复"


class _ReadOnlyMapTool(BaseTool):
    name = "mcp__amap__maps_text_search"
    description = "read-only map search"
    parameters = {"type": "object", "properties": {}}

    def _execute(self, args):
        del args
        return ToolResult(output="{}")


class _McpRuntime:
    def __init__(self):
        self.calls = 0

    def tools_for_actor(self, actor, workspace, **kwargs):
        del actor, kwargs
        self.calls += 1
        return [_ReadOnlyMapTool(workspace)]


class _SessionAccess:
    def __init__(self, context, store):
        self.context = context
        self.store = store

    def assert_chat_continuation_allowed(self, actor, session_id, request_channel):
        del actor, session_id, request_channel

    def ensure_session(self, actor, session_id, **kwargs):
        del actor, kwargs
        return SimpleNamespace(
            created=False,
            channel="travel",
            owner_user_id="user-a",
            context=self.context,
            store=self.store,
            model_context=lambda: None,
        )

    def resolve_session(self, actor, session_id, **kwargs):
        return self.ensure_session(actor, session_id, **kwargs)

    def refresh_index(self, actor, session_id):
        del actor, session_id


def _actor():
    return ActorContext(
        actor_type="user",
        user_id="user-a",
        username="user-a",
        display_name="User A",
        role_keys=frozenset({"viewer"}),
        permission_keys=frozenset(),
        channel="web",
    )


def _config(tmp_path):
    return AppConfig(
        workspace=tmp_path,
        config_dir=tmp_path / "config",
        prompts_dir=_project_prompts(),
        contexts_dir=tmp_path / "contexts",
        sessions_dir=tmp_path / "contexts" / "sessions",
        extends_dir=tmp_path / "extends",
        logs_dir=tmp_path / "logs",
    )


def _project_prompts():
    return Path(__file__).resolve().parents[3] / "prompts"


def _complete_draft():
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
        "budget_level": "",
        "transport_preferences": [],
        "stay_preferences": [],
        "interest_tags": [],
        "pace": "",
        "planning_mode": "",
        "hard_constraints": [],
    }
