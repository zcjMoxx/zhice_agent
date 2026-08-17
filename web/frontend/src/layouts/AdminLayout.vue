<script setup lang="ts">
import { Activity, ArrowLeft, BookOpen, ChevronDown, Download, ExternalLink, FileClock, Gauge, LockKeyhole, RefreshCw, Server, Shield, Trash2, Users } from "@lucide/vue";
import { computed, nextTick, onBeforeUnmount, onMounted, reactive, ref, watch } from "vue";
import { useRouter } from "vue-router";

import { baseCapabilities, groupedPermissions, permissionLabel, roleName } from "@/admin/permissions";
import QuickPreferences from "@/components/QuickPreferences.vue";
import DateTimePicker from "@/components/DateTimePicker.vue";
import { uiText } from "@/i18n";
import { useAdminStore } from "@/stores/admin";
import { useAuthStore } from "@/stores/auth";
import { errorMessage } from "@/stores/chat";
import { useUiStore } from "@/stores/ui";
import type { PublicUser } from "@/api/types";

const auth = useAuthStore();
const admin = useAdminStore();
const router = useRouter();
const ui = useUiStore();
const tab = ref("overview");
const failure = ref("");
const actionStatus = ref("");
const recentRunStatus = ref("error");
const timelineScope = ref<"errors" | "all">("errors");
const openRunRecords = ref<Record<string, boolean>>({});
const openTimelineEvents = ref<Record<string, boolean>>({});
const diagnosticBusy = ref(false);
const diagnosticUpdatedAt = ref("");
const selectedRole = ref("");
const technicalOpen = ref<Record<string, boolean>>({});
const auditFilters = reactive({ event_type: "", actor_user_id: "", outcome: "", from_ts: "", to_ts: "" });
const diagnosticFilters = reactive({ actor_user_id: "", session_id: "", component: "", error_code: "", status: "", minutes: "1440" });
const newUser = reactive({ username: "", display_name: "", password: "", roles: ["viewer"] });
const hotelCredentials = reactive({ username: "", password: "" });
const hotelCredentialsOpen = ref(false);
const deletingUser = ref<PublicUser | null>(null);
const deleteConfirmation = ref("");
const deleteConfirmationError = ref("");
const deleteBusy = ref(false);
const expandedSkillSources = ref<Record<string, boolean>>({});
const opsEmbedded = ref(false);
const opsFrameFallback = ref(false);
let opsFrameTimer: ReturnType<typeof setTimeout> | undefined;
let xhsLoginTimer: ReturnType<typeof setInterval> | undefined;
let hotelLoginTimer: ReturnType<typeof setInterval> | undefined;

function tr(chinese: string, english: string): string { return uiText(ui.language, chinese, english); }

const tabs = computed(() => [
  { key: "overview", label: tr("概览", "Overview"), icon: Gauge, visible: true },
  { key: "users", label: tr("账号管理", "Accounts"), icon: Users, visible: auth.can("auth.users.read") },
  { key: "roles", label: tr("角色与权限", "Roles & permissions"), icon: Shield, visible: auth.can("auth.roles.read") },
  { key: "skills", label: tr("MCP 与 Skills", "MCP & Skills"), icon: BookOpen, visible: auth.can("skill.sources.read") },
  { key: "monitor", label: tr("运行诊断", "Runtime diagnostics"), icon: Activity, visible: auth.can("turn.read.any") || auth.can("diagnostics.system.use") },
  { key: "operations", label: tr("服务器运维", "Server operations"), icon: Server, visible: auth.isOwner },
  { key: "advanced", label: tr("安全审计", "Security audit"), icon: FileClock, visible: auth.can("audit.read") },
].filter((item) => item.visible));
const orderedMcpServers = computed(() => {
  const servers = admin.mcpStatus?.servers || [];
  return [
    ...servers.filter((server) => server.server_id !== "xhs-readonly"),
    ...servers.filter((server) => server.server_id === "xhs-readonly"),
  ];
});
const visibleServiceCount = computed(() => orderedMcpServers.value.length);
const canOpenMonitor = computed(() => tabs.value.some((item) => item.key === "monitor"));
const auditActionOptions = computed(() => [
  ["login", tr("登录", "Login")],
  ["logout", tr("退出登录", "Logout")],
  ["registration", tr("账号注册", "Account registration")],
  ["password", tr("密码修改", "Password change")],
  ["profile", tr("个人资料修改", "Profile change")],
  ["access", tr("访问请求", "Access request")],
  ["user", tr("账号管理", "Account management")],
  ["role", tr("角色权限", "Role permissions")],
  ["audit", tr("安全审计", "Security audit")],
  ["diagnostics", tr("系统诊断", "System diagnostics")],
  ["external_identity", tr("外部账号绑定", "External account linking")],
] as const);
const auditActorOptions = computed(() => admin.users.map((user) => ({
  id: user.id,
  label: `${user.display_name || user.username} (@${user.username})`,
})));
const diagnosticActorOptions = computed(() => {
  const users = [...admin.users];
  if (auth.user && !users.some((user) => user.id === auth.user?.id)) users.unshift(auth.user);
  return users.map((user) => ({ id: user.id, label: `${user.display_name || user.username} (@${user.username})` }));
});
const diagnosticSessionOptions = computed(() => {
  const options = new Map<string, string>();
  for (const turn of admin.monitor?.activity.recent_turns || []) {
    const id = String(turn.session_id || "");
    if (id) options.set(id, String(turn.session_title || id));
  }
  if (diagnosticFilters.session_id && !options.has(diagnosticFilters.session_id)) options.set(diagnosticFilters.session_id, diagnosticFilters.session_id);
  return [...options].map(([id, label]) => ({ id, label }));
});
const diagnosticComponentOptions = computed(() => {
  const values = new Set<string>();
  for (const item of [...(admin.diagnostics?.incidents || []), ...(admin.diagnostics?.timeline || [])]) {
    if (item.component) values.add(String(item.component));
  }
  if (diagnosticFilters.component) values.add(diagnosticFilters.component);
  return [...values].sort().map((value) => ({ value, label: componentLabel(value) }));
});
const diagnosticErrorCodeOptions = computed(() => {
  const values = new Set<string>();
  for (const item of [...(admin.diagnostics?.incidents || []), ...(admin.diagnostics?.timeline || [])]) {
    if (item.code) values.add(String(item.code));
  }
  if (diagnosticFilters.error_code) values.add(diagnosticFilters.error_code);
  return [...values].sort();
});
const displayedDiagnosticTimeline = computed(() => {
  const timeline = admin.diagnostics?.timeline || [];
  return timelineScope.value === "all"
    ? timeline
    : timeline.filter(eventIsError);
});
const diagnosticHasResults = computed(() => Object.values(admin.diagnostics?.summary || {}).some((value) => Number(value) > 0));
const selectedRoleValue = computed(() => admin.roles.find((role) => role.id === selectedRole.value));
const roleOrder: Record<string, number> = { owner: 0, admin: 1, developer: 2, auditor: 3, viewer: 4 };
const orderedRoles = computed(() => [...admin.roles].sort((left, right) =>
  (roleOrder[left.key] ?? 99) - (roleOrder[right.key] ?? 99) || left.key.localeCompare(right.key)
));
const ownerRole = computed(() => selectedRoleValue.value?.key === "owner");
const adminRoleRestricted = computed(() => selectedRoleValue.value?.key === "admin" && !auth.user?.roles.includes("owner"));
const canEditSelectedRole = computed(() => auth.can("auth.roles.manage") && !ownerRole.value && !adminRoleRestricted.value);
const permissionGroups = computed(() => groupedPermissions(admin.permissions, ui.language));
const visibleBaseCapabilities = computed(() => baseCapabilities(ui.language));
const auditExportUrl = computed(() => `/api/audit/events/export?${new URLSearchParams(Object.entries(auditFilters).filter(([, value]) => value)).toString()}`);
const availableCapabilityCount = computed(() => Object.values(admin.monitor?.capabilities || {}).filter((item) => item.state === "available").length);
const diagnosticCapabilities = computed(() => Object.fromEntries(
  Object.entries(admin.monitor?.capabilities || {}).filter(([key]) => key !== "mcp"),
));
const currentDeployment = computed(() => window.location.origin);
const opsModeLabel = computed(() => {
  const labels: Record<string, string> = {
    local_process: tr("本地进程", "Local process"),
    local_docker: tr("本地 Docker", "Local Docker"),
    server_docker: tr("服务器 Docker", "Server Docker"),
  };
  return labels[admin.operationsTerminal?.mode || ""] || tr("未配置", "Not configured");
});
const opsCanOpen = computed(() => Boolean(admin.operationsTerminal?.configured && admin.operationsTerminal.url));
const opsCanEmbed = computed(() => opsCanOpen.value && ["embed", "both"].includes(admin.operationsTerminal?.presentation || ""));
const diagnosticSummaryLabels = computed<Record<string, string>>(() => ({
  turns: tr("运行记录", "Runs"),
  tools: tr("工具调用", "Tool calls"),
  trace_events: tr("时间线事件", "Timeline events"),
  incidents: tr("确定性事故", "Incidents"),
  errors: tr("错误事件", "Errors"),
}));
watch(() => auditFilters.from_ts, (value) => {
  if (value && auditFilters.to_ts && auditFilters.to_ts < value) auditFilters.to_ts = value;
});

onMounted(async () => { await loadTab("overview"); });
onBeforeUnmount(() => {
  if (opsFrameTimer) clearTimeout(opsFrameTimer);
  if (xhsLoginTimer) clearInterval(xhsLoginTimer);
  if (hotelLoginTimer) clearInterval(hotelLoginTimer);
});

