<script setup lang="ts">
import { Background } from "@vue-flow/background";
import { Controls } from "@vue-flow/controls";
import { MiniMap } from "@vue-flow/minimap";
import { BaseEdge, EdgeLabelRenderer, Handle, MarkerType, PanOnScrollMode, Position, VueFlow, getBezierPath, useVueFlow, type Connection } from "@vue-flow/core";
import { ArrowDown, ArrowLeft, ArrowRight, ArrowUp, CirclePlay, History, PanelLeftClose, PanelLeftOpen, Pencil, Plus, Redo2, RefreshCw, Rocket, Save, Trash2, Undo2, WandSparkles, X } from "@lucide/vue";
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";

import { ApiError, api } from "@/api/client";
import type { WorkflowCapabilities, WorkflowDefinitionV1, WorkflowEmailConnection, WorkflowNodeType, WorkflowRun, WorkflowSummary, WorkflowToolCatalogItem } from "@/api/types";
import DateTimePicker from "@/components/DateTimePicker.vue";
import QuickPreferences from "@/components/QuickPreferences.vue";
import { errorMessage } from "@/stores/chat";
import { defaultDefinition, useWorkflowStore } from "@/stores/workflows";
import { useUiStore } from "@/stores/ui";
import { uiText } from "@/i18n";
import { autoLayoutWorkflow, canAddNodeType, cloneSnapshot, cloneWorkflowJson, directPredecessorId, graphInputReference, insertNodeOnEdge, upstreamVariables, withGraphBoundInputs, workflowConnectionIssue, type EditorSnapshot, type WorkflowConnectionIssue } from "@/utils/workflow-editor";
import { workflowErrorLabel, workflowNodeTypeLabel, workflowRunStatusLabel, workflowStatusLabel, workflowTimeLabel, workflowToolHelp, workflowToolInputFields, workflowToolName, workflowToolProvider, workflowToolResultSummary, workflowTriggerLabel, workflowVersionLabel } from "@/utils/workflow-presentation";
import { instantiateWorkflowTemplate, workflowStarterTemplates, type WorkflowStarterTemplate } from "@/utils/workflow-templates";

const router = useRouter();
const route = useRoute();
const store = useWorkflowStore();
const ui = useUiStore();
const { endConnection, fitView, getViewport, screenToFlowCoordinate, setViewport } = useVueFlow();
// Vue's v-model needs a mutable heterogeneous config bag for node inspectors.
// eslint-disable-next-line @typescript-eslint/no-explicit-any
interface CanvasNode { id: string; type: string; label: string; position: { x: number; y: number }; data: { config: Record<string, any> } }
interface CanvasEdge { id: string; source: string; target: string; sourceHandle?: string | null; targetHandle?: string | null }
interface ConfigurationIssue { message: string; nodeId?: string }
interface ClickConnectionStart { event?: MouseEvent; nodeId?: string; handleId: string | null }
interface ClickConnectionSource { nodeId: string; handleId: string; label: string }
type RunNode = NonNullable<WorkflowRun["nodes"]>[number];
interface RunSummaryBlock { id: string; label: string; text: string }
const RUN_SUMMARY_LIMIT = 500;
const nodes = ref<CanvasNode[]>([]);
const edges = ref<CanvasEdge[]>([]);
const selectedId = ref("");
const selectedEdgeId = ref("");
const inspectorOpen = ref(false);
const failure = ref("");
const actionConsent = ref(false);
const busy = ref("");
const workflowTools = ref<WorkflowToolCatalogItem[]>([]);
const emailConnections = ref<WorkflowEmailConnection[]>([]);
const workflowCapabilities = ref<WorkflowCapabilities>({});
const connectionsFailure = ref("");
const toolsLoading = ref(false);
const toolTestResult = ref("");
const toolTestSummary = ref("");
const undoStack = ref<EditorSnapshot[]>([]);
const redoStack = ref<EditorSnapshot[]>([]);
const sidebarCollapsed = ref(typeof window !== "undefined" && window.innerWidth <= 760);
const editorView = ref<"canvas" | "runs">("canvas");
const expandedRunStep = ref("");
const expandedRunSummaries = ref<Set<string>>(new Set());
const copiedRunSummary = ref("");
const addMenu = ref<{ x: number; y: number; edgeId: string; flowPosition: { x: number; y: number } } | null>(null);
const connectionFeedback = ref("");
const connectionToast = ref("");
const clickConnectionActive = ref(false);
const clickConnectionSource = ref<ClickConnectionSource | null>(null);
const clickConnectionPreview = ref<{ fromX: number; fromY: number; toX: number; toY: number } | null>(null);
const readinessOpen = ref(false);
const copiedNode = ref<CanvasNode | null>(null);
const savedCanvasSignature = ref("");
const saveConfirmed = ref(false);
const workflowCanvas = ref<HTMLElement | null>(null);
const inspectorBubbleStyle = ref<Record<string, string>>({});
const inspectorBubblePlacement = ref<"right" | "left" | "bottom">("right");
let nodeSequence = 0;
let savedBaselineTimer: ReturnType<typeof setTimeout> | undefined;
let saveConfirmationTimer: ReturnType<typeof setTimeout> | undefined;
let connectionToastTimer: ReturnType<typeof setTimeout> | undefined;
let inspectorBubbleFrame: number | undefined;
const workflowId = computed(() => typeof route.params.workflowId === "string" ? route.params.workflowId : "");
const isEditorRoute = computed(() => Boolean(workflowId.value));

const corePalette: Array<{ type: WorkflowNodeType; zh: string; en: string }> = [
  { type: "mcp_query", zh: "获取信息", en: "Get information" },
  { type: "llm_transform", zh: "智能处理", en: "Process with AI" },
  { type: "condition", zh: "条件分支", en: "Condition" },
  { type: "template", zh: "发送结果", en: "Send result" },
];
const palette = computed<Array<{ type: WorkflowNodeType; zh: string; en: string }>>(() => [
  ...corePalette,
  ...(workflowTools.value.some((tool) => tool.kind === "action") ? [{ type: "mcp_action" as WorkflowNodeType, zh: "执行操作", en: "Take action" }] : []),
]);
const renderNodeTypes: WorkflowNodeType[] = ["schedule_trigger", "mcp_query", "mcp_action", "llm_transform", "template", "condition", "official_notification", "personal_email", "qq_notification", "weixin_notification"];
const outputNodeTypes = new Set<WorkflowNodeType>(["template", "official_notification", "personal_email", "qq_notification", "weixin_notification"]);
const triggerPalette = { type: "schedule_trigger" as WorkflowNodeType, zh: "开始", en: "Start" };
const selected = computed(() => nodes.value.find((node) => node.id === selectedId.value));
const hasAction = computed(() => nodes.value.some((node) => ["mcp_action", "personal_email", "qq_notification", "weixin_notification"].includes(String(node.type))));
const hasToolAction = computed(() => nodes.value.some((node) => node.type === "mcp_action"));
const hasEmailAction = computed(() => nodes.value.some((node) => node.type === "personal_email"));
const hasQqAction = computed(() => nodes.value.some((node) => node.type === "qq_notification"));
const hasWeixinAction = computed(() => nodes.value.some((node) => node.type === "weixin_notification"));
const actionConsentLabel = computed(() => {
  if (hasToolAction.value && (hasEmailAction.value || hasQqAction.value || hasWeixinAction.value)) return tr("我确认允许按以上配置发送通知并执行外部操作", "I confirm the configured notifications and external actions");
  if ([hasEmailAction.value, hasQqAction.value, hasWeixinAction.value].filter(Boolean).length > 1) return tr("我确认允许按以上配置发送邮件或消息通知", "I confirm the configured email or message notifications");
  if (hasWeixinAction.value) return tr("我确认允许将工作流结果发送到我已绑定的微信", "I confirm sending workflow results to my bound Weixin");
  if (hasQqAction.value) return tr("我确认允许将工作流结果发送到我已绑定的 QQ", "I confirm sending workflow results to my bound QQ");
  if (hasEmailAction.value) return tr("我确认允许使用所选账号向上述收件人发送邮件", "I confirm sending this email");
  return tr("我确认允许执行上面选择的外部操作", "I confirm this external action");
});
const selectedTool = computed(() => workflowTools.value.find((tool) => tool.name === selected.value?.data.config.tool_name));
const selectedToolFields = computed(() => selectedTool.value ? workflowToolInputFields(selectedTool.value) : []);
const selectedToolValid = computed(() => !!selectedTool.value && selectedTool.value.schema_hash === selected.value?.data.config.input_schema_hash);
const visibleTools = computed(() => workflowTools.value.filter((tool) => tool.kind === (selected.value?.type === "mcp_action" ? "action" : "query")));
const informationTaskDefinitions = [
  { id: "weather", icon: "☀", zh: "查天气", en: "Weather", help: "输入一个城市、区县或地点" , tool: "mcp__open-meteo__get_forecast" },
  { id: "train", icon: "🚆", zh: "查火车票", en: "Train tickets", help: "输入出发地、目的地和日期", tool: "mcp__12306__get-tickets" },
  { id: "place", icon: "⌖", zh: "搜地点", en: "Places", help: "搜索景点、餐厅或具体地址", tool: "mcp__amap-maps__maps_text_search" },
  { id: "web", icon: "◎", zh: "搜网页", en: "Web search", help: "搜索最新网页资料", tool: "mcp__tavily__tavily_search" },
  { id: "xhs", icon: "小", zh: "搜小红书", en: "Xiaohongshu", help: "查攻略、评价和避坑经验", tool: "mcp__xhs-readonly__search_notes" },
] as const;
const informationTasks = computed(() => informationTaskDefinitions.map((task) => ({
  ...task,
  item: workflowTools.value.find((tool) => tool.kind === "query" && tool.name === task.tool),
})).filter((task) => Boolean(task.item)));
const configurationIssueItems = computed<ConfigurationIssue[]>(() => {
  const issues: ConfigurationIssue[] = [];
  const addIssue = (message: string, nodeId?: string) => issues.push({ message, nodeId });
  if (nodes.value.length <= 1) addIssue(tr("至少添加一个处理步骤", "Add at least one processing step"));
  for (const node of nodes.value.filter((item) => ["mcp_query", "mcp_action"].includes(item.type))) {
    const name = String(node.data.config.tool_name || "");
    const tool = workflowTools.value.find((item) => item.name === name);
    if (!name || !tool) {
      addIssue(tr(`“${node.label}”还没有选择要获取的信息`, `Choose what to get for “${node.label}”`), node.id);
      continue;
    }
    if (tool.schema_hash !== node.data.config.input_schema_hash) {
      addIssue(tr(`“${node.label}”的参数规则已更新，请重新选择工具`, `Tool schema changed for “${node.label}”; select it again`), node.id);
      continue;
    }
    const args = typeof node.data.config.arguments === "object" && node.data.config.arguments !== null ? node.data.config.arguments as Record<string, unknown> : {};
    const missing = workflowToolInputFields(tool).filter((field) => {
      if (!field.required) return false;
      if (field.key === "place_name" && args.latitude !== undefined && args.longitude !== undefined) return false;
      return args[field.key] === undefined || args[field.key] === null || args[field.key] === "";
    });
    if (missing.length) addIssue(tr(`“${node.label}”还需填写：${missing.map((field) => field.label).join("、")}`, `Complete required fields for “${node.label}”`), node.id);
  }
  const trigger = nodes.value.find((item) => item.type === "schedule_trigger");
  if (trigger && trigger.data.config.schedule_mode === "every" && Number(trigger.data.config.interval_amount || 0) < 1) addIssue(tr("请填写自动运行的间隔", "Choose an interval"), trigger.id);
  if (trigger && ["daily", "weekly", "monthly"].includes(String(trigger.data.config.schedule_mode)) && !trigger.data.config.time_of_day) addIssue(tr("请选择自动运行的时间", "Choose a run time"), trigger.id);
  if (trigger?.data.config.schedule_mode === "weekly" && trigger.data.config.weekday === undefined) addIssue(tr("请选择每周哪一天运行", "Choose a weekday"), trigger.id);
  if (trigger?.data.config.schedule_mode === "monthly" && !trigger.data.config.day_of_month) addIssue(tr("请选择每月几号运行", "Choose a day of month"), trigger.id);
  if (trigger?.data.config.schedule_mode === "once" && !trigger.data.config.run_at) addIssue(tr("请选择一次性运行时间", "Choose the one-time run date"), trigger.id);
  const incoming = new Set(edges.value.map((edge) => edge.target));
  for (const node of nodes.value.filter((item) => item.type !== "schedule_trigger")) {
    if (!incoming.has(node.id)) addIssue(tr(`“${node.label}”还没有连接到前面的步骤`, `Connect “${node.label}” to a previous step`), node.id);
    const config = node.data.config;
    const directInput = graphInputReference(editorSnapshot.value, node.id);
    if ((node.type === "llm_transform" || outputNodeTypes.has(node.type as WorkflowNodeType)) && incoming.has(node.id) && !directInput) addIssue(tr(`“${node.label}”只能连接一个上一步`, `Connect exactly one previous step to “${node.label}”`), node.id);
    if (node.type === "llm_transform" && !config.instruction) addIssue(tr(`“${node.label}”的整理方式还不完整`, `Complete the task for “${node.label}”`), node.id);
    if (node.type === "template" && !config.content && !directInput) addIssue(tr(`“${node.label}”需要连接一个上一步`, `Connect a previous step to “${node.label}”`), node.id);
    if (node.type === "condition" && config.check_mode !== "status" && (!config.left || !config.operator || (config.operator !== "is_empty" && (config.right === "" || config.right === undefined)))) addIssue(tr(`“${node.label}”的判断条件还不完整`, `Complete the condition for “${node.label}”`), node.id);
    if (node.type === "condition" && config.check_mode === "status") {
      const sourceId = directPredecessorId(editorSnapshot.value, node.id);
      const source = nodes.value.find((item) => item.id === sourceId);
      if (!sourceId || config.status_node_id !== sourceId) addIssue(tr(`“${node.label}”需要重新选择检查上一步状态`, `Select the direct previous step again for “${node.label}”`), node.id);
      if (config.retry_on_failure && !["mcp_query", "llm_transform", "template"].includes(String(source?.type || ""))) addIssue(tr(`“${node.label}”的上一步会产生外部操作，不能自动重试`, `The previous step has external effects and cannot be retried`), node.id);
      if (config.retry_on_failure && (Number(config.max_attempts) < 1 || Number(config.max_attempts) > 5)) addIssue(tr(`“${node.label}”的最大尝试次数应为 1 到 5`, `Maximum attempts for “${node.label}” must be 1 to 5`), node.id);
    }
    if (node.type === "condition") {
      const branches = new Set(edges.value.filter((edge) => edge.source === node.id).map((edge) => edge.sourceHandle));
      if (!branches.has("true") || !branches.has("false")) addIssue(tr(`“${node.label}”需要分别连接“是”和“否”两个后续步骤`, `Connect both branches for “${node.label}”`), node.id);
    }
    if (node.type === "official_notification" && (!config.subject || (!config.content && !directInput))) addIssue(tr(`“${node.label}”请填写标题并连接上一步`, `Complete the subject and connect a previous step`), node.id);
    if (node.type === "official_notification" && workflowCapabilities.value.official_notification?.available === false) addIssue(workflowErrorLabel(workflowCapabilities.value.official_notification.code), node.id);
    if (node.type === "personal_email" && (!config.connection_id || !config.to || !config.subject || (!config.content && !directInput))) addIssue(tr(`“${node.label}”请选择发送账号、收件人并连接上一步`, `Complete email fields and connect a previous step`), node.id);
    if (node.type === "qq_notification" && (!config.content && !directInput)) addIssue(tr(`“${node.label}”需要连接一个有结果的上一步`, `Connect a previous result to “${node.label}”`), node.id);
    if (node.type === "qq_notification" && workflowCapabilities.value.qq_notification?.available !== true) addIssue(workflowErrorLabel(workflowCapabilities.value.qq_notification?.code || "WORKFLOW_QQ_CHANNEL_UNAVAILABLE"), node.id);
    if (node.type === "weixin_notification" && (!config.content && !directInput)) addIssue(tr(`“${node.label}”需要连接一个有结果的上一步`, `Connect a previous result to “${node.label}”`), node.id);
    if (node.type === "weixin_notification" && workflowCapabilities.value.weixin_notification?.available !== true) addIssue(workflowErrorLabel(workflowCapabilities.value.weixin_notification?.code || "WORKFLOW_WEIXIN_CHANNEL_UNAVAILABLE"), node.id);
  }
  if (hasAction.value && !actionConsent.value) addIssue(tr("请确认允许按以上配置发送消息、邮件或执行外部操作", "Confirm the configured notifications or external actions"), nodes.value.find((node) => ["mcp_action", "personal_email", "qq_notification", "weixin_notification"].includes(node.type))?.id);
  return issues;
});
const configurationIssues = computed(() => configurationIssueItems.value.map((issue) => issue.message));
const incompleteNodeIds = computed(() => new Set(configurationIssueItems.value.map((issue) => issue.nodeId).filter((id): id is string => Boolean(id))));
const selectedConfigurationIssues = computed(() => configurationIssueItems.value.filter((issue) => issue.nodeId === selectedId.value));
const editorReady = computed(() => configurationIssues.value.length === 0);
const editorSnapshot = computed<EditorSnapshot>(() => ({
  nodes: nodes.value.map((node) => ({ id: node.id, type: node.type as WorkflowNodeType, title: node.label, position: { ...node.position }, config: cloneWorkflowJson(node.data.config) })),
  edges: edges.value.map((edge) => ({
    id: edge.id,
    source_node_id: edge.source,
    target_node_id: edge.target,
    source_port: "output",
    target_port: String(edge.targetHandle || "input"),
    condition_branch: edge.sourceHandle === "true" || edge.sourceHandle === "false" ? edge.sourceHandle : undefined,
  })),
}));
const graphBoundSnapshot = computed(() => withGraphBoundInputs(editorSnapshot.value));
const variableOptions = computed(() => selected.value ? upstreamVariables(editorSnapshot.value, selected.value.id) : []);
const selectedDirectInputLabel = computed(() => {
  if (!selected.value) return "";
  const sourceId = directPredecessorId(editorSnapshot.value, selected.value.id);
  return nodes.value.find((node) => node.id === sourceId)?.label || "";
});
const activeEmailConnections = computed(() => emailConnections.value.filter((item) => item.status === "active"));
const definition = computed<WorkflowDefinitionV1>(() => ({
  schema_version: 1,
  workflow_id: store.current?.workflow_id,
  owner_user_id: store.current?.owner_user_id,
  name: store.current?.name || "未命名工作流",
  description: store.current?.description || "",
  timezone: store.current?.timezone || "Asia/Shanghai",
  version: store.current?.version || 1,
  status: store.current?.status || "draft",
  nodes: graphBoundSnapshot.value.nodes.map((node) => ({ ...node, config: node.type === "mcp_action" ? { ...node.config, published_consent_at: actionConsent.value ? String(node.config.published_consent_at || new Date().toISOString()) : "" } : ["personal_email", "qq_notification", "weixin_notification"].includes(node.type) ? { ...node.config, send_consent_at: actionConsent.value ? String(node.config.send_consent_at || new Date().toISOString()) : "" } : node.config })),
  edges: editorSnapshot.value.edges,
  required_permissions: requiredWorkflowPermissions(),
  connection_ids: [...new Set(nodes.value.filter((node) => node.type === "personal_email").map((node) => String(node.data.config.connection_id || "")).filter(Boolean))],
}));
const currentCanvasSignature = computed(() => JSON.stringify({
  name: store.current?.name || "",
  description: store.current?.description || "",
  timezone: store.current?.timezone || "Asia/Shanghai",
  nodes: editorSnapshot.value.nodes,
  edges: editorSnapshot.value.edges,
  required_permissions: requiredWorkflowPermissions(),
  connection_ids: definition.value.connection_ids,
  action_consent: actionConsent.value,
}));
const hasUnsavedChanges = computed(() => Boolean(store.current && savedCanvasSignature.value && savedCanvasSignature.value !== currentCanvasSignature.value));
const workflowChangeState = computed(() => {
  if (hasUnsavedChanges.value) return { code: "unsaved", label: tr("未保存修改", "Unsaved changes") };
  if (store.current?.has_unpublished_changes) return { code: "pending", label: tr("待发布", "Ready to publish") };
  return null;
});
const workflowEnabled = computed(() => store.current?.status === "active");
const workflowCanToggle = computed(() => store.current?.active_version != null);
function markCurrentCanvasSaved() {
  savedCanvasSignature.value = currentCanvasSignature.value;
}
function clearSaveConfirmation() {
  saveConfirmed.value = false;
  if (saveConfirmationTimer) clearTimeout(saveConfirmationTimer);
  saveConfirmationTimer = undefined;
}
function confirmSave() {
  saveConfirmed.value = true;
  if (saveConfirmationTimer) clearTimeout(saveConfirmationTimer);
  saveConfirmationTimer = setTimeout(() => { saveConfirmed.value = false; }, 2500);
}

