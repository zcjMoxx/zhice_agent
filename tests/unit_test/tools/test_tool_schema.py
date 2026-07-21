"""Tests for core Tool argument schema validation."""

from __future__ import annotations

import pytest

from agent.tools.schema import validate_tool_arguments


def test_tool_schema_accepts_valid_arguments():
    assert validate_tool_arguments(_Tools(_simple_schema()), "read_file", {"path": "a.txt"}) is None


def test_tool_schema_rejects_missing_unknown_and_wrong_type_arguments():
    tools = _Tools(_simple_schema())
    missing = validate_tool_arguments(tools, "read_file", {})
    unknown = validate_tool_arguments(tools, "read_file", {"path": "a", "extra": True})
    wrong_type = validate_tool_arguments(tools, "read_file", {"path": 3})

    assert _is_invalid_param(missing)
    assert _is_invalid_param(unknown)
    assert _is_invalid_param(wrong_type)


def test_tool_schema_resolves_local_ref_and_defs_fail_closed():
    schema = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$defs": {"path": {"type": "string", "minLength": 1}},
        "type": "object",
        "properties": {"path": {"$ref": "#/$defs/path"}},
        "required": ["path"],
        "additionalProperties": False,
    }
    tools = _Tools(schema)

    assert validate_tool_arguments(tools, "read_file", {"path": "a.txt"}) is None
    assert _is_invalid_param(validate_tool_arguments(tools, "read_file", {"path": 3}))
    assert _is_invalid_param(validate_tool_arguments(tools, "read_file", {}))
    assert _is_invalid_param(
        validate_tool_arguments(tools, "read_file", {"unexpected": True})
    )


@pytest.mark.parametrize(
    "schema",
    [
        {"$ref": "#/$defs/missing", "$defs": {}},
        {"$ref": "https://example.invalid/tool-schema.json"},
        {"$dynamicRef": "https://example.invalid/tool-schema.json"},
        {"$schema": "https://example.invalid/unknown-dialect", "type": "object"},
        {"type": 42},
    ],
)
def test_tool_schema_rejects_unresolved_external_and_invalid_schemas(schema):
    result = validate_tool_arguments(_Tools(schema), "read_file", {"path": "a.txt"})

    assert _is_invalid_param(result)


def _simple_schema() -> dict[str, object]:
    return {
        "type": "object",
        "properties": {"path": {"type": "string"}},
        "required": ["path"],
        "additionalProperties": False,
    }


def _is_invalid_param(result) -> bool:
    return result is not None and result.is_error and result.metadata["code"] == "INVALID_PARAM"


class _Tools:
    def __init__(self, schema):
        self.schema = schema

    def definitions(self):
        return [
            {
                "type": "function",
                "function": {
                    "name": "read_file",
                    "description": "read",
                    "parameters": self.schema,
                },
            }
        ]

    def execute(self, name, args):
        raise AssertionError("schema tests do not execute tools")
