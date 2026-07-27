<script setup lang="ts">
import { Activity, ArrowLeft, ChevronDown, Download, FileClock, Gauge, LockKeyhole, RefreshCw, Shield, Users } from "@lucide/vue";
import { computed, onMounted, reactive, ref } from "vue";
import { useRouter } from "vue-router";

import { baseCapabilities, groupedPermissions, permissionLabel, roleName } from "@/admin/permissions";
import QuickPreferences from "@/components/QuickPreferences.vue";
import { uiText } from "@/i18n";
import { useAdminStore } from "@/stores/admin";
import { useAuthStore } from "@/stores/auth";
import { errorMessage } from "@/stores/chat";
import { useUiStore } from "@/stores/ui";

const auth = useAuthStore();
const admin = useAdminStore();
const router = useRouter();
const ui = useUiStore();
const tab = ref("overview");
const failure = ref("");
const selectedRole = ref("");
const technicalOpen = ref<Record<string, boolean>>({});
const auditFilters = reactive({ action: "", actor_user_id: "", decision: "", from_ts: "", to_ts: "" });
const newUser = reactive({ username: "", display_name: "", password: "", roles: ["viewer"] });

function tr(chinese: string, english: string): string { return uiText(ui.language, chinese, english); }

const tabs = computed(() => [
  { key: "overview", label: tr("概览", "Overview"), icon: Gauge, visible: true },
  { key: "users", label: tr("用户管理", "Users"), icon: Users, visible: auth.can("auth.users.read") },
  { key: "roles", label: tr("角色与权限", "Roles & permissions"), icon: Shield, visible: auth.can("auth.roles.read") },
  { key: "monitor", label: tr("系统监控", "System monitor"), icon: Activity, visible: auth.can("turn.read.any") },
  { key: "audit", label: tr("安全审计", "Security audit"), icon: FileClock, visible: auth.can("audit.read") },
].filter((item) => item.visible));
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

onMounted(async () => { await loadTab("overview"); });

