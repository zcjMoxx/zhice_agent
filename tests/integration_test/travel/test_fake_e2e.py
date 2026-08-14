from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from agent.app.gateway import create_app
from agent.app.runtime import ChatTurnResult
from agent.applications.travel.config import TravelConfig
from agent.applications.travel.service import TravelApplicationService
from agent.auth.user_context import FilesystemUserContextResolver
from agent.core.context import ContextBuilder
from agent.core.loop import AgentLoop
from agent.mcp.runtime import McpRuntime
from agent.message import Message
from agent.prompt_loader import PromptLoader
from agent.protocols.auth import ActorContext
from agent.protocols.llm import LLMResponse
from agent.protocols.mcp import McpServerSpec
from agent.protocols.session import SessionState
from agent.skills.loader import SkillLoader
from agent.tools import UserScopedToolProvider, with_tool_discovery
from tests.unit_test.travel.fixtures import plan_payload
from tests.unit_test.travel.test_optimizer import _params

pytestmark = pytest.mark.integration


def test_web_chat_to_agentloop_fake_mcp_skill_store_and_plan_ready(tmp_path):
    prompts = tmp_path / "prompts"
    prompts.mkdir()
    for source in Path("prompts").glob("*.md"):
        prompts.joinpath(source.name).write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
    actor = _actor()
    resolver = FilesystemUserContextResolver(tmp_path / "contexts", workspace_dir=tmp_path)
    service = TravelApplicationService(TravelConfig(enabled=True), resolver)
    skills = SkillLoader([("official", Path("skill_repo/skills").resolve())])
    mcp = McpRuntime(
        [
            McpServerSpec(
                server_id="travel-fixtures",
                transport="stdio",
                command=sys.executable,
                args=(str(Path(__file__).with_name("fake_travel_mcp.py").resolve()),),
                startup_timeout_seconds=10,
                connect_timeout_seconds=10,
                call_timeout_seconds=10,
            )
        ],
        workspace=tmp_path,
    )
    files_dir = resolver.resolve(actor.user_id).files_dir
    tools = UserScopedToolProvider(
        files_dir=files_dir,
        shared_readonly_dir=resolver.shared_readonly_dir,
        actor=actor,
        skills=skills,
        extra_tools=[*service.tools_for_actor(actor), *mcp.tools_for_actor(actor, files_dir)],
    )
    names = [
        "load_skills",
        "mcp__travel-fixtures__amap_route",
        "mcp__travel-fixtures__tavily_search",
        "mcp__travel-fixtures__train_query",
        "mcp__travel-fixtures__forecast",
        "mcp__travel-fixtures__xhs_search",
        "run_skill",
        "finalize_travel_plan",
    ]
    llm = ScriptedLLM(
        [
            _response("discover_tools", {"query": "travel", "names": names, "max_results": 8}),
            LLMResponse(
                content="",
                tool_calls=[
                    _call("load", "load_skills", {"name": "official/travel-planner"}),
                    _call("map", names[1], {"origin": "大理站", "destination": "大理古城"}),
                    _call("web", names[2], {"query": "大理 国庆 避坑"}),
                    _call("train", names[3], {"origin": "重庆", "destination": "大理", "travel_date": "2026-10-01"}),
                    _call("weather", names[4], {"city": "大理"}),
                    _call("xhs", names[5], {"keyword": "大理 国庆"}),
                ],
            ),
            _response("run_skill", {"skill": "official/travel-planner", "params": _params()}),
            _response("finalize_travel_plan", {"plan": plan_payload()}),
            LLMResponse(content="计划已保存，请打开旅行专属页面。"),
        ]
    )
    sessions = Sessions()
    loop = AgentLoop(
        llm=llm,
        sessions=sessions,
        context_builder=ContextBuilder(
            PromptLoader(prompts),
            skills=skills,
            extra_system_prompts=("travel_planning",),
        ),
        workspace=files_dir,
        tools=with_tool_discovery(tools),
    )
    runtime = E2ERuntime(loop, actor, service)
    static = tmp_path / "static"
    static.mkdir()
    static.joinpath("index.html").write_text("travel", encoding="utf-8")
    try:
        with TestClient(create_app(config=_config(tmp_path, prompts), runtime=runtime, static_dir=static)) as client:
            response = client.post("/api/chat", json={"session_id": "travel-e2e", "message": "国庆重庆到大理两人两天"})
        assert response.status_code == 200
        assert response.json()["assistant"]["content"].startswith("计划已保存")
        summaries = service.list_plans(actor)
        assert len(summaries) == 1
        stored = service.get_plan(actor, summaries[0].plan_id)
        assert stored.data["request"]["origin"] == "重庆"
        assert any(event.get("type") == "travel.plan_ready" for event in runtime.events)
    finally:
        mcp.close()


class ScriptedLLM:
    def __init__(self, responses):
        self.responses = list(responses)

    def chat(self, messages, tools=None):
        del messages, tools
        return self.responses.pop(0)


@dataclass
class Sessions:
    states: dict[str, SessionState] = field(default_factory=dict)

    def load(self, session_id):
        return self.states.get(session_id, SessionState(session_id=session_id, messages=[]))

    def append(self, session_id, messages: list[Message]):
        state = self.load(session_id)
        self.states[session_id] = SessionState(session_id=session_id, messages=[*state.messages, *messages])


class E2ERuntime:
    def __init__(self, loop, actor, service):
        self.loop = loop
        self.actor = actor
        self.travel_service = service
        self.auth = None
        self.events = []

    def startup(self):
        return None

    def shutdown(self):
        return None

    def capability_statuses(self):
        return {}

    def current_model_label(self):
        return "fake/travel"

    def run_chat_events(self, actor, session_id, message, *, turn_id=None, on_event=None, **kwargs):
        del actor, kwargs

        def capture(event):
            self.events.append(event)
            if on_event is not None:
                on_event(event)

        content = self.loop.run_turn(
            session_id,
            message,
            turn_id=turn_id,
            actor=self.actor,
            on_event=capture,
        )
        return ChatTurnResult(content=content, turn_id=turn_id or "")


def _actor():
    return ActorContext(
        actor_type="user",
        user_id="user-e2e",
        username="traveller",
        display_name="Traveller",
        role_keys=frozenset({"viewer"}),
        permission_keys=frozenset(),
        channel="web",
    )


def _call(call_id, name, args):
    return {"id": call_id, "type": "function", "function": {"name": name, "arguments": json.dumps(args, ensure_ascii=False)}}


def _response(name, args):
    return LLMResponse(content="", tool_calls=[_call(f"call-{name}", name, args)])


def _config(tmp_path, prompts):
    from agent.config import AppConfig

    return AppConfig(
        workspace=tmp_path,
        config_dir=tmp_path / "config",
        prompts_dir=prompts,
        contexts_dir=tmp_path / "contexts",
        sessions_dir=tmp_path / "contexts" / "sessions",
        extends_dir=tmp_path / "extends",
        logs_dir=tmp_path / "logs",
    )

