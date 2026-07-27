<script setup lang="ts">
import { ArrowUp, Bot, GitFork, Menu, Square, UserRound } from "@lucide/vue";
import { nextTick, onMounted, ref, watch } from "vue";

import { useChatStore } from "@/stores/chat";
import { useModelStore } from "@/stores/models";
import { useSessionStore } from "@/stores/sessions";
import { useUiStore } from "@/stores/ui";
import MarkdownMessage from "./MarkdownMessage.vue";
import RuntimeStatus from "./RuntimeStatus.vue";

const chat = useChatStore();
const models = useModelStore();
const sessions = useSessionStore();
const ui = useUiStore();
const message = ref("");
const list = ref<HTMLElement | null>(null);

onMounted(async () => {
  chat.initialize();
  await sessions.refresh();
  const auth = (await import("@/stores/auth")).useAuthStore();
  const lastId = localStorage.getItem(`zhice.lastSession.${auth.user?.id || "local"}`) || "";
  const initial = ui.startPage === "last" && sessions.items.some((item) => item.session_id === lastId)
    ? lastId
    : sessions.items[0]?.session_id || "";
  if (initial) await sessions.open(initial);
  if (sessions.activeId) await models.refresh(sessions.activeId);
});
watch(() => sessions.activeId, (id) => void models.refresh(id));
watch(() => sessions.messages.map((item) => item.content).join(""), async () => { await nextTick(); list.value?.scrollTo({ top: list.value.scrollHeight, behavior: "smooth" }); });

async function submit() { const text = message.value; message.value = ""; await chat.send(text); }
</script>

<template>
<main class="chat-main">
  <header class="chat-header glass-panel">
    <button v-if="ui.sidebarCollapsed" class="icon-button" @click="ui.sidebarCollapsed = false"><Menu :size="20" /></button>
    <div><strong>{{ sessions.active?.title || '新对话' }}</strong><small>{{ sessions.active?.channel && sessions.active.channel !== 'web' ? `${sessions.active.channel.toUpperCase()} Session` : 'Web Session' }}</small></div>
    <label class="model-picker"><span>{{ models.endpoint || '模型' }}</span><select v-model="models.current" :disabled="!sessions.activeId || chat.sending" @change="models.select(sessions.activeId, models.current)"><option v-for="model in models.models" :key="model">{{ model }}</option></select></label>
  </header>
  <section ref="list" class="message-scroll" :class="`width-${ui.contentWidth}`">
    <div v-if="!sessions.messages.length" class="empty-chat"><span><Bot :size="27" /></span><h1>今天想一起完成什么？</h1><p>我会在当前 Session 中保留完整对话真值，并通过清晰的运行状态反馈正在发生的工作。</p></div>
    <article v-for="(item, index) in sessions.messages" :key="`${index}-${item.turn_id || ''}`" class="message" :class="item.role">
      <div class="message-avatar"><UserRound v-if="item.role === 'user'" :size="17" /><Bot v-else :size="17" /></div>
      <div class="message-content"><MarkdownMessage :content="item.content" /><RuntimeStatus v-if="item.runtime" :state="item.runtime" /></div>
    </article>
  </section>
  <section v-if="!sessions.writable" class="readonly-banner"><span>该外部渠道 Session 为只读来源。如需继续，请复制到 Web 私聊。</span><button @click="sessions.forkActive"><GitFork :size="16" />继续到 Web</button></section>
  <div class="composer-zone">
    <form class="composer glass-panel" @submit.prevent="submit">
      <textarea v-model="message" :disabled="!sessions.writable || chat.sending" rows="1" placeholder="给 ZhiCe-Agent 发消息" @keydown.enter.exact.prevent="submit" />
      <button v-if="chat.sending" class="send-control stop" type="button" aria-label="停止" @click="chat.stop"><Square :size="16" fill="currentColor" /></button>
      <button v-else class="send-control" type="submit" aria-label="发送" :disabled="!message.trim() || !sessions.writable"><ArrowUp :size="18" /></button>
    </form>
    <small>ZhiCe-Agent 可能会犯错，请检查重要信息。</small>
  </div>
</main>
</template>
