import type { WorkflowNodeType, WorkflowToolCatalogItem } from "@/api/types";

export interface WorkflowInputOption { value: string; label: string }
export interface WorkflowInputField {
  key: string;
  label: string;
  hint: string;
  type: "text" | "number" | "boolean" | "date" | "url";
  required: boolean;
  options?: WorkflowInputOption[];
  min?: number;
  max?: number;
}

const toolNames: Record<string, string> = {
  "mcp__12306__get-tickets": "查询火车票与余票",
  "mcp__amap-maps__maps_search_detail": "查询地点详情",
  "mcp__amap-maps__maps_text_search": "搜索地点",
  "mcp__open-meteo__get_forecast": "查询天气预报",
  "mcp__open-meteo__get_historical_weather": "查询历史天气",
  "mcp__tavily__tavily_search": "搜索网页信息",
  "mcp__xhs-readonly__get_note_detail": "查看小红书笔记详情",
  "mcp__xhs-readonly__search_notes": "搜索小红书笔记",
};

const providerNames: Record<string, string> = {
  "12306": "铁路 12306",
  "amap-maps": "高德地图",
  "open-meteo": "天气服务",
  tavily: "网页搜索",
  "xhs-readonly": "小红书",
};

const fieldNames: Record<string, string> = {
  date: "查询日期",
  fromStation: "出发站",
  toStation: "到达站",
  departure_name: "出发城市",
  arrival_name: "到达城市",
  trainFilterFlags: "车次类型",
  query: "搜索内容",
  keyword: "搜索关键词",
  keywords: "搜索关键词",
  city: "城市",
  location: "地点",
  address: "地址",
  latitude: "纬度",
  longitude: "经度",
  forecast_days: "查询几天",
  past_days: "历史天数",
  start_date: "开始日期",
  end_date: "结束日期",
  note_id: "笔记编号",
  noteId: "笔记编号",
  id: "内容编号",
  url: "网页地址",
  limit: "返回数量",
  max_results: "返回数量",
  language: "内容语言",
  region: "地区",
  adcode: "地区编码",
  place_name: "地点",
  note_url: "小红书笔记链接",
  sort_by: "排序方式",
  note_type: "内容类型",
  feed_id: "笔记编号",
  xsec_token: "访问参数",
  include_comments: "同时读取评论",
  timezone: "时区",
  search_depth: "搜索范围",
  topic: "内容类别",
};

const fieldHints: Record<string, string> = {
  date: "例如：2026-08-22",
  fromStation: "输入出发城市或车站名称",
  toStation: "输入到达城市或车站名称",
  departure_name: "例如：北京、杭州",
  arrival_name: "例如：上海、广州",
  trainFilterFlags: "可选，例如 G 表示高铁、D 表示动车；留空表示不限车型",
  query: "输入想要查找的内容",
  keyword: "输入搜索关键词",
  keywords: "输入搜索关键词",
  city: "输入城市名称",
  location: "输入地点名称或坐标",
  address: "输入完整地址",
  latitude: "输入纬度，例如 31.23",
  longitude: "输入经度，例如 121.47",
  forecast_days: "默认查询今天和明天，可填写 1 至 16 天",
  past_days: "输入向前查询的天数",
  start_date: "选择开始日期",
  end_date: "选择结束日期",
  note_id: "粘贴笔记链接或编号",
  noteId: "粘贴笔记链接或编号",
  url: "粘贴完整网页地址",
  limit: "输入最多返回多少条结果",
  max_results: "输入最多返回多少条结果",
  place_name: "例如：上海、杭州西湖、云南大理",
  note_url: "粘贴浏览器中打开的小红书笔记完整链接",
  sort_by: "选择结果排序方式",
  note_type: "选择要查看的内容类型",
  search_depth: "选择搜索速度和覆盖范围",
  topic: "选择更符合内容的类别",
};

