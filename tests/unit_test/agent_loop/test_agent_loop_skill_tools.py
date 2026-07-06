"""AgentLoop coverage for a complete local Skill tool chain."""

import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from agent.message import Message
from agent.protocols.llm import LLMResponse
from agent.protocols.session import SessionState
from agent.skills.loader import SkillLoader
from agent.tools import create_default_tool_registry


def test_agent_loop_can_load_skill_then_exec_script(tmp_path):
    """The generic tool loop should load Skill docs, then execute scripts through exec."""

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
                        "exec",
                        {
                            "command": _skill_script_command({"value": 7}),
                            "timeout_seconds": 10,
                        },
                    )
                ],
            ),
            LLMResponse(content="skill done"),
        ]
    )

    loop = _make_loop(workspace, llm=llm, tools=tools, sessions=sessions)
    result = loop.run_turn("default", "use demo")

    assert result == "skill done"
    assert [message.role for message in sessions.appended["default"]] == [
        "user",
        "assistant",
        "tool",
        "assistant",
        "tool",
        "assistant",
    ]
    load_payload = json.loads(sessions.appended["default"][2].content)
    run_payload = json.loads(sessions.appended["default"][4].content)
    assert load_payload["status"] == "success"
    assert "skill: official/demo" in load_payload["output"]
    assert run_payload["status"] == "success"
    assert '"data": {"value": 7}' in run_payload["output"]


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
print(json.dumps({
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


def _skill_script_command(params: dict[str, Any]) -> str:
    params_json = json.dumps(params, separators=(",", ":"))
    escaped_params = params_json.replace('"', r'\"')
    return f'"{sys.executable}" skills/demo/scripts/main.py --params "{escaped_params}"'


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

