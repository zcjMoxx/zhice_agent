<script setup lang="ts">
import { AlertCircle, CheckCircle2, LoaderCircle } from "@lucide/vue";
import { computed, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";

import QuickPreferences from "@/components/QuickPreferences.vue";
import { uiText } from "@/i18n";
import AuthLayout from "@/layouts/AuthLayout.vue";
import { useAuthStore } from "@/stores/auth";
import { useChannelStore } from "@/stores/channels";
import { useUiStore } from "@/stores/ui";

type BindingState = "ready" | "binding" | "success" | "error" | "missing";

const auth = useAuthStore();
const channels = useChannelStore();
const ui = useUiStore();
const route = useRoute();
const router = useRouter();
const state = ref<BindingState>("ready");
const busy = ref(false);
const closeHint = ref(false);
const token = computed(() => typeof route.query.token === "string" ? route.query.token.trim() : "");

function tr(chinese: string, english: string): string { return uiText(ui.language, chinese, english); }

async function completeBinding() {
  if (!token.value) {
    state.value = "missing";
    return;
  }
  if (!auth.authenticated || busy.value) return;
  busy.value = true;
  state.value = "binding";
  channels.pendingQqToken = token.value;
  try {
    await channels.authorizeQq(token.value);
    state.value = "success";
    await router.replace({ name: "qq-binding" });
  } catch {
    state.value = "error";
  } finally {
    busy.value = false;
  }
}

function closeAndReturnToQq() {
  closeHint.value = false;
  window.close();
  window.setTimeout(() => {
    if (document.hidden) return;
    if (window.history.length > 1) window.history.back();
    window.setTimeout(() => {
      if (!document.hidden) closeHint.value = true;
    }, 250);
  }, 150);
}

watch(token, (value) => {
  if (value) channels.pendingQqToken = value;
  else if (state.value !== "success") state.value = "missing";
}, { immediate: true });
watch(() => auth.authenticated, (authenticated) => {
  if (authenticated) void completeBinding();
}, { immediate: true });
</script>

<template>
  <AuthLayout v-if="!auth.authenticated && token" flow="qq-binding" @authenticated="completeBinding" />
  <main v-else class="channel-binding-page">
    <QuickPreferences />
    <section class="channel-binding-card">
      <span v-if="state === 'binding' || state === 'ready'" class="binding-result-icon is-loading"><LoaderCircle :size="30" /></span>
      <span v-else-if="state === 'success'" class="binding-result-icon is-success"><CheckCircle2 :size="32" /></span>
      <span v-else class="binding-result-icon is-error"><AlertCircle :size="32" /></span>

      <template v-if="state === 'binding' || state === 'ready'">
        <h1>{{ tr('正在绑定 QQ', 'Connecting QQ') }}</h1>
        <p>{{ tr('正在确认账号与 QQ 身份，请稍候。', 'Confirming your account and QQ identity.') }}</p>
      </template>
      <template v-else-if="state === 'success'">
        <h1>{{ tr('QQ 绑定成功', 'QQ connected') }}</h1>
        <p>{{ tr('现在可以关闭此页面，返回 QQ 继续和机器人聊天。', 'You can close this page and return to QQ to continue chatting.') }}</p>
        <button class="primary-button binding-primary-action" type="button" @click="closeAndReturnToQq">{{ tr('关闭并返回 QQ', 'Close and return to QQ') }}</button>
        <button class="binding-secondary-action" type="button" @click="router.push('/')">{{ tr('进入 ZhiCe-Agent', 'Open ZhiCe-Agent') }}</button>
        <p v-if="closeHint" class="binding-close-hint">{{ tr('当前浏览器未允许自动关闭，请点击右上角关闭并返回 QQ。', 'This browser did not allow automatic closing. Use the top-right close control to return to QQ.') }}</p>
      </template>
      <template v-else-if="state === 'error'">
        <h1>{{ tr('QQ 绑定未完成', 'QQ connection failed') }}</h1>
        <p class="form-error">{{ channels.qqAuthorizationError }}</p>
        <button class="primary-button binding-primary-action" :disabled="busy" type="button" @click="completeBinding">{{ tr('重新绑定', 'Try again') }}</button>
        <button class="binding-secondary-action" type="button" @click="router.push('/')">{{ tr('进入聊天首页', 'Open chat') }}</button>
      </template>
      <template v-else>
        <h1>{{ tr('绑定链接无效', 'Invalid binding link') }}</h1>
        <p>{{ tr('请返回 QQ 私聊机器人，重新发送 /bind 获取新链接。', 'Return to the QQ bot and send /bind again for a new link.') }}</p>
        <button class="primary-button binding-primary-action" type="button" @click="router.push('/')">{{ tr('进入聊天首页', 'Open chat') }}</button>
      </template>
    </section>
  </main>
</template>
