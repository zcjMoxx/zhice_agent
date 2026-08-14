<script setup lang="ts">
import { ArrowUp, Bot, GitFork, Menu, Square, UserRound } from "@lucide/vue";
import { nextTick, onBeforeUnmount, onMounted, ref, watch } from "vue";

import { useAuthStore } from "@/stores/auth";
import { useChatStore } from "@/stores/chat";
import { useModelStore } from "@/stores/models";
import { useSessionStore } from "@/stores/sessions";
import { useUiStore } from "@/stores/ui";
import { uiText } from "@/i18n";
import { consumeChatHandoff } from "@/travel/chatHandoff";
import MarkdownMessage from "./MarkdownMessage.vue";
import QuickPreferences from "./QuickPreferences.vue";
import RuntimeStatus from "./RuntimeStatus.vue";

const chat = useChatStore();
const auth = useAuthStore();
const models = useModelStore();
const sessions = useSessionStore();
const ui = useUiStore();
const message = ref("");
const list = ref<HTMLElement | null>(null);
const followingLatest = ref(true);
const restoringPosition = ref(false);
let smoothNextFollow = false;

function tr(chinese: string, english: string): string { return uiText(ui.language, chinese, english); }
function activeSource(): string {
  const active = sessions.active;
  if (!active || !active.channel || active.channel === "web") return "Web Session";
  if (["cli", "cli_legacy"].includes(active.channel.toLowerCase())) return "CLI Session";
  if (active.channel === "qq" && active.conversation_type === "group") return tr("QQ群 Session", "QQ group Session");
  if (active.channel === "weixin") return tr("微信 Session", "Weixin Session");
  return `${active.channel.toUpperCase()} Session`;
}

function positionKey(sessionId: string): string {
  return `zhice.scroll.${auth.user?.id || "local"}.${sessionId}`;
}

function nearLatest(container: HTMLElement): boolean {
  return container.scrollHeight - container.clientHeight - container.scrollTop <= 80;
}

function savePosition(sessionId = sessions.activeId): void {
  if (!sessionId || !list.value || restoringPosition.value) return;
  try { sessionStorage.setItem(positionKey(sessionId), String(Math.max(0, list.value.scrollTop))); }
  catch { /* Browser storage can be unavailable without breaking chat. */ }
}

function scrollToLatest(behavior: ScrollBehavior = "auto"): void {
  const container = list.value;
  if (!container) return;
  if (typeof container.scrollTo === "function") container.scrollTo({ top: container.scrollHeight, behavior });
  else container.scrollTop = container.scrollHeight;
}

async function restorePosition(sessionId: string): Promise<void> {
  restoringPosition.value = true;
  await nextTick();
  const container = list.value;
  if (!container || sessions.activeId !== sessionId) { restoringPosition.value = false; return; }
  let saved: number | null = null;
  try {
    const raw = sessionStorage.getItem(positionKey(sessionId));
    if (raw !== null && Number.isFinite(Number(raw))) saved = Number(raw);
  } catch { /* Fall back to the latest message. */ }
  const maximum = Math.max(0, container.scrollHeight - container.clientHeight);
  container.scrollTop = saved === null ? maximum : Math.min(Math.max(0, saved), maximum);
  followingLatest.value = nearLatest(container);
  restoringPosition.value = false;
}

function handleScroll(): void {
  if (!list.value || restoringPosition.value) return;
  followingLatest.value = nearLatest(list.value);
  savePosition();
}