const fieldOptions: Record<string, WorkflowInputOption[]> = {
  trainFilterFlags: [
    { value: "G", label: "只看高铁" },
    { value: "D", label: "只看动车" },
    { value: "Z", label: "只看直达特快" },
    { value: "T", label: "只看特快" },
    { value: "K", label: "只看快速列车" },
  ],
  sort_by: [
    { value: "general", label: "综合排序" },
    { value: "latest", label: "最新发布" },
    { value: "most_liked", label: "最多点赞" },
    { value: "most_commented", label: "最多评论" },
    { value: "most_collected", label: "最多收藏" },
  ],
  note_type: [
    { value: "all", label: "全部类型" },
    { value: "image", label: "图文" },
    { value: "video", label: "视频" },
  ],
  search_depth: [
    { value: "basic", label: "快速搜索" },
    { value: "advanced", label: "深入搜索" },
  ],
  topic: [
    { value: "general", label: "综合信息" },
    { value: "news", label: "新闻资讯" },
    { value: "finance", label: "财经信息" },
  ],
};

const workflowStatuses: Record<string, string> = {
  draft: "未开启",
  active: "已开启",
  paused: "已关闭",
  paused_attention: "已关闭，需要处理",
  archived: "已归档",
};

const runStatuses: Record<string, string> = {
  queued: "等待执行",
  running: "执行中",
  succeeded: "执行成功",
  failed: "执行失败",
  partial: "部分完成",
  cancelled: "已取消",
  done: "已完成",
  error: "执行失败",
  skipped: "已跳过",
  pending: "等待执行",
};

const triggerTypes: Record<string, string> = {
  manual: "手动触发",
  date: "指定时间",
  interval: "固定间隔",
  cron: "周期定时",
  schedule: "定时触发",
};

const nodeTypes: Record<WorkflowNodeType, string> = {
  schedule_trigger: "触发器",
  mcp_query: "获取信息",
  mcp_action: "执行操作",
  llm_transform: "智能处理",
  template: "发送结果",
  condition: "条件分支",
  official_notification: "发送结果",
  personal_email: "发送结果",
  qq_notification: "发送结果",
  weixin_notification: "发送结果",
};

