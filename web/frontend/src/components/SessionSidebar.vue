<script setup lang="ts">
import { Ellipsis, MessageSquarePlus, PanelLeftClose, Search, Settings, SlidersHorizontal, UserRound, X } from "@lucide/vue";
import { onBeforeUnmount, onMounted, ref } from "vue";
import { useRouter } from "vue-router";

import type { SessionSummary } from "@/api/types";
import { uiText } from "@/i18n";
import { useAuthStore } from "@/stores/auth";
import { errorMessage } from "@/stores/chat";
import { useSessionStore } from "@/stores/sessions";
import { useUiStore } from "@/stores/ui";
import UserAvatar from "./UserAvatar.vue";

const auth = useAuthStore();
const sessions = useSessionStore();
const ui = useUiStore();
const router = useRouter();
const actionSession = ref("");
const renameId = ref("");
const renameTitle = ref("");
const deleteId = ref("");
const failure = ref("");
const openingId = ref("");
const renameBusy = ref(false);
const deleteBusy = ref(false);
const accountArea = ref<HTMLElement | null>(null);

function tr(chinese: string, english: string): string { return uiText(ui.language, chinese, english); }

function sourceLabel(session: SessionSummary): string {
  const channel = session.channel.toLowerCase();
  if (!channel || channel === "web") return "";
  if (channel === "cli" || channel === "cli_legacy") return "CLI";
  const name = channel === "qq"
    ? (session.conversation_type === "group" ? tr("QQ群", "QQ group") : "QQ")
    : channel === "weixin" ? tr("微信", "Weixin") : channel.toUpperCase();
  const isReadOnlyGroup = channel === "qq"
    && session.conversation_type === "group"
    && session.continuation_mode === "fork_only";
  return isReadOnlyGroup
    ? `${name} · ${tr("只读来源", "Read-only source")}`
    : `${name} · ${tr("可继续", "Writable")}`;
}

function closeMenus(event?: KeyboardEvent) {
  if (event && event.key !== "Escape") return;
  actionSession.value = "";
  ui.accountMenuOpen = false;
}
function closeOnOutsidePointer(event: PointerEvent) {
  const target = event.target;
  if (!(target instanceof Element)) return;
  if (accountArea.value && !accountArea.value.contains(target)) ui.accountMenuOpen = false;
  if (!target.closest(".session-actions")) actionSession.value = "";
}
onMounted(() => {
  document.addEventListener("keydown", closeMenus);
  document.addEventListener("pointerdown", closeOnOutsidePointer);
});
onBeforeUnmount(() => {
  document.removeEventListener("keydown", closeMenus);
  document.removeEventListener("pointerdown", closeOnOutsidePointer);
});

async function openSession(id: string) {
  openingId.value = id;
  failure.value = "";
  try { await sessions.open(id); }
  catch (error) { failure.value = errorMessage(error); }
  finally { openingId.value = ""; }
}
function beginRename(id: string, title: string) { failure.value = ""; renameId.value = id; renameTitle.value = title || tr("新对话", "New chat"); actionSession.value = ""; }
function beginDelete(id: string) { failure.value = ""; deleteId.value = id; actionSession.value = ""; }
async function saveRename() {
  if (renameBusy.value) return;
  renameBusy.value = true;
  failure.value = "";
  try { await sessions.rename(renameId.value, renameTitle.value); renameId.value = ""; }
  catch (error) { failure.value = errorMessage(error); }
  finally { renameBusy.value = false; }
}
async function confirmDelete() {
  if (deleteBusy.value || !deleteId.value) return;
  deleteBusy.value = true;
  failure.value = "";
  try { await sessions.remove(deleteId.value); deleteId.value = ""; }
  catch (error) { failure.value = errorMessage(error); }
  finally { deleteBusy.value = false; }
}
async function logout() { await auth.logout(); ui.accountMenuOpen = false; }
</script>