onMounted(async () => {
  chat.initialize();
  const handoff = consumeChatHandoff();
  if (handoff) {
    sessions.startDraft();
    message.value = handoff;
    await sessions.refresh();
    return;
  }
  await sessions.refresh();
  if (ui.startPage === "new") {
    sessions.startDraft();
    return;
  }
  if (sessions.items[0]) await sessions.open(sessions.items[0].session_id);
});
onBeforeUnmount(() => savePosition());
watch(() => sessions.activeId, async (id, previousId) => {
  if (previousId) savePosition(previousId);
  const modelRefresh = models.refresh(id);
  if (id) await restorePosition(id);
  await modelRefresh;
}, { immediate: true });
watch(
  () => sessions.messages.map((item) => `${item.content}\u0000${item.pending ? "1" : "0"}`).join("\u0001"),
  async () => {
    if (restoringPosition.value || !followingLatest.value) return;
    const sessionId = sessions.activeId;
    await nextTick();
    if (!sessionId || sessions.activeId !== sessionId || restoringPosition.value) return;
    scrollToLatest(smoothNextFollow ? "smooth" : "auto");
    smoothNextFollow = false;
    savePosition(sessionId);
  },
);

async function submit() {
  const text = message.value;
  if (!text.trim()) return;
  message.value = "";
  followingLatest.value = true;
  smoothNextFollow = true;
  await chat.send(text);
}
</script>

<template>
<main class="chat-main">
  <header class="chat-header glass-panel">
    <button v-if="ui.sidebarCollapsed" class="icon-button" @click="ui.sidebarCollapsed = false"><Menu :size="20" /></button>
    <div class="chat-heading"><strong>{{ sessions.active?.title || tr('新对话', 'New chat') }}</strong><small>{{ activeSource() }}</small></div>
    <QuickPreferences />
    <label class="model-picker"><select v-model="models.current" :aria-label="tr('模型', 'Model')" :disabled="chat.sending || models.loading || !models.models.length" @change="models.select(sessions.activeId, models.current)"><option v-for="model in models.models" :key="model">{{ model }}</option></select></label>
  </header>
  <section ref="list" class="message-scroll" @scroll="handleScroll">
    <div class="message-column">
      <div v-if="!sessions.messages.length" class="empty-chat"><span><Bot :size="27" /></span><h1>{{ tr('今天想一起完成什么？', 'What would you like to accomplish today?') }}</h1><p>{{ tr('我会在当前 Session 中保留完整对话真值，并通过清晰的运行状态反馈正在发生的工作。', 'I keep the complete conversation truth in this Session and show clear runtime feedback while work is in progress.') }}</p></div>
      <article v-for="(item, index) in sessions.messages" :key="`${index}-${item.turn_id || ''}`" class="message" :class="item.role">
        <div class="message-avatar"><UserRound v-if="item.role === 'user'" :size="17" /><Bot v-else :size="17" /></div>
        <div class="message-content"><MarkdownMessage :content="item.content" /><RuntimeStatus v-if="item.runtime" :state="item.runtime" /></div>
      </article>
    </div>
  </section>
  <section v-if="!sessions.writable" class="readonly-banner"><span>{{ tr('该 QQ 群 Session 在 Web 中为只读来源。如需继续，请复制到 Web 私聊。', 'This QQ group Session is read-only on the Web. Copy it to a private Web chat to continue.') }}</span><button @click="sessions.forkActive"><GitFork :size="16" />{{ tr('继续到 Web', 'Continue on Web') }}</button></section>
  <div class="composer-zone">
    <form class="composer glass-panel" @submit.prevent="submit">
      <textarea v-model="message" :disabled="!sessions.writable || chat.sending" rows="1" :placeholder="tr('给 ZhiCe-Agent 发消息', 'Message ZhiCe-Agent')" @keydown.enter.exact.prevent="submit" />
      <button v-if="chat.sending" class="send-control stop" type="button" :aria-label="tr('停止', 'Stop')" @click="chat.stop"><Square :size="16" fill="currentColor" /></button>
      <button v-else class="send-control" type="submit" :aria-label="tr('发送', 'Send')" :disabled="!message.trim() || !sessions.writable"><ArrowUp :size="18" /></button>
    </form>
    <small>{{ tr('ZhiCe-Agent 可能会犯错，请检查重要信息。', 'ZhiCe-Agent can make mistakes. Check important information.') }}</small>
  </div>
</main>
</template>