const workflowErrors: Record<string, string> = {
  WORKFLOW_DRAFT_CONFLICT: "这个工作流刚刚在其他页面更新，系统会保留当前画布并基于最新版本重试；如果仍然失败，请刷新页面后再试",
  WORKFLOW_TOOL_NOT_ALLOWED: "当前账号不能使用这个工具，请重新选择",
  WORKFLOW_TOOL_SCHEMA_CHANGED: "工具参数规则已经更新，请重新选择并检查参数",
  WORKFLOW_TOOL_ARGUMENTS_INVALID: "工具参数不完整或格式不正确",
  WORKFLOW_GRAPH_CYCLE: "步骤之间形成了循环，请调整连线",
  WORKFLOW_TRIGGER_INVALID: "触发时间配置不正确",
  WORKFLOW_TIMEZONE_INVALID: "时区配置不正确",
  WORKFLOW_NOT_FOUND: "工作流不存在或你无权访问",
  WORKFLOW_PERMISSION_DENIED: "当前账号没有执行此操作的权限",
  WORKFLOW_CONNECTION_REQUIRED: "需要先绑定对应账号或发送通道",
  WORKFLOW_LOCATION_NOT_FOUND: "没有找到这个地点，请输入更完整的城市或地点名称",
  WORKFLOW_LOCATION_SERVICE_UNAVAILABLE: "地点查询服务暂时不可用，请稍后重试",
  WORKFLOW_SOURCE_AUTH_REQUIRED: "外部信息账号的登录已失效，请重新登录后再试",
  WORKFLOW_SOURCE_TIMEOUT: "外部信息查询时间过长，请稍后重试；若是小红书，请先确认登录状态",
  WORKFLOW_SOURCE_RATE_LIMITED: "外部信息查询过于频繁，请稍后再试",
  WORKFLOW_SOURCE_UNAVAILABLE: "外部信息服务暂时不可用，请稍后重试",
  WORKFLOW_XHS_LINK_INVALID: "小红书笔记链接不完整，请从浏览器地址栏复制完整链接",
  WORKFLOW_STATION_NOT_FOUND: "没有找到对应的铁路城市，请输入城市名称，例如北京、上海",
  WORKFLOW_STATION_SERVICE_UNAVAILABLE: "铁路站点查询暂时不可用，请稍后重试",
  WORKFLOW_NODE_CONFIG_INVALID: "这个步骤还没有配置完整，请检查标出的必填项",
  WORKFLOW_NODE_FAILED: "这个步骤执行失败，请检查输入内容或稍后重试",
  WORKFLOW_TOOL_NEEDS_REVIEW: "外部工具的配置已变化，请重新选择工具并确认参数",
  WORKFLOW_ACTION_OUTCOME_UNKNOWN: "外部操作结果暂时无法确认，请先到对应平台核对，避免重复执行",
  WORKFLOW_QQ_ACCOUNT_REQUIRED: "请先登录后再连接 QQ",
  WORKFLOW_QQ_NOT_BOUND: "还没有连接 QQ，请先在“连接与账号”中完成绑定",
  WORKFLOW_QQ_CHANNEL_UNAVAILABLE: "QQ 通知服务暂时不可用，请稍后再试",
  WORKFLOW_QQ_C2C_DISABLED: "当前 QQ 机器人没有开启私聊通知，请联系管理员",
  WORKFLOW_QQ_MESSAGE_EMPTY: "没有可发送的内容，请连接一个有结果的上一步",
  WORKFLOW_QQ_SEND_FAILED: "QQ 没有接受这次发送请求，请稍后再试",
  WORKFLOW_WEIXIN_ACCOUNT_REQUIRED: "请先登录后再连接微信",
  WORKFLOW_WEIXIN_NOT_BOUND: "还没有连接微信，请先用当前账号绑定微信",
  WORKFLOW_WEIXIN_RECONNECT_REQUIRED: "微信连接已失效，请重新连接微信",
  WORKFLOW_WEIXIN_CHANNEL_UNAVAILABLE: "微信通知服务暂时不可用，请稍后再试",
  WORKFLOW_WEIXIN_CONTEXT_REQUIRED: "请先在微信里给智策发送一条消息，再回来发布或运行工作流",
  OFFICIAL_EMAIL_NOT_CONFIGURED: "系统还没有配置官方通知通道，请联系管理员",
  NOTIFICATION_EMAIL_NOT_VERIFIED: "我的邮箱尚未验证，请先在“连接与账号”中完成验证",
  CONNECTION_PROVIDER_UNSUPPORTED: "当前没有可用的个人邮件服务，请先绑定发送账号",
  CONNECTION_REAUTHORIZATION_REQUIRED: "邮件账号授权已失效，请重新绑定后再试",
  REQUEST_FAILED: "请求没有成功。请刷新后重试；若仍失败，请查看具体步骤提示",
  AUTH_PERMISSION_DENIED: "当前账号没有使用这项功能的权限，请联系管理员开通",
  WORKFLOW_ACCESS_DENIED: "当前账号不能访问这个工作流",
};

function parsedToolName(name: string): { provider: string; operation: string } {
  const match = name.match(/^mcp__([^_]+(?:-[^_]+)*)__([^]+)$/);
  return match ? { provider: match[1], operation: match[2] } : { provider: "", operation: name };
}

