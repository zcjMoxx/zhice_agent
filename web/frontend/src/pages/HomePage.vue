<script setup lang="ts">
import { onMounted, ref } from "vue";
import { useRouter } from "vue-router";

import { api } from "@/api/client";
import AppShell from "@/layouts/AppShell.vue";
import AuthLayout from "@/layouts/AuthLayout.vue";
import { useAuthStore } from "@/stores/auth";
import { errorMessage } from "@/stores/chat";
import { useUiStore } from "@/stores/ui";

const auth = useAuthStore();
const ui = useUiStore();
const router = useRouter();
const bindError = ref("");

async function completeChannelBinding() {
  const params = new URLSearchParams(window.location.search);
  const token = params.get("channel_bind");
  if (!token || !auth.authenticated) return;
  try {
    await api.qqAuthorize(token);
    history.replaceState({}, "", "/");
    ui.openSettings("channels");
  } catch (error) { bindError.value = errorMessage(error); }
}

function authenticated() {
  void completeChannelBinding();
  void router.replace("/");
}

onMounted(() => void completeChannelBinding());
</script>

<template>
  <template v-if="auth.authenticated">
    <AppShell />
    <p v-if="bindError" class="channel-bind-notice form-error">{{ bindError }}</p>
  </template>
  <AuthLayout v-else @authenticated="authenticated" />
</template>