function requiredWorkflowPermissions(): string[] {
  const permissions = new Set(store.current?.required_permissions || ["workflow.use"]);
  permissions.add("workflow.use");
  if (nodes.value.some((node) => node.type === "schedule_trigger" && node.data.config.trigger_type !== "manual")) permissions.add("workflow.schedule");
  if (nodes.value.some((node) => node.type === "official_notification")) permissions.add("workflow.notify.self");
  if (nodes.value.some((node) => node.type === "qq_notification")) permissions.add("workflow.notify.self");
  if (nodes.value.some((node) => node.type === "weixin_notification")) permissions.add("workflow.notify.self");
  if (nodes.value.some((node) => node.type === "personal_email")) permissions.add("workflow.email.send");
  return [...permissions];
}

function tr(zh: string, en: string) { return uiText(ui.language, zh, en); }
function nodeMeta(type: string) {
  const values: Record<string, { icon: string; category: string }> = {
    schedule_trigger: { icon: "▶", category: tr("触发方式", "Trigger") }, mcp_query: { icon: "⌕", category: tr("信息", "Information") },
    mcp_action: { icon: "↗", category: tr("操作", "Action") }, llm_transform: { icon: "✦", category: tr("处理", "AI") },
    template: { icon: "→", category: tr("结果", "Result") }, condition: { icon: "◇", category: tr("判断", "Condition") },
    official_notification: { icon: "→", category: tr("发送结果", "Result") }, personal_email: { icon: "→", category: tr("发送结果", "Result") }, qq_notification: { icon: "QQ", category: tr("发送结果", "Result") }, weixin_notification: { icon: "微", category: tr("发送结果", "Result") },
  };
  return values[type] || { icon: "·", category: type };
}
function paletteHelp(type: WorkflowNodeType): string {
  const values: Partial<Record<WorkflowNodeType, [string, string]>> = {
    mcp_query: ["天气、车票、地点、网页等", "Weather, trains, places, web and more"],
    llm_transform: ["摘要、重点、润色或分类", "Summarize, extract, rewrite or classify"],
    condition: ["根据结果走不同分支", "Choose a branch from a result"],
    template: ["保留结果，或发到微信、QQ、我的邮箱和 SMTP 收件人", "Keep, send to Weixin, QQ, My email, or an SMTP recipient"],
    mcp_action: ["执行已审核的外部操作", "Run a reviewed external action"],
  };
  const value = values[type];
  return value ? tr(value[0], value[1]) : nodeMeta(type).category;
}
function nodeSummary(type: string, config: Record<string, unknown>): string {
  if (type === "schedule_trigger") return workflowTriggerLabel(String(config.trigger_type || "manual"));
  if (type === "mcp_query" || type === "mcp_action") {
    const tool = workflowTools.value.find((item) => item.name === config.tool_name);
    return tool ? workflowToolName(tool) : tr("请选择工具", "Select a tool");
  }
  if (type === "llm_transform") return ({ advice: "生成生活建议", summary: "生成摘要", key_points: "提取重点", rewrite: "润色改写", classify: "分类整理", custom: "自定义整理" } as Record<string, string>)[String(config.task || "")] || tr("请选择整理方式", "Choose a task");
  if (type === "condition") return config.check_mode === "status" ? tr("检查上一步是否成功", "Check previous step status") : ({ eq: "等于", ne: "不等于", contains: "包含", gt: "大于", gte: "大于或等于", lt: "小于", lte: "小于或等于", is_empty: "为空" } as Record<string, string>)[String(config.operator || "")] || tr("配置判断条件", "Configure condition");
  if (outputNodeTypes.has(type as WorkflowNodeType)) return type === "personal_email" ? tr("SMTP 发送", "Send via SMTP") : type === "qq_notification" ? tr("QQ 通知", "QQ notification") : type === "weixin_notification" ? tr("微信通知", "Weixin notification") : type === "official_notification" ? tr("邮箱通知", "Email notification") : tr("仅作记录", "Record only");
  return tr("等待配置", "Not configured");
}
function labelFor(type: string) {
  if (type === "schedule_trigger") return tr("开始", "Start");
  if (outputNodeTypes.has(type as WorkflowNodeType)) return tr("发送结果", "Send result");
  const found = palette.value.find((item) => item.type === type);
  return found ? tr(found.zh, found.en) : tr("步骤", "Step");
}
function defaultConfig(type: WorkflowNodeType): Record<string, unknown> {
  switch (type) {
    case "schedule_trigger": return { trigger_type: "manual", schedule_mode: "manual" };
    case "mcp_query": return { tool_name: "", input_schema_hash: "", arguments: {} };
    case "mcp_action": return { tool_name: "", input_schema_hash: "", arguments: {}, published_consent_at: "" };
    case "llm_transform": return { task: "summary", tone: "plain", output_length: "medium", advice_topics: ["umbrella", "clothing", "travel"], commute_mode: "general", temperature_preference: "normal", additional_instruction: "", instruction: "将上一步数据整理成普通用户可直接阅读的中文纯文本；不要输出 Markdown、JSON、内部字段名或代码；保留关键事实、日期、单位和必要建议", input: "" };
    case "template": return { delivery_mode: "result", content: "", source_ref: "", template: "", variables: {} };
    case "condition": return { check_mode: "value", left: "", operator: "contains", right: "", status_node_id: "", retry_on_failure: false, max_attempts: 3 };
    case "official_notification": return { delivery_mode: "notification", subject: "工作流通知", content: "", source_ref: "", body: "" };
    case "personal_email": return { delivery_mode: "email", connection_id: "", to: "", subject: "工作流结果", content: "", source_ref: "", body: "" };
    case "qq_notification": return { delivery_mode: "qq", content: "", source_ref: "", body: "", send_consent_at: "" };
    case "weixin_notification": return { delivery_mode: "weixin", content: "", source_ref: "", body: "", send_consent_at: "" };
    default: return {};
  }
}
function normalizeTriggerConfig(config: Record<string, unknown>): Record<string, unknown> {
  if (config.schedule_mode) return config;
  const type = String(config.trigger_type || "manual");
  if (type === "manual") return { ...config, schedule_mode: "manual" };
  if (type === "date") return { ...config, schedule_mode: "once" };
  if (type === "interval") {
    const unit = config.days ? "days" : config.hours ? "hours" : config.minutes ? "minutes" : "seconds";
    return { ...config, schedule_mode: "every", interval_unit: unit, interval_amount: Number(config[unit] || 1) };
  }
  const parts = String(config.expression || "0 9 * * *").split(" ");
  const time = `${String(parts[1] || "9").padStart(2, "0")}:${String(parts[0] || "0").padStart(2, "0")}`;
  if (parts[2] !== "*") return { ...config, schedule_mode: "monthly", day_of_month: Number(parts[2]), time_of_day: time };
  if (parts[4] !== "*") return { ...config, schedule_mode: "weekly", weekday: Number(parts[4]), time_of_day: time };
  return { ...config, schedule_mode: "daily", time_of_day: time };
}
function normalizeNodeConfig(type: string, raw: Record<string, unknown>): Record<string, unknown> {
  const config = { ...defaultConfig(type as WorkflowNodeType), ...raw };
  if (type === "schedule_trigger") return normalizeTriggerConfig(config);
  if (type === "llm_transform") {
    if (!raw.task) config.task = "custom";
    config.custom_instruction = raw.task ? String(raw.custom_instruction || "") : String(raw.instruction || "");
  }
  if (type === "template") {
    const variables = typeof raw.variables === "object" && raw.variables ? raw.variables as Record<string, unknown> : {};
    config.content = raw.content || (String(raw.template || "").includes("{{") ? "工作流结果：" : raw.template) || "工作流结果：";
    config.source_ref = raw.source_ref || variables.result || variables.text || "";
    syncTemplateConfig(config);
  }
  if (["official_notification", "personal_email", "qq_notification", "weixin_notification"].includes(type)) {
    config.delivery_mode = type === "personal_email" ? "email" : type === "qq_notification" ? "qq" : type === "weixin_notification" ? "weixin" : "notification";
    config.content = raw.content || (!isResultReference(raw.body) ? raw.body : "") || "";
    config.source_ref = raw.source_ref || (isResultReference(raw.body) ? raw.body : "") || "";
    syncDeliveryConfig(config, type as WorkflowNodeType);
  }
  return config;
}
function normalizedNodeTitle(type: WorkflowNodeType, title?: string): string {
  const legacy = new Set(["手动触发", "触发器", "外部工具查询", "外部工具操作", "智能整理", "内容模板", "条件判断", "官方通知", "个人邮件"]);
  return title && !legacy.has(title) ? title : labelFor(type);
}
function hydrate() {
  const source = store.current;
  if (savedBaselineTimer) clearTimeout(savedBaselineTimer);
  savedCanvasSignature.value = "";
  if (!source) { nodes.value = []; edges.value = []; return; }
  nodes.value = source.nodes.map((node) => ({ id: node.id, type: node.type, label: normalizedNodeTitle(node.type, node.title), position: node.position, data: { config: normalizeNodeConfig(node.type, node.config) } }));
  edges.value = source.edges.map((edge) => ({ id: edge.id, source: edge.source_node_id, target: edge.target_node_id, sourceHandle: edge.condition_branch || edge.source_port, targetHandle: edge.target_port }));
  undoStack.value = [];
  redoStack.value = [];
  addMenu.value = null;
  clickConnectionActive.value = false;
  clickConnectionSource.value = null;
  clickConnectionPreview.value = null;
  readinessOpen.value = false;
  selectedId.value = source.nodes[0]?.id || "";
  selectedEdgeId.value = "";
  actionConsent.value = source.nodes.some((node) => node.type === "mcp_action" ? Boolean(node.config.published_consent_at) : ["personal_email", "qq_notification", "weixin_notification"].includes(node.type) && Boolean(node.config.send_consent_at));
  void nextTick(async () => {
    await nextTick();
    if (source.nodes.length <= 1) void setViewport({ x: 110, y: 110, zoom: 0.72 });
    else void fitView({ padding: 0.25, maxZoom: 0.9 });
  });
  savedBaselineTimer = setTimeout(markCurrentCanvasSaved, 500);
}
function updateInspectorBubblePosition() {
  if (!inspectorOpen.value || !selectedId.value || !workflowCanvas.value) return;
  const canvas = workflowCanvas.value;
  const escapedId = window.CSS?.escape ? window.CSS.escape(selectedId.value) : selectedId.value.replace(/["\\]/g, "\\$&");
  const nodeElement = canvas.querySelector<HTMLElement>(`.vue-flow__node[data-id="${escapedId}"]`);
  if (!nodeElement) return;
  const canvasRect = canvas.getBoundingClientRect();
  const nodeRect = nodeElement.getBoundingClientRect();
  const gap = 12;
  const inset = 12;
  const width = Math.max(240, Math.min(360, canvasRect.width - inset * 2));
  const nodeLeft = nodeRect.left - canvasRect.left;
  const nodeRight = nodeRect.right - canvasRect.left;
  const nodeTop = nodeRect.top - canvasRect.top;
  const nodeBottom = nodeRect.bottom - canvasRect.top;
  let placement: "right" | "left" | "bottom" = "right";
  let left = nodeRight + gap;
  if (left + width > canvasRect.width - inset) {
    placement = "left";
    left = nodeLeft - width - gap;
  }
  if (left < inset) {
    placement = "bottom";
    left = nodeLeft + nodeRect.width / 2 - width / 2;
  }
  left = Math.min(Math.max(inset, left), Math.max(inset, canvasRect.width - width - inset));
  const preferredTop = placement === "bottom" ? nodeBottom + gap : nodeTop - 18;
  const top = Math.min(Math.max(inset, preferredTop), Math.max(inset, canvasRect.height - 170));
  inspectorBubblePlacement.value = placement;
  inspectorBubbleStyle.value = {
    left: `${Math.round(left)}px`,
    top: `${Math.round(top)}px`,
    width: `${Math.round(width)}px`,
    maxHeight: `${Math.max(150, Math.round(canvasRect.height - top - inset))}px`,
  };
}
function scheduleInspectorBubblePosition() {
  if (inspectorBubbleFrame !== undefined) window.cancelAnimationFrame(inspectorBubbleFrame);
  inspectorBubbleFrame = window.requestAnimationFrame(() => {
    inspectorBubbleFrame = undefined;
    updateInspectorBubblePosition();
  });
}
watch(() => store.current?.workflow_id, hydrate, { immediate: true });
watch(() => store.runDetail?.run_id || store.runDetail?.id || "", () => {
  expandedRunStep.value = "";
  expandedRunSummaries.value = new Set();
  copiedRunSummary.value = "";
});
watch([selectedId, inspectorOpen], scheduleInspectorBubblePosition);
watch(() => nodes.value.map((node) => `${node.id}:${node.position.x}:${node.position.y}`).join("|"), scheduleInspectorBubblePosition);
watch(workflowId, async (id) => {
  editorView.value = "canvas";
  if (!id) {
    if (store.current) store.close();
    return;
  }
  if (store.current?.workflow_id === id) return;
  await perform("open", () => store.open(id));
});
async function loadToolCatalog() {
  toolsLoading.value = true;
  try { workflowTools.value = (await api.workflowTools()).items || []; }
  catch (error) { failure.value = errorMessage(error); workflowTools.value = []; }
  finally { toolsLoading.value = false; }
}
async function loadEmailConnections() {
  try {
    const response = await api.workflowEmailConnections();
    emailConnections.value = response.connections || [];
    connectionsFailure.value = "";
  } catch (error) {
    emailConnections.value = [];
    connectionsFailure.value = error instanceof ApiError ? workflowErrorLabel(error.code) : tr("暂时无法读取邮件账号", "Unable to load email accounts");
  }
}
async function loadWorkflowCapabilities() {
  try { workflowCapabilities.value = await api.workflowCapabilities(); }
  catch { workflowCapabilities.value = {}; }
}
function handleWindowFocus() { void loadWorkflowCapabilities(); }
function handleVisibilityChange() {
  if (document.visibilityState === "visible") void loadWorkflowCapabilities();
}
watch(() => ui.settingsOpen, (open, previous) => {
  if (previous && !open) void Promise.all([loadEmailConnections(), loadWorkflowCapabilities()]);
});
onMounted(async () => {
  window.addEventListener("keydown", handleKeyboard);
  window.addEventListener("resize", scheduleInspectorBubblePosition);
  window.addEventListener("focus", handleWindowFocus);
  document.addEventListener("visibilitychange", handleVisibilityChange);
  try {
    await Promise.all([store.loadAll(), loadToolCatalog(), loadEmailConnections(), loadWorkflowCapabilities()]);
    if (workflowId.value && store.current?.workflow_id !== workflowId.value) await store.open(workflowId.value);
  } catch (error) { failure.value = errorMessage(error); }
});
onBeforeUnmount(() => {
  window.removeEventListener("keydown", handleKeyboard);
  window.removeEventListener("resize", scheduleInspectorBubblePosition);
  window.removeEventListener("focus", handleWindowFocus);
  document.removeEventListener("visibilitychange", handleVisibilityChange);
  if (inspectorBubbleFrame !== undefined) window.cancelAnimationFrame(inspectorBubbleFrame);
  if (savedBaselineTimer) clearTimeout(savedBaselineTimer);
  if (saveConfirmationTimer) clearTimeout(saveConfirmationTimer);
  if (connectionToastTimer) clearTimeout(connectionToastTimer);
});

const availableStarterTemplates = computed(() => workflowStarterTemplates.filter((template) => workflowTools.value.some((tool) => tool.name === template.requiredTool)));
async function useTemplate(template: WorkflowStarterTemplate) {
  const timezone = Intl.DateTimeFormat().resolvedOptions().timeZone || "Asia/Shanghai";
  await createAndOpen(instantiateWorkflowTemplate(template, workflowTools.value, timezone, workflowCapabilities.value));
}

async function createAndOpen(workflowDefinition: WorkflowDefinitionV1 = defaultDefinition()) {
  await perform("create", async () => {
    if (store.current && hasUnsavedChanges.value) await saveCurrent();
    await store.create(workflowDefinition);
    if (store.current) await router.push({ name: "workflow-detail", params: { workflowId: store.current.workflow_id } });
    if (window.matchMedia?.("(max-width: 760px)").matches) sidebarCollapsed.value = true;
  });
}

async function openWorkflow(id: string) {
  await perform("open", async () => {
    if (store.current?.workflow_id !== id && hasUnsavedChanges.value) await saveCurrent();
    await store.open(id);
    await router.push({ name: "workflow-detail", params: { workflowId: id } });
    if (window.matchMedia?.("(max-width: 760px)").matches) sidebarCollapsed.value = true;
  });
}

function workflowSummaryMeta(item: WorkflowSummary): string {
  const details = [workflowStatusLabel(item.status)];
  if (item.next_run_at) details.push(`${tr("下次", "Next")} ${workflowTimeLabel(item.next_run_at)}`);
  else if (item.has_unpublished_changes) details.push(tr("有修改待发布", "Changes to publish"));
  else details.push(workflowVersionLabel(item.version));
  return details.join(" · ");
}

async function deleteOverviewWorkflow(item: { workflow_id: string; name: string }) {
  if (!window.confirm(tr(`确认删除“${item.name}”？运行历史也会删除。`, `Delete “${item.name}” and its run history?`))) return;
  await perform("delete", async () => {
    await api.deleteWorkflow(item.workflow_id);
    if (store.current?.workflow_id === item.workflow_id) {
      store.close();
      await router.push({ name: "workflows" });
    }
    await store.loadAll();
  });
}

async function perform(name: string, operation: () => Promise<void>) {
  busy.value = name; failure.value = "";
  try { await operation(); }
  catch (error) { failure.value = error instanceof ApiError ? workflowErrorLabel(error.code) : errorMessage(error); }
  finally { busy.value = ""; }
}
async function saveCurrent() {
  await store.save(definition.value);
  markCurrentCanvasSaved();
}
async function saveFromToolbar() {
  clearSaveConfirmation();
  await saveCurrent();
  confirmSave();
}
async function publishCurrent() {
  await store.publish(definition.value);
  markCurrentCanvasSaved();
}
async function runCurrent() {
  await store.runNow(definition.value);
  markCurrentCanvasSaved();
  editorView.value = "runs";
}
async function toggleWorkflow() {
  await store.togglePaused();
}
function applySnapshot(snapshot: EditorSnapshot) {
  nodes.value = snapshot.nodes.map((node) => ({ id: node.id, type: node.type, label: node.title || labelFor(node.type), position: { ...node.position }, data: { config: cloneWorkflowJson(node.config) } }));
  edges.value = snapshot.edges.map((edge) => ({ id: edge.id, source: edge.source_node_id, target: edge.target_node_id, sourceHandle: edge.condition_branch || edge.source_port || "output", targetHandle: edge.target_port || "input" }));
}
function recordHistory() {
  const snapshot = cloneSnapshot(editorSnapshot.value);
  const last = undoStack.value.at(-1);
  if (!last || JSON.stringify(last) !== JSON.stringify(snapshot)) undoStack.value.push(snapshot);
  if (undoStack.value.length > 60) undoStack.value.shift();
  redoStack.value = [];
}
function undo() {
  const snapshot = undoStack.value.pop();
  if (!snapshot) return;
  redoStack.value.push(cloneSnapshot(editorSnapshot.value));
  applySnapshot(snapshot);
}
function redo() {
  const snapshot = redoStack.value.pop();
  if (!snapshot) return;
  undoStack.value.push(cloneSnapshot(editorSnapshot.value));
  applySnapshot(snapshot);
}
function createCanvasNode(type: WorkflowNodeType, position?: { x: number; y: number }): CanvasNode {
  nodeSequence += 1;
  const id = `${type}-${Date.now()}-${nodeSequence}`;
  return { id, type, label: labelFor(type), position: position || { x: 220 + nodeSequence * 54, y: 140 + (nodeSequence % 3) * 120 }, data: { config: defaultConfig(type) } };
}
function addNode(type: WorkflowNodeType, options: { position?: { x: number; y: number }; sourceId?: string; sourceHandle?: string; edgeId?: string } = {}) {
  if (!canAddNodeType(type, editorSnapshot.value.nodes)) {
    failure.value = tr("一个工作流只能有一个触发器", "A workflow can only have one trigger");
    return;
  }
  recordHistory();
  const node = createCanvasNode(type, options.position);
  if (options.edgeId) {
    const inserted = insertNodeOnEdge(editorSnapshot.value, options.edgeId, { id: node.id, type, title: node.label, position: node.position, config: node.data.config });
    applySnapshot(inserted);
  } else {
    nodes.value.push(node);
    const previous = options.sourceId ? nodes.value.find((item) => item.id === options.sourceId) : nodes.value.filter((item) => item.id !== node.id && item.type !== "schedule_trigger").at(-1) || nodes.value.find((item) => item.type === "schedule_trigger" && item.id !== node.id);
    if (previous) {
      const usedBranches = new Set(edges.value.filter((edge) => edge.source === previous.id).map((edge) => edge.sourceHandle));
      const sourceHandle = options.sourceHandle || (previous.type === "condition" ? (!usedBranches.has("true") ? "true" : !usedBranches.has("false") ? "false" : "") : "output");
      if (sourceHandle) edges.value.push({ id: `edge-${previous.id}-${node.id}`, source: previous.id, target: node.id, sourceHandle, targetHandle: "input" });
    }
  }
  selectedId.value = node.id;
  selectedEdgeId.value = "";
  inspectorOpen.value = true;
  addMenu.value = null;
}
function addOrSelectTrigger() {
  const existing = nodes.value.find((node) => node.type === "schedule_trigger");
  if (existing) { selectNode(existing.id); return; }
  addNode("schedule_trigger");
}
function connectionIssueText(issue: WorkflowConnectionIssue): string {
  const labels: Record<WorkflowConnectionIssue, [string, string]> = {
    "missing-node": ["找不到要连接的步骤，请刷新后重试", "A connection endpoint is missing; refresh and try again"],
    self: ["不能把步骤连接到自己", "A step cannot connect to itself"],
    "target-trigger": ["“开始”只能放在流程最前面", "Start must remain at the beginning"],
    duplicate: ["该路径已存在", "This path already exists"],
    "invalid-branch": ["请先选择“是”或“否”分支", "Choose the Yes or No branch first"],
    "branch-used": ["这个分支已经连接；如需更换，请先删除原连线", "This branch is already connected; delete it before replacing it"],
    cycle: ["这样会让流程绕回前面的步骤，无法连接", "This connection would create a cycle"],
  };
  return tr(...labels[issue]);
}
function showConnectionToast(message: string): void {
  connectionToast.value = message;
  if (connectionToastTimer) clearTimeout(connectionToastTimer);
  connectionToastTimer = setTimeout(() => { connectionToast.value = ""; }, 1000);
}
function addValidatedConnection(sourceId: string, targetId: string, sourceHandle = "output", targetHandle = "input"): boolean {
  if (targetHandle !== "input") {
    showConnectionToast(tr("非法连接：只能连接到输入点", "Invalid connection: connect to an input port"));
    return false;
  }
  const issue = workflowConnectionIssue(editorSnapshot.value, sourceId, targetId, sourceHandle);
  if (issue) {
    showConnectionToast(connectionIssueText(issue));
    return false;
  }
  recordHistory();
  edges.value.push({ id: `edge-${sourceId}-${targetId}-${Date.now()}`, source: sourceId, target: targetId, sourceHandle, targetHandle });
  selectedEdgeId.value = "";
  connectionFeedback.value = "";
  return true;
}
function connect(connection: Connection) {
  if (!connection.source || !connection.target) return;
  addValidatedConnection(connection.source, connection.target, connection.sourceHandle || "output", connection.targetHandle || "input");
  cancelClickConnection();
}
function handleNodeClick(nodeId: string) {
  if (clickConnectionActive.value && clickConnectionSource.value) {
    completeTapConnection(nodeId);
    return;
  }
  if (window.matchMedia?.("(max-width: 760px)").matches) {
    selectedId.value = nodeId;
    selectedEdgeId.value = "";
    inspectorOpen.value = false;
    return;
  }
  selectNode(nodeId);
}
function selectNode(nodeId: string) {
  selectedId.value = nodeId;
  selectedEdgeId.value = "";
  inspectorOpen.value = true;
}
function focusConfigurationIssue(issue: ConfigurationIssue) {
  if (!issue.nodeId) return;
  readinessOpen.value = false;
  selectNode(issue.nodeId);
}
function selectEdge(edgeId: string) {
  connectionFeedback.value = "";
  selectedId.value = "";
  selectedEdgeId.value = edgeId;
  inspectorOpen.value = false;
  addMenu.value = null;
}
function closeInspectorFromCanvas() {
  inspectorOpen.value = false;
  selectedEdgeId.value = "";
  addMenu.value = null;
}
function handlePaneClick() {
  if (clickConnectionActive.value) {
    clickConnectionPreview.value = null;
    return;
  }
  closeInspectorFromCanvas();
  if (!clickConnectionActive.value) connectionFeedback.value = "";
}
function handleViewportMoveStart() {
  inspectorOpen.value = false;
  selectedEdgeId.value = "";
  addMenu.value = null;
}
function handleCanvasGeometryChange() {
  scheduleInspectorBubblePosition();
  void nextTick(syncClickConnectionPreviewSource);
}
function deleteEdge(edgeId: string) {
  if (!edges.value.some((edge) => edge.id === edgeId)) return;
  recordHistory();
  edges.value = edges.value.filter((edge) => edge.id !== edgeId);
  if (selectedEdgeId.value === edgeId) selectedEdgeId.value = "";
  addMenu.value = null;
}
function deleteSelected() {
  if (selectedEdgeId.value) {
    deleteEdge(selectedEdgeId.value);
    return;
  }
  if (!selected.value || selected.value.type === "schedule_trigger") return;
  recordHistory();
  nodes.value = nodes.value.filter((node) => node.id !== selectedId.value);
  edges.value = edges.value.filter((edge) => edge.source !== selectedId.value && edge.target !== selectedId.value);
  selectedId.value = "";
}
function autoLayout() {
  recordHistory();
  applySnapshot(autoLayoutWorkflow(editorSnapshot.value));
  void nextTick(() => fitView({ padding: 0.24, maxZoom: 0.9, duration: 260 }));
}
function panCanvas(deltaX: number, deltaY: number) {
  const viewport = getViewport();
  void setViewport({ x: viewport.x + deltaX, y: viewport.y + deltaY, zoom: viewport.zoom }, { duration: 160 });
}
function handleCanvasWheel(event: WheelEvent) {
  if (event.ctrlKey || event.metaKey) {
    event.preventDefault();
    event.stopPropagation();
    const viewport = getViewport();
    const canvasRect = workflowCanvas.value?.getBoundingClientRect();
    if (!canvasRect || viewport.zoom <= 0) return;
    const nextZoom = Math.min(1.5, Math.max(0.25, viewport.zoom * Math.exp(-event.deltaY * 0.002)));
    const scale = nextZoom / viewport.zoom;
    const pointerX = event.clientX - canvasRect.left;
    const pointerY = event.clientY - canvasRect.top;
    void setViewport({
      x: pointerX - (pointerX - viewport.x) * scale,
      y: pointerY - (pointerY - viewport.y) * scale,
      zoom: nextZoom,
    });
    return;
  }
  if (!event.altKey) return;
  event.preventDefault();
  event.stopPropagation();
  const viewport = getViewport();
  const delta = Math.abs(event.deltaX) > Math.abs(event.deltaY) ? event.deltaX : event.deltaY;
  void setViewport({ x: viewport.x - delta, y: viewport.y, zoom: viewport.zoom });
}
function openEdgeMenu(edgeId: string, event: MouseEvent) {
  const edge = edges.value.find((item) => item.id === edgeId);
  if (!edge) return;
  const source = nodes.value.find((item) => item.id === edge.source);
  const target = nodes.value.find((item) => item.id === edge.target);
  const flowPosition = source && target
    ? { x: (source.position.x + target.position.x) / 2 + 112, y: (source.position.y + target.position.y) / 2 + 34 }
    : screenToFlowCoordinate({ x: event.clientX, y: event.clientY });
  addMenu.value = { x: event.clientX, y: event.clientY, edgeId, flowPosition };
}
function startClickConnection(params: ClickConnectionStart) {
  if (!params.nodeId) return;
  const handleId = params.handleId || "output";
  if (clickConnectionSource.value?.nodeId === params.nodeId && clickConnectionSource.value.handleId === handleId) {
    cancelClickConnection(params.event);
    return;
  }
  clickConnectionActive.value = true;
  clickConnectionSource.value = { nodeId: params.nodeId, handleId, label: nodes.value.find((node) => node.id === params.nodeId)?.label || tr("当前节点", "Current node") };
  connectionFeedback.value = "";
  inspectorOpen.value = false;
  const canvasRect = workflowCanvas.value?.getBoundingClientRect();
  const handleRect = params.event?.currentTarget instanceof Element
    ? params.event.currentTarget.getBoundingClientRect()
    : null;
  const point = params.event;
  const fromX = handleRect && canvasRect ? handleRect.left + handleRect.width / 2 - canvasRect.left : Number(point?.clientX || 0) - Number(canvasRect?.left || 0);
  const fromY = handleRect && canvasRect ? handleRect.top + handleRect.height / 2 - canvasRect.top : Number(point?.clientY || 0) - Number(canvasRect?.top || 0);
  clickConnectionPreview.value = { fromX, fromY, toX: fromX, toY: fromY };
  syncClickConnectionPreviewSource();
}
function startTapConnection(nodeId: string, handleId: string, event: MouseEvent) {
  if (clickConnectionActive.value) {
    showConnectionToast(tr("连接方向错误", "Invalid connection direction"));
    cancelClickConnection();
    return;
  }
  startClickConnection({ nodeId, handleId, event });
}
function completeTapConnection(nodeId: string) {
  if (!clickConnectionSource.value) return;
  const source = clickConnectionSource.value;
  const connected = addValidatedConnection(source.nodeId, nodeId, source.handleId, "input");
  if (connected) cancelClickConnection();
  else if (connectionFeedback.value) cancelClickConnection(undefined, true);
}
function cancelClickConnection(event?: MouseEvent | TouchEvent, preserveFeedback = false) {
  endConnection(event, true);
  clickConnectionActive.value = false;
  clickConnectionSource.value = null;
  clickConnectionPreview.value = null;
  if (!preserveFeedback) connectionFeedback.value = "";
}
function clickConnectionSourcePoint(): { x: number; y: number } | null {
  const source = clickConnectionSource.value;
  const canvas = workflowCanvas.value;
  if (!source || !canvas) return null;
  const canvasRect = canvas.getBoundingClientRect();
  const sourceNode = Array.from(canvas.querySelectorAll<HTMLElement>(".vue-flow__node")).find((element) => element.dataset.id === source.nodeId || element.dataset.nodeId === source.nodeId);
  if (!sourceNode) return null;
  const sourceHandle = Array.from(sourceNode.querySelectorAll<HTMLElement>('.vue-flow__handle.source, .vue-flow__handle[data-handle-type="source"]')).find((element) => {
    const handleId = element.dataset.handleid || element.dataset.handleId || element.getAttribute("data-handleid") || element.getAttribute("data-handle-id") || "output";
    return handleId === source.handleId;
  });
  if (!sourceHandle) return null;
  const handleRect = sourceHandle.getBoundingClientRect();
  return { x: handleRect.left + handleRect.width / 2 - canvasRect.left, y: handleRect.top + handleRect.height / 2 - canvasRect.top };
}
function syncClickConnectionPreviewSource() {
  if (!clickConnectionPreview.value) return;
  const sourcePoint = clickConnectionSourcePoint();
  if (!sourcePoint) return;
  clickConnectionPreview.value = { ...clickConnectionPreview.value, fromX: sourcePoint.x, fromY: sourcePoint.y };
}
function trackClickConnectionPointer(event: PointerEvent) {
  if (!clickConnectionActive.value || !clickConnectionPreview.value || !workflowCanvas.value) return;
  const canvasRect = workflowCanvas.value.getBoundingClientRect();
  const sourcePoint = clickConnectionSourcePoint();
  clickConnectionPreview.value = {
    ...clickConnectionPreview.value,
    ...(sourcePoint ? { fromX: sourcePoint.x, fromY: sourcePoint.y } : {}),
    toX: event.clientX - canvasRect.left,
    toY: event.clientY - canvasRect.top,
  };
}
function isBlankCanvasEvent(event: MouseEvent): boolean {
  const target = event.target;
  if (!(target instanceof Element) || (!workflowCanvas.value?.contains(target) && !target.closest(".vue-flow__pane"))) return false;
  if (target.closest(".vue-flow__node, .vue-flow__handle, .edge-actions, .canvas-toolbar, .workflow-connection-banner, .mobile-edge-actions, .mobile-edge-selector, input, select, textarea")) return false;
  return true;
}
function handleCanvasContextMenu(event: MouseEvent) {
  if (!clickConnectionActive.value || !isBlankCanvasEvent(event)) return;
  event.preventDefault();
  event.stopPropagation();
  cancelClickConnection(event);
}
function handleCanvasDoubleClick(event: MouseEvent) {
  if (!clickConnectionActive.value || !isBlankCanvasEvent(event)) return;
  event.preventDefault();
  event.stopPropagation();
  cancelClickConnection(event);
}
function selectResult(field: string, event: Event) {
  if (!selected.value) return;
  selected.value.data.config[field] = (event.target as HTMLSelectElement).value;
  if (selected.value.type === "template") syncTemplateConfig(selected.value.data.config);
  if (outputNodeTypes.has(selected.value.type as WorkflowNodeType)) syncDeliveryConfig(selected.value.data.config, selected.value.type as WorkflowNodeType);
}
function setConditionMode(event: Event) {
  if (!selected.value || selected.value.type !== "condition") return;
  const config = selected.value.data.config;
  config.check_mode = (event.target as HTMLSelectElement).value;
  if (config.check_mode === "status") {
    config.status_node_id = directPredecessorId(editorSnapshot.value, selected.value.id) || "";
    config.retry_on_failure = Boolean(config.retry_on_failure);
    config.max_attempts = Math.min(5, Math.max(1, Number(config.max_attempts || 3)));
  }
}
function isResultReference(value: unknown): boolean { return typeof value === "string" && value.startsWith("${nodes."); }
function syncScheduleConfig() {
  if (!selected.value || selected.value.type !== "schedule_trigger") return;
  const config = selected.value.data.config;
  const mode = String(config.schedule_mode || "manual");
  delete config.seconds; delete config.minutes; delete config.hours; delete config.days; delete config.expression;
  if (mode === "manual") config.trigger_type = "manual";
  if (mode === "once") config.trigger_type = "date";
  if (mode === "every") {
    config.trigger_type = "interval";
    const unit = ["minutes", "hours", "days"].includes(String(config.interval_unit)) ? String(config.interval_unit) : "hours";
    config.interval_unit = unit;
    config[unit] = Math.max(1, Number(config.interval_amount || 1));
  }
  if (["daily", "weekly", "monthly"].includes(mode)) {
    config.trigger_type = "cron";
    const [hour, minute] = String(config.time_of_day || "09:00").split(":").map(Number);
    const day = mode === "monthly" ? Math.min(28, Math.max(1, Number(config.day_of_month || 1))) : "*";
    const weekday = mode === "weekly" ? Math.min(6, Math.max(0, Number(config.weekday || 0))) : "*";
    config.expression = `${minute || 0} ${hour || 0} ${day} * ${weekday}`;
  }
}
function changeScheduleMode(event: Event) {
  if (!selected.value) return;
  selected.value.data.config.schedule_mode = (event.target as HTMLSelectElement).value;
  if (!selected.value.data.config.interval_amount) selected.value.data.config.interval_amount = 1;
  if (!selected.value.data.config.interval_unit) selected.value.data.config.interval_unit = "hours";
  if (!selected.value.data.config.time_of_day) selected.value.data.config.time_of_day = "09:00";
  if (selected.value.data.config.weekday === undefined) selected.value.data.config.weekday = 1;
  if (!selected.value.data.config.day_of_month) selected.value.data.config.day_of_month = 1;
  syncScheduleConfig();
}
function scheduleKindFor(mode: unknown): "manual" | "recurring" | "once" {
  const value = String(mode || "manual");
  if (value === "once") return "once";
  if (["every", "daily", "weekly", "monthly"].includes(value)) return "recurring";
  return "manual";
}
function changeScheduleKind(event: Event) {
  if (!selected.value) return;
  const kind = (event.target as HTMLSelectElement).value;
  const currentMode = String(selected.value.data.config.schedule_mode || "manual");
  selected.value.data.config.schedule_mode = kind === "recurring"
    ? (["every", "daily", "weekly", "monthly"].includes(currentMode) ? currentMode : "daily")
    : kind === "once" ? "once" : "manual";
  if (!selected.value.data.config.interval_amount) selected.value.data.config.interval_amount = 1;
  if (!selected.value.data.config.interval_unit) selected.value.data.config.interval_unit = "hours";
  if (!selected.value.data.config.time_of_day) selected.value.data.config.time_of_day = "09:00";
  if (selected.value.data.config.weekday === undefined) selected.value.data.config.weekday = 1;
  if (!selected.value.data.config.day_of_month) selected.value.data.config.day_of_month = 1;
  syncScheduleConfig();
}
function updateScheduleTime(value: string) {
  if (!selected.value || selected.value.type !== "schedule_trigger") return;
  selected.value.data.config.time_of_day = value;
  syncScheduleConfig();
}
function updateSingleRunTime(value: string) {
  if (!selected.value || selected.value.type !== "schedule_trigger") return;
  selected.value.data.config.run_at = value;
  syncScheduleConfig();
}
function syncTransformInstruction() {
  if (!selected.value || selected.value.type !== "llm_transform") return;
  const config = selected.value.data.config;
  const tasks: Record<string, string> = { summary: "生成摘要", key_points: "提取关键事实并用要点列出", rewrite: "润色改写，保持原意", classify: "按主题分类整理" };
  const tones: Record<string, string> = { plain: "使用清晰自然的中文", professional: "使用专业正式的中文", friendly: "使用亲切易懂的中文" };
  const lengths: Record<string, string> = { short: "最多 3 行、90 个汉字", medium: "最多 5 行、180 个汉字", long: "最多 8 行、350 个汉字" };
  if (config.task === "advice") {
    const topicLabels: Record<string, string> = { umbrella: "是否需要带伞", clothing: "适合穿什么", travel: "是否适合出行以及需要避开的时段" };
    const commuteLabels: Record<string, string> = { general: "一般出行", walk: "步行", bicycle: "骑行", transit: "公交或地铁", drive: "开车" };
    const preferenceLabels: Record<string, string> = { cold: "比较怕冷", normal: "体感正常", hot: "比较怕热" };
    const topics = (Array.isArray(config.advice_topics) ? config.advice_topics : []).map((item) => topicLabels[String(item)]).filter(Boolean).join("、") || "必要的生活提醒";
    config.instruction = `${tones[String(config.tone || "friendly")]}，根据上一步事实写一条今天能直接看的提醒；第一行概括天气；只写${topics}中今天真正有用的内容；用户主要采用${commuteLabels[String(config.commute_mode || "general")]}，${preferenceLabels[String(config.temperature_preference || "normal")]}；只有存在雷雨、大风、高温、低温或明显温差时才提醒，不要逐项说明不存在的风险，不解释原因，不重复天气数据；${String(config.additional_instruction || "").trim()}；${lengths[String(config.output_length || "short")]}；输出普通中文纯文本，不使用 Markdown、JSON、内部字段名或代码`;
    return;
  }
  config.instruction = config.task === "custom" ? String(config.custom_instruction || "") : `${tones[String(config.tone || "plain")]}，${tasks[String(config.task || "summary")]}；${lengths[String(config.output_length || "medium")]}；输出普通中文纯文本，不使用 Markdown、JSON、内部字段名或代码`;
}
function toggleAdviceTopic(topic: string, event: Event) {
  if (!selected.value || selected.value.type !== "llm_transform") return;
  const checked = (event.target as HTMLInputElement).checked;
  const topics = new Set(Array.isArray(selected.value.data.config.advice_topics) ? selected.value.data.config.advice_topics.map(String) : []);
  if (checked) topics.add(topic); else topics.delete(topic);
  selected.value.data.config.advice_topics = [...topics];
  syncTransformInstruction();
}
function syncTemplateConfig(config = selected.value?.data.config) {
  if (!config) return;
  config.template = config.source_ref ? `${String(config.content || "").trim()}\n{{result}}`.trim() : String(config.content || "");
  config.variables = config.source_ref ? { result: config.source_ref } : {};
}
function syncDeliveryConfig(config = selected.value?.data.config, type = selected.value?.type as WorkflowNodeType | undefined) {
  if (!config || !type) return;
  if (type === "template") { syncTemplateConfig(config); return; }
  config.body = config.source_ref || String(config.content || "");
}
function deliveryModeFor(type: string): "result" | "notification" | "email" | "qq" | "weixin" {
  return type === "personal_email" ? "email" : type === "qq_notification" ? "qq" : type === "weixin_notification" ? "weixin" : type === "official_notification" ? "notification" : "result";
}
function isOutputNode(type: string): boolean { return outputNodeTypes.has(type as WorkflowNodeType); }
function changeDeliveryMode(event: Event) {
  if (!selected.value || !outputNodeTypes.has(selected.value.type as WorkflowNodeType)) return;
  const mode = (event.target as HTMLSelectElement).value as "result" | "notification" | "email" | "qq" | "weixin";
  const nextType: WorkflowNodeType = mode === "email" ? "personal_email" : mode === "qq" ? "qq_notification" : mode === "weixin" ? "weixin_notification" : mode === "notification" ? "official_notification" : "template";
  if (nextType === selected.value.type) return;
  recordHistory();
  const previous = selected.value.data.config;
  const next = {
    ...defaultConfig(nextType),
    content: previous.content || (!isResultReference(previous.body) ? previous.body : "") || "",
    source_ref: previous.source_ref || (isResultReference(previous.body) ? previous.body : "") || "",
    subject: previous.subject || (nextType === "official_notification" ? "工作流通知" : "工作流结果"),
    connection_id: previous.connection_id || "",
    to: previous.to || "",
  };
  selected.value.type = nextType;
  selected.value.label = tr("发送结果", "Send result");
  selected.value.data.config = next;
  syncDeliveryConfig(next, nextType);
  if (["personal_email", "qq_notification", "weixin_notification"].includes(nextType)) actionConsent.value = false;
}
function openConnectionSettings() { ui.openSettings("connections"); }
function copySelected() {
  if (!selected.value || selected.value.type === "schedule_trigger") return;
  copiedNode.value = cloneWorkflowJson(selected.value);
}
function pasteNode() {
  if (!copiedNode.value) return;
  recordHistory();
  const copy = createCanvasNode(copiedNode.value.type as WorkflowNodeType, { x: copiedNode.value.position.x + 36, y: copiedNode.value.position.y + 36 });
  copy.label = `${copiedNode.value.label} ${tr("副本", "copy")}`;
  copy.data.config = cloneWorkflowJson(copiedNode.value.data.config);
  nodes.value.push(copy);
  selectedId.value = copy.id;
}
function handleKeyboard(event: KeyboardEvent) {
  if (event.key === "Escape" && clickConnectionActive.value) {
    event.preventDefault();
    cancelClickConnection();
    return;
  }
  if (event.key === "Escape" && connectionFeedback.value) {
    event.preventDefault();
    connectionFeedback.value = "";
    return;
  }
  const target = event.target as HTMLElement | null;
  if (target?.matches("input, textarea, select") || target?.isContentEditable) return;
  const command = event.ctrlKey || event.metaKey;
  if (command && event.key.toLowerCase() === "z") { event.preventDefault(); if (event.shiftKey) redo(); else undo(); return; }
  if (command && event.key.toLowerCase() === "y") { event.preventDefault(); redo(); return; }
  if (command && event.key.toLowerCase() === "c") { event.preventDefault(); copySelected(); return; }
  if (command && event.key.toLowerCase() === "v") { event.preventDefault(); pasteNode(); return; }
  if (event.key === "Delete" || event.key === "Backspace") { event.preventDefault(); deleteSelected(); }
}
function selectTool(event: Event) {
  if (!selected.value) return;
  const tool = workflowTools.value.find((item) => item.name === (event.target as HTMLSelectElement).value);
  toolTestResult.value = "";
  toolTestSummary.value = "";
  selected.value.data.config.tool_name = tool?.name || "";
  selected.value.data.config.input_schema_hash = tool?.schema_hash || "";
  selected.value.data.config.arguments = {};
}
function selectInformationTask(tool: WorkflowToolCatalogItem | undefined) {
  if (!selected.value || !tool) return;
  toolTestResult.value = "";
  toolTestSummary.value = "";
  selected.value.data.config.tool_name = tool.name;
  selected.value.data.config.input_schema_hash = tool.schema_hash;
  selected.value.data.config.arguments = {};
}
function toolArgument(field: string): string | number | boolean {
  const argumentsValue = selected.value?.data.config.arguments;
  return typeof argumentsValue === "object" && argumentsValue !== null ? (argumentsValue as Record<string, string | number | boolean>)[field] ?? "" : "";
}
function runNodeLabel(nodeId: string, nodeType?: string): string {
  return nodes.value.find((node) => node.id === nodeId)?.label || workflowNodeTypeLabel(nodeType);
}
function runSummaryText(summary?: string): string {
  if (!summary) return "";
  let value: unknown = summary;
  try { value = JSON.parse(summary); } catch { return summary; }
  const readable = (item: unknown): string => {
    if (typeof item === "string") return item;
    if (item && typeof item === "object") {
      const record = item as Record<string, unknown>;
      for (const key of ["text", "content", "source_ref", "body", "message"]) {
        if (record[key] !== undefined && record[key] !== "") return readable(record[key]);
      }
    }
    return JSON.stringify(item, null, 2);
  };
  return readable(value);
}
function meaningfulRunSummary(summary?: string): string {
  const text = runSummaryText(summary).trim();
  return ["", "{}", "[]", "null"].includes(text) ? "" : text;
}
function runStepSummaries(node: RunNode): RunSummaryBlock[] {
  const input = meaningfulRunSummary(node.input_summary);
  const output = meaningfulRunSummary(node.output_summary);
  const blocks: RunSummaryBlock[] = [];
  const add = (id: string, label: string, text: string) => { if (text) blocks.push({ id, label, text }); };
  if (node.node_type === "template") add("result", tr("结果内容", "Result content"), output);
  else if (["official_notification", "personal_email", "qq_notification", "weixin_notification"].includes(node.node_type)) {
    add("delivery-content", tr("发送内容摘要", "Sent content summary"), input);
    add("delivery-receipt", tr("投递结果", "Delivery result"), output);
  } else if (node.node_type === "mcp_query") {
    add("query-input", tr("查询条件", "Query input"), input);
    add("query-output", tr("查询结果摘要", "Query result summary"), output);
  } else if (node.node_type === "mcp_action") {
    add("action-input", tr("操作参数", "Action input"), input);
    add("action-output", tr("执行结果", "Action result"), output);
  } else if (node.node_type === "llm_transform") {
    add("ai-input", tr("输入摘要", "Input summary"), input);
    add("ai-output", tr("生成结果", "Generated result"), output);
  } else if (node.node_type === "condition") {
    add("condition-input", tr("判断条件", "Condition"), input);
    add("condition-output", tr("判断结果", "Condition result"), output);
  } else {
    add("input", tr("输入摘要", "Input summary"), input);
    add("output", tr("输出摘要", "Output summary"), output);
  }
  return blocks;
}
function runSummaryKey(scope: string, id: string): string { return `${scope}:${id}`; }
function runSummaryDisplay(text: string, key: string): string {
  const characters = Array.from(text);
  return characters.length <= RUN_SUMMARY_LIMIT || expandedRunSummaries.value.has(key)
    ? text
    : `${characters.slice(0, RUN_SUMMARY_LIMIT).join("")}…`;
}
function runSummaryOmitted(text: string): number { return Math.max(0, Array.from(text).length - RUN_SUMMARY_LIMIT); }
function toggleRunSummary(key: string): void {
  const next = new Set(expandedRunSummaries.value);
  if (next.has(key)) next.delete(key); else next.add(key);
  expandedRunSummaries.value = next;
}
async function copyRunSummary(key: string, text: string): Promise<void> {
  if (navigator.clipboard?.writeText) await navigator.clipboard.writeText(text);
  else {
    const textarea = document.createElement("textarea");
    textarea.value = text;
    textarea.style.position = "fixed";
    textarea.style.opacity = "0";
    document.body.appendChild(textarea);
    textarea.select();
    if (typeof document.execCommand === "function") document.execCommand("copy");
    textarea.remove();
  }
  copiedRunSummary.value = key;
  window.setTimeout(() => { if (copiedRunSummary.value === key) copiedRunSummary.value = ""; }, 1500);
}
function deliveryResultLabel(nodeType: string): string {
  if (nodeType === "official_notification") return tr("我的邮箱投递结果", "My email delivery");
  if (nodeType === "personal_email") return tr("SMTP 投递结果", "SMTP delivery");
  if (nodeType === "qq_notification") return tr("QQ 投递结果", "QQ delivery");
  if (nodeType === "weixin_notification") return tr("微信投递结果", "Weixin delivery");
  return tr("保存结果", "Saved result");
}
function runStepKey(nodeId: string, attempt: number): string {
  return `${nodeId}-${attempt}`;
}
function toggleRunStep(nodeId: string, attempt: number): void {
  const key = runStepKey(nodeId, attempt);
  expandedRunStep.value = expandedRunStep.value === key ? "" : key;
}
function updateToolArgument(field: string, schema: Record<string, unknown>, event: Event) {
  if (!selected.value) return;
  const target = event.target as HTMLInputElement;
  let value: string | number | boolean = target.value;
  if ((schema.type === "number" || schema.type === "integer") && target.value !== "") value = Number(target.value);
  if (schema.type === "boolean") value = target.checked;
  const current = typeof selected.value.data.config.arguments === "object" && selected.value.data.config.arguments !== null ? selected.value.data.config.arguments as Record<string, unknown> : {};
  const next = { ...current };
  if (value === "" && schema.type !== "boolean") delete next[field];
  else next[field] = value;
  selected.value.data.config.arguments = next;
}
async function backToWorkflowHome() {
  if (!store.current) return;
  await perform("back", async () => {
    if (hasUnsavedChanges.value) await saveCurrent();
    store.close();
    await router.push({ name: "workflows" });
  });
}
async function returnToChat() {
  await perform("back", async () => {
    if (store.current && hasUnsavedChanges.value) await saveCurrent();
    await router.push("/");
  });
}
async function showRunHistory() {
  editorView.value = "runs";
  await perform("runs", () => store.loadRuns());
}
async function testSelectedTool() {
  if (!selectedTool.value || selectedTool.value.kind !== "query" || !selected.value) return;
  const argumentsValue = selected.value.data.config.arguments;
  const args = typeof argumentsValue === "object" && argumentsValue !== null ? argumentsValue as Record<string, unknown> : {};
  toolTestResult.value = "";
  toolTestSummary.value = "";
  await perform("tool-test", async () => {
    const result = await api.testWorkflowTool(selectedTool.value!.name, args);
    toolTestSummary.value = workflowToolResultSummary(result.output);
    toolTestResult.value = JSON.stringify(result.output, null, 2);
  });
}
</script>

<template>
  <main class="workflow-page workflow-product-page" :class="{ 'has-workflow': isEditorRoute, 'workflow-sidebar-collapsed': sidebarCollapsed }">
    <header class="workflow-overview-topbar glass-panel">
      <a class="brand-lockup compact" href="/" @click.prevent="returnToChat"><img :src="'/static/zhice-logo-a.png'" alt="" /><strong>ZhiCe Workflow</strong></a>
      <nav><button type="button" @click="returnToChat"><ArrowLeft :size="16" />{{ tr('返回聊天', 'Back to chat') }}</button><QuickPreferences /></nav>
    </header>
    <div class="workflow-product-workspace">
      <aside class="workflow-overview-sidebar" :class="{ collapsed: sidebarCollapsed }">
        <button v-if="sidebarCollapsed" class="workflow-sidebar-expand icon-button" type="button" :aria-label="tr('展开我的工作流', 'Expand my workflows')" @click="sidebarCollapsed = false"><PanelLeftOpen :size="18" /></button>
        <section v-else class="workflow-overview-saved">
          <header><div><span class="eyebrow">{{ tr('我的工作流', 'MY WORKFLOWS') }}</span><h2>{{ tr('最近编辑', 'Recently edited') }}</h2></div><div class="workflow-saved-actions"><button class="icon-button" type="button" :aria-label="tr('刷新', 'Refresh')" @click="store.loadAll"><RefreshCw :size="16" /></button><button class="icon-button" type="button" :aria-label="tr('收起我的工作流', 'Collapse my workflows')" @click="sidebarCollapsed = true"><PanelLeftClose :size="16" /></button></div></header>
          <button class="workflow-overview-new" type="button" :disabled="!!busy" @click="createAndOpen()"><Plus :size="16" />{{ tr('新建工作流', 'New workflow') }}</button>
          <p v-if="store.loading && !store.items.length">{{ tr('正在读取…', 'Loading…') }}</p>
          <p v-else-if="!store.items.length" class="workflow-overview-empty">{{ tr('还没有工作流草稿。', 'No workflow drafts yet.') }}</p>
          <button v-for="item in store.items" :key="item.workflow_id" type="button" class="workflow-overview-row" :class="{ active: item.workflow_id === workflowId }" @click="openWorkflow(item.workflow_id)"><span><strong>{{ item.name }}</strong><small><em :data-status="item.status">{{ workflowStatusLabel(item.status) }}</em> · {{ workflowSummaryMeta(item).split(' · ').slice(1).join(' · ') }}</small></span><span role="button" tabindex="0" :aria-label="`${tr('删除', 'Delete')} ${item.name}`" @click.stop="deleteOverviewWorkflow(item)" @keydown.enter.stop="deleteOverviewWorkflow(item)"><Trash2 :size="15" /></span></button>
        </section>
      </aside>

      <section v-if="!isEditorRoute" class="workflow-overview-results">
        <div class="workflow-overview-scroll">
          <header class="workflow-overview-intro"><div><span class="eyebrow">{{ tr('工作流', 'WORKFLOWS') }}</span><h1>{{ tr('让重复工作自动完成', 'Automate repeated work') }}</h1><p>{{ tr('从模板开始，或新建一个空白工作流。每个步骤都可以进入画布后自由调整。', 'Start from a template or create a blank workflow. Every step remains editable on the canvas.') }}</p></div></header>
          <section class="workflow-overview-section"><header><div><span class="eyebrow">{{ tr('完整模板', 'COMPLETE TEMPLATES') }}</span><h2>{{ tr('选择一个已经搭好的流程', 'Choose a ready-made workflow') }}</h2></div><small>{{ tr('创建后直接在右侧画布填写和调整', 'Create it, then complete and edit it on the right') }}</small></header><div class="workflow-overview-grid"><button v-for="template in availableStarterTemplates" :key="template.id" class="workflow-overview-card workflow-template-card" @click="useTemplate(template)"><span class="template-icon">{{ template.icon }}</span><span class="template-node-count">{{ tr(`${template.nodes.length} 个节点已搭好`, `${template.nodes.length} nodes ready`) }}</span><strong>{{ template.title }}</strong><p>{{ template.description }}</p><small>{{ template.requirements }}</small><span class="template-link">{{ tr('使用这个模板', 'Use this template') }}</span></button></div><p v-if="!availableStarterTemplates.length" class="workflow-overview-empty">{{ tr('外部信息能力暂不可用，你仍可以创建只包含智能处理和条件判断的工作流。', 'External information is unavailable, but you can still create an AI and condition workflow.') }}</p></section>
          <p v-if="failure" class="form-error" role="alert">{{ failure }}</p>
        </div>
      </section>

      <section v-else-if="store.current" class="workflow-editor-results">
        <header class="workflow-editor-toolbar">
          <div class="workflow-brand"><input v-model="store.current.name" class="workflow-title-input" :aria-label="tr('工作流名称', 'Workflow name')" maxlength="120" /><span v-if="workflowChangeState" class="workflow-status" :data-status="workflowChangeState.code">{{ workflowChangeState.label }}</span></div>
          <nav class="workflow-view-tabs"><button type="button" @click="backToWorkflowHome">{{ tr('模板', 'Templates') }}</button><button type="button" :class="{ active: editorView === 'canvas' }" @click="editorView = 'canvas'">{{ tr('流程画布', 'Canvas') }}</button><button type="button" :class="{ active: editorView === 'runs' }" @click="showRunHistory">{{ tr('执行记录', 'Runs') }}<span v-if="store.runs.length">{{ store.runs.length }}</span></button></nav>
          <div class="workflow-editor-actions"><button class="workflow-power-switch" type="button" :data-enabled="workflowEnabled" :disabled="!!busy || !workflowCanToggle" :title="workflowCanToggle ? (workflowEnabled ? tr('停用后不会再按计划自动运行', 'Disable scheduled automatic runs') : tr('启用已发布版本的自动运行', 'Enable automatic runs for the published version')) : tr('首次发布后才能启用或停用', 'Publish once before enabling or disabling')" @click="perform('toggle', toggleWorkflow)"><span class="workflow-switch-track"><span /></span><span class="workflow-switch-copy"><strong>{{ workflowEnabled ? tr('已启用', 'Enabled') : tr('已停用', 'Disabled') }}</strong><small>{{ tr('自动运行开关', 'Automatic runs') }}</small></span></button><button :disabled="!!busy" :title="tr('只保存编辑内容，不影响当前已发布版本', 'Save edits without changing the published version')" aria-live="polite" @click="perform('save', saveFromToolbar)"><Save :size="16" />{{ busy === 'save' ? tr('保存中…', 'Saving…') : saveConfirmed && !hasUnsavedChanges ? tr('已保存到工作流', 'Saved to workflow') : tr('保存草稿', 'Save draft') }}</button><button :disabled="!!busy || !editorReady" :title="editorReady ? tr('保存当前草稿，发布为新版本并启用自动运行', 'Save, publish a new version, and enable automatic runs') : configurationIssues.join('；')" @click="perform('publish', publishCurrent)"><Rocket :size="16" />{{ tr('发布并启用', 'Publish & enable') }}</button><button class="primary-button" :disabled="!!busy || !editorReady" :title="editorReady ? tr('保存当前草稿并试运行一次，不发布、不改变线上版本', 'Save and test the current draft once without publishing or changing the live version') : configurationIssues.join('；')" @click="perform('run', runCurrent)"><CirclePlay :size="16" />{{ tr('立即试运行', 'Run once now') }}</button></div>
        </header>

        <section v-if="editorView === 'canvas'" class="workflow-body" :class="{ 'inspector-collapsed': !inspectorOpen }">
      <aside class="workflow-palette workflow-node-bar" :aria-label="tr('添加流程步骤', 'Add workflow steps')">
        <strong class="workflow-node-bar-label">{{ tr('添加步骤', 'Add step') }}</strong>
        <button class="palette-node" :title="tr('选择什么时候运行', 'Choose when to run')" @click="addOrSelectTrigger"><span class="node-kind-icon">{{ nodeMeta(triggerPalette.type).icon }}</span><strong>{{ tr(triggerPalette.zh, triggerPalette.en) }}</strong></button>
        <button v-for="item in palette" :key="item.type" class="palette-node" :title="paletteHelp(item.type)" @click="addNode(item.type)"><span class="node-kind-icon">{{ nodeMeta(item.type).icon }}</span><strong>{{ tr(item.zh, item.en) }}</strong></button>
        <span class="tool-count" :data-loading="toolsLoading">{{ toolsLoading ? '…' : tr(`${informationTasks.length} 类信息可用`, `${informationTasks.length} information tasks`) }}</span>
      </aside>

      <section ref="workflowCanvas" class="workflow-canvas" :data-connection-active="clickConnectionActive || undefined" :aria-label="tr('工作流画布', 'Workflow canvas')" @pointermove="trackClickConnectionPointer" @wheel.capture="handleCanvasWheel" @contextmenu.capture="handleCanvasContextMenu" @dblclick.capture="handleCanvasDoubleClick">
        <aside class="workflow-readiness" :data-ready="editorReady" :data-open="readinessOpen || undefined"><header><strong>{{ editorReady ? tr('可以发布', 'Ready to publish') : tr(`还需完成 ${configurationIssues.length} 项`, `${configurationIssues.length} items remaining`) }}</strong><button class="workflow-readiness-close" type="button" :aria-label="tr('关闭配置问题', 'Close configuration issues')" @click="readinessOpen = false"><X :size="15" /></button></header><ul v-if="!editorReady"><li v-for="(issue, index) in configurationIssueItems" :key="`${issue.nodeId || 'workflow'}-${index}`"><button type="button" :disabled="!issue.nodeId" @click="focusConfigurationIssue(issue)">{{ issue.message }}</button></li></ul><small v-else>{{ tr('所有步骤的工具和必填参数都已配置', 'All tools and required fields are configured') }}</small></aside>
        <div class="canvas-toolbar">
          <button :disabled="!undoStack.length" :title="tr('撤销 Ctrl+Z', 'Undo Ctrl+Z')" @click="undo"><Undo2 :size="16" /></button>
          <button :disabled="!redoStack.length" :title="tr('重做 Ctrl+Y', 'Redo Ctrl+Y')" @click="redo"><Redo2 :size="16" /></button>
          <span />
          <div class="canvas-pan-controls" :aria-label="tr('移动画布', 'Pan canvas')">
            <button :title="tr('画布向左', 'Pan left')" @click="panCanvas(-120, 0)"><ArrowLeft :size="14" /></button>
            <button :title="tr('画布向上', 'Pan up')" @click="panCanvas(0, -120)"><ArrowUp :size="14" /></button>
            <button :title="tr('画布向下', 'Pan down')" @click="panCanvas(0, 120)"><ArrowDown :size="14" /></button>
            <button :title="tr('画布向右', 'Pan right')" @click="panCanvas(120, 0)"><ArrowRight :size="14" /></button>
          </div>
          <span />
          <button class="canvas-layout-button" :title="tr('自动整理节点', 'Auto layout')" @click="autoLayout"><WandSparkles :size="16" />{{ tr('自动布局', 'Layout') }}</button>
          <button v-if="clickConnectionActive" class="workflow-connection-status" type="button" :title="tr('取消当前连线', 'Cancel current connection')" @click="cancelClickConnection()">{{ tr('连接中 · 取消', 'Connecting · Cancel') }}</button>
          <button v-else class="workflow-readiness-toggle" type="button" :data-ready="editorReady" :aria-expanded="readinessOpen" @click="readinessOpen = !readinessOpen">{{ editorReady ? tr('配置完成', 'Ready') : tr(`待配置 ${configurationIssues.length}`, `${configurationIssues.length} to configure`) }}</button>
        </div>
        <div class="canvas-interaction-hint"><span class="canvas-hint-desktop">{{ tr('拖动空白网格移动 · 滚轮平移 · Ctrl + 滚轮缩放 · Alt + 滚轮横移', 'Drag empty grid to pan · Scroll to pan · Ctrl + scroll to zoom · Alt + scroll to pan horizontally') }}</span><span class="canvas-hint-mobile">{{ tr('拖动空白处移动 · 双指缩放 · 点击连接点开始连线', 'Drag empty space to pan · Pinch to zoom · Tap a port to connect') }}</span></div>
        <div v-if="clickConnectionActive" class="workflow-connection-banner" role="status">
          <span><strong>{{ connectionFeedback || tr(`正在连接：${clickConnectionSource?.label || '当前节点'}`, `Connecting: ${clickConnectionSource?.label || 'current node'}`) }}</strong><small v-if="clickConnectionActive && !connectionFeedback">{{ tr('点击目标节点完成；拖动空白处移动连线终点，画布保持不动', 'Tap a target node to finish; drag on empty space to move the line endpoint while the canvas stays fixed') }}</small></span>
          <button type="button" @click="clickConnectionActive ? cancelClickConnection() : connectionFeedback = ''"><X :size="14" />{{ clickConnectionActive ? tr('取消', 'Cancel') : tr('关闭', 'Close') }}</button>
        </div>
        <div v-if="connectionToast" class="workflow-connection-toast" role="status" aria-live="polite">{{ connectionToast }}</div>
        <svg v-if="clickConnectionPreview" class="workflow-click-connection-preview" aria-hidden="true">
          <path :d="`M ${clickConnectionPreview.fromX} ${clickConnectionPreview.fromY} C ${clickConnectionPreview.fromX + 70} ${clickConnectionPreview.fromY}, ${clickConnectionPreview.toX - 70} ${clickConnectionPreview.toY}, ${clickConnectionPreview.toX} ${clickConnectionPreview.toY}`" />
          <circle :cx="clickConnectionPreview.toX" :cy="clickConnectionPreview.toY" r="4" />
        </svg>
        <VueFlow v-model:nodes="nodes" v-model:edges="edges" :min-zoom="0.25" :max-zoom="1.5" :default-viewport="{ x: 110, y: 110, zoom: 0.72 }" :default-edge-options="{ markerEnd: MarkerType.ArrowClosed }" :pan-on-scroll="true" :pan-on-scroll-mode="PanOnScrollMode.Free" :pan-on-drag="!clickConnectionActive" :connect-on-click="false" :prevent-scrolling="true" :zoom-on-scroll="false" :zoom-on-pinch="true" :snap-to-grid="true" :snap-grid="[16, 16]" @connect="connect" @move-start="handleViewportMoveStart" @move="handleCanvasGeometryChange" @node-drag="handleCanvasGeometryChange" @node-drag-start="recordHistory" @node-drag-stop="handleCanvasGeometryChange" @node-click="handleNodeClick($event.node.id)" @edge-click="selectEdge($event.edge.id)" @pane-click="handlePaneClick">
          <template #node-default="slotProps"><div class="workflow-node" :data-connection-source="clickConnectionSource?.nodeId === slotProps.id || undefined"><Handle class="workflow-node-handle" type="target" :position="Position.Left" @mousedown.stop @touchstart.stop @pointerdown.stop @click.stop="completeTapConnection(slotProps.id)" /><span class="workflow-node-icon">{{ nodeMeta(slotProps.type).icon }}</span><div class="workflow-node-copy"><small>{{ nodeMeta(slotProps.type).category }}</small><strong>{{ slotProps.label }}</strong><span>{{ nodeSummary(slotProps.type, slotProps.data?.config || {}) }}</span></div><button class="workflow-node-edit nodrag nopan" :title="tr('编辑这个节点', 'Edit this node')" @click.stop="selectNode(slotProps.id)"><Pencil :size="12" /></button><Handle class="workflow-node-handle" type="source" :position="Position.Right" @mousedown.stop @touchstart.stop @pointerdown.stop @click.stop="startTapConnection(slotProps.id, 'output', $event)" /></div></template>
          <template v-for="type in renderNodeTypes" :key="type" #[`node-${type}`]="slotProps"><div class="workflow-node" :data-node-type="type" :data-incomplete="incompleteNodeIds.has(slotProps.id)" :data-connection-source="clickConnectionSource?.nodeId === slotProps.id || undefined"><Handle v-if="type !== 'schedule_trigger'" id="input" class="workflow-node-handle" type="target" :position="Position.Left" @mousedown.stop @touchstart.stop @pointerdown.stop @click.stop="completeTapConnection(slotProps.id)" /><span v-if="incompleteNodeIds.has(slotProps.id)" class="node-config-status">{{ tr('待配置', 'Needs setup') }}</span><span class="workflow-node-icon">{{ nodeMeta(type).icon }}</span><div class="workflow-node-copy"><small>{{ nodeMeta(type).category }}</small><strong>{{ slotProps.label }}</strong><span>{{ nodeSummary(type, slotProps.data?.config || {}) }}</span></div><button class="workflow-node-edit nodrag nopan" :title="tr('编辑这个节点', 'Edit this node')" @click.stop="selectNode(slotProps.id)"><Pencil :size="12" /></button><template v-if="type === 'condition'"><span class="condition-port condition-yes">{{ tr('是', 'YES') }}</span><Handle id="true" class="workflow-node-handle condition-handle condition-handle-yes" type="source" :position="Position.Right" @mousedown.stop @touchstart.stop @pointerdown.stop @click.stop="startTapConnection(slotProps.id, 'true', $event)" /><span class="condition-port condition-no">{{ tr('否', 'NO') }}</span><Handle id="false" class="workflow-node-handle condition-handle condition-handle-no" type="source" :position="Position.Right" @mousedown.stop @touchstart.stop @pointerdown.stop @click.stop="startTapConnection(slotProps.id, 'false', $event)" /></template><Handle v-else id="output" class="workflow-node-handle" type="source" :position="Position.Right" @mousedown.stop @touchstart.stop @pointerdown.stop @click.stop="startTapConnection(slotProps.id, 'output', $event)" /></div></template>
          <template #edge-default="{ id, sourceX, sourceY, targetX, targetY, sourcePosition, targetPosition, markerEnd }">
            <BaseEdge :id="id" :class="{ 'selected-workflow-edge': selectedEdgeId === id }" :path="getBezierPath({ sourceX, sourceY, targetX, targetY, sourcePosition, targetPosition })[0]" :marker-end="markerEnd" :interaction-width="56" />
            <EdgeLabelRenderer>
              <div class="edge-actions" :data-selected="selectedEdgeId === id || undefined" :style="{ transform: `translate(-50%, -50%) translate(${getBezierPath({ sourceX, sourceY, targetX, targetY, sourcePosition, targetPosition })[1]}px, ${getBezierPath({ sourceX, sourceY, targetX, targetY, sourcePosition, targetPosition })[2]}px)` }"><button class="edge-add-button" :title="tr('在连线上插入节点', 'Insert node on edge')" @click.stop="openEdgeMenu(id, $event)"><Plus :size="13" /></button><button class="edge-delete-button" :title="tr('删除这条连线', 'Delete this connection')" @click.stop="deleteEdge(id)"><Trash2 :size="12" /></button></div>
              <button class="mobile-edge-selector" type="button" :data-selected="selectedEdgeId === id || undefined" :aria-label="tr('编辑这条连线', 'Edit this connection')" :title="tr('编辑连线', 'Edit connection')" :style="{ transform: `translate(-50%, -50%) translate(${getBezierPath({ sourceX, sourceY, targetX, targetY, sourcePosition, targetPosition })[1]}px, ${getBezierPath({ sourceX, sourceY, targetX, targetY, sourcePosition, targetPosition })[2]}px)` }" @click.stop="selectEdge(id)"><Pencil :size="14" /></button>
            </EdgeLabelRenderer>
          </template>
          <Background pattern-color="var(--line-strong)" :gap="20" /><MiniMap pannable zoomable /><Controls />
        </VueFlow>
        <div v-if="selectedEdgeId" class="mobile-edge-actions" role="toolbar" :aria-label="tr('连线操作', 'Connection actions')">
          <button class="mobile-edge-insert" type="button" @click.stop="openEdgeMenu(selectedEdgeId, $event)"><Plus :size="17" />{{ tr('插入节点', 'Insert node') }}</button>
          <button class="mobile-edge-delete" type="button" @click.stop="deleteEdge(selectedEdgeId)"><Trash2 :size="17" />{{ tr('删除连线', 'Delete connection') }}</button>
        </div>
        <div v-if="addMenu" class="quick-add-menu" :style="{ left: `${addMenu.x}px`, top: `${addMenu.y}px` }" @click.stop>
          <header><div><span class="eyebrow">{{ tr('下一步', 'NEXT STEP') }}</span><strong>{{ tr('添加节点', 'Add node') }}</strong></div><button @click="addMenu = null"><X :size="15" /></button></header>
          <button v-for="item in palette" :key="item.type" @click="addNode(item.type, { position: addMenu!.flowPosition, edgeId: addMenu!.edgeId })"><span class="node-kind-icon">{{ nodeMeta(item.type).icon }}</span><span><strong>{{ tr(item.zh, item.en) }}</strong><small>{{ paletteHelp(item.type) }}</small></span></button>
        </div>
      <div v-if="inspectorOpen && selected" class="workflow-node-bubble-shell" :data-placement="inspectorBubblePlacement" :style="inspectorBubbleStyle" @pointerdown.stop @wheel.stop>
        <span class="workflow-node-bubble-arrow" />
      <aside class="workflow-inspector workflow-node-bubble nodrag nopan nowheel" @focusin="recordHistory">
        <template v-if="selected">
          <header><div><span class="eyebrow">{{ tr('节点详情', 'NODE DETAILS') }}</span><h2>{{ selected.label }}</h2></div><div class="inspector-header-actions"><button class="icon-button" :title="tr('收起节点详情', 'Collapse node details')" @click="inspectorOpen = false"><X :size="16" /></button><button class="icon-button danger-text" :disabled="selected.type === 'schedule_trigger'" @click="deleteSelected"><Trash2 :size="17" /></button></div></header>
          <section v-if="selectedConfigurationIssues.length" class="inspector-config-warning"><strong>{{ tr('这个节点还需要配置', 'This node needs setup') }}</strong><span v-for="issue in selectedConfigurationIssues" :key="issue.message">{{ issue.message }}</span></section>
          <label>{{ tr('节点名称', 'Node label') }}<input v-model="selected.label" maxlength="80" /></label>
          <template v-if="selected.type === 'schedule_trigger'">
            <label>{{ tr('运行方式', 'Run mode') }}<select :value="scheduleKindFor(selected.data.config.schedule_mode)" @change="changeScheduleKind"><option value="manual">{{ tr('手动触发', 'Manual trigger') }}</option><option value="recurring">{{ tr('周期运行', 'Recurring') }}</option><option value="once">{{ tr('定时一次', 'Run once at a set time') }}</option></select></label>
            <label v-if="scheduleKindFor(selected.data.config.schedule_mode) === 'recurring'">{{ tr('重复周期', 'Repeat') }}<select :value="selected.data.config.schedule_mode" @change="changeScheduleMode"><option value="every">{{ tr('固定间隔', 'Fixed interval') }}</option><option value="daily">{{ tr('每天运行', 'Daily') }}</option><option value="weekly">{{ tr('每周运行', 'Weekly') }}</option><option value="monthly">{{ tr('每月运行', 'Monthly') }}</option></select></label>
            <div v-if="selected.data.config.schedule_mode === 'every'" class="workflow-inline-fields"><label>{{ tr('每隔', 'Every') }}<input v-model.number="selected.data.config.interval_amount" type="number" min="1" max="999" @change="syncScheduleConfig" /></label><label>{{ tr('时间单位', 'Unit') }}<select v-model="selected.data.config.interval_unit" @change="syncScheduleConfig"><option value="minutes">{{ tr('分钟', 'Minutes') }}</option><option value="hours">{{ tr('小时', 'Hours') }}</option><option value="days">{{ tr('天', 'Days') }}</option></select></label></div>
            <DateTimePicker v-if="['daily', 'weekly', 'monthly'].includes(selected.data.config.schedule_mode)" :model-value="String(selected.data.config.time_of_day || '')" mode="time" :label="tr('几点运行', 'Run time')" :language="ui.language" @update:model-value="updateScheduleTime" />
            <label v-if="selected.data.config.schedule_mode === 'weekly'">{{ tr('星期几', 'Weekday') }}<select v-model.number="selected.data.config.weekday" @change="syncScheduleConfig"><option :value="1">星期一</option><option :value="2">星期二</option><option :value="3">星期三</option><option :value="4">星期四</option><option :value="5">星期五</option><option :value="6">星期六</option><option :value="0">星期日</option></select></label>
            <label v-if="selected.data.config.schedule_mode === 'monthly'">{{ tr('每月几号', 'Day of month') }}<input v-model.number="selected.data.config.day_of_month" type="number" min="1" max="28" @change="syncScheduleConfig" /><small>{{ tr('为避免短月份跳过，支持 1 至 28 号', 'Days 1-28 avoid skipped months') }}</small></label>
            <DateTimePicker v-if="selected.data.config.schedule_mode === 'once'" :model-value="String(selected.data.config.run_at || '')" :label="tr('执行时间', 'Run at')" :language="ui.language" @update:model-value="updateSingleRunTime" />
            <p v-if="selected.data.config.schedule_mode === 'once'" class="workflow-field-help">{{ tr('在指定时间自动运行一次。', 'Runs once at the selected time.') }}</p>
            <p class="workflow-field-help">{{ tr('系统会自动生成调度规则，你不需要填写定时表达式。', 'The schedule rule is generated automatically.') }}</p>
          </template>
          <template v-else-if="isOutputNode(selected.type)">
            <label>{{ tr('结果去向', 'Result destination') }}<select :value="deliveryModeFor(selected.type)" @change="changeDeliveryMode"><option value="result">{{ tr('仅作记录', 'Record only') }}</option><option value="weixin" :disabled="workflowCapabilities.weixin_notification?.available !== true">{{ tr('微信通知', 'Weixin notification') }}</option><option value="qq" :disabled="workflowCapabilities.qq_notification?.available !== true">{{ tr('QQ 通知', 'QQ notification') }}</option><option value="notification" :disabled="workflowCapabilities.official_notification?.available === false">{{ tr('邮箱通知', 'Email notification') }}</option><option value="email" :disabled="!activeEmailConnections.length">{{ tr('SMTP 发送', 'Send via SMTP') }}</option></select></label>
            <p class="workflow-field-help">{{ tr('所有结果都会保存在执行记录中；选择通知方式后，还会发送到对应渠道。', 'Every result is saved in run history; notification modes also send it to the selected channel.') }}</p>
            <p v-if="deliveryModeFor(selected.type) === 'weixin' && workflowCapabilities.weixin_notification?.available === true" class="workflow-field-help">{{ tr('将发送到当前账号已经绑定的微信，不需要填写微信号。若很久没有与智策对话，请先在微信里发一条消息刷新会话。', 'Uses the Weixin bound to this account; no Weixin identifier is required.') }}</p>
            <p v-if="workflowCapabilities.weixin_notification && workflowCapabilities.weixin_notification.available !== true" class="workflow-field-help workflow-channel-setup-hint">{{ tr('要使用微信通知，请先在“连接与账号”绑定微信，再到微信里给智策发送一条消息。完成后返回此页即可选择。', 'To use Weixin notifications, connect Weixin under Connections & accounts, then send ZhiCe one message in Weixin. Return here when finished.') }} <button class="inline-settings-link" @click="openConnectionSettings">{{ tr('去连接', 'Connect') }}</button></p>
            <p v-if="deliveryModeFor(selected.type) === 'qq' && workflowCapabilities.qq_notification?.available === true" class="workflow-field-help">{{ tr('将发送到当前账号已经绑定的 QQ，不需要填写 QQ 号。QQ 平台接受请求后会记为执行成功。', 'Uses the QQ bound to this account; no QQ identifier is required.') }}</p>
            <p v-else-if="deliveryModeFor(selected.type) === 'qq'" class="form-error">{{ workflowErrorLabel(workflowCapabilities.qq_notification?.code || 'WORKFLOW_QQ_CHANNEL_UNAVAILABLE') }} <button class="inline-settings-link" @click="openConnectionSettings">{{ tr('去连接', 'Connect') }}</button></p>
            <p v-else-if="workflowCapabilities.qq_notification?.available !== true" class="workflow-field-help">{{ workflowErrorLabel(workflowCapabilities.qq_notification?.code || 'WORKFLOW_QQ_CHANNEL_UNAVAILABLE') }} <button class="inline-settings-link" @click="openConnectionSettings">{{ tr('去连接', 'Connect') }}</button></p>
            <p v-if="deliveryModeFor(selected.type) === 'notification' && workflowCapabilities.official_notification?.available === false" class="form-error">{{ workflowErrorLabel(workflowCapabilities.official_notification.code) }}</p>
            <p v-if="connectionsFailure" class="form-error">{{ connectionsFailure }}</p><p v-else-if="deliveryModeFor(selected.type) === 'result' && !activeEmailConnections.length" class="workflow-field-help">{{ tr('需要用自己的邮箱向其他人发信？请先在“连接与账号”中配置 SMTP 代发。', 'Want to email other recipients from your mailbox? Configure SMTP sending under Connections & accounts.') }} <button class="inline-settings-link" @click="openConnectionSettings">{{ tr('去配置', 'Configure') }}</button></p>
            <label v-if="!['result', 'qq', 'weixin'].includes(deliveryModeFor(selected.type))">{{ tr('标题', 'Subject') }}<input v-model="selected.data.config.subject" :placeholder="tr('例如：今日工作流结果', 'For example: Today’s workflow result')" /></label>
            <template v-if="deliveryModeFor(selected.type) === 'email'">
              <label>{{ tr('使用哪个发送账号', 'Sending account') }}<select v-model="selected.data.config.connection_id"><option value="">{{ activeEmailConnections.length ? tr('请选择发送账号', 'Choose an account') : tr('还没有可用的发送账号', 'No sending account available') }}</option><option v-for="connection in activeEmailConnections" :key="connection.id" :value="connection.id">{{ connection.account_display }}（SMTP）</option></select></label>
              <label>{{ tr('收件人', 'Recipient') }}<input v-model="selected.data.config.to" type="email" placeholder="name@example.com" /></label>
            </template>
            <label>{{ tr('附加说明（可选）', 'Intro text (optional)') }}<textarea v-model="selected.data.config.content" rows="4" :placeholder="tr('例如：这是今天自动整理的结果', 'For example: Here is today’s automated result')" @input="syncDeliveryConfig()" /></label>
            <p class="workflow-auto-input"><strong>{{ tr('自动使用上一步结果', 'Uses the previous step automatically') }}</strong><span>{{ selectedDirectInputLabel || tr('请先连接一个上一步', 'Connect a previous step') }}</span></p>
            <p class="workflow-field-help">{{ tr('系统会发送连线直接接入的上一步结果，不需要选择内部变量。', 'The directly connected previous result is sent automatically.') }}</p>
          </template>
          <template v-else-if="selected.type === 'llm_transform'">
            <p class="workflow-auto-input"><strong>{{ tr('自动整理为可读内容', 'Automatically creates readable content') }}</strong><span>{{ selectedDirectInputLabel ? tr(`使用“${selectedDirectInputLabel}”的结果`, `Uses “${selectedDirectInputLabel}”`) : tr('请先连接一个上一步', 'Connect a previous step') }}</span></p>
            <p class="workflow-field-help">{{ tr('默认生成适合邮件和消息直接阅读的纯文本，去掉 Markdown、JSON、内部字段和代码形式，保留日期、数值、单位与关键建议。', 'Creates plain text for email and messages while removing Markdown, JSON, internal fields, and code.') }}</p>
            <details class="workflow-optional-settings">
              <summary>{{ tr('调整处理规则（可选）', 'Adjust processing rules (optional)') }}</summary>
              <label>{{ tr('想得到什么', 'Task') }}<select v-model="selected.data.config.task" @change="syncTransformInstruction"><option value="advice">{{ tr('生成生活建议', 'Create practical advice') }}</option><option value="summary">{{ tr('生成摘要', 'Summarize') }}</option><option value="key_points">{{ tr('提取重点', 'Extract key points') }}</option><option value="rewrite">{{ tr('润色改写', 'Rewrite') }}</option><option value="classify">{{ tr('分类整理', 'Classify') }}</option><option value="custom">{{ tr('自定义要求', 'Custom') }}</option></select></label>
              <section v-if="selected.data.config.task === 'advice'" class="advice-settings">
                <span>{{ tr('需要哪些建议', 'Advice topics') }}</span>
                <label><input type="checkbox" :checked="selected.data.config.advice_topics?.includes('umbrella')" @change="toggleAdviceTopic('umbrella', $event)" />{{ tr('带伞与天气风险', 'Umbrella and weather risks') }}</label>
                <label><input type="checkbox" :checked="selected.data.config.advice_topics?.includes('clothing')" @change="toggleAdviceTopic('clothing', $event)" />{{ tr('穿衣建议', 'Clothing') }}</label>
                <label><input type="checkbox" :checked="selected.data.config.advice_topics?.includes('travel')" @change="toggleAdviceTopic('travel', $event)" />{{ tr('出行建议', 'Travel') }}</label>
                <div class="workflow-inline-fields"><label>{{ tr('主要出行方式', 'Travel mode') }}<select v-model="selected.data.config.commute_mode" @change="syncTransformInstruction"><option value="general">{{ tr('一般出行', 'General') }}</option><option value="walk">{{ tr('步行', 'Walking') }}</option><option value="bicycle">{{ tr('骑行', 'Cycling') }}</option><option value="transit">{{ tr('公交或地铁', 'Public transit') }}</option><option value="drive">{{ tr('开车', 'Driving') }}</option></select></label><label>{{ tr('体感偏好', 'Temperature preference') }}<select v-model="selected.data.config.temperature_preference" @change="syncTransformInstruction"><option value="cold">{{ tr('比较怕冷', 'Feels cold easily') }}</option><option value="normal">{{ tr('正常', 'Typical') }}</option><option value="hot">{{ tr('比较怕热', 'Feels hot easily') }}</option></select></label></div>
                <label>{{ tr('补充要求（可选）', 'Additional needs (optional)') }}<textarea v-model="selected.data.config.additional_instruction" rows="3" :placeholder="tr('例如：需要接送孩子，重点提醒早晚天气', 'For example: focus on school drop-off times')" @input="syncTransformInstruction" /></label>
              </section>
              <div v-if="selected.data.config.task !== 'custom'" class="workflow-inline-fields"><label>{{ tr('表达风格', 'Tone') }}<select v-model="selected.data.config.tone" @change="syncTransformInstruction"><option value="plain">{{ tr('清晰自然', 'Clear') }}</option><option value="professional">{{ tr('专业正式', 'Professional') }}</option><option value="friendly">{{ tr('亲切易懂', 'Friendly') }}</option></select></label><label>{{ tr('结果长度', 'Length') }}<select v-model="selected.data.config.output_length" @change="syncTransformInstruction"><option value="short">{{ tr('简短', 'Short') }}</option><option value="medium">{{ tr('适中', 'Medium') }}</option><option value="long">{{ tr('详细', 'Detailed') }}</option></select></label></div>
              <label v-else>{{ tr('具体要求', 'Custom instruction') }}<textarea v-model="selected.data.config.custom_instruction" rows="5" :placeholder="tr('例如：按城市分组，保留价格和来源', 'For example: group by city and keep prices and sources')" @input="syncTransformInstruction" /></label>
            </details>
          </template>
          <template v-else-if="selected.type === 'condition'">
            <label>{{ tr('判断类型', 'Condition type') }}<select :value="selected.data.config.check_mode || 'value'" @change="setConditionMode"><option value="value">{{ tr('判断上一步的结果内容', 'Check previous result value') }}</option><option value="status">{{ tr('检查上一步是否成功', 'Check whether previous step succeeded') }}</option></select></label>
            <template v-if="selected.data.config.check_mode !== 'status'">
              <label>{{ tr('判断哪个步骤的结果', 'Result to check') }}<select :value="selected.data.config.left" @change="selectResult('left', $event)"><option value="">{{ tr('请选择前面的步骤', 'Choose a previous step') }}</option><option v-for="option in variableOptions" :key="option.nodeId" :value="option.value">{{ option.label }}</option></select></label>
              <label>{{ tr('判断方式', 'Operator') }}<select v-model="selected.data.config.operator"><option value="eq">{{ tr('等于', 'Equals') }}</option><option value="ne">{{ tr('不等于', 'Does not equal') }}</option><option value="contains">{{ tr('包含', 'Contains') }}</option><option value="is_empty">{{ tr('没有内容', 'Is empty') }}</option><option value="gt">{{ tr('大于', 'Greater than') }}</option><option value="gte">{{ tr('大于或等于', 'Greater than or equal') }}</option><option value="lt">{{ tr('小于', 'Less than') }}</option><option value="lte">{{ tr('小于或等于', 'Less than or equal') }}</option></select></label>
              <label v-if="selected.data.config.operator !== 'is_empty'">{{ tr('要比较的内容', 'Compare with') }}<input v-model="selected.data.config.right" :placeholder="tr('输入文字或数字', 'Enter text or a number')" /></label>
            </template>
            <template v-else>
              <p class="workflow-field-help">{{ selectedDirectInputLabel ? tr(`检查“${selectedDirectInputLabel}”是否执行成功。`, `Check whether “${selectedDirectInputLabel}” succeeded.`) : tr('请先把条件节点连接到要检查的上一步。', 'Connect the condition to the previous step to check.') }}</p>
              <label class="workflow-checkbox"><input v-model="selected.data.config.retry_on_failure" type="checkbox" />{{ tr('失败时重新执行上一步', 'Retry the previous step when it fails') }}</label>
              <label v-if="selected.data.config.retry_on_failure">{{ tr('最大尝试次数（包含第一次）', 'Maximum attempts (including the first)') }}<input v-model.number="selected.data.config.max_attempts" type="number" min="1" max="5" /></label>
              <p class="workflow-field-help">{{ tr('任一次成功走“是”分支；达到次数仍失败走“否”分支。发送消息、邮件和外部写操作不会自动重试。', 'Any success follows Yes; exhaustion follows No. Notifications, email, and external writes are never retried automatically.') }}</p>
            </template>
            <p class="workflow-field-help">{{ tr('条件成立走“是”分支，不成立走“否”分支。', 'True follows Yes; false follows No.') }}</p>
          </template>
          <template v-else-if="selected.type === 'mcp_query'">
            <div class="tool-live-status" :data-ready="selectedToolValid"><span class="run-dot" />{{ selectedToolValid ? tr('当前账号可以使用，填写下面的信息即可', 'Available to this account; complete the fields below') : selectedTool ? tr('查询规则已更新，请重新选择并检查内容', 'The query rules changed; select it again and review') : tr('请选择你想获取的信息', 'Choose what information you want') }}</div>
            <div class="information-task-grid"><button v-for="task in informationTasks" :key="task.id" :class="{ active: selected.data.config.tool_name === task.tool }" @click="selectInformationTask(task.item)"><span>{{ task.icon }}</span><strong>{{ tr(task.zh, task.en) }}</strong><small>{{ task.help }}</small></button></div>
            <p v-if="!informationTasks.length" class="form-error">{{ tr('当前没有通过真实连接检查的信息能力。', 'No information capability passed the live connection check.') }}</p>
            <section v-if="selectedTool" class="selected-tool-card"><header><span>{{ workflowToolProvider(selectedTool.name) }}</span><em>{{ selectedTool.kind === 'action' ? tr('会产生外部操作', 'External action') : tr('只读查询', 'Read-only query') }}</em></header><strong>{{ workflowToolName(selectedTool) }}</strong><p>{{ workflowToolHelp(selectedTool.name, selectedTool.description) }}</p></section>
            <div v-if="selectedTool" class="schema-fields"><label v-for="field in selectedToolFields" :key="field.key"><span>{{ field.label }}</span><small v-if="field.required">{{ tr('必填', 'Required') }}</small><select v-if="field.options" :value="toolArgument(field.key)" @change="updateToolArgument(field.key, { type: 'string' }, $event)"><option value="">{{ tr('使用默认设置', 'Use default') }}</option><option v-for="option in field.options" :key="option.value" :value="option.value">{{ option.label }}</option></select><input v-else-if="field.type !== 'boolean'" :type="field.type" :min="field.min" :max="field.max" :placeholder="field.hint" :value="toolArgument(field.key)" @input="updateToolArgument(field.key, { type: field.type }, $event)" /><input v-else type="checkbox" :checked="Boolean(toolArgument(field.key))" @change="updateToolArgument(field.key, { type: 'boolean' }, $event)" /></label></div>
            <button v-if="selectedTool?.kind === 'query'" class="tool-test-button" :disabled="busy === 'tool-test' || !selectedToolValid" @click="testSelectedTool"><CirclePlay :size="14" />{{ busy === 'tool-test' ? tr('测试中…', 'Testing…') : tr('测试查询结果', 'Test query') }}</button><section v-if="toolTestSummary" class="tool-test-summary"><strong>{{ tr('查询成功', 'Query succeeded') }}</strong><p>{{ toolTestSummary }}</p><details><summary>{{ tr('查看详细响应', 'View full response') }}</summary><pre class="tool-test-result">{{ toolTestResult }}</pre></details></section>
          </template>
          <template v-else-if="selected.type === 'mcp_action'">
            <p class="workflow-field-help">{{ tr('这里只展示已经审核并且当前账号真实可用的操作。', 'Only reviewed actions available to this account are shown.') }}</p>
            <label>{{ tr('选择要执行的操作', 'Choose an action') }}<select :value="selected.data.config.tool_name || ''" @change="selectTool"><option value="">{{ visibleTools.length ? tr('请选择操作', 'Choose an action') : tr('当前没有可用操作', 'No actions available') }}</option><option v-for="tool in visibleTools" :key="tool.name" :value="tool.name">{{ workflowToolName(tool) }}</option></select></label>
            <section v-if="selectedTool" class="selected-tool-card"><strong>{{ workflowToolName(selectedTool) }}</strong><p>{{ workflowToolHelp(selectedTool.name, selectedTool.description) }}</p></section>
            <div v-if="selectedTool" class="schema-fields"><label v-for="field in selectedToolFields" :key="field.key"><span>{{ field.label }}</span><small v-if="field.required">{{ tr('必填', 'Required') }}</small><input :type="field.type === 'number' ? 'number' : 'text'" :placeholder="field.hint" :value="toolArgument(field.key)" @input="updateToolArgument(field.key, { type: field.type }, $event)" /></label></div>
          </template>
          <p v-else>{{ tr('这个步骤暂时没有需要填写的内容。', 'This step has no editable fields.') }}</p>
          <p>{{ tr('这里只会运行平台提供的安全步骤，不会执行自定义程序或访问本地文件。', 'Only reviewed platform steps can run; custom programs and local files are not accessible.') }}</p>
        </template>
        <p v-else>{{ tr('选择一个节点以编辑属性。', 'Select a node to edit its properties.') }}</p>
        <label v-if="hasAction" class="workflow-consent"><input v-model="actionConsent" type="checkbox" />{{ actionConsentLabel }}</label>
      </aside>
      </div>
      </section>
    </section>

        <section v-else class="workflow-timeline">
      <header>
        <div><History :size="16" /><strong>{{ tr('执行记录', 'Run history') }}</strong><span v-if="store.runs.length">{{ store.runs.length }}</span></div><button :disabled="!store.current || !!busy" @click="showRunHistory"><RefreshCw :size="15" />{{ tr('刷新', 'Refresh') }}</button>
      </header>
      <p v-if="configurationIssues.length" class="workflow-issues-summary" role="status">{{ tr(`发布前还需完成 ${configurationIssues.length} 项配置。`, `${configurationIssues.length} configuration items remain.`) }}</p><p v-if="failure" class="form-error" role="alert">{{ failure }}</p>
      <div v-if="store.runs.length" class="workflow-run-layout">
        <div class="workflow-runs"><button v-for="run in store.runs" :key="run.run_id" :class="{ active: (store.runDetail?.id || store.runDetail?.run_id) === run.run_id }" @click="perform('run-detail', () => store.toggleRun(run.run_id))"><span class="run-dot" :data-status="run.status" /><strong>{{ workflowRunStatusLabel(run.status) }}</strong><span>{{ workflowTriggerLabel(run.trigger_type) }}</span><small>{{ workflowTimeLabel(run.started_at) }}</small></button></div><div v-if="store.runDetail" class="run-detail">
        <section class="run-result-section">
          <header><strong>{{ tr('本次运行结果', 'Run result') }}</strong><small>{{ workflowRunStatusLabel(store.runDetail.status) }}</small></header>
          <article v-for="result in store.runDetail.results || []" :key="`${result.node_id}-${result.node_type}`" class="run-final-result">
            <strong>{{ runNodeLabel(result.node_id, result.node_type) }}</strong>
            <p v-if="runSummaryText(result.content_summary)">{{ runSummaryDisplay(runSummaryText(result.content_summary), runSummaryKey(`result-${result.node_id}`, 'content')) }}</p>
            <p v-else class="run-result-empty">{{ tr('本次没有可展示的正文摘要。', 'No content summary is available for this result.') }}</p>
            <div v-if="runSummaryText(result.content_summary)" class="run-summary-actions">
              <button v-if="runSummaryOmitted(runSummaryText(result.content_summary))" type="button" @click="toggleRunSummary(runSummaryKey(`result-${result.node_id}`, 'content'))">{{ expandedRunSummaries.has(runSummaryKey(`result-${result.node_id}`, 'content')) ? tr('收起', 'Collapse') : tr(`展开全部（已省略 ${runSummaryOmitted(runSummaryText(result.content_summary))} 字）`, `Show all (${runSummaryOmitted(runSummaryText(result.content_summary))} omitted)`) }}</button>
              <button type="button" @click="copyRunSummary(runSummaryKey(`result-${result.node_id}`, 'content'), runSummaryText(result.content_summary))">{{ copiedRunSummary === runSummaryKey(`result-${result.node_id}`, 'content') ? tr('已复制', 'Copied') : tr('复制完整内容', 'Copy full content') }}</button>
            </div>
            <footer v-if="result.node_type !== 'template' || result.delivery_summary || result.error_code">
              <b>{{ deliveryResultLabel(result.node_type) }}</b>
              <span v-if="result.error_code" class="run-error-copy">{{ workflowErrorLabel(result.error_code) }}</span>
              <span v-else>{{ runSummaryDisplay(runSummaryText(result.delivery_summary) || workflowRunStatusLabel(result.status), runSummaryKey(`result-${result.node_id}`, 'delivery')) }}</span>
              <div v-if="result.delivery_summary" class="run-summary-actions">
                <button v-if="runSummaryOmitted(runSummaryText(result.delivery_summary))" type="button" @click="toggleRunSummary(runSummaryKey(`result-${result.node_id}`, 'delivery'))">{{ expandedRunSummaries.has(runSummaryKey(`result-${result.node_id}`, 'delivery')) ? tr('收起', 'Collapse') : tr(`展开全部（已省略 ${runSummaryOmitted(runSummaryText(result.delivery_summary))} 字）`, `Show all (${runSummaryOmitted(runSummaryText(result.delivery_summary))} omitted)`) }}</button>
                <button type="button" @click="copyRunSummary(runSummaryKey(`result-${result.node_id}`, 'delivery'), runSummaryText(result.delivery_summary))">{{ copiedRunSummary === runSummaryKey(`result-${result.node_id}`, 'delivery') ? tr('已复制', 'Copied') : tr('复制完整内容', 'Copy full content') }}</button>
              </div>
            </footer>
          </article>
          <p v-if="!(store.runDetail.results || []).length" class="run-result-empty">{{ tr('本次运行尚未产生可展示的结果。', 'This run has not produced a visible result yet.') }}</p>
        </section>
        <section class="run-steps-section">
          <strong>{{ tr('步骤详情', 'Execution steps') }}</strong>
          <details v-for="node in store.runDetail.nodes || []" :key="runStepKey(node.node_id, node.attempt)" class="run-step-detail" :open="expandedRunStep === runStepKey(node.node_id, node.attempt)"><summary @click.prevent="toggleRunStep(node.node_id, node.attempt)"><span class="run-dot" :data-status="node.status" /><b>{{ runNodeLabel(node.node_id, node.node_type) }}</b><small>{{ workflowRunStatusLabel(node.status) }} · {{ workflowTimeLabel(node.started_at) }}</small></summary><div v-for="block in runStepSummaries(node)" :key="block.id" class="run-summary-block"><b>{{ block.label }}</b><pre>{{ runSummaryDisplay(block.text, runSummaryKey(runStepKey(node.node_id, node.attempt), block.id)) }}</pre><div class="run-summary-actions"><button v-if="runSummaryOmitted(block.text)" type="button" @click="toggleRunSummary(runSummaryKey(runStepKey(node.node_id, node.attempt), block.id))">{{ expandedRunSummaries.has(runSummaryKey(runStepKey(node.node_id, node.attempt), block.id)) ? tr('收起', 'Collapse') : tr(`展开全部（已省略 ${runSummaryOmitted(block.text)} 字）`, `Show all (${runSummaryOmitted(block.text)} omitted)`) }}</button><button type="button" @click="copyRunSummary(runSummaryKey(runStepKey(node.node_id, node.attempt), block.id), block.text)">{{ copiedRunSummary === runSummaryKey(runStepKey(node.node_id, node.attempt), block.id) ? tr('已复制', 'Copied') : tr('复制完整内容', 'Copy full content') }}</button></div></div><p v-if="node.error_code" class="run-error-copy">{{ workflowErrorLabel(node.error_code) }}</p></details>
          <p v-if="!(store.runDetail.nodes || []).length">{{ tr('本次运行还没有步骤记录。', 'No step records for this run yet.') }}</p>
        </section>
        </div>
      </div>
      <p v-else>{{ tr('尚无运行记录。发布后可以立即运行，也可以设置指定时间、固定间隔或周期定时。', 'No runs yet. Publish and run now, or choose a supported schedule.') }}</p>
        </section>
      </section>
      <section v-else class="workflow-detail-loading" aria-live="polite"><span class="eyebrow">{{ tr('工作流编辑器', 'WORKFLOW EDITOR') }}</span><strong>{{ failure || tr('正在加载工作流…', 'Loading workflow…') }}</strong><button v-if="failure" @click="router.push({ name: 'workflows' })">{{ tr('返回工作流总览', 'Back to workflows') }}</button></section>
    </div>
  </main>
</template>
