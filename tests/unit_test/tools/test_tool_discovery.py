from __future__ import annotations

import json

from agent.protocols.auth import ActorContext
from agent.protocols.tool import ToolExecutionContext, ToolResult
from agent.tools.discovery import DiscoverableToolProvider


class _Provider:
    def __init__(self):
        self.calls = []

    def definitions(self):
        return [
            _definition("read_file", "Read one UTF-8 workspace file."),
            _definition("exec", "Execute a bounded non-interactive workspace command."),
            _definition("delegate_tasks", "Delegate work to a Subagent child."),
        ]

    def execute(self, name, args):
        self.calls.append((name, args))
        return ToolResult(output=f"ran:{name}")

    def execute_with_context(self, name, args, context):
        self.calls.append((name, args, context.turn_id))
        return ToolResult(output=f"context:{name}")


def test_initial_schema_only_exposes_discover_tools():
    provider = DiscoverableToolProvider(_Provider())
    assert _names(provider.definitions()) == ["discover_tools"]
    assert provider.available_tool_names == ("read_file", "exec", "delegate_tasks")


def test_explicit_initial_names_are_exposed_without_activating_unrelated_tools():
    provider = DiscoverableToolProvider(_Provider(), initial_names=("read_file", "missing"))

    assert provider.activated_tool_names == ("read_file",)
    assert _names(provider.definitions()) == ["discover_tools", "read_file"]
    result = provider.execute("exec", {"command": "echo no"})
    assert result.is_error is True
    assert result.metadata["code"] == "TOOL_NOT_ACTIVATED"


def test_discovery_activates_only_matching_schemas_for_next_step():
    provider = DiscoverableToolProvider(_Provider())
    result = provider.execute("discover_tools", {"query": "read file", "max_results": 2})
    payload = json.loads(result.output)
    assert result.is_error is False
    assert payload["status"] == "activated"
    assert payload["activated"][0]["name"] == "read_file"
    assert _names(provider.definitions()) == ["discover_tools", "read_file"]


def test_unactivated_tool_cannot_bypass_discovery():
    base = _Provider()
    provider = DiscoverableToolProvider(base)
    result = provider.execute("exec", {"command": "echo no"})
    assert result.is_error is True
    assert result.metadata["code"] == "TOOL_NOT_ACTIVATED"
    assert base.calls == []


def test_exact_name_discovery_accumulates_and_preserves_context_dispatch():
    base = _Provider()
    provider = DiscoverableToolProvider(base)
    provider.execute("discover_tools", {"query": "subagent", "names": ["delegate_tasks"]})
    provider.execute("discover_tools", {"query": "command", "names": ["exec"]})
    context = ToolExecutionContext(
        actor=ActorContext(
            actor_type="local_operator",
            user_id=None,
            username="owner",
            display_name="Owner",
            role_keys=frozenset({"owner"}),
            permission_keys=frozenset(),
            channel="cli",
        ),
        session_id="s",
        turn_id="t",
        turn_index=1,
        channel="cli",
    )
    result = provider.execute_with_context("delegate_tasks", {}, context)
    assert result.output == "context:delegate_tasks"
    assert provider.activated_tool_names == ("exec", "delegate_tasks")
    assert _names(provider.definitions()) == ["discover_tools", "exec", "delegate_tasks"]
    assert base.calls == [("delegate_tasks", {}, "t")]


def test_discovery_catalog_never_exposes_tools_absent_from_wrapped_provider():
    class _FilteredProvider(_Provider):
        def definitions(self):
            return [_definition("read_file", "Read one file.")]

    provider = DiscoverableToolProvider(_FilteredProvider())
    result = provider.execute("discover_tools", {"query": "subagent execute files"})
    payload = json.loads(result.output)
    assert [item["name"] for item in payload["activated"]] == ["read_file"]
    assert payload["activated_names"] == ["read_file"]
    assert payload["available_count"] == 1


def test_discovery_rejects_invalid_bounds_without_activating_tools():
    provider = DiscoverableToolProvider(_Provider())

    result = provider.execute("discover_tools", {"query": "files", "max_results": 9})

    assert result.is_error is True
    assert result.metadata["code"] == "INVALID_PARAM"
    assert provider.activated_tool_names == ()


def _definition(name: str, description: str):
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {"type": "object", "properties": {}},
        },
    }


def _names(definitions):
    return [item["function"]["name"] for item in definitions]
