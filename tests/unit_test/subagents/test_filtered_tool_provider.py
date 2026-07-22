from copy import deepcopy

import pytest

from agent.protocols.auth import ActorContext
from agent.protocols.tool import ToolExecutionContext, ToolResult
from agent.tools.filtered import FilteredToolProvider


class FakeProvider:
    def __init__(self):
        self.schemas = [
            _definition("read_file"),
            _definition("exec"),
            _definition("delegate_tasks"),
            _definition("mcp__github__search"),
            _definition("mcp__mail__send"),
        ]
        self.calls = []

    def definitions(self):
        return deepcopy(self.schemas)

    def execute(self, name, args):
        self.calls.append((name, args))
        return ToolResult(output=f"ran:{name}")


class ContextualFakeProvider(FakeProvider):
    def execute_with_context(self, name, args, context):
        self.calls.append((name, args, context))
        return ToolResult(output=f"context:{context.task_id}")


def _definition(name):
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": name,
            "parameters": {"type": "object"},
        },
    }


def _context():
    return ToolExecutionContext(
        actor=ActorContext(
            actor_type="user",
            user_id="user-1",
            username="owner",
            display_name="Owner",
            role_keys=frozenset({"owner"}),
            permission_keys=frozenset(),
            channel="web",
        ),
        session_id="child-session",
        turn_id="child-turn",
        turn_index=1,
        channel="web",
        source="subagent",
        root_session_id="root-session",
        root_turn_id="root-turn",
        parent_session_id="parent-session",
        parent_turn_id="parent-turn",
        subagent_id="subagent-1",
        task_id="task-1",
    )


def test_filters_schema_by_exact_and_mcp_server_pattern():
    parent = FakeProvider()
    filtered = FilteredToolProvider(
        parent,
        allowed_tools=["read_file", "exec", "mcp__github__*"],
        denied_tools=["exec"],
    )

    definitions = filtered.definitions()
    names = [item["function"]["name"] for item in definitions]

    assert names == ["read_file", "mcp__github__search"]
    assert filtered.effective_tool_names == ("read_file", "mcp__github__search")
    definitions[0]["function"]["description"] = "changed"
    assert parent.schemas[0]["function"]["description"] == "read_file"


def test_dispatch_rechecks_filter_and_never_calls_parent_for_forged_tool():
    parent = FakeProvider()
    filtered = FilteredToolProvider(
        parent,
        allowed_tools=["read_file", "delegate_tasks", "mcp__github__*"],
    )

    hidden = filtered.execute("exec", {"command": "whoami"})
    kernel_denied = filtered.execute("delegate_tasks", {})
    other_server = filtered.execute("mcp__mail__send", {})
    allowed = filtered.execute("read_file", {"path": "README.md"})

    assert hidden.metadata["code"] == "SUBAGENT_TOOL_NOT_ALLOWED"
    assert kernel_denied.metadata["code"] == "SUBAGENT_TOOL_NOT_ALLOWED"
    assert other_server.metadata["code"] == "SUBAGENT_TOOL_NOT_ALLOWED"
    assert allowed.output == "ran:read_file"
    assert parent.calls == [("read_file", {"path": "README.md"})]


def test_contextual_dispatch_preserves_trusted_child_context():
    parent = ContextualFakeProvider()
    filtered = FilteredToolProvider(parent, allowed_tools=["read_file"])
    context = _context()

    result = filtered.execute_with_context("read_file", {"path": "."}, context)

    assert result.output == "context:task-1"
    assert parent.calls == [("read_file", {"path": "."}, context)]


def test_contextual_dispatch_falls_back_for_legacy_provider():
    parent = FakeProvider()
    filtered = FilteredToolProvider(parent, allowed_tools=["read_file"])

    result = filtered.execute_with_context("read_file", {}, _context())

    assert result.output == "ran:read_file"


@pytest.mark.parametrize("pattern", ["*", "mcp__*", "mcp__github__search*", "read.*"])
def test_rejects_arbitrary_tool_patterns(pattern):
    with pytest.raises(ValueError, match="invalid tool pattern"):
        FilteredToolProvider(FakeProvider(), allowed_tools=[pattern])
