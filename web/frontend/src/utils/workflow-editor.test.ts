import { describe, expect, it } from "vitest";
import { reactive } from "vue";

import type { WorkflowNode } from "@/api/types";
import { autoLayoutWorkflow, canAddNodeType, cloneSnapshot, directPredecessorId, graphInputReference, insertNodeOnEdge, upstreamVariables, withGraphBoundInputs, workflowConnectionIssue } from "./workflow-editor";

const nodes: WorkflowNode[] = [
  { id: "trigger", type: "schedule_trigger", title: "开始", position: { x: 9, y: 9 }, config: {} },
  { id: "query", type: "mcp_query", title: "查天气", position: { x: 9, y: 9 }, config: {} },
  { id: "summary", type: "llm_transform", title: "总结", position: { x: 9, y: 9 }, config: {} },
];
const edges = [
  { id: "a", source_node_id: "trigger", target_node_id: "query" },
  { id: "b", source_node_id: "query", target_node_id: "summary" },
];

describe("workflow editor helpers", () => {
  it("clones Vue-reactive snapshots into independent workflow JSON", () => {
    const source = reactive({ nodes, edges });
    const result = cloneSnapshot(source);

    expect(result).toEqual({ nodes, edges });
    result.nodes[0].config.mode = "changed";
    expect(source.nodes[0].config).toEqual({});
  });

  it("lays a DAG out from left to right without mutating the source", () => {
    const result = autoLayoutWorkflow({ nodes, edges });
    expect(result.nodes.map((node) => node.position.x)).toEqual([100, 410, 720]);
    expect(nodes[0].position).toEqual({ x: 9, y: 9 });
  });

  it("inserts a node into an edge and keeps a condition branch on the upstream half", () => {
    const action: WorkflowNode = { id: "notify", type: "official_notification", position: { x: 300, y: 200 }, config: {} };
    const result = insertNodeOnEdge({ nodes, edges: [{ ...edges[0], condition_branch: "true" }] }, "a", action);
    expect(result.edges).toHaveLength(2);
    expect(result.edges[0]).toMatchObject({ source_node_id: "trigger", target_node_id: "notify", condition_branch: "true" });
    expect(result.edges[1]).toMatchObject({ source_node_id: "notify", target_node_id: "query" });
  });

  it("offers only transitive upstream node outputs as variables", () => {
    expect(upstreamVariables({ nodes, edges }, "summary").map((item) => item.value)).toEqual([
      "${nodes.query.output}",
    ]);
  });

  it("binds processing and delivery to their one directly connected previous step", () => {
    const delivery: WorkflowNode = {
      id: "mail",
      type: "personal_email",
      title: "发送结果",
      position: { x: 9, y: 9 },
      config: { source_ref: "${nodes.query.output}", body: "${nodes.query.output}" },
    };
    const snapshot = {
      nodes: [...nodes, delivery],
      edges: [...edges, { id: "c", source_node_id: "summary", target_node_id: "mail" }],
    };

    expect(directPredecessorId(snapshot, "mail")).toBe("summary");
    expect(graphInputReference(snapshot, "mail")).toBe("${nodes.summary.output}");
    const result = withGraphBoundInputs(snapshot);
    expect(result.nodes.find((node) => node.id === "summary")?.config.input).toBe("${nodes.query.output}");
    expect(result.nodes.find((node) => node.id === "mail")?.config).toMatchObject({
      source_ref: "${nodes.summary.output}",
      body: "${nodes.summary.output}",
    });

    const qqResult = withGraphBoundInputs({
      nodes: [
        ...nodes,
        { ...delivery, id: "qq", type: "qq_notification", config: {} },
      ],
      edges: [...edges, { id: "qq-edge", source_node_id: "summary", target_node_id: "qq" }],
    });
    expect(qqResult.nodes.find((node) => node.id === "qq")?.config).toMatchObject({
      source_ref: "${nodes.summary.output}",
      body: "${nodes.summary.output}",
    });
  });

  it("allows only one schedule trigger", () => {
    expect(canAddNodeType("schedule_trigger", nodes)).toBe(false);
    expect(canAddNodeType("template", nodes)).toBe(true);
  });

  it("validates explicit connections before they are added", () => {
    expect(workflowConnectionIssue({ nodes, edges }, "summary", "trigger")).toBe("target-trigger");
    expect(workflowConnectionIssue({ nodes, edges }, "query", "query")).toBe("self");
    expect(workflowConnectionIssue({ nodes, edges }, "query", "summary")).toBe("duplicate");
    expect(workflowConnectionIssue({ nodes, edges }, "summary", "query")).toBe("cycle");
    expect(workflowConnectionIssue({ nodes, edges }, "trigger", "summary")).toBeNull();
  });

  it("keeps each condition branch unique", () => {
    const condition: WorkflowNode = { id: "condition", type: "condition", title: "判断", position: { x: 0, y: 0 }, config: {} };
    const snapshot = {
      nodes: [...nodes, condition],
      edges: [...edges, { id: "yes", source_node_id: "condition", target_node_id: "summary", source_port: "output", target_port: "input", condition_branch: "true" as const }],
    };
    expect(workflowConnectionIssue(snapshot, "condition", "query", "true")).toBe("branch-used");
    expect(workflowConnectionIssue(snapshot, "condition", "query", "false")).toBeNull();
    expect(workflowConnectionIssue({ nodes: [...nodes, condition], edges }, "condition", "trigger", "false")).toBe("target-trigger");
    expect(workflowConnectionIssue({ nodes: [...nodes, condition], edges }, "condition", "summary", "other")).toBe("invalid-branch");
  });
});
