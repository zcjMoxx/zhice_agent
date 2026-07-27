<script setup lang="ts">
import { Ellipsis, MessageSquarePlus, PanelLeftClose, Search, Settings, SlidersHorizontal, UserRound, X } from "@lucide/vue";
import { onBeforeUnmount, onMounted, ref } from "vue";
import { useRouter } from "vue-router";

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

function closeMenus(event?: KeyboardEvent) {
  if (event && event.key !== "Escape") return;
  actionSession.value = "";
  ui.accountMenuOpen = false;
}
onMounted(() => document.addEventListener("keydown", closeMenus));
onBeforeUnmount(() => document.removeEventListener("keydown", closeMenus));

function beginRename(id: string, title: string) { renameId.value = id; renameTitle.value = title || "新对话"; actionSession.value = ""; }
async function saveRename() {
  try { await sessions.rename(renameId.value, renameTitle.value); renameId.value = ""; }
  catch (error) { failure.value = errorMessage(error); }
}
async function confirmDelete() {
  try { await sessions.remove(deleteId.value); deleteId.value = ""; }
  catch (error) { failure.value = errorMessage(error); }
}
async function logout() { await auth.logout(); ui.accountMenuOpen = false; }
</script>

<template>
  <aside class="session-sidebar" :class="{ collapsed: ui.sidebarCollapsed }" @click.self="closeMenus()">
    <header class="sidebar-brand">
      <a class="brand-lockup compact" href="/"><img :src="'/static/zhice-logo-a.png'" alt="" /><strong>ZhiCe-Agent</strong></a>
      <button class="icon-button" title="收起 Session 侧栏" @click="ui.sidebarCollapsed = !ui.sidebarCollapsed"><PanelLeftClose :size="19" /></button>
    </header>
    <div class="sidebar-tools">
      <button class="new-chat" @click="sessions.create()"><MessageSquarePlus :size="18" /><span>新对话</span></button>
      <label class="session-search"><Search :size="16" /><input v-model="sessions.search" placeholder="搜索 Session" /></label>
    </div>
    <div class="session-heading"><span>最近</span><span>{{ sessions.filtered.length }}</span></div>
    <nav class="session-list" aria-label="Session 列表">
      <button v-for="session in sessions.filtered" :key="session.session_id" class="session-row" :class="{ active: sessions.activeId === session.session_id }" @click="sessions.open(session.session_id)">
        <span class="session-copy"><strong>{{ session.title || session.preview || '新对话' }}</strong><small v-if="session.channel && session.channel !== 'web'">{{ session.channel.toUpperCase() }} · 只读来源</small></span>
        <span class="session-actions">
          <button class="ellipsis-button" aria-label="Session 操作" @click.stop="actionSession = actionSession === session.session_id ? '' : session.session_id"><Ellipsis :size="18" /></button>
          <span v-if="actionSession === session.session_id" class="popover session-menu" @click.stop>
            <button @click="beginRename(session.session_id, session.title || session.preview)">重命名</button>
            <button class="danger-text" @click="deleteId = session.session_id; actionSession = ''">删除</button>
          </span>
        </span>
      </button>
      <p v-if="!sessions.loading && !sessions.filtered.length" class="sidebar-empty">还没有 Session</p>
    </nav>
    <div class="account-area">
      <button class="account-trigger" @click="ui.accountMenuOpen = !ui.accountMenuOpen">
        <UserAvatar :name="auth.user?.display_name || auth.user?.username || ''" />
        <span><strong>{{ auth.user?.display_name || auth.user?.username }}</strong><small>{{ auth.user?.roles.join(' · ') }}</small></span>
        <Ellipsis :size="18" />
      </button>
      <div v-if="ui.accountMenuOpen" class="popover account-menu">
        <div class="account-summary"><UserAvatar :name="auth.user?.display_name || ''" /><span><strong>{{ auth.user?.display_name }}</strong><small>@{{ auth.user?.username }}</small></span></div>
        <button @click="ui.openSettings('personalization')"><SlidersHorizontal :size="17" />个性化</button>
        <button @click="ui.openSettings('profile')"><UserRound :size="17" />个人资料</button>
        <button @click="ui.openSettings('general')"><Settings :size="17" />设置</button>
        <button v-if="auth.canOpenAdmin" @click="router.push('/admin')"><Settings :size="17" />管理后台</button>
        <hr /><button class="danger-text" @click="logout"><X :size="17" />退出登录</button>
      </div>
    </div>
  </aside>

  <div v-if="renameId" class="modal-backdrop" @click.self="renameId = ''">
    <form class="dialog-card compact-dialog" @submit.prevent="saveRename"><h2>重命名 Session</h2><input v-model="renameTitle" maxlength="120" autofocus required /><p v-if="failure" class="form-error">{{ failure }}</p><div class="dialog-actions"><button type="button" @click="renameId = ''">取消</button><button class="primary-button">保存</button></div></form>
  </div>
  <div v-if="deleteId" class="modal-backdrop" @click.self="deleteId = ''">
    <section class="dialog-card compact-dialog"><h2>删除这个 Session？</h2><p>消息与相关 Session 元数据将被删除，此操作无法撤销。</p><div class="dialog-actions"><button @click="deleteId = ''">取消</button><button class="danger-button" @click="confirmDelete">确认删除</button></div></section>
  </div>
</template>