async function loadTab(next: string) {
  tab.value = next;
  failure.value = "";
  try {
    if (next === "users") await admin.loadUsers();
    if (next === "roles") { await admin.loadRoles(); selectedRole.value ||= orderedRoles.value[0]?.id || ""; }
    if (next === "monitor") await admin.loadMonitor();
    if (next === "audit") await admin.loadAudit(auditFilters);
  } catch (error) { failure.value = errorMessage(error); }
}
async function createUser() {
  try { await import("@/api/client").then(({ api }) => api.createUser({ ...newUser })); Object.assign(newUser, { username: "", display_name: "", password: "", roles: ["viewer"] }); await admin.loadUsers(); }
  catch (error) { failure.value = errorMessage(error); }
}
async function updateUser(id: string, payload: Record<string, unknown>) {
  try { await import("@/api/client").then(({ api }) => api.updateUser(id, payload)); await admin.loadUsers(); }
  catch (error) { failure.value = errorMessage(error); }
}
async function togglePermission(key: string, enabled: boolean) {
  const role = selectedRoleValue.value;
  if (!role || !canEditSelectedRole.value) return;
  const keys = enabled ? [...role.permission_keys, key] : role.permission_keys.filter((item) => item !== key);
  try { await admin.updateRole(role.id, [...new Set(keys)]); }
  catch (error) { failure.value = errorMessage(error); }
}
function fmt(value: unknown): string { return value ? new Date(String(value)).toLocaleString() : "—"; }
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
      <header class="admin-header"><div><span class="eyebrow">Administration</span><h1>{{ tabs.find((item) => item.key === tab)?.label }}</h1></div><div class="admin-header-actions"><QuickPreferences /><button v-if="tab !== 'overview'" class="icon-button" :title="tr('刷新', 'Refresh')" @click="loadTab(tab)"><RefreshCw :size="18" /></button></div></header>
      <p v-if="failure" class="form-error">{{ failure }}</p>

      <section v-if="tab === 'overview'" class="admin-overview">
        <div class="overview-hero"><div><span class="eyebrow">{{ tr('本地优先 Agent Runtime', 'Local-first Agent Runtime') }}</span><h2>{{ tr('欢迎', 'Welcome') }}，{{ auth.user?.display_name }}</h2><p>{{ tr('这里汇总账号治理、角色能力、运行真值和安全审计。深层根因诊断将在 Part 17 接入同一界面。', 'Account governance, role capabilities, runtime truth, and security audit are collected here. Part 17 will add deeper diagnostics to this interface.') }}</p></div><Gauge :size="52" /></div>
        <div class="overview-grid"><button v-for="item in tabs.filter((item) => item.key !== 'overview')" :key="item.key" @click="loadTab(item.key)"><component :is="item.icon" :size="22" /><strong>{{ item.label }}</strong><span>{{ { users: tr('账号状态与角色分配', 'Account status and role assignment'), roles: tr('能力域与技术 key', 'Capability domains and technical keys'), monitor: 'Gateway, Capability & Activity', audit: tr('安全和管理事件', 'Security and administration events') }[item.key] }}</span></button></div>
      </section>

      <section v-else-if="tab === 'users'" class="admin-section">
        <form v-if="auth.can('auth.users.manage')" class="admin-create-form" @submit.prevent="createUser"><h2>{{ tr('创建用户', 'Create user') }}</h2><input v-model="newUser.username" required :placeholder="tr('用户名', 'Username')" /><input v-model="newUser.display_name" :placeholder="tr('显示名称（可选）', 'Display name (optional)')" /><input v-model="newUser.password" type="password" minlength="8" required :placeholder="tr('初始密码', 'Initial password')" /><select v-model="newUser.roles[0]"><option v-for="role in ['viewer','developer','auditor','admin']" :key="role" :value="role">{{ roleName(role, ui.language) }}</option></select><button class="primary-button">{{ tr('创建', 'Create') }}</button></form>
        <div class="data-table user-table"><div class="table-head"><span>{{ tr('用户', 'User') }}</span><span>{{ tr('角色', 'Role') }}</span><span>{{ tr('状态', 'Status') }}</span><span>{{ tr('管理', 'Actions') }}</span></div><div v-for="user in admin.users" :key="user.id" class="table-row"><span><strong>{{ user.display_name }}</strong><small>@{{ user.username }}</small></span><span><select v-if="auth.can('auth.users.manage') && !user.roles.includes('owner')" :value="user.roles[0]" @change="updateUser(user.id, { roles: [($event.target as HTMLSelectElement).value] })"><option v-for="role in ['viewer','developer','auditor','admin']" :key="role" :value="role">{{ roleName(role, ui.language) }}</option></select><template v-else>{{ user.roles.map((role) => roleName(role, ui.language)).join('、') }}</template></span><span><i :class="`status-dot ${user.status}`"></i>{{ user.status === 'active' ? tr('启用', 'Active') : tr('停用', 'Disabled') }}</span><span class="row-actions"><button v-if="auth.can('auth.users.manage') && !user.roles.includes('owner')" @click="updateUser(user.id, { status: user.status === 'active' ? 'disabled' : 'active' })">{{ user.status === 'active' ? tr('停用', 'Disable') : tr('启用', 'Enable') }}</button><button v-if="auth.can('auth.admin.manage') && user.roles.includes('admin')" @click="updateUser(user.id, { can_manage_admins: !user.can_manage_admins })">{{ user.can_manage_admins ? tr('撤销委派', 'Revoke delegation') : tr('委派管理', 'Delegate management') }}</button><span v-if="user.roles.includes('owner')" class="readonly-pill">{{ tr('固定只读', 'Read-only') }}</span></span></div></div>
      </section>

      <section v-else-if="tab === 'roles'" class="roles-layout">
        <aside class="role-list"><button v-for="role in orderedRoles" :key="role.id" :class="{ active: selectedRole === role.id }" @click="selectedRole = role.id"><strong>{{ roleName(role.key, ui.language) || role.name }}</strong><small>{{ role.permission_keys.length }} {{ tr('项附加特权', 'additional permissions') }}</small></button></aside>
        <div v-if="selectedRoleValue" class="role-detail"><header><div><span class="eyebrow">{{ selectedRoleValue.key }}</span><h2>{{ roleName(selectedRoleValue.key, ui.language) || selectedRoleValue.name }}</h2><p>{{ selectedRoleValue.description }}</p></div><span v-if="ownerRole || adminRoleRestricted" class="role-lock"><LockKeyhole :size="14" />{{ adminRoleRestricted ? tr('仅系统所有者可修改', 'Only Owner can modify') : tr('系统固定，权限不可修改', 'System role · permissions locked') }}</span></header><div class="base-capabilities"><strong>{{ tr('所有登录用户的基础能力', 'Base capabilities for all signed-in users') }}</strong><span v-for="capability in visibleBaseCapabilities" :key="capability">{{ capability }}</span></div><section v-for="(keys, group) in permissionGroups" :key="group" class="permission-group"><h3>{{ group }}</h3><label v-for="key in keys" :key="key"><span><strong>{{ permissionLabel(key, ui.language) }}</strong><small>{{ key }}</small></span><input type="checkbox" :checked="selectedRoleValue.permission_keys.includes(key)" :disabled="!canEditSelectedRole" @change="togglePermission(key, ($event.target as HTMLInputElement).checked)" /></label></section><details class="technical-details" :open="technicalOpen[selectedRoleValue.id]" @toggle="technicalOpen[selectedRoleValue.id] = ($event.target as HTMLDetailsElement).open"><summary>{{ tr('技术详情', 'Technical details') }} <ChevronDown :size="15" /></summary><code v-for="key in selectedRoleValue.permission_keys" :key="key">{{ key }}</code></details></div>
      </section>

      <section v-else-if="tab === 'monitor'" class="monitor-section">
        <div class="truth-banner"><Activity :size="22" /><span><strong>{{ tr('当前真值视图', 'Current truth view') }}</strong><small>{{ tr('仅展示 Gateway、Capability 和结构化 Runtime Activity；不推断根因。', 'Shows only Gateway, Capability, and structured Runtime Activity without inferring root causes.') }}</small></span></div>
        <div class="monitor-grid"><article><span>Gateway</span><strong>{{ admin.monitor?.gateway.status || 'unknown' }}</strong><small>{{ admin.monitor?.gateway.current_model }}</small></article><article v-for="(value, key) in admin.monitor?.activity.summary" :key="key"><span>{{ key }}</span><strong>{{ value }}</strong><small>近期结构化记录</small></article></div>
        <h2>Capability</h2><div class="capability-grid"><article v-for="(capability, key) in admin.monitor?.capabilities" :key="key"><i :class="`status-dot ${capability.state}`"></i><div><strong>{{ capability.name }}</strong><small>{{ capability.message }}</small><code>{{ capability.code }}</code></div></article></div>
        <h2>{{ tr('近期 Turn Activity', 'Recent Turn Activity') }}</h2><div class="data-table activity-table"><div class="table-head"><span>{{ tr('状态', 'Status') }}</span><span>{{ tr('渠道', 'Channel') }}</span><span>{{ tr('开始', 'Started') }}</span><span>{{ tr('耗时', 'Duration') }}</span></div><div v-for="turn in admin.monitor?.activity.recent_turns" :key="String(turn.turn_id)" class="table-row"><span>{{ turn.status }}</span><span>{{ turn.channel }}</span><span>{{ fmt(turn.started_at) }}</span><span>{{ turn.duration_ms ? `${turn.duration_ms} ms` : '—' }}</span></div></div>
      </section>

      <section v-else class="audit-section">
        <form class="audit-filters" @submit.prevent="admin.loadAudit(auditFilters)"><input v-model="auditFilters.action" :placeholder="tr('事件类型', 'Event type')" /><input v-model="auditFilters.actor_user_id" :placeholder="tr('操作者 ID', 'Actor ID')" /><select v-model="auditFilters.decision"><option value="">{{ tr('全部结果', 'All decisions') }}</option><option value="allow">{{ tr('允许', 'Allow') }}</option><option value="deny">{{ tr('拒绝', 'Deny') }}</option></select><input v-model="auditFilters.from_ts" type="datetime-local" /><input v-model="auditFilters.to_ts" type="datetime-local" /><button class="primary-button">{{ tr('筛选', 'Filter') }}</button><a v-if="auth.can('audit.export')" class="button-link" :href="auditExportUrl"><Download :size="16" />{{ tr('导出 CSV', 'Export CSV') }}</a></form>
        <div class="audit-list"><details v-for="event in admin.auditEvents" :key="String(event.id)"><summary><span><i :class="`status-dot ${event.decision || 'neutral'}`"></i><strong>{{ event.action }}</strong></span><span>{{ event.channel || '—' }}</span><span>{{ fmt(event.ts) }}</span></summary><dl><template v-for="(value, key) in event" :key="key"><dt>{{ key }}</dt><dd><code v-if="typeof value === 'object'">{{ JSON.stringify(value) }}</code><span v-else>{{ value || '—' }}</span></dd></template></dl></details></div><button v-if="admin.auditHasMore" class="load-more" @click="admin.loadAudit(auditFilters, true)">{{ tr('加载更多', 'Load more') }}</button>
      </section>
    </main>
  </div>
</template>