function shortChineseDescription(description: string): string {
  const clean = description.replace(/`[^`]+`/g, "").replace(/\s+/g, " ").trim();
  const first = clean.split(/[。；;\n]/)[0]?.split(/[，,]/)[0]?.trim() || "";
  return /[\u3400-\u9fff]/.test(first) && first.length <= 24 ? first : "";
}

export function workflowToolProvider(name: string): string {
  const provider = parsedToolName(name).provider;
  return providerNames[provider] || "扩展服务";
}

export function workflowToolName(tool: Pick<WorkflowToolCatalogItem, "name" | "description"> | string): string {
  const name = typeof tool === "string" ? tool : tool.name;
  if (toolNames[name]) return toolNames[name];
  if (typeof tool !== "string") {
    const description = shortChineseDescription(tool.description);
    if (description) return description;
  }
  const provider = workflowToolProvider(name);
  return provider === "扩展服务" ? "扩展工具" : `${provider}工具`;
}

export function workflowFieldName(field: string, schema: Record<string, unknown> = {}): string {
  if (fieldNames[field]) return fieldNames[field];
  const description = shortChineseDescription(String(schema.description || ""));
  return description || "配置项";
}

export function workflowFieldHint(field: string, schema: Record<string, unknown> = {}): string {
  if (fieldHints[field]) return fieldHints[field];
  const description = shortChineseDescription(String(schema.description || ""));
  return description || `请填写${workflowFieldName(field, schema)}`;
}

export function workflowToolInputFields(tool: Pick<WorkflowToolCatalogItem, "name" | "parameters">): WorkflowInputField[] {
  const properties = tool.parameters.properties || {};
  const required = new Set(tool.parameters.required || []);
  let entries = Object.entries(properties);
  if (tool.name === "mcp__12306__get-tickets") {
    entries = [
      ["departure_name", { type: "string" }],
      ["arrival_name", { type: "string" }],
      ["date", { type: "string" }],
      ["trainFilterFlags", { type: "string" }],
    ];
    required.clear();
    ["departure_name", "arrival_name", "date"].forEach((key) => required.add(key));
  } else if (tool.name === "mcp__amap-maps__maps_text_search") {
    entries = [
      ["keywords", { type: "string" }],
      ["city", { type: "string" }],
    ];
    required.clear();
    required.add("keywords");
  } else if (tool.name === "mcp__tavily__tavily_search") {
    entries = [
      ["query", { type: "string" }],
      ["search_depth", { type: "string" }],
      ["topic", { type: "string" }],
      ["max_results", { type: "integer", minimum: 1, maximum: 20 }],
    ];
    required.clear();
    required.add("query");
  } else if (tool.name === "mcp__open-meteo__get_forecast") {
    entries = [["place_name", { type: "string" }], ["forecast_days", { type: "integer", minimum: 1, maximum: 16 }]];
    required.add("place_name");
  } else if (tool.name === "mcp__open-meteo__get_historical_weather") {
    entries = [["place_name", { type: "string" }], ...entries.filter(([key]) => !["latitude", "longitude", "timezone"].includes(key))];
    required.add("place_name");
  }
  if (tool.name === "mcp__xhs-readonly__get_note_detail") {
    entries = [["note_url", { type: "string" }], ...entries.filter(([key]) => !["feed_id", "xsec_token"].includes(key))];
    required.add("note_url");
  }
  return entries.map(([key, schema]) => {
    const type = String(schema.type || "string");
    const dateField = key === "date" || key.endsWith("_date");
    return {
      key,
      label: workflowFieldName(key, schema),
      hint: workflowFieldHint(key, schema),
      type: key === "note_url" ? "url" : dateField ? "date" : type === "boolean" ? "boolean" : type === "number" || type === "integer" ? "number" : "text",
      required: required.has(key),
      options: fieldOptions[key],
      min: typeof schema.minimum === "number" ? schema.minimum : undefined,
      max: typeof schema.maximum === "number" ? schema.maximum : undefined,
    };
  });
}

export function workflowToolHelp(name: string, fallback = ""): string {
  if (name === "mcp__12306__get-tickets") return "填写出发城市、到达城市和日期即可；系统会自动匹配铁路站码。具体车站可在返回结果中选择。";
  if (name === "mcp__amap-maps__maps_text_search") return "输入地点名称或关键词，可选填城市来缩小范围；不需要 POI 编号或类型代码。";
  if (name === "mcp__tavily__tavily_search") return "像搜索引擎一样输入问题，可选择快速或深入搜索。";
  if (name === "mcp__open-meteo__get_forecast") return "只需填写地点，默认查询今天和明天；系统会自动查找坐标和日期。";
  if (name === "mcp__open-meteo__get_historical_weather") return "填写地点和历史日期即可，系统会自动查找对应坐标。";
  if (name === "mcp__xhs-readonly__search_notes") return "按关键词搜索公开笔记，可选择排序和内容类型。";
  if (name === "mcp__xhs-readonly__get_note_detail") return "粘贴小红书笔记链接即可读取详情，不需要填写内部编号。";
  return fallback;
}

export function workflowToolResultSummary(output: unknown): string {
  if (output === null || output === undefined) return "查询完成，但没有返回内容。";
  if (typeof output === "string") {
    try { return workflowToolResultSummary(JSON.parse(output)); }
    catch { return output.slice(0, 240); }
  }
  if (Array.isArray(output)) return `查询成功，共返回 ${output.length} 条结果。`;
  if (typeof output !== "object") return String(output);
  const value = output as Record<string, unknown>;
  if (Array.isArray(value.pois)) {
    const names = value.pois.slice(0, 3).map((item) => typeof item === "object" && item ? String((item as Record<string, unknown>).name || "") : "").filter(Boolean);
    return `找到 ${value.pois.length} 个地点${names.length ? `：${names.join("、")}` : ""}。`;
  }
  if (Array.isArray(value.results)) {
    const titles = value.results.slice(0, 3).map((item) => typeof item === "object" && item ? String((item as Record<string, unknown>).title || "") : "").filter(Boolean);
    return `查询成功，共返回 ${value.results.length} 条结果${titles.length ? `：${titles.join("、")}` : ""}。`;
  }
  if (Array.isArray(value.feeds)) {
    const titles = value.feeds.slice(0, 3).map((item) => {
      if (!item || typeof item !== "object") return "";
      const card = (item as Record<string, unknown>).noteCard;
      return card && typeof card === "object" ? String((card as Record<string, unknown>).displayTitle || "") : "";
    }).filter(Boolean);
    return `找到 ${value.feeds.length} 篇公开笔记${titles.length ? `：${titles.join("、")}` : ""}。`;
  }
  if (value.data && typeof value.data === "object") return workflowToolResultSummary(value.data);
  if (value.daily && typeof value.daily === "object") {
    const daily = value.daily as Record<string, unknown>;
    const dates = Array.isArray(daily.time) ? daily.time : [];
    const highs = Array.isArray(daily.temperature_2m_max) ? daily.temperature_2m_max : [];
    const lows = Array.isArray(daily.temperature_2m_min) ? daily.temperature_2m_min : [];
    const rain = Array.isArray(daily.precipitation_probability_max) ? daily.precipitation_probability_max : [];
    if (dates.length) return `${dates[0]}：最高 ${highs[0] ?? "--"}℃，最低 ${lows[0] ?? "--"}℃，最高降水概率 ${rain[0] ?? "--"}%${dates.length > 1 ? `；共 ${dates.length} 天` : ""}。`;
  }
  if (typeof value.text === "string") {
    try { return workflowToolResultSummary(JSON.parse(value.text)); }
    catch { /* Plain text responses continue through the user-facing summaries below. */ }
    const trainCount = value.text.split("\n").filter((line) => /^[A-Z0-9]+\(实际车次/.test(line)).length;
    if (trainCount) return `找到 ${trainCount} 趟列车，车次、时间、票价和余票详情已收起。`;
    const firstLine = value.text.split("\n").find((line) => line.trim())?.trim() || "";
    if (firstLine) return firstLine.slice(0, 180);
  }
  const place = String(value.name || value.city || "");
  const location = String(value.location || "");
  if (place || location) return `查询成功${place ? `：${place}` : ""}${location ? `（${location}）` : ""}。`;
  if (value.status === "success" || value.status === "succeeded") return "查询成功，已收到结果。";
  return "查询成功，详细响应已收起。";
}

export function workflowStatusLabel(status?: string): string {
  return workflowStatuses[String(status || "draft")] || "状态未知";
}

export function workflowVersionLabel(version?: number): string {
  return `版本 ${Math.max(1, Number(version) || 1)}`;
}

export function workflowRunStatusLabel(status?: string): string {
  return runStatuses[String(status || "queued")] || "状态未知";
}

export function workflowTriggerLabel(trigger?: string): string {
  return triggerTypes[String(trigger || "manual")] || "自动触发";
}

export function workflowNodeTypeLabel(type?: string): string {
  return nodeTypes[type as WorkflowNodeType] || "工作流步骤";
}

export function workflowErrorLabel(code?: string | null): string {
  if (!code) return "";
  return workflowErrors[code] || "执行时遇到问题，请检查该步骤配置后重试";
}

export function workflowTimeLabel(value?: string | null): string {
  if (!value) return "尚未开始";
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return "时间未知";
  return new Intl.DateTimeFormat("zh-CN", {
    month: "numeric",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).format(parsed);
}

export function workflowRunOutputLabel(value?: string | null): string {
  if (!value) return "";
  try {
    const parsed: unknown = JSON.parse(value);
    if (typeof parsed === "string") return parsed;
    if (parsed && typeof parsed === "object" && typeof (parsed as Record<string, unknown>).text === "string") {
      return String((parsed as Record<string, unknown>).text);
    }
  } catch {
    return value;
  }
  return value;
}