async function loadTab(next: string) {
  tab.value = next;
  failure.value = "";
  actionStatus.value = "";
  try {
    if (next === "overview") {
      const loads: Promise<unknown>[] = [];
      if (auth.can("turn.read.any")) loads.push(admin.loadMonitor("error"));
      if (auth.can("diagnostics.system.use")) loads.push(admin.loadDiagnostics());
      await Promise.all(loads);
    }
    if (next === "users") {
      const loads: Promise<unknown>[] = [admin.loadUsers()];
      if (auth.isOwner) loads.push(admin.loadRegistrationPolicy());
      await Promise.all(loads);
    }
    if (next === "roles") { await admin.loadRoles(); selectedRole.value ||= orderedRoles.value[0]?.id || ""; }
    if (next === "skills") {
      const loads: Promise<unknown>[] = [admin.loadSkillSources(), admin.loadMcpStatus()];
      if (auth.isOwner) loads.push(admin.loadXhsStatus(), admin.loadHotelBrowserStatus());
      await Promise.all(loads);
      convergeXhsAdminStatus();
    }
    if (next === "monitor") {
      const loads: Promise<unknown>[] = [];
      if (auth.can("turn.read.any")) loads.push(admin.loadMonitor(recentRunStatus.value));
      if (auth.can("diagnostics.system.use")) loads.push(admin.loadDiagnostics(diagnosticFilters));
      if (auth.can("auth.users.read")) loads.push(admin.loadUsers());
      await Promise.all(loads);
    }
    if (next === "advanced") {
      const loads: Promise<unknown>[] = [admin.loadAudit(auditFilters)];
      if (auth.can("auth.users.read")) loads.push(admin.loadUsers());
      await Promise.all(loads);
    }
    if (next === "operations") await admin.loadOperationsTerminal();
  } catch (error) { failure.value = errorMessage(error); }
}
async function openMonitorSection(target: "failures" | "incidents") {
  if (!canOpenMonitor.value) return;
  if (target === "failures") recentRunStatus.value = "error";
  else {
    Object.assign(diagnosticFilters, {
      actor_user_id: "",
      session_id: "",
      component: "",
      error_code: "",
      status: "",
      minutes: "60",
    });
    timelineScope.value = "errors";
  }
  await loadTab("monitor");
  await nextTick();
  const section = document.getElementById(target === "failures" ? "monitor-runs" : "monitor-incidents");
  section?.focus({ preventScroll: true });
  section?.scrollIntoView?.({ behavior: "smooth", block: "start" });
}
async function createUser() {
  failure.value = ""; actionStatus.value = "";
  try { await import("@/api/client").then(({ api }) => api.createUser({ ...newUser })); Object.assign(newUser, { username: "", display_name: "", password: "", roles: ["viewer"] }); await admin.loadUsers(); actionStatus.value = tr("账号已创建", "Account created"); }
  catch (error) { failure.value = errorMessage(error); }
}
async function toggleRegistrationPolicy(enabled: boolean) {
  failure.value = "";
  actionStatus.value = "";
  try {
    await admin.updateRegistrationPolicy(enabled);
    auth.registrationEnabled = enabled;
    auth.registrationPolicyLoaded = true;
    actionStatus.value = enabled ? tr("注册已开放", "Registration opened") : tr("注册已关闭", "Registration closed");
  } catch (error) { failure.value = errorMessage(error); }
}
async function updateUser(id: string, payload: Record<string, unknown>) {
  failure.value = ""; actionStatus.value = "";
  try { await import("@/api/client").then(({ api }) => api.updateUser(id, payload)); await admin.loadUsers(); actionStatus.value = tr("账号设置已更新", "Account settings updated"); }
  catch (error) { failure.value = errorMessage(error); }
}
async function loadRecentRuns() {
  try { await admin.loadMonitor(recentRunStatus.value); }
  catch (error) { failure.value = errorMessage(error); }
}
async function runDiagnostics() {
  diagnosticBusy.value = true;
  failure.value = "";
  try {
    await admin.loadDiagnostics(diagnosticFilters);
    diagnosticUpdatedAt.value = fmt(new Date().toISOString());
  } catch (error) { failure.value = errorMessage(error); }
  finally { diagnosticBusy.value = false; }
}
function openDeleteUser(user: PublicUser) {
  deletingUser.value = user;
  deleteConfirmation.value = "";
  deleteConfirmationError.value = "";
  failure.value = "";
}
async function confirmDeleteUser() {
  if (!deletingUser.value) return;
  if (deleteConfirmation.value !== deletingUser.value.username) {
    deleteConfirmationError.value = tr("账号不一致，请重新输入", "Account does not match. Try again.");
    return;
  }
  deleteConfirmationError.value = "";
  deleteBusy.value = true;
  try {
    await import("@/api/client").then(({ api }) => api.deleteUser(deletingUser.value!.id, deleteConfirmation.value));
    deletingUser.value = null;
    deleteConfirmation.value = "";
    deleteConfirmationError.value = "";
    await admin.loadUsers();
    actionStatus.value = tr("账号已永久删除", "Account permanently deleted");
  } catch (error) { failure.value = errorMessage(error); }
  finally { deleteBusy.value = false; }
}
async function togglePermission(key: string, enabled: boolean) {
  const role = selectedRoleValue.value;
  if (!role || !canEditSelectedRole.value) return;
  const keys = enabled ? [...role.permission_keys, key] : role.permission_keys.filter((item) => item !== key);
  failure.value = ""; actionStatus.value = "";
  try { await admin.updateRole(role.id, [...new Set(keys)]); actionStatus.value = tr("角色权限已更新", "Role permissions updated"); }
  catch (error) { failure.value = errorMessage(error); }
}
function skillsForSource(source: string) {
  return (admin.skillSources?.skills || []).filter((skill) => skill.source === source);
}
async function syncSkillSource(source: string) {
  failure.value = ""; actionStatus.value = "";
  try { await admin.syncSkillSource(source); actionStatus.value = tr("Skill source 已同步", "Skill source synced"); }
  catch (error) { failure.value = errorMessage(error); }
}
async function refreshSkillSourceIndex(source: string) {
  failure.value = ""; actionStatus.value = "";
  try { await admin.refreshSkillSourceIndex(source); actionStatus.value = tr("Skill 索引已刷新", "Skill index refreshed"); }
  catch (error) { failure.value = errorMessage(error); }
}
function openOpsWindow() {
  const url = admin.operationsTerminal?.url;
  if (url) window.open(url, "_blank", "noopener,noreferrer");
}
function startOpsEmbed() {
  if (!opsCanEmbed.value) return;
  opsEmbedded.value = true;
  opsFrameFallback.value = false;
  if (opsFrameTimer) clearTimeout(opsFrameTimer);
  opsFrameTimer = setTimeout(() => fallbackOpsFrame(), 8000);
}
function markOpsFrameLoaded() {
  if (opsFrameTimer) clearTimeout(opsFrameTimer);
  opsFrameTimer = undefined;
}
function fallbackOpsFrame() {
  if (!opsEmbedded.value || opsFrameFallback.value) return;
  if (opsFrameTimer) clearTimeout(opsFrameTimer);
  opsFrameTimer = undefined;
  opsFrameFallback.value = true;
  opsEmbedded.value = false;
  openOpsWindow();
}
function fmt(value: unknown): string {
  if (!value) return "—";
  const date = new Date(String(value));
  if (Number.isNaN(date.getTime())) return String(value);
  const pad = (part: number) => String(part).padStart(2, "0");
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(date.getSeconds())}`;
}
function fmtDuration(value: unknown): string {
  if (value === null || value === undefined || value === "") return "—";
  const milliseconds = Number(value);
  if (!Number.isFinite(milliseconds) || milliseconds < 0) return "—";
  if (milliseconds < 1000) return `${milliseconds.toFixed(2)} ${tr("毫秒", "ms")}`;
  if (milliseconds < 60_000) return `${(milliseconds / 1000).toFixed(2)} ${tr("秒", "sec")}`;
  if (milliseconds < 3_600_000) return `${(milliseconds / 60_000).toFixed(2)} ${tr("分钟", "min")}`;
  return `${(milliseconds / 3_600_000).toFixed(2)} ${tr("小时", "hr")}`;
}
function statusLabel(value: unknown): string {
  return ({ error: tr("失败", "Failed"), completed: tr("完成", "Completed"), started: tr("运行中", "Running"), stopped: tr("已停止", "Stopped") } as Record<string, string>)[String(value)] || String(value || "—");
}
function channelLabel(value: unknown): string {
  return ({ web: tr("网页", "Web"), cli: tr("命令行", "CLI"), qq: "QQ", weixin: tr("微信", "WeChat") } as Record<string, string>)[String(value)] || String(value || "—");
}
async function refreshMcpAdmin() {
  failure.value = "";
  try {
    const loads: Promise<unknown>[] = [admin.loadMcpStatus()];
    if (auth.isOwner) loads.push(admin.loadXhsStatus(), admin.loadHotelBrowserStatus());
    await Promise.all(loads);
    convergeXhsAdminStatus();
  } catch (error) { failure.value = errorMessage(error); }
}
function convergeXhsAdminStatus() {
  if (!auth.isOwner || !admin.xhsStatus?.enabled) return;
  if (admin.xhsStatus.login_in_progress) {
    watchXhsLogin();
    return;
  }
  if (admin.xhsStatus.state === "unknown" && !admin.xhsAction) void checkXhsLogin(true);
}
async function checkXhsLogin(silent = false) {
  if (!silent) {
    failure.value = "";
    actionStatus.value = "";
  }
  try {
    await admin.checkXhsLogin();
    if (!silent) {
      actionStatus.value = admin.xhsStatus?.state === "authenticated"
        ? tr("小红书只读账号已登录", "Xiaohongshu read-only account is logged in")
        : tr("小红书只读账号需要重新登录", "Xiaohongshu read-only account needs login");
    }
  } catch (error) {
    if (!silent) failure.value = errorMessage(error);
  }
}
async function startXhsLogin() {
  failure.value = "";
  actionStatus.value = "";
  try {
    await admin.startXhsLogin();
    if (!admin.xhsStatus?.login_in_progress) {
      failure.value = xhsActionMessage(admin.xhsStatus?.code || "XHS_LOGIN_START_FAILED");
      return;
    }
    actionStatus.value = tr("扫码窗口已打开，完成后会自动检查登录状态", "The QR login window is open. Login will be checked automatically when it closes.");
    watchXhsLogin();
  } catch (error) { failure.value = errorMessage(error); }
}
async function restartXhsSidecar() {
  failure.value = "";
  actionStatus.value = "";
  try {
    await admin.restartXhsSidecar();
    if (admin.xhsStatus?.code !== "XHS_RESTARTED") {
      failure.value = xhsActionMessage(admin.xhsStatus?.code || "XHS_RESTART_FAILED");
      return;
    }
    actionStatus.value = tr("小红书只读服务已重启", "Xiaohongshu read-only service restarted");
  } catch (error) { failure.value = errorMessage(error); }
}
function watchXhsLogin() {
  if (xhsLoginTimer) clearInterval(xhsLoginTimer);
  xhsLoginTimer = setInterval(async () => {
    if (admin.xhsAction) return;
    try {
      await admin.loadXhsStatus();
      if (admin.xhsStatus?.login_in_progress) return;
      if (xhsLoginTimer) clearInterval(xhsLoginTimer);
      xhsLoginTimer = undefined;
      await checkXhsLogin();
    } catch {
      // The normal page error surface remains authoritative for explicit actions.
    }
  }, 2500);
}
function xhsStateLabel(value: string | undefined) {
  return ({ authenticated: tr("已登录", "Logged in"), auth_required: tr("需要登录", "Login required"), login_pending: tr("等待扫码", "Waiting for QR scan"), unavailable: tr("不可用", "Unavailable"), unknown: tr("未检查", "Not checked") } as Record<string, string>)[value || "unknown"];
}
async function saveHotelCredentials() {
  failure.value = "";
  actionStatus.value = "";
  const username = hotelCredentials.username.trim();
  const password = hotelCredentials.password;
  if (!username || !password) {
    failure.value = tr("请输入携程账号和登录密码", "Enter the Ctrip account and password");
    return;
  }
  hotelCredentials.password = "";
  try {
    await admin.saveHotelBrowserCredentials(username, password);
    hotelCredentialsOpen.value = false;
    actionStatus.value = admin.hotelBrowserStatus?.login_in_progress
      ? tr("凭据已保存到运行配置，正在自动登录携程", "Credentials were saved to runtime configuration and Ctrip login is running")
      : hotelActionMessage(admin.hotelBrowserStatus?.code || "HOTEL_LOGIN_START_FAILED");
    if (admin.hotelBrowserStatus?.login_in_progress) watchHotelLogin();
  } catch (error) {
    failure.value = errorMessage(error);
  }
}
async function startHotelLogin() {
  failure.value = "";
  actionStatus.value = "";
  try {
    await admin.startHotelBrowserLogin();
    if (!admin.hotelBrowserStatus?.login_in_progress) {
      failure.value = hotelActionMessage(admin.hotelBrowserStatus?.code || "HOTEL_LOGIN_START_FAILED");
      return;
    }
    actionStatus.value = tr("正在使用已保存凭据登录；需要验证时会保留可见浏览器", "Signing in with saved credentials; a visible browser remains open if verification is required");
    watchHotelLogin();
  } catch (error) { failure.value = errorMessage(error); }
}
async function deleteHotelCredentials() {
  if (!window.confirm(tr("删除运行配置中保存的携程账号密码？浏览器登录态不会同时删除。", "Delete the Ctrip credentials saved in runtime configuration? The existing browser session will remain."))) return;
  failure.value = "";
  actionStatus.value = "";
  try {
    await admin.deleteHotelBrowserCredentials();
    if (admin.hotelBrowserStatus?.code === "HOTEL_CREDENTIALS_EXTERNALLY_MANAGED") {
      actionStatus.value = hotelActionMessage(admin.hotelBrowserStatus.code);
      return;
    }
    hotelCredentials.username = "";
    hotelCredentials.password = "";
    actionStatus.value = tr("已删除保存的携程账号密码", "Saved Ctrip credentials were deleted");
  } catch (error) { failure.value = errorMessage(error); }
}
function watchHotelLogin() {
  if (hotelLoginTimer) clearInterval(hotelLoginTimer);
  hotelLoginTimer = setInterval(async () => {
    if (admin.hotelBrowserAction) return;
    try {
      await admin.loadHotelBrowserStatus();
      if (admin.hotelBrowserStatus?.login_in_progress) return;
      if (hotelLoginTimer) clearInterval(hotelLoginTimer);
      hotelLoginTimer = undefined;
      actionStatus.value = admin.hotelBrowserStatus?.state === "authenticated"
        ? tr("携程账号登录成功，后续查询会自动复用", "Ctrip login succeeded and will be reused for later queries")
        : hotelActionMessage(admin.hotelBrowserStatus?.code || "HOTEL_LOGIN_FAILED");
    } catch {
      // Explicit actions continue to use the normal page error surface.
    }
  }, 2500);
}
function hotelStateLabel(value: string | undefined) {
  return ({ authenticated: tr("已登录", "Logged in"), auth_required: tr("需要验证", "Verification required"), login_pending: tr("登录中", "Signing in"), not_configured: tr("未配置", "Not configured"), unavailable: tr("不可用", "Unavailable"), unknown: tr("未检查", "Not checked") } as Record<string, string>)[value || "unknown"];
}
function hotelActionMessage(code: string) {
  return ({
    HOTEL_BROWSER_DEPENDENCY_MISSING: tr("未安装 hotel-browser 可选依赖，暂时不能打开携程登录浏览器", "The optional hotel-browser dependency is not installed"),
    HOTEL_CREDENTIAL_STORE_UNAVAILABLE: tr("运行配置中的凭据保存不可用", "Runtime environment credential storage is unavailable"),
    HOTEL_CREDENTIALS_EXTERNALLY_MANAGED: tr("凭据由服务器环境变量或部署 Secret 管理，请在部署配置中修改", "Credentials are managed by environment variables or deployment Secrets; update the deployment configuration"),
    HOTEL_CREDENTIALS_NOT_CONFIGURED: tr("请先保存携程账号密码", "Save the Ctrip account credentials first"),
    HOTEL_MANUAL_VERIFICATION_REQUIRED: tr("携程要求验证码或安全验证，请在弹出的浏览器中完成", "Ctrip requires manual verification in the opened browser"),
    HOTEL_LOGIN_VERIFICATION_TIMEOUT: tr("携程安全验证等待超时，请重新登录", "Ctrip verification timed out; start login again"),
    HOTEL_LOGIN_START_FAILED: tr("携程登录助手启动失败", "The Ctrip login helper could not be started"),
    HOTEL_LOGIN_FAILED: tr("携程登录未完成", "Ctrip login did not complete"),
  } as Record<string, string>)[code] || tr("携程账号操作未完成", "The Ctrip account action did not complete");
}
function xhsActionMessage(code: string) {
  return ({
    XHS_LOGIN_UNSUPPORTED: tr("当前运行环境不能弹出本机扫码窗口", "This runtime cannot open a local QR login window"),
    XHS_LOGIN_START_FAILED: tr("小红书扫码窗口打开失败", "The Xiaohongshu QR login window could not be opened"),
    XHS_RESTART_NOT_OWNED: tr("当前小红书服务由外部进程管理，不能从这里重启", "The Xiaohongshu service is externally managed and cannot be restarted here"),
    XHS_RESTART_UNAVAILABLE: tr("小红书本地服务当前不可用", "The local Xiaohongshu service is unavailable"),
    XHS_RESTART_FAILED: tr("小红书只读服务重启失败", "The Xiaohongshu read-only service could not be restarted"),
  } as Record<string, string>)[code] || tr("小红书管理操作未完成", "The Xiaohongshu management action did not complete");
}
function mcpAuthLabel(serverId: string, value: unknown): string {
  if (serverId === "xhs-readonly") return tr("扫码 / Cookie", "QR / Cookie");
  if (["amap-maps", "tavily"].includes(serverId)) return "API Key";
  const state = String(value || "").toLowerCase();
  return ({
    disabled: tr("无需认证", "No authentication"),
    ready: tr("OAuth 已连接", "OAuth connected"),
    authenticated: tr("OAuth 已连接", "OAuth connected"),
    connected: tr("OAuth 已连接", "OAuth connected"),
    required: tr("需要 OAuth", "OAuth required"),
    pending: tr("等待 OAuth 授权", "OAuth pending"),
    error: tr("OAuth 异常", "OAuth error"),
  } as Record<string, string>)[state] || tr("无需认证", "No authentication");
}
function componentLabel(value: string): string {
  return ({ agent: tr("Agent 运行时", "Agent runtime"), gateway: "Gateway", turn: tr("对话运行", "Turn runtime"), llm: tr("模型服务", "Model service"), tool: tr("工具调用", "Tool calls"), channel: tr("外部渠道", "Channels"), mcp: "MCP", session: tr("会话", "Sessions"), context: tr("上下文", "Context"), memory: "Memory", subagent: tr("子智能体", "Subagents") } as Record<string, string>)[value] || value;
}
function diagnosticGuide(value: unknown): { title: string; explanation: string; impact: string; action: string } {
  const code = String(value || "RUNTIME_ERROR");
  const exact: Record<string, { title: string; explanation: string; impact: string; action: string }> = {
    WEIXIN_TOKEN_STALE: {
      title: tr("微信连接凭据已失效", "Weixin credentials expired"),
      explanation: tr("系统中某个已经绑定的微信账号令牌被微信服务判定为失效，系统已停止继续使用旧凭据。这不表示无人连接；无人绑定时不会产生该事故。", "Weixin marked the token of an already-bound account as stale, so the system stopped using it. This does not mean nobody is connected; an unbound system does not produce this incident."),
      impact: tr("仅该微信账号暂时不能收发消息；Web、CLI 和其他渠道不受影响。跨用户诊断按隐私边界不显示账号归属。", "Only that Weixin account cannot send or receive messages. Web, CLI, and other channels are unaffected. Cross-user diagnostics intentionally hide account ownership."),
      action: tr("需要该微信账号所属用户在自己的“设置 → 渠道连接”中重新连接并扫码。管理员无法代替其他用户完成个人微信授权。", "The account owner must reconnect and scan the QR code in their own Settings → Channel connections. An administrator cannot complete another user's personal Weixin authorization."),
    },
    GATEWAY_RESTART_INTERRUPTED: {
      title: tr("运行被 Gateway 重启中断", "Run interrupted by a Gateway restart"),
      explanation: tr("这条运行开始后，Gateway 在完成前退出或重启，因此没有正常结束。", "The Gateway exited or restarted before this run could finish."),
      impact: tr("只影响这一次尚未完成的运行，不表示会话数据损坏。", "Only this unfinished run was affected; it does not indicate session data corruption."),
      action: tr("确认 Gateway 已稳定运行后重新发送该请求。", "Confirm the Gateway is stable, then retry the request."),
    },
    RATE_LIMITED: {
      title: tr("模型服务触发限流", "Model provider rate limit"),
      explanation: tr("模型服务拒绝了短时间内过多的请求。", "The model provider rejected too many requests in a short period."),
      impact: tr("相关模型调用失败或被延迟，其他服务通常不受影响。", "Related model calls may fail or be delayed; other services are usually unaffected."),
      action: tr("稍后重试，并检查模型端点的限额、并发配置和备用模型状态。", "Retry later and check endpoint limits, concurrency, and fallback model status."),
    },
  };
  if (exact[code]) return exact[code];
  if (code.includes("TIMEOUT")) return { title: tr("调用等待超时", "Request timed out"), explanation: tr("目标组件未在限定时间内返回结果。", "The target component did not respond within the allowed time."), impact: tr("当前请求没有完成。", "The current request did not complete."), action: tr("检查对应组件的连通性和负载后重试。", "Check the component's connectivity and load, then retry.") };
  if (code.includes("AUTH") || code.includes("TOKEN") || code.includes("CREDENTIAL")) return { title: tr("认证凭据不可用", "Credentials unavailable"), explanation: tr("目标服务拒绝或无法读取当前凭据。", "The target service rejected or could not read the current credentials."), impact: tr("依赖该凭据的功能暂时不可用。", "Features using these credentials are temporarily unavailable."), action: tr("检查相应服务的账号绑定或密钥配置。", "Check the account binding or credential configuration for the service.") };
  return { title: tr("运行组件报告错误", "Runtime component reported an error"), explanation: tr(`组件返回错误码 ${code}，当前证据不足以进一步自动判断根因。`, `The component returned ${code}; current evidence is insufficient to determine a deeper root cause.`), impact: tr("影响范围请结合下方技术证据中的组件、会话和请求标识判断。", "Use the component, session, and request identifiers below to determine the affected scope."), action: tr("展开技术证据，按错误码、会话或请求标识继续筛选。", "Expand the technical evidence and filter by code, session, or request ID.") };
}
function diagnosticEventLabel(event: Record<string, unknown>): string {
  const name = String(event.event || event.tool_name || event.status || "");
  return ({
    "channel.start_failed": tr("渠道启动失败", "Channel failed to start"),
    "channel.ready": tr("渠道已就绪", "Channel ready"),
    "channel.enabled": tr("渠道已启用", "Channel enabled"),
    "channel.stop": tr("渠道已停止", "Channel stopped"),
    "channel.weixin.reconnect_required": tr("微信账号需要重新连接", "Weixin account requires reconnection"),
    "mcp.runtime_closed": tr("MCP 运行时已关闭", "MCP runtime closed"),
    "memory.scheduler.stop": tr("Memory 调度器已停止", "Memory scheduler stopped"),
    "llm.error": tr("模型调用失败", "Model call failed"),
  } as Record<string, string>)[name] || tr("运行事件", "Runtime event");
}
function diagnosticEventKey(event: Record<string, unknown>): string {
  return String(event.event || event.tool_name || event.status || event.kind || "runtime.event");
}
function eventIsError(event: Record<string, unknown>): boolean {
  if (typeof event.is_error === "boolean") return event.is_error;
  return false;
}
function diagnosticFieldLabel(value: string): string {
  return ({ error_message: tr("错误消息", "Error message"), reason_code: tr("原因代码", "Reason code"), status: tr("状态", "Status"), route: tr("请求路径", "Route"), session_id: "Session ID", turn_id: "Turn ID", request_id: "Request / Trace ID", model: tr("模型", "Model"), endpoint: tr("模型端点", "Endpoint"), duration_ms: tr("耗时（毫秒）", "Duration (ms)") } as Record<string, string>)[value] || value;
}
function diagnosticWindowLabel(value: unknown): string {
  const minutes = Number(value);
  if (minutes < 60) return tr(`${minutes} 分钟`, `${minutes} minutes`);
  if (minutes < 1440) return tr(`${minutes / 60} 小时`, `${minutes / 60} hours`);
  return tr(`${minutes / 1440} 天`, `${minutes / 1440} days`);
}
function incidentRuleLabel(value: unknown): string {
  return String(value) === "same_component_code_subject_within_query_window"
    ? tr("同一组件、错误码和对象在当前查询时间范围内合并为一项事故", "Events with the same component, code, and subject are grouped in this query window")
    : String(value || "—");
}
function incidentEvidence(incident: Record<string, unknown>): Record<string, unknown>[] {
  return Array.isArray(incident.evidence)
    ? incident.evidence.filter((item): item is Record<string, unknown> => Boolean(item) && typeof item === "object")
    : [];
}
function toggleRunRecord(turnId: unknown) {
  const key = String(turnId || "unknown");
  openRunRecords.value[key] = !openRunRecords.value[key];
}
function toggleTimelineEvent(evidenceId: unknown) {
  const key = String(evidenceId || "unknown");
  openTimelineEvents.value[key] = !openTimelineEvents.value[key];
}
</script>

<template>
  <div class="admin-shell">
    <aside class="admin-sidebar glass-panel">
      <a class="brand-lockup compact" href="/"><img :src="'/static/zhice-logo-a.png'" alt="" /><strong>ZhiCe-Agent</strong></a>
      <p class="admin-kicker">{{ tr('管理后台', 'Administration') }}</p>
      <nav><button v-for="item in tabs" :key="item.key" :class="{ active: tab === item.key }" @click="loadTab(item.key)"><component :is="item.icon" :size="18" />{{ item.label }}</button></nav>
      <button class="back-chat" @click="router.push('/')"><ArrowLeft :size="17" />{{ tr('返回聊天', 'Back to chat') }}</button>
    </aside>
    <main class="admin-main">
      <header class="admin-header"><div><span class="eyebrow">{{ tr('管理后台', 'Administration') }}</span><h1>{{ tabs.find((item) => item.key === tab)?.label }}</h1></div><div class="admin-header-actions"><QuickPreferences /><button v-if="tab !== 'overview'" class="icon-button" :title="tr('刷新', 'Refresh')" @click="loadTab(tab)"><RefreshCw :size="18" /></button></div></header>
      <p v-if="failure" class="form-error admin-action-feedback" role="alert" aria-live="assertive">{{ failure }}</p><p v-if="actionStatus" class="form-success admin-action-feedback" role="status" aria-live="polite">{{ actionStatus }}</p>

      <section v-if="tab === 'overview'" class="admin-overview">
        <div class="overview-hero"><div><span class="eyebrow">{{ tr('系统运行概览', 'System overview') }}</span><h2>{{ (admin.diagnostics?.summary.incidents || admin.monitor?.activity.summary.failed) ? tr('发现需要关注的运行问题', 'Runtime issues need attention') : tr('系统运行正常', 'System is operating normally') }}</h2><p>{{ tr('先看服务、模型和近期异常；需要排查时进入运行诊断查看事故证据与跨组件时间线。', 'Review services, model, and recent failures first. Open Runtime diagnostics for incident evidence and the cross-component timeline.') }}</p></div><Activity :size="52" /></div>
        <div class="overview-status-grid">
          <article><span>Gateway</span><strong>{{ admin.monitor?.gateway.status || tr('无权限', 'Unavailable') }}</strong><small>ZhiCe-Agent</small></article>
          <article><span>{{ tr('当前模型', 'Current model') }}</span><strong class="overview-model">{{ admin.monitor?.gateway.current_model || '—' }}</strong><small>{{ tr(`${availableCapabilityCount} 项能力可用`, `${availableCapabilityCount} capabilities available`) }}</small></article>
          <button type="button" data-overview-target="failures" :disabled="!canOpenMonitor" :class="{ attention: Number(admin.monitor?.activity.summary.failed || 0) > 0 }" :aria-label="tr('查看近期失败运行', 'View recent failed runs')" @click="openMonitorSection('failures')"><span>{{ tr('近期失败', 'Recent failures') }}</span><strong>{{ admin.monitor?.activity.summary.failed ?? '—' }}</strong><small>{{ tr('结构化运行记录', 'Structured run records') }}</small></button>
          <button type="button" data-overview-target="incidents" :disabled="!canOpenMonitor" :class="{ attention: Number(admin.diagnostics?.summary.incidents || 0) > 0 }" :aria-label="tr('查看当前事故证据', 'View current incident evidence')" @click="openMonitorSection('incidents')"><span>{{ tr('当前事故', 'Current incidents') }}</span><strong>{{ admin.diagnostics?.summary.incidents ?? '—' }}</strong><small>{{ tr('近 60 分钟确定性聚合', 'Deterministic, last 60 minutes') }}</small></button>
        </div>
        <div class="overview-grid"><button v-for="item in tabs.filter((item) => item.key !== 'overview')" :key="item.key" @click="loadTab(item.key)"><component :is="item.icon" :size="22" /><strong>{{ item.label }}</strong><span>{{ { users: tr('账号状态与角色分配', 'Account status and role assignment'), roles: tr('能力域与技术 key', 'Capability domains and technical keys'), skills: tr('MCP 服务与 Skill source 状态、健康及同步', 'MCP services and Skill source status, health, and sync'), monitor: tr('事故、失败运行与诊断时间线', 'Incidents, failed runs, and diagnostic timeline'), operations: tr('独立受保护的服务器运维入口', 'Independent protected server operations entry'), advanced: tr('低频安全审计与导出', 'Low-frequency security audit and export') }[item.key] }}</span></button></div>
      </section>

      <section v-else-if="tab === 'users'" class="admin-section">
        <div v-if="auth.isOwner && admin.registrationPolicy" class="registration-policy-card">
          <span><strong>{{ tr('允许新用户注册', 'Allow new user registration') }}</strong><small>{{ admin.registrationPolicy.registration_enabled ? tr('公网登录页和注册接口当前已开放。', 'The public registration page and API are currently open.') : tr('注册入口和接口均已关闭；管理员仍可在下方手工创建账号。', 'The registration entry and API are closed. Administrators can still create accounts below.') }}</small></span>
          <label class="policy-switch"><input type="checkbox" :checked="admin.registrationPolicy.registration_enabled" :disabled="admin.registrationPolicyBusy" :aria-label="tr('允许新用户注册', 'Allow new user registration')" @change="toggleRegistrationPolicy(($event.target as HTMLInputElement).checked)" /><i></i><em>{{ admin.registrationPolicy.registration_enabled ? tr('已开放', 'Open') : tr('已关闭', 'Closed') }}</em></label>
        </div>
        <form v-if="auth.can('auth.users.manage')" class="admin-create-form" autocomplete="off" @submit.prevent="createUser"><h2>{{ tr('创建账号', 'Create account') }}</h2><input v-model="newUser.username" name="admin-new-username" autocomplete="off" required :placeholder="tr('新账号', 'New account')" /><input v-model="newUser.display_name" name="admin-new-display-name" autocomplete="off" :placeholder="tr('昵称（可选）', 'Nickname (optional)')" /><input v-model="newUser.password" name="admin-new-password" type="password" autocomplete="new-password" minlength="8" required :placeholder="tr('设置初始密码', 'Set initial password')" /><select v-model="newUser.roles[0]"><option v-for="role in ['viewer','developer','auditor','admin']" :key="role" :value="role">{{ roleName(role, ui.language) }}</option></select><button class="primary-button">{{ tr('创建', 'Create') }}</button></form>
        <div class="data-table user-table"><div class="table-head"><span>{{ tr('账号', 'Account') }}</span><span>{{ tr('角色', 'Role') }}</span><span>{{ tr('状态', 'Status') }}</span><span>{{ tr('管理', 'Actions') }}</span></div><div v-for="user in admin.users" :key="user.id" class="table-row"><span><strong>{{ user.display_name }}</strong><small>@{{ user.username }}</small></span><span><select v-if="auth.can('auth.users.manage') && !user.roles.includes('owner')" :value="user.roles[0]" @change="updateUser(user.id, { roles: [($event.target as HTMLSelectElement).value] })"><option v-for="role in ['viewer','developer','auditor','admin']" :key="role" :value="role">{{ roleName(role, ui.language) }}</option></select><template v-else>{{ user.roles.map((role) => roleName(role, ui.language)).join('、') }}</template></span><span><i :class="`status-dot ${user.status}`"></i>{{ user.status === 'active' ? tr('启用', 'Active') : tr('停用', 'Disabled') }}</span><span class="row-actions"><button v-if="auth.can('auth.users.manage') && !user.roles.includes('owner')" @click="updateUser(user.id, { status: user.status === 'active' ? 'disabled' : 'active' })">{{ user.status === 'active' ? tr('停用', 'Disable') : tr('启用', 'Enable') }}</button><button v-if="auth.can('auth.admin.manage') && user.roles.includes('admin')" @click="updateUser(user.id, { can_manage_admins: !user.can_manage_admins })">{{ user.can_manage_admins ? tr('撤销委派', 'Revoke delegation') : tr('委派管理', 'Delegate management') }}</button><button v-if="auth.user?.roles.includes('owner') && user.status === 'disabled' && !user.roles.includes('owner')" class="danger-text-button" @click="openDeleteUser(user)"><Trash2 :size="14" />{{ tr('永久删除', 'Delete permanently') }}</button><span v-if="user.roles.includes('owner')" class="readonly-pill">{{ tr('固定只读', 'Read-only') }}</span></span></div></div>
      </section>

      <section v-else-if="tab === 'roles'" class="roles-layout">
        <aside class="role-list"><button v-for="role in orderedRoles" :key="role.id" :class="{ active: selectedRole === role.id }" @click="selectedRole = role.id"><strong>{{ roleName(role.key, ui.language) || role.name }}</strong><small>{{ role.permission_keys.length }} {{ tr('项附加特权', 'additional permissions') }}</small></button></aside>
        <div v-if="selectedRoleValue" class="role-detail"><header><div><span class="eyebrow">{{ selectedRoleValue.key }}</span><h2>{{ roleName(selectedRoleValue.key, ui.language) || selectedRoleValue.name }}</h2><p>{{ selectedRoleValue.description }}</p></div><span v-if="ownerRole || adminRoleRestricted" class="role-lock"><LockKeyhole :size="14" />{{ adminRoleRestricted ? tr('仅系统所有者可修改', 'Only Owner can modify') : tr('系统固定，权限不可修改', 'System role · permissions locked') }}</span></header><div class="base-capabilities"><strong>{{ tr('所有登录用户的基础能力', 'Base capabilities for all signed-in users') }}</strong><span v-for="capability in visibleBaseCapabilities" :key="capability">{{ capability }}</span></div><section v-for="(keys, group) in permissionGroups" :key="group" class="permission-group"><h3>{{ group }}</h3><label v-for="key in keys" :key="key"><span><strong>{{ permissionLabel(key, ui.language) }}</strong><small>{{ key }}</small></span><input type="checkbox" :checked="selectedRoleValue.permission_keys.includes(key)" :disabled="!canEditSelectedRole" @change="togglePermission(key, ($event.target as HTMLInputElement).checked)" /></label></section><details class="technical-details" :open="technicalOpen[selectedRoleValue.id]" @toggle="technicalOpen[selectedRoleValue.id] = ($event.target as HTMLDetailsElement).open"><summary>{{ tr('技术详情', 'Technical details') }} <ChevronDown :size="15" /></summary><code v-for="key in selectedRoleValue.permission_keys" :key="key">{{ key }}</code></details></div>
      </section>

      <section v-else-if="tab === 'skills'" class="admin-section skill-sources-section">
        <div class="truth-banner"><BookOpen :size="22" /><span><strong>{{ tr('Skill source 运行真值', 'Skill source runtime truth') }}</strong><small>{{ tr('状态来自持久同步记录和派生索引；页面不显示凭据、仓库 URL、宿主机路径或原始 stderr。', 'Status comes from persistent sync records and the derived index. Credentials, repository URLs, host paths, and raw stderr are never shown.') }}</small></span></div>
        <section class="mcp-monitor-section">
          <header><div><span class="eyebrow">MCP Runtime</span><h2>{{ tr('MCP 服务监控', 'MCP server monitoring') }}</h2><p>{{ tr('连接、Catalog 和调用统计来自当前 Gateway 运行时；自动重连由 Runtime 按退避策略持续处理。', 'Connection, catalog, and call facts come from the current Gateway runtime. Runtime continues automatic reconnect with backoff.') }}</p></div><button type="button" @click="refreshMcpAdmin"><RefreshCw :size="15" />{{ tr('刷新', 'Refresh') }}</button></header>
          <div class="mcp-summary-grid"><article><span>Servers</span><strong>{{ visibleServiceCount }}</strong></article><article><span>{{ tr('活动调用', 'Active calls') }}</span><strong>{{ admin.mcpStatus?.active_calls || 0 }}</strong></article><article><span>{{ tr('自动重连', 'Reconnects') }}</span><strong>{{ admin.mcpStatus?.reconnect_count || 0 }}</strong></article><article><span>Catalog</span><strong>v{{ admin.mcpStatus?.catalog_version || 0 }}</strong></article></div>
          <div class="mcp-server-grid">
            <article v-for="server in orderedMcpServers" :key="server.server_id" class="mcp-server-card" :data-state="server.state">
              <header><span><i :class="`status-dot ${server.state === 'ready' ? 'available' : server.state}`"></i><strong>{{ server.server_id }}</strong></span><b>{{ server.state }}</b></header>
              <dl><dt>Tools</dt><dd>{{ server.tool_count }}</dd><dt>{{ tr('调用', 'Calls') }}</dt><dd>{{ server.call_count }}</dd><dt>{{ tr('成功', 'Success') }}</dt><dd>{{ server.success_count }}</dd><dt>{{ tr('失败', 'Failures') }}</dt><dd>{{ server.failure_count }}</dd><dt>{{ tr('取消', 'Cancelled') }}</dt><dd>{{ server.cancelled_count }}</dd><dt>{{ tr('认证方式', 'Authentication') }}</dt><dd>{{ mcpAuthLabel(server.server_id, server.oauth_state) }}</dd></dl>
              <div v-if="auth.isOwner && server.server_id === 'xhs-readonly' && admin.xhsStatus" class="mcp-server-actions"><button type="button" :disabled="Boolean(admin.xhsAction) || !admin.xhsStatus.restart_supported" @click="restartXhsSidecar">{{ admin.xhsAction === 'restart' ? tr('重启中…', 'Restarting…') : tr('重启 MCP 服务', 'Restart MCP server') }}</button></div>
              <div v-if="server.error_code || server.last_tool_error_code || server.last_connection_reason_code" class="source-safe-error"><code>{{ server.error_code || server.last_tool_error_code || server.last_connection_reason_code }}</code><span>{{ tr('最近连接或调用存在结构化错误；Runtime 会继续自动恢复连接，未知结果的调用不会自动重放。', 'A recent connection or call has a structured error. Runtime continues reconnecting automatically; calls with unknown outcomes are not replayed.') }}</span></div>
              <small v-if="server.last_connection_state">{{ tr('最近连接', 'Last connection') }}：{{ server.last_connection_state }} · {{ fmt(server.last_connection_at * 1000) }}</small>
            </article>
            <p v-if="!admin.mcpStatus?.servers.length" class="empty-note">{{ tr('当前没有已配置的 MCP Server。', 'No MCP Servers are currently configured.') }}</p>
          </div>
        </section>
        <section v-if="auth.isOwner && (admin.xhsStatus || admin.hotelBrowserStatus)" class="external-platform-section">
          <header><div><span class="eyebrow">External platforms</span><h2>{{ tr('外部平台账号', 'External platform accounts') }}</h2><p>{{ tr('这里只管理业务平台登录态；协议连接、Catalog 和调用健康仍归 MCP 服务监控。', 'This area manages business-platform sessions only. Protocol connections, catalogs, and call health remain in MCP server monitoring.') }}</p></div></header>
          <div class="platform-account-grid">
            <article v-if="admin.xhsStatus" class="platform-account-card" :data-state="admin.xhsStatus.state">
              <header><span><i :class="`status-dot ${admin.xhsStatus.state === 'authenticated' ? 'available' : admin.xhsStatus.state === 'auth_required' ? 'degraded' : admin.xhsStatus.state}`"></i><strong>{{ tr('小红书', 'Xiaohongshu') }}</strong></span><b>{{ xhsStateLabel(admin.xhsStatus.state) }}</b></header>
              <dl class="platform-account-facts"><dt>{{ tr('认证方式', 'Authentication') }}</dt><dd>{{ tr('扫码 / Cookie', 'QR scan / Cookie') }}</dd></dl>
              <section class="platform-account-panel">
                <header><span><LockKeyhole :size="15" /><strong>{{ tr('小红书账号登录', 'Xiaohongshu account login') }}</strong></span><b>{{ xhsStateLabel(admin.xhsStatus.state) }}</b></header>
                <p>{{ admin.xhsStatus.state === 'authenticated' ? tr('旅行规划可读取社区公开笔记，后续会自动复用当前登录态。', 'Travel planning can read public community notes and will reuse the current session.') : admin.xhsStatus.state === 'login_pending' ? tr('请在本机扫码窗口完成登录。', 'Complete login in the local QR window.') : tr('登录态失效后需要重新扫码或完成手机验证，成功后系统自动复用 Cookie。', 'After the session expires, scan again or complete phone verification; the resulting Cookie is reused automatically.') }}</p>
                <small>{{ tr('Cookie 最近更新', 'Cookie last updated') }}：{{ fmt(admin.xhsStatus.cookie_updated_at) }}</small>
                <div class="platform-account-actions"><button type="button" :disabled="Boolean(admin.xhsAction) || admin.xhsStatus.login_in_progress" @click="checkXhsLogin()">{{ admin.xhsAction === 'check' ? tr('检查中…', 'Checking…') : tr('检查登录', 'Check login') }}</button><button type="button" :disabled="Boolean(admin.xhsAction) || !admin.xhsStatus.login_supported" @click="startXhsLogin">{{ admin.xhsStatus.login_in_progress ? tr('等待扫码…', 'Waiting for QR…') : admin.xhsAction === 'login' ? tr('打开中…', 'Opening…') : tr('重新登录', 'Log in again') }}</button></div>
              </section>
            </article>
            <article v-if="admin.hotelBrowserStatus" class="platform-account-card" :data-state="admin.hotelBrowserStatus.state">
              <header><span><i :class="`status-dot ${admin.hotelBrowserStatus.state === 'authenticated' ? 'available' : admin.hotelBrowserStatus.state === 'auth_required' ? 'degraded' : admin.hotelBrowserStatus.state}`"></i><strong>{{ tr('携程', 'Ctrip') }}</strong></span><b>{{ hotelStateLabel(admin.hotelBrowserStatus.state) }}</b></header>
              <dl class="platform-account-facts"><dt>{{ tr('认证方式', 'Authentication') }}</dt><dd>{{ tr('账号登录', 'Account login') }}</dd></dl>
              <section class="platform-account-panel">
                <header><span><LockKeyhole :size="15" /><strong>{{ tr('携程账号登录', 'Ctrip account login') }}</strong></span><b>{{ admin.hotelBrowserStatus.account_hint || tr('未保存账号', 'No account saved') }}</b></header>
                <p>{{ admin.hotelBrowserStatus.credential_configured ? tr('登录态会自动复用；仅查询酒店与账号观察价。', 'The session is reused automatically for read-only hotel and account-observed prices.') : tr('首次保存账号密码后自动登录，安全验证需要在弹出窗口完成。', 'Save credentials once to sign in; complete any security verification in the opened browser.') }}</p>
                <form v-if="!admin.hotelBrowserStatus.credential_configured || hotelCredentialsOpen" class="platform-credential-form" autocomplete="off" @submit.prevent="saveHotelCredentials">
                  <input v-model="hotelCredentials.username" name="ctrip-account" autocomplete="off" maxlength="320" required :placeholder="tr('携程手机号、用户名或邮箱', 'Ctrip phone, username, or email')" />
                  <input v-model="hotelCredentials.password" name="ctrip-password" type="password" autocomplete="new-password" maxlength="4096" required :placeholder="admin.hotelBrowserStatus.credential_configured ? tr('输入新密码替换已保存凭据', 'Enter a new password to replace saved credentials') : tr('携程登录密码', 'Ctrip login password')" />
                  <button type="submit" :disabled="Boolean(admin.hotelBrowserAction) || !admin.hotelBrowserStatus.credential_store_supported">{{ admin.hotelBrowserAction === 'save' ? tr('保存中…', 'Saving…') : admin.hotelBrowserStatus.credential_configured ? tr('保存并重新登录', 'Save and sign in again') : tr('保存并自动登录', 'Save and sign in') }}</button>
                </form>
                <small>{{ tr('凭据更新', 'Credentials updated') }}：{{ fmt(admin.hotelBrowserStatus.credentials_updated_at) }} · {{ admin.hotelBrowserStatus.browser_supported ? tr('浏览器已就绪', 'Browser ready') : tr('缺少浏览器依赖', 'Browser dependency missing') }}</small>
                <div class="platform-account-actions">
                  <button type="button" :disabled="Boolean(admin.hotelBrowserAction) || !admin.hotelBrowserStatus.login_supported || !admin.hotelBrowserStatus.credential_configured" @click="startHotelLogin">{{ admin.hotelBrowserStatus.login_in_progress ? tr('登录中…', 'Signing in…') : admin.hotelBrowserStatus.state === 'authenticated' ? tr('重新登录', 'Sign in again') : tr('使用已保存凭据登录', 'Sign in with saved credentials') }}</button>
                  <button v-if="admin.hotelBrowserStatus.credential_configured && admin.hotelBrowserStatus.credential_store_supported" type="button" :disabled="Boolean(admin.hotelBrowserAction)" @click="hotelCredentialsOpen = !hotelCredentialsOpen">{{ hotelCredentialsOpen ? tr('取消更新', 'Cancel update') : tr('更新账号密码', 'Update credentials') }}</button>
                  <button type="button" :disabled="Boolean(admin.hotelBrowserAction) || !admin.hotelBrowserStatus.credential_configured || admin.hotelBrowserStatus.credential_source === 'environment'" @click="deleteHotelCredentials">{{ admin.hotelBrowserAction === 'delete' ? tr('删除中…', 'Deleting…') : tr('删除凭据', 'Delete credentials') }}</button>
                </div>
              </section>
              <div v-if="admin.hotelBrowserStatus.code && !['OK','HOTEL_AUTH_NOT_CHECKED','HOTEL_CREDENTIALS_NOT_CONFIGURED'].includes(admin.hotelBrowserStatus.code)" class="source-safe-error"><code>{{ admin.hotelBrowserStatus.code }}</code><span>{{ hotelActionMessage(admin.hotelBrowserStatus.code) }}</span></div>
            </article>
          </div>
        </section>
        <div class="skill-source-list">
          <article v-for="source in admin.skillSources?.sources" :key="source.source" class="skill-source-card">
            <header><div><span class="eyebrow">Skill source</span><h2>{{ source.source }}</h2></div><span class="source-health"><i :class="`status-dot ${source.health === 'healthy' ? 'available' : source.health}`"></i>{{ source.health }}</span></header>
            <dl class="skill-source-facts"><dt>{{ tr('启用', 'Enabled') }}</dt><dd>{{ source.enabled ? tr('是', 'Yes') : tr('否', 'No') }}</dd><dt>{{ tr('同步', 'Sync') }}</dt><dd>{{ source.sync_enabled ? tr('启用', 'Enabled') : tr('停用', 'Disabled') }}</dd><dt>Target</dt><dd><code>{{ source.configured_target || '—' }}</code></dd><dt>Commit</dt><dd><code>{{ source.current_commit || '—' }}</code></dd><dt>{{ tr('上次同步', 'Last sync') }}</dt><dd>{{ fmt(source.last_sync_finished_at) }}</dd><dt>{{ tr('状态', 'Status') }}</dt><dd>{{ source.last_status }}</dd><dt>Skills</dt><dd>{{ source.skill_count }}</dd><dt>{{ tr('加载错误', 'Load errors') }}</dt><dd>{{ source.load_error_count }}</dd></dl>
            <div v-if="source.last_error_code || source.last_error_message_safe" class="source-safe-error"><code>{{ source.last_error_code || 'SKILL_SOURCE_ERROR' }}</code><span>{{ source.last_error_message_safe }}</span></div>
            <div class="row-actions skill-source-actions"><button v-if="auth.can('skill.sync')" :disabled="admin.skillActionSource === source.source || !source.sync_enabled" @click="syncSkillSource(source.source)">{{ admin.skillActionSource === source.source ? tr('处理中…', 'Working…') : tr('同步', 'Sync') }}</button><button :disabled="admin.skillActionSource === source.source" @click="refreshSkillSourceIndex(source.source)">{{ admin.skillActionSource === source.source ? tr('处理中…', 'Working…') : tr('刷新索引', 'Refresh index') }}</button><button @click="expandedSkillSources[source.source] = !expandedSkillSources[source.source]">{{ expandedSkillSources[source.source] ? tr('收起 Skills', 'Hide Skills') : tr('查看 Skills', 'View Skills') }}</button></div>
            <div v-if="expandedSkillSources[source.source]" class="source-skill-list"><article v-for="skill in skillsForSource(source.source)" :key="skill.qualified_name"><span><strong>{{ skill.qualified_name }}</strong><small>{{ skill.description }}</small></span><span v-if="skill.executable" class="readonly-pill">{{ tr('可执行', 'Executable') }}</span></article><p v-if="!skillsForSource(source.source).length" class="empty-note">{{ tr('当前账号没有可见 Skill。', 'No Skills are visible to this account.') }}</p></div>
          </article>
          <p v-if="!admin.skillSources?.sources.length" class="empty-note">{{ tr('没有已配置的 Skill source。', 'No Skill sources are configured.') }}</p>
        </div>
      </section>

      <section v-else-if="tab === 'operations'" class="admin-section operations-section">
        <div class="truth-banner"><Server :size="22" /><span><strong>{{ tr('ZhiCe 独立运维控制面', 'Independent ZhiCe operations control plane') }}</strong><small>{{ tr('Ops 自动监控当前真正启动 ZhiCe-Agent 的进程或固定容器；主 Web 只负责导航和投影。', 'Ops monitors the process or fixed container that actually launched ZhiCe-Agent. The main Web only navigates and projects it.') }}</small></span></div>
        <div class="operations-card">
          <dl><dt>{{ tr('当前部署', 'Current deployment') }}</dt><dd><code>{{ currentDeployment }}</code></dd><dt>{{ tr('运行形态', 'Runtime mode') }}</dt><dd>{{ opsModeLabel }}</dd><dt>{{ tr('监控目标', 'Target') }}</dt><dd><code>{{ admin.operationsTerminal?.target_name || '—' }}</code></dd><dt>Ops</dt><dd>{{ admin.operationsTerminal?.configured ? tr('已配置', 'Configured') : tr('未配置', 'Not configured') }}</dd><dt>{{ tr('展示方式', 'Presentation') }}</dt><dd>{{ admin.operationsTerminal?.presentation || 'both' }}</dd></dl>
          <p v-if="!opsCanOpen" class="empty-note">{{ tr('当前启动方式尚未提供 Ops endpoint。终端启动应自动拉起本地 Ops；Docker/服务器部署应同时启动独立 Ops 服务。', 'The current launcher has not provided an Ops endpoint. Terminal startup should launch local Ops automatically; Docker/server deployment should start an independent Ops service.') }}</p>
          <div v-else class="operations-actions"><button class="operations-action-button primary-button" @click="openOpsWindow"><ExternalLink :size="16" />{{ tr('独立窗口打开', 'Open in new window') }}</button><button v-if="opsCanEmbed" class="operations-action-button operations-secondary-button" @click="startOpsEmbed">{{ tr('页面内嵌', 'Embed in page') }}</button></div>
          <div v-if="opsFrameFallback" class="source-safe-error"><strong>{{ tr('页面内嵌不可用', 'Embedded Ops is unavailable') }}</strong><span>{{ tr('Cloudflare Access 或浏览器策略阻止了 iframe，已尝试回退到独立窗口。', 'Cloudflare Access or browser policy blocked the iframe. A new-window fallback was attempted.') }}</span><button class="operations-action-button operations-secondary-button" @click="openOpsWindow">{{ tr('再次打开', 'Open again') }}</button></div>
          <div v-if="opsEmbedded && opsCanEmbed" class="operations-frame-wrap"><header><strong>{{ tr('ZhiCe 运维终端', 'ZhiCe Ops terminal') }}</strong><button class="operations-close-button" @click="opsEmbedded = false">{{ tr('关闭投影', 'Close projection') }}</button></header><iframe :src="admin.operationsTerminal?.url" :title="tr('ZhiCe 运维终端', 'ZhiCe Ops terminal')" allow="clipboard-read; clipboard-write" @load="markOpsFrameLoaded" @error="fallbackOpsFrame" /></div>
        </div>
      </section>

      <section v-else-if="tab === 'monitor'" class="monitor-section">
        <div class="truth-banner"><Activity :size="22" /><span><strong>{{ tr('定位系统哪里出错，以及为什么出错', 'Find where the system failed and why') }}</strong><small>{{ tr('事故由确定性规则聚合；时间线仅包含白名单脱敏字段，不写入模型推断。', 'Incidents use deterministic rules; timelines contain only allowlisted redacted fields and never persist model inference.') }}</small></span></div>
        <section v-if="auth.can('diagnostics.system.use')" id="monitor-incidents" class="diagnostics-panel" tabindex="-1">
          <h2>{{ tr('事故与错误证据', 'Incidents and error evidence') }}</h2>
          <form class="diagnostic-filters" @submit.prevent="runDiagnostics"><select v-model="diagnosticFilters.actor_user_id" :aria-label="tr('账号', 'Account')"><option value="">{{ tr('全部账号', 'All accounts') }}</option><option v-for="actor in diagnosticActorOptions" :key="actor.id" :value="actor.id">{{ actor.label }}</option></select><select v-model="diagnosticFilters.session_id" :aria-label="tr('会话', 'Session')"><option value="">{{ tr('全部会话', 'All sessions') }}</option><option v-for="session in diagnosticSessionOptions" :key="session.id" :value="session.id">{{ session.label }}</option></select><select v-model="diagnosticFilters.component" :aria-label="tr('组件', 'Component')"><option value="">{{ tr('全部组件', 'All components') }}</option><option v-for="component in diagnosticComponentOptions" :key="component.value" :value="component.value">{{ component.label }}</option></select><select v-model="diagnosticFilters.error_code" :aria-label="tr('错误码', 'Error code')"><option value="">{{ tr('全部错误码', 'All error codes') }}</option><option v-for="code in diagnosticErrorCodeOptions" :key="code" :value="code">{{ code }}</option></select><select v-model="diagnosticFilters.status" :aria-label="tr('状态', 'Status')"><option value="">{{ tr('全部状态', 'All statuses') }}</option><option value="error">{{ tr('失败', 'Failed') }}</option><option value="stopped">{{ tr('已停止', 'Stopped') }}</option><option value="completed">{{ tr('完成', 'Completed') }}</option></select><select v-model="diagnosticFilters.minutes" :aria-label="tr('时间范围', 'Time range')"><option value="60">{{ tr('最近 1 小时', 'Last hour') }}</option><option value="360">{{ tr('最近 6 小时', 'Last 6 hours') }}</option><option value="1440">{{ tr('最近 24 小时', 'Last 24 hours') }}</option><option value="10080">{{ tr('最近 7 天', 'Last 7 days') }}</option></select><button class="primary-button" :disabled="diagnosticBusy">{{ diagnosticBusy ? tr('诊断中…', 'Diagnosing…') : tr('诊断', 'Diagnose') }}</button></form>
          <div class="monitor-grid"><article v-for="(value, key) in admin.diagnostics?.summary" :key="key"><span>{{ diagnosticSummaryLabels[String(key)] || key }}</span><strong>{{ value }}</strong><small>{{ tr('查询范围', 'Window') }}：{{ diagnosticWindowLabel(admin.diagnostics?.window_minutes || diagnosticFilters.minutes) }}</small></article></div>
          <p v-if="diagnosticUpdatedAt && diagnosticHasResults" class="diagnostic-feedback success">{{ tr(`诊断已更新 · ${diagnosticUpdatedAt} · 最近 ${diagnosticWindowLabel(admin.diagnostics?.window_minutes || diagnosticFilters.minutes)}`, `Diagnostics updated · ${diagnosticUpdatedAt} · Last ${diagnosticWindowLabel(admin.diagnostics?.window_minutes || diagnosticFilters.minutes)}`) }}</p><div v-else-if="diagnosticUpdatedAt" class="diagnostic-feedback empty"><strong>{{ tr('当前筛选和时间范围内没有匹配记录', 'No records match the current filters and time range') }}</strong><span>{{ diagnosticFilters.actor_user_id ? tr('账号筛选只覆盖能关联到该账号的运行记录；Gateway、渠道启动等系统事件通常没有用户归属。请选择“全部账号”后重新诊断。', 'Account filters only cover activity correlated to that account. Gateway and channel startup events often have no user owner. Select All accounts and diagnose again.') : tr('可以切换到更长的时间范围，或放宽 Session、组件、错误码和状态条件后重新诊断。', 'Choose a longer time range or broaden the session, component, error-code, and status filters, then diagnose again.') }}</span></div>
          <div class="incident-list"><details v-for="incident in admin.diagnostics?.incidents" :key="String(incident.incident_id)"><summary><span><strong>{{ diagnosticGuide(incident.code).title }}</strong><small>{{ componentLabel(String(incident.component || '')) }} · <code>{{ incident.code }}</code><template v-if="incident.subject"> · {{ incident.subject }}</template></small></span><span>{{ incident.count }} ×</span><span>{{ fmt(incident.last_seen_at) }}</span><ChevronDown :size="18" /></summary><div class="incident-detail"><div class="diagnosis-copy"><article><strong>{{ tr('发生了什么', 'What happened') }}</strong><p>{{ diagnosticGuide(incident.code).explanation }}</p></article><article><strong>{{ tr('影响', 'Impact') }}</strong><p>{{ diagnosticGuide(incident.code).impact }}</p></article><article><strong>{{ tr('建议处理', 'Recommended action') }}</strong><p>{{ diagnosticGuide(incident.code).action }}</p></article></div><details class="technical-evidence"><summary>{{ tr('查看技术证据', 'View technical evidence') }} <ChevronDown :size="15" /></summary><dl><dt>{{ tr('事故标识', 'Incident ID') }}</dt><dd><code>{{ incident.incident_id }}</code></dd><dt>{{ tr('首次发生', 'First seen') }}</dt><dd>{{ fmt(incident.first_seen_at) }}</dd><dt>{{ tr('最后发生', 'Last seen') }}</dt><dd>{{ fmt(incident.last_seen_at) }}</dd><dt>{{ tr('聚合规则', 'Grouping rule') }}</dt><dd>{{ incidentRuleLabel(incident.rule) }}</dd></dl><div v-for="evidence in incidentEvidence(incident)" :key="String(evidence.evidence_id || evidence.ts)" class="evidence-event"><strong>{{ diagnosticEventLabel(evidence) }}</strong><span>{{ fmt(evidence.ts) }} · {{ componentLabel(String(evidence.component || evidence.kind || '')) }}</span><p v-if="evidence.error_message">{{ evidence.error_message }}</p><code>{{ evidence.code || evidence.error_code || evidence.reason_code || '—' }}</code></div></details></div></details><p v-if="!admin.diagnostics?.incidents.length" class="empty-note">{{ tr('当前筛选范围没有确定性事故记录。', 'No deterministic incidents in the selected window.') }}</p></div>
          <div class="diagnostic-timeline-heading"><div><h3>{{ tr('诊断证据时间线', 'Diagnostic evidence timeline') }}</h3><p>{{ tr('按时间排列各组件留下的证据，用于还原错误发生前后的过程；证据标识只是日志关联编号，不代表错误原因。', 'Evidence from each component is ordered by time to reconstruct what happened. An evidence ID is only a log correlation identifier, not an error cause.') }}</p></div><label><span>{{ tr('显示范围', 'Scope') }}</span><select v-model="timelineScope"><option value="errors">{{ tr('仅异常证据', 'Errors only') }}</option><option value="all">{{ tr('全部上下文', 'All context') }}</option></select></label></div><div class="data-table diagnostic-timeline"><div class="table-head"><span>{{ tr('时间 / 状态', 'Time / status') }}</span><span>{{ tr('组件', 'Component') }}</span><span>{{ tr('事件含义 / 内部标识', 'Meaning / internal key') }}</span><span>{{ tr('错误码', 'Error code') }}</span><span></span></div><div v-for="event in displayedDiagnosticTimeline" :key="String(event.evidence_id)" class="timeline-event" :class="{ open: openTimelineEvents[String(event.evidence_id)], error: eventIsError(event), normal: !eventIsError(event) }"><div class="table-row" role="button" tabindex="0" @click="toggleTimelineEvent(event.evidence_id)" @keydown.enter="toggleTimelineEvent(event.evidence_id)" @keydown.space.prevent="toggleTimelineEvent(event.evidence_id)"><span><strong>{{ fmt(event.ts) }}</strong><small class="evidence-status"><i :class="`status-dot ${eventIsError(event) ? 'error' : 'completed'}`"></i>{{ eventIsError(event) ? tr('异常', 'Error') : tr('正常', 'Normal') }}</small></span><span><strong>{{ componentLabel(String(event.component || event.kind || '')) }}</strong><code>{{ event.component || event.kind || '—' }}</code></span><span><strong>{{ diagnosticEventLabel(event) }}</strong><code>{{ diagnosticEventKey(event) }}</code></span><span><code>{{ event.code || '—' }}</code></span><span class="timeline-chevron" :title="openTimelineEvents[String(event.evidence_id)] ? tr('收起证据', 'Collapse evidence') : tr('展开证据', 'Expand evidence')"><ChevronDown :size="18" /></span></div><dl v-if="openTimelineEvents[String(event.evidence_id)]"><template v-for="key in ['error_message','reason_code','status','route','session_id','turn_id','request_id','model','endpoint','duration_ms']" :key="key"><template v-if="event[key]"><dt>{{ diagnosticFieldLabel(key) }}</dt><dd><code v-if="key.endsWith('_id') || key.endsWith('_code')">{{ event[key] }}</code><span v-else>{{ event[key] }}</span></dd></template></template><dt>{{ tr('证据标识', 'Evidence ID') }}</dt><dd><code>{{ event.evidence_id }}</code><small>{{ tr('用于在诊断结果和脱敏日志中唯一定位这条事件', 'Uniquely identifies this event across diagnostics and redacted logs') }}</small></dd></dl></div><p v-if="!displayedDiagnosticTimeline.length" class="empty-note timeline-empty">{{ tr('当前范围没有异常证据，可切换到“全部上下文”查看正常生命周期事件。', 'No error evidence in this scope. Switch to All context to view normal lifecycle events.') }}</p></div>
        </section>
        <template v-if="auth.can('turn.read.any')">
          <section id="monitor-runs" class="recent-runs-section" tabindex="-1">
            <header><div><h2>{{ tr('近期运行记录', 'Recent runs') }}</h2><p>{{ tr('默认只看失败；需要时再切换到运行中、已停止或全部记录。', 'Failures are shown by default. Switch to running, stopped, completed, or all runs when needed.') }}</p></div><label><span>{{ tr('运行状态', 'Run status') }}</span><select v-model="recentRunStatus" @change="loadRecentRuns"><option value="error">{{ tr('失败', 'Failed') }}</option><option value="started">{{ tr('运行中', 'Running') }}</option><option value="stopped">{{ tr('已停止', 'Stopped') }}</option><option value="completed">{{ tr('完成', 'Completed') }}</option><option value="">{{ tr('全部记录', 'All runs') }}</option></select></label></header>
            <div class="data-table activity-table"><div class="table-head"><span>{{ tr('Session 会话标题 / 账号', 'Session title / account') }}</span><span>{{ tr('状态 / 错误', 'Status / error') }}</span><span>{{ tr('渠道', 'Channel') }}</span><span>{{ tr('开始时间', 'Started') }}</span><span>{{ tr('耗时', 'Duration') }}</span><span></span></div><div v-for="turn in admin.monitor?.activity.recent_turns" :key="String(turn.turn_id)" class="run-record" :class="{ open: openRunRecords[String(turn.turn_id)] }"><div class="table-row"><span><small class="record-kind">{{ tr('Session 会话标题', 'Session title') }}</small><strong>{{ turn.session_title || tr('未命名会话', 'Untitled session') }}</strong><small>{{ tr('账号', 'Account') }}：{{ turn.actor_display_name || turn.actor_username || turn.actor_user_id }}<template v-if="turn.actor_username"> · @{{ turn.actor_username }}</template></small></span><span><strong><i :class="`status-dot ${turn.status}`"></i>{{ statusLabel(turn.status) }}</strong><code v-if="turn.error_code">{{ turn.error_code }}</code></span><span :data-label="tr('渠道', 'Channel')">{{ channelLabel(turn.channel) }}</span><span :data-label="tr('开始', 'Started')">{{ fmt(turn.started_at) }}</span><span :data-label="tr('耗时', 'Duration')">{{ fmtDuration(turn.duration_ms) }}</span><button class="record-toggle" type="button" @click="toggleRunRecord(turn.turn_id)"><span>{{ openRunRecords[String(turn.turn_id)] ? tr('收起', 'Collapse') : tr('查看诊断', 'View diagnosis') }}</span><ChevronDown :size="17" /></button></div><div v-if="openRunRecords[String(turn.turn_id)]" class="run-detail"><div v-if="turn.error_code" class="diagnosis-copy"><article><strong>{{ tr('发生了什么', 'What happened') }}</strong><p>{{ diagnosticGuide(turn.error_code).explanation }}</p></article><article><strong>{{ tr('影响', 'Impact') }}</strong><p>{{ diagnosticGuide(turn.error_code).impact }}</p></article><article><strong>{{ tr('建议处理', 'Recommended action') }}</strong><p>{{ diagnosticGuide(turn.error_code).action }}</p></article></div><dl><dt>Session ID</dt><dd><code>{{ turn.session_id || '—' }}</code></dd><dt>Turn ID</dt><dd><code>{{ turn.turn_id || '—' }}</code></dd><dt>Request / Trace ID</dt><dd><code>{{ turn.request_id || '—' }}</code></dd><dt>{{ tr('账号 ID', 'Account ID') }}</dt><dd><code>{{ turn.actor_user_id || '—' }}</code></dd><dt>{{ tr('结束时间', 'Finished') }}</dt><dd>{{ fmt(turn.finished_at) }}</dd><dt>{{ tr('错误码', 'Error code') }}</dt><dd><code>{{ turn.error_code || '—' }}</code></dd></dl></div></div></div>
            <p v-if="!admin.monitor?.activity.recent_turns.length" class="empty-note">{{ tr('当前状态下没有运行记录。', 'No runs match this status.') }}</p>
          </section>
          <section class="runtime-health-section"><h2>{{ tr('服务与能力状态', 'Services and capabilities') }}</h2><div class="monitor-grid"><article><span>Gateway</span><strong>{{ admin.monitor?.gateway.status || 'unknown' }}</strong><small>{{ admin.monitor?.gateway.current_model }}</small></article><article v-for="(value, key) in admin.monitor?.activity.summary" :key="key"><span>{{ { turns: tr('总运行数', 'Total runs'), running: tr('运行中', 'Running'), failed: tr('失败', 'Failed'), stopped: tr('已停止', 'Stopped'), tool_errors: tr('工具错误', 'Tool errors') }[String(key)] || key }}</span><strong>{{ value }}</strong><small>{{ tr('结构化运行记录', 'Structured activity') }}</small></article></div><div class="capability-grid"><article v-for="(capability, key) in diagnosticCapabilities" :key="key"><i :class="`status-dot ${capability.state}`"></i><div><strong>{{ capability.name }}</strong><small>{{ capability.message }}</small><code>{{ capability.code }}</code></div></article></div></section>
        </template>
      </section>

      <section v-else-if="tab === 'advanced'" class="audit-section">
        <div class="advanced-heading"><FileClock :size="22" /><div><h2>{{ tr('安全审计', 'Security audit') }}</h2><p>{{ tr('这里只记录登录异常、账号与权限变更、外部身份绑定、跨账号访问和危险操作等敏感事件；普通运行错误请到运行诊断查看。', 'This ledger only contains sensitive events such as authentication anomalies, account and permission changes, identity linking, cross-account access, and dangerous operations. Use Runtime diagnostics for ordinary failures.') }}</p></div></div>
        <form class="audit-filters" @submit.prevent="admin.loadAudit(auditFilters)"><label class="audit-filter-field"><span>{{ tr('事件类型', 'Event type') }}</span><select v-model="auditFilters.event_type"><option value="">{{ tr('全部事件', 'All events') }}</option><option v-for="([key, label]) in auditActionOptions" :key="key" :value="key">{{ label }}</option></select><small>{{ tr('选择登录、账号管理或系统诊断等事件', 'Choose login, account management, diagnostics, or another event') }}</small></label><label class="audit-filter-field"><span>{{ tr('操作者账号', 'Actor account') }}</span><select v-model="auditFilters.actor_user_id"><option value="">{{ tr('全部账号', 'All accounts') }}</option><option v-for="actor in auditActorOptions" :key="actor.id" :value="actor.id">{{ actor.label }}</option></select><small>{{ auditActorOptions.length ? tr('按账号筛选操作记录', 'Filter activity by account') : tr('当前权限下没有可选账号', 'No accounts are available with current permissions') }}</small></label><label class="audit-filter-field"><span>{{ tr('执行结果', 'Outcome') }}</span><select v-model="auditFilters.outcome"><option value="">{{ tr('全部结果', 'All outcomes') }}</option><option value="success">{{ tr('成功', 'Success') }}</option><option value="failure">{{ tr('失败', 'Failure') }}</option></select><small>{{ tr('按状态码和错误决策筛选成功或失败记录', 'Filter successful or failed records by status and error decision') }}</small></label><DateTimePicker v-model="auditFilters.from_ts" :label="tr('开始时间', 'From')" :language="ui.language" /><DateTimePicker v-model="auditFilters.to_ts" :label="tr('结束时间', 'To')" :language="ui.language" :min-value="auditFilters.from_ts" /><button class="primary-button">{{ tr('筛选', 'Filter') }}</button><a v-if="auth.can('audit.export')" class="button-link" :href="auditExportUrl"><Download :size="16" />{{ tr('导出 CSV', 'Export CSV') }}</a></form>
        <div class="audit-list"><details v-for="event in admin.auditEvents" :key="String(event.id)"><summary><span><i :class="`status-dot ${event.decision || 'neutral'}`"></i><strong>{{ event.action }}</strong></span><span>{{ event.channel || '—' }}</span><span>{{ fmt(event.ts) }}</span></summary><dl><template v-for="(value, key) in event" :key="key"><dt>{{ key }}</dt><dd><code v-if="typeof value === 'object'">{{ JSON.stringify(value) }}</code><span v-else>{{ value || '—' }}</span></dd></template></dl></details></div><nav class="audit-pagination" :aria-label="tr('安全审计分页', 'Security audit pagination')"><button type="button" :disabled="admin.auditPageIndex === 0" @click="admin.loadAudit(auditFilters, 'previous')">{{ tr('上一页', 'Previous') }}</button><span>{{ tr(`第 ${admin.auditPageIndex + 1} 页`, `Page ${admin.auditPageIndex + 1}`) }}</span><button type="button" :disabled="!admin.auditHasMore" @click="admin.loadAudit(auditFilters, 'next')">{{ tr('下一页', 'Next') }}</button></nav>
      </section>
    </main>
    <div v-if="deletingUser" class="modal-backdrop" @click.self="deletingUser = null">
      <form class="dialog-card compact-dialog" @submit.prevent="confirmDeleteUser">
        <h2>{{ tr('永久删除账号？', 'Permanently delete account?') }}</h2>
        <p>{{ tr('账号、QQ 绑定、登录状态、Session、Memory 和用户文件将永久删除。此操作无法撤销。', 'The account, QQ binding, login state, sessions, memory, and user files will be permanently deleted. This cannot be undone.') }}</p>
        <label><span>{{ tr(`请输入账号 ${deletingUser.username} 确认`, `Type account ${deletingUser.username} to confirm`) }}</span><input v-model="deleteConfirmation" name="delete-user-confirmation" autocomplete="off" autofocus required :aria-invalid="Boolean(deleteConfirmationError)" @input="deleteConfirmationError = ''" /></label>
        <p v-if="deleteConfirmationError" class="form-error" role="alert">{{ deleteConfirmationError }}</p>
        <div class="dialog-actions"><button type="button" @click="deletingUser = null">{{ tr('取消', 'Cancel') }}</button><button class="danger-button" :disabled="deleteBusy">{{ deleteBusy ? tr('删除中…', 'Deleting…') : tr('确认永久删除', 'Delete permanently') }}</button></div>
      </form>
    </div>
  </div>
</template>
