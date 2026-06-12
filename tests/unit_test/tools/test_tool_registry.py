"""Tests for ToolRegistry."""

import pytest

from agent.protocols.tool import ToolResult
from agent.tools.registry import ToolRegistry


def test_definitions_are_openai_compatible_and_copied():
    """Definitions should not expose mutable registry internals."""

    registry = ToolRegistry([FakeTool("read_file"), FakeTool("grep")])

    definitions = registry.definitions()
    definitions[0]["function"]["parameters"]["properties"]["path"]["type"] = "number"

    fresh = registry.definitions()
    assert [definition["type"] for definition in fresh] == ["function", "function"]
    assert fresh[0]["function"]["name"] == "read_file"
    assert fresh[0]["function"]["parameters"]["properties"]["path"]["type"] == "string"


def test_duplicate_tool_names_are_rejected():
    """Tool names must be unique inside one registry."""

    with pytest.raises(ValueError, match="duplicate tool name"):
        ToolRegistry([FakeTool("read_file"), FakeTool("read_file")])


def test_invalid_tool_names_are_rejected():
    """Tool names should stay provider-compatible."""

    with pytest.raises(ValueError, match="invalid tool name"):
        ToolRegistry([FakeTool("read file")])


def test_unknown_tool_returns_structured_error():
    """Unknown tool calls should be returned to the model as tool errors."""

    result = ToolRegistry([FakeTool("read_file")]).execute("missing", {})

    assert result.is_error is True
    assert result.metadata["code"] == "UNKNOWN_TOOL"
    assert "missing" in result.output


def test_non_object_arguments_return_structured_error():
    """Tool arguments must already be decoded as a JSON object."""

    result = ToolRegistry([FakeTool("read_file")]).execute("read_file", "bad")  # type: ignore[arg-type]

    assert result.is_error is True
    assert result.metadata["code"] == "INVALID_PARAM"


def test_execute_dispatches_registered_tool():
    """A registered tool should receive decoded arguments."""

    tool = FakeTool("read_file")
    result = ToolRegistry([tool]).execute("read_file", {"path": "README.md"})

    assert result.output == "read_file:{'path': 'README.md'}"
    assert tool.calls == [{"path": "README.md"}]


class FakeTool:
    description = "fake tool"
    parameters = {
        "type": "object",
        "properties": {"path": {"type": "string"}},
        "required": ["path"],
    }

    def __init__(self, name: str):
        self.name = name
        self.calls = []

    def execute(self, args):
        self.calls.append(args)
        return ToolResult(output=f"{self.name}:{args}")
