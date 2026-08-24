import type { WorkflowCapabilities, WorkflowDefinitionV1, WorkflowEdge, WorkflowNode, WorkflowToolCatalogItem } from "@/api/types";

export interface WorkflowStarterTemplate {
  id: string;
  requiredTool: string;
  icon: string;
  title: string;
  description: string;
  requirements: string;
  nodes: WorkflowNode[];
  edges: WorkflowEdge[];
}

const weatherAdvice = "根据天气事实写一条今天能直接看的简短提醒。第一行用“今日天气：”概括气温、体感和降雨；第二行只写带伞、穿衣、出行中今天真正有用的建议；只有存在高温、雷雨、大风、低温或明显温差时，第三行才用“注意：”提醒。不要逐项说明不存在的风险，不解释原因，不重复天气数据。总共不超过 3 行、90 个汉字。输出中文纯文本，不使用 Markdown、JSON、内部字段名或代码。";

export const workflowStarterTemplates: WorkflowStarterTemplate[] = [
  {
    id: "weather",
    requiredTool: "mcp__open-meteo__get_forecast",
    icon: "☀",
    title: "每日天气建议",
    description: "每天查询天气，生成穿衣、带伞和出行建议，再发送给你。",
    requirements: "进入画布后填写：提醒时间、地点，并确认发送方式",
    nodes: [
      { id: "trigger", type: "schedule_trigger", title: "定时运行", position: { x: 80, y: 180 }, config: { trigger_type: "cron", schedule_mode: "daily", time_of_day: "" } },
      { id: "weather", type: "mcp_query", title: "查询天气", position: { x: 360, y: 180 }, config: { tool_name: "mcp__open-meteo__get_forecast", input_schema_hash: "", arguments: { place_name: "", forecast_days: 1 } } },
      { id: "advice", type: "llm_transform", title: "生成今日建议", position: { x: 640, y: 180 }, config: { task: "advice", tone: "friendly", output_length: "short", advice_topics: ["umbrella", "clothing", "travel"], commute_mode: "general", temperature_preference: "normal", additional_instruction: "", instruction: weatherAdvice, input: "${nodes.weather.output}" } },
      { id: "delivery", type: "personal_email", title: "发送结果", position: { x: 920, y: 180 }, config: { delivery_mode: "email", connection_id: "", to: "", subject: "今日天气与生活建议", content: "", source_ref: "${nodes.advice.output}", body: "${nodes.advice.output}" } },
    ],
    edges: [
      { id: "weather-e1", source_node_id: "trigger", target_node_id: "weather" },
      { id: "weather-e2", source_node_id: "weather", target_node_id: "advice" },
      { id: "weather-e3", source_node_id: "advice", target_node_id: "delivery" },
    ],
  },
  {
    id: "digest",
    requiredTool: "mcp__tavily__tavily_search",
    icon: "✦",
    title: "每日信息摘要",
    description: "定时搜索关注的信息，整理成一份可读摘要，再发送给你。",
    requirements: "进入画布后填写：提醒时间、搜索内容，并确认发送方式",
    nodes: [
      { id: "trigger", type: "schedule_trigger", title: "定时运行", position: { x: 80, y: 180 }, config: { trigger_type: "cron", schedule_mode: "daily", time_of_day: "" } },
      { id: "query", type: "mcp_query", title: "搜索信息", position: { x: 360, y: 180 }, config: { tool_name: "mcp__tavily__tavily_search", input_schema_hash: "", arguments: { query: "" } } },
      { id: "summary", type: "llm_transform", title: "整理摘要", position: { x: 640, y: 180 }, config: { task: "summary", tone: "plain", output_length: "medium", instruction: "提炼重要信息和来源，输出清晰的中文纯文本，不使用 Markdown、JSON、内部字段名或代码", input: "${nodes.query.output}" } },
      { id: "delivery", type: "personal_email", title: "发送结果", position: { x: 920, y: 180 }, config: { delivery_mode: "email", connection_id: "", to: "", subject: "每日信息摘要", content: "", source_ref: "${nodes.summary.output}", body: "${nodes.summary.output}" } },
    ],
    edges: [
      { id: "digest-e1", source_node_id: "trigger", target_node_id: "query" },
      { id: "digest-e2", source_node_id: "query", target_node_id: "summary" },
      { id: "digest-e3", source_node_id: "summary", target_node_id: "delivery" },
    ],
  },
];

export function instantiateWorkflowTemplate(template: WorkflowStarterTemplate, tools: WorkflowToolCatalogItem[], timezone: string, capabilities: WorkflowCapabilities = {}): WorkflowDefinitionV1 {
  const useQq = capabilities.qq_notification?.available === true;
  const nodes = template.nodes.map((node) => {
    const config = structuredClone(node.config);
    if (node.type === "mcp_query" || node.type === "mcp_action") {
      const tool = tools.find((item) => item.name === config.tool_name);
      config.input_schema_hash = tool?.schema_hash || "";
    }
    if (node.id === "delivery" && useQq) {
      return {
        ...node,
        type: "qq_notification" as const,
        position: { ...node.position },
        config: {
          delivery_mode: "qq",
          content: "",
          source_ref: config.source_ref || "",
          body: config.body || "",
          send_consent_at: "",
        },
      };
    }
    return { ...node, position: { ...node.position }, config };
  });
  return {
    schema_version: 1,
    name: template.title,
    description: template.description,
    timezone,
    nodes,
    edges: template.edges.map((edge) => ({ ...edge })),
    required_permissions: ["workflow.use", useQq ? "workflow.notify.self" : "workflow.email.send"],
    connection_ids: [],
  };
}
