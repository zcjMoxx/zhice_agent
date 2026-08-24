import type { WorkflowEdge, WorkflowNode, WorkflowNodeType } from "@/api/types";

export interface EditorSnapshot {
  nodes: WorkflowNode[];
  edges: WorkflowEdge[];
}

export interface WorkflowVariableOption {
  nodeId: string;
  label: string;
  value: string;
}

export type WorkflowConnectionIssue =
  | "missing-node"
  | "self"
  | "target-trigger"
  | "duplicate"
  | "invalid-branch"
  | "branch-used"
  | "cycle";

export function cloneWorkflowJson<T>(value: T): T {
  return JSON.parse(JSON.stringify(value)) as T;
}

export function cloneSnapshot(snapshot: EditorSnapshot): EditorSnapshot {
  return cloneWorkflowJson(snapshot);
}

export function autoLayoutWorkflow(snapshot: EditorSnapshot): EditorSnapshot {
  const nodes = cloneSnapshot(snapshot).nodes;
  const edges = snapshot.edges;
  const nodeIds = new Set(nodes.map((node) => node.id));
  const incoming = new Map(nodes.map((node) => [node.id, 0]));
  const outgoing = new Map(nodes.map((node) => [node.id, [] as string[]]));
  for (const edge of edges) {
    if (!nodeIds.has(edge.source_node_id) || !nodeIds.has(edge.target_node_id)) continue;
    incoming.set(edge.target_node_id, (incoming.get(edge.target_node_id) || 0) + 1);
    outgoing.get(edge.source_node_id)?.push(edge.target_node_id);
  }

  const ranks = new Map<string, number>();
  const queue = nodes.filter((node) => (incoming.get(node.id) || 0) === 0).map((node) => node.id);
  for (const id of queue) ranks.set(id, 0);
  for (let index = 0; index < queue.length; index += 1) {
    const id = queue[index];
    for (const target of outgoing.get(id) || []) {
      ranks.set(target, Math.max(ranks.get(target) || 0, (ranks.get(id) || 0) + 1));
      incoming.set(target, (incoming.get(target) || 0) - 1);
      if (incoming.get(target) === 0) queue.push(target);
    }
  }

  const fallbackRank = Math.max(0, ...ranks.values()) + 1;
  const rows = new Map<number, number>();
  for (const node of nodes) {
    const rank = ranks.get(node.id) ?? fallbackRank;
    const row = rows.get(rank) || 0;
    node.position = { x: 100 + rank * 310, y: 110 + row * 150 };
    rows.set(rank, row + 1);
  }
  return { nodes, edges: cloneWorkflowJson(edges) };
}

export function insertNodeOnEdge(snapshot: EditorSnapshot, edgeId: string, node: WorkflowNode): EditorSnapshot {
  const edge = snapshot.edges.find((item) => item.id === edgeId);
  if (!edge) return cloneSnapshot(snapshot);
  const edges = snapshot.edges.filter((item) => item.id !== edgeId);
  edges.push(
    {
      id: `${edge.id}-before-${node.id}`,
      source_node_id: edge.source_node_id,
      target_node_id: node.id,
      source_port: edge.source_port,
      target_port: "input",
      condition_branch: edge.condition_branch,
    },
    {
      id: `${edge.id}-after-${node.id}`,
      source_node_id: node.id,
      target_node_id: edge.target_node_id,
      source_port: "output",
      target_port: edge.target_port,
    },
  );
  return { nodes: [...cloneWorkflowJson(snapshot.nodes), cloneWorkflowJson(node)], edges };
}

export function directPredecessorId(snapshot: EditorSnapshot, nodeId: string): string | undefined {
  const sources = snapshot.edges
    .filter((edge) => edge.target_node_id === nodeId)
    .map((edge) => edge.source_node_id);
  return sources.length === 1 ? sources[0] : undefined;
}

export function graphInputReference(snapshot: EditorSnapshot, nodeId: string): string {
  const sourceId = directPredecessorId(snapshot, nodeId);
  return sourceId ? `\${nodes.${sourceId}.output}` : "";
}

export function withGraphBoundInputs(snapshot: EditorSnapshot): EditorSnapshot {
  const result = cloneSnapshot(snapshot);
  const outputTypes = new Set<WorkflowNodeType>(["template", "official_notification", "personal_email", "qq_notification", "weixin_notification"]);
  for (const node of result.nodes) {
    const reference = graphInputReference(result, node.id);
    if (node.type === "llm_transform") node.config.input = reference;
    if (outputTypes.has(node.type)) {
      node.config.source_ref = reference;
      if (node.type !== "template") node.config.body = reference || String(node.config.content || "");
    }
  }
  return result;
}

export function upstreamVariables(snapshot: EditorSnapshot, selectedNodeId: string): WorkflowVariableOption[] {
  const byId = new Map(snapshot.nodes.map((node) => [node.id, node]));
  const incoming = new Map<string, string[]>();
  for (const edge of snapshot.edges) {
    const values = incoming.get(edge.target_node_id) || [];
    values.push(edge.source_node_id);
    incoming.set(edge.target_node_id, values);
  }
  const seen = new Set<string>();
  const queue = [...(incoming.get(selectedNodeId) || [])];
  while (queue.length) {
    const id = queue.shift()!;
    if (seen.has(id)) continue;
    seen.add(id);
    queue.push(...(incoming.get(id) || []));
  }
  return [...seen]
    .map((id) => byId.get(id))
    .filter((node): node is WorkflowNode => Boolean(node) && node?.type !== "schedule_trigger")
    .map((node) => ({ nodeId: node.id, label: node.title || node.id, value: `\${nodes.${node.id}.output}` }));
}

export function canAddNodeType(type: WorkflowNodeType, nodes: WorkflowNode[]): boolean {
  return type !== "schedule_trigger" || !nodes.some((node) => node.type === "schedule_trigger");
}

export function workflowConnectionIssue(
  snapshot: EditorSnapshot,
  sourceNodeId: string,
  targetNodeId: string,
  sourceHandle = "output",
): WorkflowConnectionIssue | null {
  const source = snapshot.nodes.find((node) => node.id === sourceNodeId);
  const target = snapshot.nodes.find((node) => node.id === targetNodeId);
  if (!source || !target) return "missing-node";
  if (sourceNodeId === targetNodeId) return "self";
  if (target.type === "schedule_trigger") return "target-trigger";

  const branch = source.type === "condition" ? sourceHandle : "output";
  if (source.type === "condition" && !["true", "false"].includes(branch)) return "invalid-branch";
  if (source.type === "condition" && snapshot.edges.some((edge) => edge.source_node_id === sourceNodeId && edge.condition_branch === branch)) return "branch-used";
  if (snapshot.edges.some((edge) => edge.source_node_id === sourceNodeId && edge.target_node_id === targetNodeId && (edge.condition_branch || "output") === branch)) return "duplicate";

  const outgoing = new Map<string, string[]>();
  for (const edge of snapshot.edges) {
    const targets = outgoing.get(edge.source_node_id) || [];
    targets.push(edge.target_node_id);
    outgoing.set(edge.source_node_id, targets);
  }
  const pending = [targetNodeId];
  const visited = new Set<string>();
  while (pending.length) {
    const current = pending.shift()!;
    if (current === sourceNodeId) return "cycle";
    if (visited.has(current)) continue;
    visited.add(current);
    pending.push(...(outgoing.get(current) || []));
  }
  return null;
}
