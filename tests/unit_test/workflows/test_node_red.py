import pytest

from agent.workflows.node_red import NodeRedFlowError, compile_flow, parse_flow
from agent.workflows.schemas import WorkflowDefinitionV1, WorkflowEdge, WorkflowNode


def definition():
    return WorkflowDefinitionV1(
        workflow_id="w1", owner_user_id="u1", name="weather",
        nodes=(WorkflowNode("trigger", "schedule_trigger"), WorkflowNode("query", "mcp_query", config={"tool_name": "mcp__open-meteo__get_forecast"}), WorkflowNode("weixin", "weixin_notification", config={"send_consent_at": "2026-08-24T00:00:00Z"})),
        edges=(WorkflowEdge("e1", "trigger", target_node_id="query"), WorkflowEdge("e2", "query", target_node_id="weixin")),
    )


def test_compile_and_roundtrip_restricted_flow():
    payload = compile_flow(definition())
    restored = parse_flow(payload, owner_user_id="u1", workflow_id="w2", name="copy")
    assert [node.type for node in restored.nodes] == ["schedule_trigger", "mcp_query", "weixin_notification"]
    assert restored.edges[0].target_node_id == "query"


def test_rejects_arbitrary_node_red_nodes():
    with pytest.raises(NodeRedFlowError):
        parse_flow([{"id": "x", "type": "function", "wires": [[]]}], owner_user_id="u1", workflow_id="w1", name="bad")
