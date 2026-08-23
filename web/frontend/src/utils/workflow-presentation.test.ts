import { describe, expect, it } from "vitest";

import {
  workflowErrorLabel,
  workflowFieldHint,
  workflowFieldName,
  workflowNodeTypeLabel,
  workflowRunOutputLabel,
  workflowRunStatusLabel,
  workflowStatusLabel,
  workflowToolName,
  workflowToolInputFields,
  workflowToolProvider,
  workflowToolResultSummary,
  workflowTriggerLabel,
  workflowVersionLabel,
} from "./workflow-presentation";

describe("workflow presentation", () => {
  it("presents known MCP tools without exposing their internal names", () => {
    expect(workflowToolName("mcp__12306__get-tickets")).toBe("查询火车票与余票");
    expect(workflowToolProvider("mcp__xhs-readonly__search_notes")).toBe("小红书");
    expect(workflowToolName({ name: "mcp__future__lookup", description: "查询公开资料。仅返回已验证结果。" })).toBe("查询公开资料");
    expect(workflowToolName("mcp__future__lookup")).toBe("扩展工具");
  });

  it("presents schema fields as Chinese product labels", () => {
    expect(workflowFieldName("fromStation")).toBe("出发站");
    expect(workflowFieldName("future_key", { description: "出行偏好，用于筛选结果。" })).toBe("出行偏好");
    expect(workflowFieldName("future_key")).toBe("配置项");
    expect(workflowFieldHint("fromStation")).not.toContain("station_code");
    expect(workflowFieldHint("date")).not.toContain("get-current-date");
  });

  it("uses task inputs instead of weather coordinates and Xiaohongshu tokens", () => {
    const weatherFields = workflowToolInputFields({
      name: "mcp__open-meteo__get_forecast",
      parameters: { type: "object", properties: { latitude: { type: "number" }, longitude: { type: "number" }, start_date: { type: "string" }, end_date: { type: "string" } }, required: ["latitude", "longitude", "start_date", "end_date"] },
    });
    expect(weatherFields.map((field) => field.key)).toEqual(["place_name", "forecast_days"]);
    expect(weatherFields[0]).toMatchObject({ label: "地点", required: true });

    const xhsFields = workflowToolInputFields({
      name: "mcp__xhs-readonly__get_note_detail",
      parameters: { type: "object", properties: { feed_id: { type: "string" }, xsec_token: { type: "string" }, include_comments: { type: "boolean" } }, required: ["feed_id", "xsec_token"] },
    });
    expect(xhsFields.map((field) => field.key)).toEqual(["note_url", "include_comments"]);
    expect(xhsFields[0]).toMatchObject({ label: "小红书笔记链接", type: "url", required: true });
  });

  it("uses place names instead of station codes and hides optional web-search internals", () => {
    const ticketFields = workflowToolInputFields({
      name: "mcp__12306__get-tickets",
      parameters: { type: "object", properties: { date: { type: "string" }, fromStation: { type: "string" }, toStation: { type: "string" } }, required: ["date", "fromStation", "toStation"] },
    });
    expect(ticketFields.map((field) => field.key)).toEqual(["departure_name", "arrival_name", "date", "trainFilterFlags"]);
    expect(ticketFields.slice(0, 3).every((field) => field.required)).toBe(true);

    const searchFields = workflowToolInputFields({
      name: "mcp__tavily__tavily_search",
      parameters: { type: "object", properties: { query: { type: "string" }, include_domains: { type: "array" }, raw_content: { type: "boolean" } }, required: ["query"] },
    });
    expect(searchFields.map((field) => field.key)).toEqual(["query", "search_depth", "topic", "max_results"]);
  });

  it("summarizes tool responses before showing raw details", () => {
    expect(workflowToolResultSummary({ pois: [{ name: "南岸区" }, { name: "南坪" }] })).toContain("2 个地点");
    expect(workflowToolResultSummary({ status: "success" })).toContain("查询成功");
    expect(workflowToolResultSummary({ daily: { time: ["2026-08-21"], temperature_2m_max: [31.8], temperature_2m_min: [24.4], precipitation_probability_max: [99] } })).toContain("最高 31.8℃");
    expect(workflowToolResultSummary({ text: "车次 | 详情\nG1(实际车次train_no: x) 北京 -> 上海" })).toContain("1 趟列车");
    expect(workflowToolResultSummary('{"results":[{"title":"杭州本地宝"}]}')).toContain("1 条结果：杭州本地宝");
    expect(workflowToolResultSummary({ status: "success", data: { text: '{"feeds":[{"noteCard":{"displayTitle":"杭州两日游"}}]}' } })).toContain("1 篇公开笔记：杭州两日游");
  });

  it("presents workflow runtime values as Chinese labels", () => {
    expect(workflowStatusLabel("draft")).toBe("未开启");
    expect(workflowStatusLabel("active")).toBe("已开启");
    expect(workflowStatusLabel("paused")).toBe("已关闭");
    expect(workflowVersionLabel(2)).toBe("版本 2");
    expect(workflowRunStatusLabel("succeeded")).toBe("执行成功");
    expect(workflowTriggerLabel("manual")).toBe("手动触发");
    expect(workflowNodeTypeLabel("mcp_query")).toBe("获取信息");
    expect(workflowNodeTypeLabel("qq_notification")).toBe("发送结果");
    expect(workflowErrorLabel("WORKFLOW_GRAPH_CYCLE")).toContain("循环");
    expect(workflowErrorLabel("WORKFLOW_QQ_NOT_BOUND")).toContain("连接 QQ");
    expect(workflowErrorLabel("WORKFLOW_DRAFT_CONFLICT")).toContain("保留当前画布");
    expect(workflowErrorLabel("UNKNOWN_INTERNAL_CODE")).not.toContain("UNKNOWN_INTERNAL_CODE");
    expect(workflowRunOutputLabel('{"text":"杭州今天有雨"}')).toBe("杭州今天有雨");
  });
});
