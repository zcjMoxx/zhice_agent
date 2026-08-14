<script setup lang="ts">
import { onMounted, watch } from "vue";
import { RouterView, useRouter } from "vue-router";

import { uiText } from "@/i18n";
import { installAuthorizationRefresh, useAuthStore } from "@/stores/auth";
import { useTravelStore } from "@/stores/travel";
import { useUiStore } from "@/stores/ui";

const auth = useAuthStore();
const ui = useUiStore();
const travel = useTravelStore();
const router = useRouter();

installAuthorizationRefresh();

onMounted(async () => {
  ui.load();
  await auth.fetchCurrentUser();
});

watch(() => auth.user?.id, (id) => {
  ui.load(id || "pre-auth");
  if (id) void travel.initialize(id);
  else travel.resetForIdentity();
  if (!id && ["/admin", "/travel"].includes(router.currentRoute.value.path)) void router.replace("/");
});
</script>

<template>
  <div v-if="!auth.initialized" class="boot-screen" aria-live="polite">
    <img :src="'/static/zhice-logo-a.png'" alt="" />
    <span>{{ uiText(ui.language, '正在连接 ZhiCe-Agent…', 'Connecting to ZhiCe-Agent…') }}</span>
  </div>
  <RouterView v-else />
</template>
