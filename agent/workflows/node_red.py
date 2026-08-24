"""Restricted Node-RED compatibility adapter.

Only the reviewed ZhiCe node catalog is accepted. Arbitrary Node-RED Function,
exec, filesystem, and HTTP nodes are intentionally rejected.
"""
from __future__ import annotations

from typing import Any

from agent.workflows.schemas import RetryPolicy, WorkflowDefinitionV1, WorkflowEdge, WorkflowNode

_TYPE_TO_KIND = {
    "schedule_trigger": "zhice-trigger", "mcp_query": "zhice-mcp-query",
    "mcp_action": "zhice-mcp-action", "llm_transform": "zhice-llm-transform",
    "template": "zhice-template", "condition": "zhice-condition",
    "official_notification": "zhice-official-notification", "personal_email": "zhice-personal-email",
    "qq_notification": "zhice-qq-notification",
    "weixin_notification": "zhice-weixin-notification",
}
_KIND_TO_TYPE = {value: key for key, value in _TYPE_TO_KIND.items()}


class NodeRedFlowError(ValueError):
    """Raised when an imported flow is outside the restricted catalog."""


def compile_flow(definition: WorkflowDefinitionV1) -> list[dict[str, Any]]:
    """Compile a ZhiCe definition into a portable Node-RED flow document."""
    return [
        {
            "id": node.id, "type": _TYPE_TO_KIND[node.type], "name": node.title or node.type,
            "x": int(node.position.get("x", 0)), "y": int(node.position.get("y", 0)),
            "wires": [[edge.target_node_id for edge in definition.edges if edge.source_node_id == node.id]],
            "zhice": {"schema_version": definition.schema_version, "node_type": node.type,
                      "config": node.config, "input_bindings": node.input_bindings,
                      "edges": [{"id": edge.id, "target": edge.target_node_id, "condition_branch": edge.condition_branch}
                                for edge in definition.edges if edge.source_node_id == node.id],
                      "timeout_seconds": node.timeout_seconds,
                      "retry_policy": {"max_attempts": node.retry_policy.max_attempts,
                                       "backoff_seconds": node.retry_policy.backoff_seconds}},
        }
        for node in definition.nodes
    ]


def parse_flow(payload: list[dict[str, Any]], *, owner_user_id: str, workflow_id: str, name: str) -> WorkflowDefinitionV1:
    """Import a flow, rejecting every node not supplied by the ZhiCe adapter."""
    if not isinstance(payload, list) or not payload:
        raise NodeRedFlowError("flow must be a non-empty array")
    nodes: list[WorkflowNode] = []
    edges: list[WorkflowEdge] = []
    for raw in payload:
        if not isinstance(raw, dict):
            raise NodeRedFlowError("flow node must be an object")
        node_type = _KIND_TO_TYPE.get(str(raw.get("type", "")))
        if node_type is None:
            raise NodeRedFlowError(f"unsupported Node-RED node type: {raw.get('type')}")
        metadata = raw.get("zhice") if isinstance(raw.get("zhice"), dict) else {}
        retry = metadata.get("retry_policy") if isinstance(metadata.get("retry_policy"), dict) else {}
        nodes.append(WorkflowNode(id=str(raw.get("id") or ""), type=node_type,
            title=str(raw.get("name") or node_type), position={"x": float(raw.get("x", 0)), "y": float(raw.get("y", 0))},
            config=dict(metadata.get("config") or {}), input_bindings=dict(metadata.get("input_bindings") or {}),
            timeout_seconds=float(metadata.get("timeout_seconds", 60)), retry_policy=RetryPolicy(**retry)))
    ids = {node.id for node in nodes}
    for raw in payload:
        source = str(raw.get("id") or "")
        metadata = raw.get("zhice") if isinstance(raw.get("zhice"), dict) else {}
        metadata_edges = metadata.get("edges") if isinstance(metadata.get("edges"), list) else []
        if metadata_edges:
            for item in metadata_edges:
                if not isinstance(item, dict) or str(item.get("target")) not in ids:
                    raise NodeRedFlowError("edge references an unknown node")
                edges.append(WorkflowEdge(id=str(item.get("id") or f"{source}->{item['target']}"), source_node_id=source,
                                          target_node_id=str(item["target"]), condition_branch=item.get("condition_branch")))
            continue
        wires = raw.get("wires") or []
        targets = wires[0] if isinstance(wires, list) and wires and isinstance(wires[0], list) else []
        for target in targets:
            if str(target) not in ids:
                raise NodeRedFlowError("wire references an unknown node")
            edges.append(WorkflowEdge(id=f"{source}->{target}", source_node_id=source, target_node_id=str(target)))
    return WorkflowDefinitionV1(workflow_id=workflow_id, owner_user_id=owner_user_id, name=name,
                                nodes=tuple(nodes), edges=tuple(edges))
