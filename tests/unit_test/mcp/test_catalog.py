from types import SimpleNamespace

from agent.mcp.catalog import build_tool_descriptors


def test_catalog_accepts_valid_object_schema_and_preserves_annotations():
    tool = SimpleNamespace(
        name="send mail",
        description="Send one message",
        inputSchema={"type": "object", "properties": {"to": {"type": "string"}}},
        annotations=SimpleNamespace(model_dump=lambda **_: {"readOnlyHint": False}),
    )

    descriptors, errors = build_tool_descriptors("mail", [tool])

    assert not errors
    assert descriptors[0].local_name == "mcp__mail__send_mail"
    assert descriptors[0].annotations == {"readOnlyHint": False}


def test_catalog_rejects_non_object_schema_and_collision():
    invalid = SimpleNamespace(name="bad", description="", inputSchema={"type": "string"})
    valid = SimpleNamespace(name="same", description="", inputSchema={"type": "object"})

    descriptors, errors = build_tool_descriptors(
        "server", [invalid, valid], reserved_names={"mcp__server__same"}
    )

    assert descriptors == ()
    assert any("MCP_SCHEMA_INVALID" in error for error in errors)
    assert any("MCP_TOOL_NAME_COLLISION" in error for error in errors)
