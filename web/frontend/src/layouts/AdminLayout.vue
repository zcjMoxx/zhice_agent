<script setup lang="ts">
import { Activity, ArrowLeft, ChevronDown, Download, FileClock, Gauge, RefreshCw, Shield, Users } from "@lucide/vue";
import { computed, onMounted, reactive, ref } from "vue";
import { useRouter } from "vue-router";

import { BASE_CAPABILITIES, groupedPermissions, permissionLabel, ROLE_NAMES } from "@/admin/permissions";
import { useAdminStore } from "@/stores/admin";
import { useAuthStore } from "@/stores/auth";
import { errorMessage } from "@/stores/chat";

const auth = useAuthStore();
const admin = useAdminStore();
const router = useRouter();
const tab = ref("overview");
const failure = ref("");
const selectedRole = ref("");
const technicalOpen = ref<Record<string, boolean>>({});
const auditFilters = reactive({ action: "", actor_user_id: "", decision: "", from_ts: "", to_ts: "" });
const newUser = reactive({ username: "", display_name: "", password: "", roles: ["viewer"] });

const tabs = computed(() => [
  { key: "overview", label: "概览", icon: Gauge, visible: true },
  { key: "users", label: "用户管理", icon: Users, visible: auth.can("auth.users.read") },
  { key: "roles", label: "角色与权限", icon: Shield, visible: auth.can("auth.roles.read") },
  { key: "monitor", label: "系统监控", icon: Activity, visible: auth.can("turn.read.any") },
  { key: "audit", label: "安全审计", icon: FileClock, visible: auth.can("audit.read") },
].filter((item) => item.visible));
const selectedRoleValue = computed(() => admin.roles.find((role) => role.id === selectedRole.value));
const permissionGroups = computed(() => groupedPermissions(admin.permissions));
const auditExportUrl = computed(() => `/api/audit/events/export?${new URLSearchParams(Object.entries(auditFilters).filter(([, value]) => value)).toString()}`);

onMounted(async () => { await loadTab("overview"); });

