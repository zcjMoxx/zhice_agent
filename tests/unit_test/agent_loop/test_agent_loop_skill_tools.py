"""AgentLoop coverage for a complete local Skill tool chain."""

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from agent.app.auth import local_operator_actor
from agent.message import Message
from agent.protocols.llm import LLMResponse
from agent.protocols.session import SessionState
from agent.skills.loader import SkillLoader
from agent.tools import create_default_tool_registry


def test_agent_loop_can_load_then_formally_run_skill(tmp_path):
    """The generic loop should preserve Skill Runtime events and final Tool facts."""

    workspace, loader = _make_skill(tmp_path)
    tools = create_default_tool_registry(workspace, skills=loader)
    sessions = InMemorySessionStore()
    llm = ScriptedLLM(
        [
            LLMResponse(
                content="",
                tool_calls=[_tool_call("call_load", "load_skills", {"name": "official/demo"})],
            ),
            LLMResponse(
                content="",
                tool_calls=[
                    _tool_call(
                        "call_run",
                        "run_skill",
                        {
                            "skill": "official/demo",
                            "params": {"value": 7},
                        },
                    )
                ],
            ),
            LLMResponse(content="skill done"),
        ]
    )

    loop = _make_loop(workspace, llm=llm, tools=tools, sessions=sessions)
    events = []
    result = loop.run_turn(
        "default",
        "use demo",
        actor=local_operator_actor(channel="web"),
        on_event=events.append,
    )

    assert result == "skill done"
    assert [message.role for message in sessions.appended["default"]] == [
        "user",
        "assistant",
        "tool",
        "assistant",
        "tool",
        "assistant",
    ]
    assert {message.turn_index for message in sessions.appended["default"]} == {1}
    turn_ids = {message.turn_id for message in sessions.appended["default"]}
    assert None not in turn_ids
    assert len(turn_ids) == 1
    load_payload = json.loads(sessions.appended["default"][2].content)
    run_payload = json.loads(sessions.appended["default"][4].content)
    assert load_payload["status"] == "success"
    assert "skill: official/demo" in load_payload["output"]
    assert run_payload["status"] == "success"
    assert json.loads(run_payload["output"])["data"] == {"value": 7}
    runtime_events = [event for event in events if event.get("protocol_version") == 1]
    skill_events = [event for event in runtime_events if event["type"].startswith("skill.")]
    assert [event["type"] for event in skill_events] == [
        "skill.started",
        "skill.progress",
        "skill.completed",
    ]
    assert skill_events[0]["parent_event_id"]
    assert all(event["skill_run_id"] == skill_events[0]["skill_run_id"] for event in skill_events)
    assert sessions.appended["default"][4].metadata["skill_run_id"] == skill_events[0][
        "skill_run_id"
    ]
    assert len(sessions.appended["default"]) == 6


def _make_loop(workspace, *, llm, tools, sessions):
    from agent.core.loop import AgentLoop

    return AgentLoop(
        llm=llm,
        sessions=sessions,
        context_builder=FakeContextBuilder(),
        workspace=workspace,
        tools=tools,
    )


def _tool_call(call_id: str, name: str, args: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": call_id,
        "type": "function",
        "function": {"name": name, "arguments": json.dumps(args)},
    }


def _make_skill(tmp_path):
    workspace = tmp_path / "workspace"
    scripts_dir = workspace / "skills" / "demo" / "scripts"
    scripts_dir.mkdir(parents=True)
    scripts_dir.parent.joinpath("SKILL.md").write_text(
        """---
name: demo
description: Demo skill.
runtime:
  type: python
  entrypoint: scripts/main.py
  protocol: ndjson-v1
  timeout_seconds: 10
---

Demo body.
""",
        encoding="utf-8",
    )
    scripts_dir.joinpath("main.py").write_text(
        """import argparse, json
parser = argparse.ArgumentParser()
parser.add_argument("--params", required=True)
params = json.loads(parser.parse_args().params)
print(json.dumps({"type": "progress", "message": "working", "percent": 50}))
print(json.dumps({
    "type": "result",
    "status": "success",
    "code": "OK",
    "data": params,
    "message": "done",
    "error_stack": ""
}))
""",
        encoding="utf-8",
    )
    return workspace, SkillLoader([("official", workspace / "skills")])


@dataclass
class InMemorySessionStore:
    states: dict[str, SessionState] = field(default_factory=dict)
    appended: dict[str, list[Message]] = field(default_factory=dict)

    def load(self, session_id: str) -> SessionState:
        return self.states.get(session_id, SessionState(session_id=session_id, messages=[]))

    def append(self, session_id: str, messages: list[Message]) -> None:
        self.appended.setdefault(session_id, []).extend(messages)


class FakeContextBuilder:
    def build(
        self,
        history: list[Message],
        user_message: Message,
        workspace: Path,
        session_id: str,
    ) -> list[dict[str, Any]]:
        return [{"role": user_message.role, "content": user_message.content}]


class ScriptedLLM:
    def __init__(self, responses):
        self.responses = list(responses)

    def chat(self, messages, tools=None):
        response = self.responses.pop(0)
        return response

