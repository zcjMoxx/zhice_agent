"""Fail-closed JSON Schema validation for public Tool call arguments."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from jsonschema import exceptions, validators

from agent.protocols.tool import ToolProvider, ToolResult


class _ToolSchemaValidationError(ValueError):
    """Safe validation failure that never includes argument values or schemas."""


def validate_tool_arguments(
    tools: ToolProvider | None,
    name: str,
    arguments: dict[str, Any],
) -> ToolResult | None:
    """Return a structured error when one Tool call violates its public schema."""

    if tools is None:
        return ToolResult(
            output="Tool provider is not configured.",
            is_error=True,
            metadata={"code": "TOOLS_UNAVAILABLE", "tool_name": name},
        )
    definition = _find_definition(tools.definitions(), name)
    if definition is None:
        return ToolResult(
            output=f"Unknown tool: {name}",
            is_error=True,
            metadata={"code": "UNKNOWN_TOOL", "tool_name": name},
        )
    schema = definition.get("function", {}).get("parameters", {})
    try:
        _validate(arguments, schema)
    except _ToolSchemaValidationError as exc:
        return ToolResult(
            output=str(exc),
            is_error=True,
            metadata={"code": "INVALID_PARAM", "tool_name": name},
        )
    return None


def _find_definition(definitions: list[dict[str, Any]], name: str) -> dict[str, Any] | None:
    for definition in definitions:
        function = definition.get("function")
        if isinstance(function, dict) and function.get("name") == name:
            return definition
    return None


def _validate(arguments: dict[str, Any], schema: Any) -> None:
    if not isinstance(schema, dict):
        raise _ToolSchemaValidationError("Tool schema is invalid or unsupported.")
    try:
        _reject_non_local_references(schema)
        validator_type = (
            validators.validator_for(schema, default=None)
            if "$schema" in schema
            else validators.validator_for(schema)
        )
        if validator_type is None:
            raise _ToolSchemaValidationError("Tool schema dialect is not supported.")
        validator_type.check_schema(schema)
        error = next(iter(validator_type(schema).iter_errors(arguments)), None)
    except _ToolSchemaValidationError:
        raise
    except exceptions.SchemaError as exc:
        raise _ToolSchemaValidationError("Tool schema is invalid or unsupported.") from exc
    except Exception as exc:
        # Resolution and validator selection must fail closed. The public error
        # intentionally omits all schema and argument contents.
        raise _ToolSchemaValidationError(
            "Tool schema reference cannot be resolved safely."
        ) from exc
    if error is not None:
        path = _validation_path(error.absolute_path)
        raise _ToolSchemaValidationError(
            f"Invalid tool parameter at {path} ({error.validator})."
        )


def _reject_non_local_references(value: Any) -> None:
    if isinstance(value, dict):
        for keyword in ("$ref", "$dynamicRef", "$recursiveRef"):
            reference = value.get(keyword)
            if reference is not None and (
                not isinstance(reference, str) or not reference.startswith("#")
            ):
                raise _ToolSchemaValidationError(
                    "External Tool schema references are not allowed."
                )
        for child in value.values():
            _reject_non_local_references(child)
    elif isinstance(value, list):
        for child in value:
            _reject_non_local_references(child)


def _validation_path(parts: Iterable[Any]) -> str:
    path = "arguments"
    for part in parts:
        path += f"[{part}]" if isinstance(part, int) else f".{part}"
    return path