<template>
  <aside class="session-sidebar" :class="{ collapsed: ui.sidebarCollapsed }" @click.self="closeMenus()">
    <header class="sidebar-brand">
      <a class="brand-lockup compact" href="/"><img :src="'/static/zhice-logo-a.png'" alt="" /><strong>ZhiCe-Agent</strong></a>
      <button class="icon-button" :title="tr('收起 Session 侧栏', 'Collapse Session sidebar')" @click="ui.sidebarCollapsed = !ui.sidebarCollapsed"><PanelLeftClose :size="19" /></button>
    </header>
    <div class="sidebar-tools">
      <button class="new-chat" @click="sessions.startDraft()"><MessageSquarePlus :size="18" /><span>{{ tr('新对话', 'New chat') }}</span></button>
      <label class="session-search"><Search :size="16" /><input v-model="sessions.search" :placeholder="tr('搜索 Session', 'Search Sessions')" /></label>
    </div>
    <div class="session-heading"><span>{{ tr('最近', 'Recent') }}</span><span>{{ sessions.filtered.length }}</span></div>
    <nav class="session-list" :aria-label="tr('Session 列表', 'Session list')">
      <button v-for="session in sessions.filtered" :key="session.session_id" class="session-row" :class="{ active: sessions.activeId === session.session_id, pending: openingId === session.session_id }" :disabled="openingId === session.session_id" @click="openSession(session.session_id)">
        <span class="session-copy"><strong>{{ session.title || session.preview || tr('新对话', 'New chat') }}</strong><small v-if="sourceLabel(session)">{{ sourceLabel(session) }}</small></span>
        <span class="session-actions">
          <button class="ellipsis-button" :aria-label="tr('Session 操作', 'Session actions')" @click.stop="actionSession = actionSession === session.session_id ? '' : session.session_id"><Ellipsis :size="18" /></button>
          <span v-if="actionSession === session.session_id" class="popover session-menu" @click.stop>
            <button @click="beginRename(session.session_id, session.title || session.preview)">{{ tr('重命名', 'Rename') }}</button>
            <button class="danger-text" @click="beginDelete(session.session_id)">{{ tr('删除', 'Delete') }}</button>
          </span>
        </span>
      </button>
      <p v-if="!sessions.loading && !sessions.filtered.length" class="sidebar-empty">{{ tr('还没有 Session', 'No Sessions yet') }}</p>
    </nav>
    <p v-if="failure && !renameId && !deleteId" class="sidebar-action-feedback form-error" role="alert" aria-live="assertive">{{ failure }}</p>
    <div ref="accountArea" class="account-area">
      <button class="account-trigger" @click="ui.accountMenuOpen = !ui.accountMenuOpen">
        <UserAvatar :name="auth.user?.display_name || auth.user?.username || ''" />
        <span><strong>{{ auth.user?.display_name || auth.user?.username }}</strong><small>{{ auth.user?.roles.join(' · ') }}</small></span>
        <Ellipsis :size="18" />
      </button>
      <div v-if="ui.accountMenuOpen" class="popover account-menu">
        <div class="account-summary"><UserAvatar :name="auth.user?.display_name || ''" /><span><strong>{{ auth.user?.display_name }}</strong><small>@{{ auth.user?.username }}</small></span></div>
        <button @click="ui.openSettings('personalization')"><SlidersHorizontal :size="17" />{{ tr('个性化', 'Personalization') }}</button>
        <button @click="ui.openSettings('profile')"><UserRound :size="17" />{{ tr('个人资料', 'Profile') }}</button>
        <button @click="ui.openSettings('general')"><Settings :size="17" />{{ tr('设置', 'Settings') }}</button>
        <button v-if="auth.canOpenAdmin" @click="router.push('/admin')"><Settings :size="17" />{{ tr('管理后台', 'Administration') }}</button>
        <hr /><button class="danger-text" @click="logout"><X :size="17" />{{ tr('退出登录', 'Log out') }}</button>
      </div>
    </div>
  </aside>

  <div v-if="renameId" class="modal-backdrop" @click.self="renameId = ''">
    <form class="dialog-card compact-dialog" @submit.prevent="saveRename"><h2>{{ tr('重命名 Session', 'Rename Session') }}</h2><input v-model="renameTitle" maxlength="120" autofocus required /><p v-if="failure" class="form-error" role="alert" aria-live="assertive">{{ failure }}</p><div class="dialog-actions"><button type="button" :disabled="renameBusy" @click="renameId = ''">{{ tr('取消', 'Cancel') }}</button><button class="primary-button" :disabled="renameBusy">{{ renameBusy ? tr('保存中…', 'Saving…') : tr('保存', 'Save') }}</button></div></form>
  </div>
  <div v-if="deleteId" class="modal-backdrop" @click.self="deleteId = ''">
    <section class="dialog-card compact-dialog"><h2>{{ tr('删除这个 Session？', 'Delete this Session?') }}</h2><p>{{ tr('消息、相关元数据和外部渠道路由将被删除；下一条渠道消息会创建新 Session。此操作无法撤销。', 'Messages, related metadata, and the external-channel route will be deleted. The next channel message creates a new Session. This cannot be undone.') }}</p><p v-if="failure" class="form-error" role="alert" aria-live="assertive">{{ failure }}</p><div class="dialog-actions"><button :disabled="deleteBusy" @click="deleteId = ''">{{ tr('取消', 'Cancel') }}</button><button class="danger-button" :disabled="deleteBusy" @click="confirmDelete">{{ deleteBusy ? tr('删除中…', 'Deleting…') : tr('确认删除', 'Delete') }}</button></div></section>
  </div>
</template>