async function loadTab(next: string) {
  tab.value = next;
  failure.value = "";
  try {
    if (next === "users") await admin.loadUsers();
    if (next === "roles") { await admin.loadRoles(); selectedRole.value ||= admin.roles[0]?.id || ""; }
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
  if (!role) return;
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
      <p class="admin-kicker">管理后台</p>
      <nav><button v-for="item in tabs" :key="item.key" :class="{ active: tab === item.key }" @click="loadTab(item.key)"><component :is="item.icon" :size="18" />{{ item.label }}</button></nav>
      <button class="back-chat" @click="router.push('/')"><ArrowLeft :size="17" />返回聊天</button>
    </aside>
    <main class="admin-main">
      <header class="admin-header"><div><span class="eyebrow">Administration</span><h1>{{ tabs.find((item) => item.key === tab)?.label }}</h1></div><button v-if="tab !== 'overview'" class="icon-button" title="刷新" @click="loadTab(tab)"><RefreshCw :size="18" /></button></header>
      <p v-if="failure" class="form-error">{{ failure }}</p>

      <section v-if="tab === 'overview'" class="admin-overview">
        <div class="overview-hero"><div><span class="eyebrow">本地优先 Agent Runtime</span><h2>欢迎，{{ auth.user?.display_name }}</h2><p>这里汇总账号治理、角色能力、运行真值和安全审计。深层根因诊断将在 Part 17 接入同一界面。</p></div><Gauge :size="52" /></div>
        <div class="overview-grid"><button v-for="item in tabs.filter((item) => item.key !== 'overview')" :key="item.key" @click="loadTab(item.key)"><component :is="item.icon" :size="22" /><strong>{{ item.label }}</strong><span>{{ { users: '账号状态与角色分配', roles: '中文能力域与技术 key', monitor: 'Gateway、Capability 与 Activity', audit: '安全和管理事件' }[item.key] }}</span></button></div>
      </section>

      <section v-else-if="tab === 'users'" class="admin-section">
        <form v-if="auth.can('auth.users.manage')" class="admin-create-form" @submit.prevent="createUser"><h2>创建用户</h2><input v-model="newUser.username" required placeholder="用户名" /><input v-model="newUser.display_name" placeholder="显示名称（可选）" /><input v-model="newUser.password" type="password" minlength="8" required placeholder="初始密码" /><select v-model="newUser.roles[0]"><option v-for="role in ['viewer','developer','auditor','admin']" :key="role" :value="role">{{ ROLE_NAMES[role] }}</option></select><button class="primary-button">创建</button></form>
        <div class="data-table user-table"><div class="table-head"><span>用户</span><span>角色</span><span>状态</span><span>管理</span></div><div v-for="user in admin.users" :key="user.id" class="table-row"><span><strong>{{ user.display_name }}</strong><small>@{{ user.username }}</small></span><span><select v-if="auth.can('auth.users.manage') && !user.roles.includes('owner')" :value="user.roles[0]" @change="updateUser(user.id, { roles: [($event.target as HTMLSelectElement).value] })"><option v-for="role in ['viewer','developer','auditor','admin']" :key="role" :value="role">{{ ROLE_NAMES[role] }}</option></select><template v-else>{{ user.roles.map((role) => ROLE_NAMES[role] || role).join('、') }}</template></span><span><i :class="`status-dot ${user.status}`"></i>{{ user.status === 'active' ? '启用' : '停用' }}</span><span class="row-actions"><button v-if="auth.can('auth.users.manage') && !user.roles.includes('owner')" @click="updateUser(user.id, { status: user.status === 'active' ? 'disabled' : 'active' })">{{ user.status === 'active' ? '停用' : '启用' }}</button><button v-if="auth.can('auth.admin.manage') && user.roles.includes('admin')" @click="updateUser(user.id, { can_manage_admins: !user.can_manage_admins })">{{ user.can_manage_admins ? '撤销委派' : '委派管理' }}</button><span v-if="user.roles.includes('owner')" class="readonly-pill">固定只读</span></span></div></div>
      </section>

      <section v-else-if="tab === 'roles'" class="roles-layout">
        <aside class="role-list"><button v-for="role in admin.roles" :key="role.id" :class="{ active: selectedRole === role.id }" @click="selectedRole = role.id"><strong>{{ ROLE_NAMES[role.key] || role.name }}</strong><small>{{ role.permission_keys.length }} 项附加特权</small></button></aside>
        <div v-if="selectedRoleValue" class="role-detail"><header><div><span class="eyebrow">{{ selectedRoleValue.key }}</span><h2>{{ ROLE_NAMES[selectedRoleValue.key] || selectedRoleValue.name }}</h2><p>{{ selectedRoleValue.description }}</p></div><span v-if="['owner','admin'].includes(selectedRoleValue.key)" class="readonly-pill">系统固定</span></header><div class="base-capabilities"><strong>所有登录用户的基础能力</strong><span v-for="capability in BASE_CAPABILITIES" :key="capability">{{ capability }}</span></div><section v-for="(keys, group) in permissionGroups" :key="group" class="permission-group"><h3>{{ group }}</h3><label v-for="key in keys" :key="key"><span><strong>{{ permissionLabel(key) }}</strong><small>{{ key }}</small></span><input type="checkbox" :checked="selectedRoleValue.permission_keys.includes(key)" :disabled="['owner','admin'].includes(selectedRoleValue.key) || !auth.can('auth.roles.manage')" @change="togglePermission(key, ($event.target as HTMLInputElement).checked)" /></label></section><details class="technical-details" :open="technicalOpen[selectedRoleValue.id]" @toggle="technicalOpen[selectedRoleValue.id] = ($event.target as HTMLDetailsElement).open"><summary>技术详情 <ChevronDown :size="15" /></summary><code v-for="key in selectedRoleValue.permission_keys" :key="key">{{ key }}</code></details></div>
      </section>

      <section v-else-if="tab === 'monitor'" class="monitor-section">
        <div class="truth-banner"><Activity :size="22" /><span><strong>当前真值视图</strong><small>仅展示 Gateway、Capability 和结构化 Runtime Activity；不推断根因。</small></span></div>
        <div class="monitor-grid"><article><span>Gateway</span><strong>{{ admin.monitor?.gateway.status || 'unknown' }}</strong><small>{{ admin.monitor?.gateway.current_model }}</small></article><article v-for="(value, key) in admin.monitor?.activity.summary" :key="key"><span>{{ key }}</span><strong>{{ value }}</strong><small>近期结构化记录</small></article></div>
        <h2>Capability</h2><div class="capability-grid"><article v-for="(capability, key) in admin.monitor?.capabilities" :key="key"><i :class="`status-dot ${capability.state}`"></i><div><strong>{{ capability.name }}</strong><small>{{ capability.message }}</small><code>{{ capability.code }}</code></div></article></div>
        <h2>近期 Turn Activity</h2><div class="data-table activity-table"><div class="table-head"><span>状态</span><span>渠道</span><span>开始</span><span>耗时</span></div><div v-for="turn in admin.monitor?.activity.recent_turns" :key="String(turn.turn_id)" class="table-row"><span>{{ turn.status }}</span><span>{{ turn.channel }}</span><span>{{ fmt(turn.started_at) }}</span><span>{{ turn.duration_ms ? `${turn.duration_ms} ms` : '—' }}</span></div></div>
      </section>

      <section v-else class="audit-section">
        <form class="audit-filters" @submit.prevent="admin.loadAudit(auditFilters)"><input v-model="auditFilters.action" placeholder="事件类型" /><input v-model="auditFilters.actor_user_id" placeholder="操作者 ID" /><select v-model="auditFilters.decision"><option value="">全部结果</option><option value="allow">允许</option><option value="deny">拒绝</option></select><input v-model="auditFilters.from_ts" type="datetime-local" /><input v-model="auditFilters.to_ts" type="datetime-local" /><button class="primary-button">筛选</button><a v-if="auth.can('audit.export')" class="button-link" :href="auditExportUrl"><Download :size="16" />导出 CSV</a></form>
        <div class="audit-list"><details v-for="event in admin.auditEvents" :key="String(event.id)"><summary><span><i :class="`status-dot ${event.decision || 'neutral'}`"></i><strong>{{ event.action }}</strong></span><span>{{ event.channel || '—' }}</span><span>{{ fmt(event.ts) }}</span></summary><dl><template v-for="(value, key) in event" :key="key"><dt>{{ key }}</dt><dd><code v-if="typeof value === 'object'">{{ JSON.stringify(value) }}</code><span v-else>{{ value || '—' }}</span></dd></template></dl></details></div><button v-if="admin.auditHasMore" class="load-more" @click="admin.loadAudit(auditFilters, true)">加载更多</button>
      </section>
    </main>
  </div>
</template>
