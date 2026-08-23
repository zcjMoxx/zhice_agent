"""Workflow node catalog and strict DAG validation."""

from __future__ import annotations

import json
from collections import defaultdict
from hashlib import sha256
from typing import Any

from agent.workflows.schemas import WorkflowDefinitionV1


class WorkflowValidationError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def schema_hash(schema: dict[str, Any]) -> str:
    return sha256(json.dumps(schema, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def validate_definition(definition: WorkflowDefinitionV1, *, max_nodes: int = 30, max_edges: int = 60) -> list[str]:
    if len(definition.nodes) > max_nodes or len(definition.edges) > max_edges:
        raise WorkflowValidationError("WORKFLOW_GRAPH_TOO_LARGE", "workflow graph exceeds configured limits")
    ids = [node.id for node in definition.nodes]
    if len(ids) != len(set(ids)):
        raise WorkflowValidationError("WORKFLOW_SCHEMA_INVALID", "duplicate node id")
    triggers = [node for node in definition.nodes if node.type == "schedule_trigger"]
    if len(triggers) != 1:
        raise WorkflowValidationError("WORKFLOW_SCHEMA_INVALID", "exactly one schedule trigger is required")
    by_id = {node.id: node for node in definition.nodes}
    incoming: dict[str, int] = defaultdict(int)
    outgoing: dict[str, list[str]] = defaultdict(list)
    edge_ids: set[str] = set()
    branches: dict[str, set[str]] = defaultdict(set)
    for edge in definition.edges:
        if not edge.id or edge.id in edge_ids or edge.source_node_id not in by_id or edge.target_node_id not in by_id:
            raise WorkflowValidationError("WORKFLOW_SCHEMA_INVALID", "edge identity or endpoint is invalid")
        edge_ids.add(edge.id)
        source = by_id[edge.source_node_id]
        if edge.source_port != "output" or edge.target_port != "input":
            raise WorkflowValidationError("WORKFLOW_SCHEMA_INVALID", "unsupported edge port")
        if source.type == "condition":
            if edge.condition_branch not in {"true", "false"}:
                raise WorkflowValidationError("WORKFLOW_SCHEMA_INVALID", "condition edge requires true or false branch")
            if edge.condition_branch in branches[source.id]:
                raise WorkflowValidationError("WORKFLOW_SCHEMA_INVALID", "condition branch must be unique")
            branches[source.id].add(edge.condition_branch)
        elif edge.condition_branch is not None:
            raise WorkflowValidationError("WORKFLOW_SCHEMA_INVALID", "branch is only valid on condition edges")
        outgoing[edge.source_node_id].append(edge.target_node_id)
        incoming[edge.target_node_id] += 1
    action_types = {"mcp_query", "mcp_action", "llm_transform", "template", "condition", "official_notification", "personal_email", "qq_notification"}
    if any(node.type in action_types and incoming[node.id] == 0 for node in definition.nodes):
        raise WorkflowValidationError("WORKFLOW_SCHEMA_INVALID", "isolated action node")
    for node in definition.nodes:
        if node.type == "condition" and branches[node.id] != {"true", "false"}:
            raise WorkflowValidationError("WORKFLOW_SCHEMA_INVALID", "condition must have true and false branches")
    indegree = {node_id: incoming[node_id] for node_id in ids}
    ready = sorted(node_id for node_id, degree in indegree.items() if degree == 0)
    ordered: list[str] = []
    while ready:
        current = ready.pop(0)
        ordered.append(current)
        for target in sorted(outgoing[current]):
            indegree[target] -= 1
            if indegree[target] == 0:
                ready.append(target)
                ready.sort()
    if len(ordered) != len(ids):
        raise WorkflowValidationError("WORKFLOW_GRAPH_CYCLE", "workflow graph contains a cycle")
    return ordered
