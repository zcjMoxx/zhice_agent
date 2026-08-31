import { describe, expect, it } from "vitest";

import type { WorkflowToolCatalogItem } from "@/api/types";
import { instantiateWorkflowTemplate, workflowStarterTemplates } from "@/utils/workflow-templates";

const tools: WorkflowToolCatalogItem[] = [
  { name: "mcp__open-meteo__get_forecast", description: "weather", kind: "query", parameters: {}, schema_hash: "weather-hash", available: true },
  { name: "mcp__tavily__tavily_search", description: "search", kind: "query", parameters: {}, schema_hash: "search-hash", available: true },
];

describe("workflow starter templates", () => {
  it.each(workflowStarterTemplates)("creates the complete $id blueprint", (template) => {
    const definition = instantiateWorkflowTemplate(template, tools, "Asia/Shanghai");
    const types = new Set(definition.nodes.map((node) => node.type));
    const connected = new Set(definition.edges.flatMap((edge) => [edge.source_node_id, edge.target_node_id]));

    expect(definition.nodes).toHaveLength(4);
    expect(types).toEqual(new Set(["schedule_trigger", "mcp_query", "llm_transform", "template"]));
    expect(connected).toEqual(new Set(definition.nodes.map((node) => node.id)));
    expect(definition.edges).toHaveLength(3);
    expect(definition.nodes.find((node) => node.type === "mcp_query")?.config.input_schema_hash).toBeTruthy();
    expect(definition.nodes.find((node) => node.type === "schedule_trigger")?.config.time_of_day).toBe("");
    expect(definition.nodes.find((node) => node.type === "template")?.config).toMatchObject({ template: "{{result}}" });
    expect(definition.required_permissions).toEqual(["workflow.use"]);
  });

  it("prepares weather advice as editable ordinary nodes", () => {
    const weather = workflowStarterTemplates.find((item) => item.id === "weather")!;
    const definition = instantiateWorkflowTemplate(weather, tools, "Asia/Shanghai");
    const advice = definition.nodes.find((node) => node.id === "advice")!;

    expect(advice.config.task).toBe("advice");
    expect(advice.config.output_length).toBe("short");
    expect(advice.config.advice_topics).toEqual(["umbrella", "clothing", "travel"]);
    expect(String(advice.config.instruction)).toContain("带伞");
    expect(String(advice.config.instruction)).toContain("不超过 3 行、90 个汉字");
    expect(String(advice.config.instruction)).toContain("不要逐项说明不存在的风险");
    expect(definition.nodes.find((node) => node.id === "weather")?.retry_policy).toEqual({
      max_attempts: 3,
      backoff_seconds: 5,
    });
  });

  it("keeps record-only delivery as the safe default even when QQ is available", () => {
    const weather = workflowStarterTemplates.find((item) => item.id === "weather")!;
    const definition = instantiateWorkflowTemplate(weather, tools, "Asia/Shanghai", {
      qq_notification: { available: true, bound: true, code: "" },
    });
    const delivery = definition.nodes.find((node) => node.id === "delivery")!;

    expect(delivery.type).toBe("template");
    expect(delivery.config.template).toBe("{{result}}");
    expect(delivery.config.source_ref).toBe("${nodes.advice.output}");
    expect(definition.required_permissions).toEqual(["workflow.use"]);
  });
});
