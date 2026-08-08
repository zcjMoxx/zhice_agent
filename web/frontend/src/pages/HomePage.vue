<script setup lang="ts">
import { onMounted } from "vue";
import { useRoute, useRouter } from "vue-router";

import AppShell from "@/layouts/AppShell.vue";
import AuthLayout from "@/layouts/AuthLayout.vue";
import { useAuthStore } from "@/stores/auth";

const auth = useAuthStore();
const route = useRoute();
const router = useRouter();

onMounted(() => {
  const legacyToken = typeof route.query.channel_bind === "string" ? route.query.channel_bind.trim() : "";
  if (!legacyToken) return;
  const query = { ...route.query };
  delete query.channel_bind;
  void router.replace({ name: "qq-binding", query: { ...query, token: legacyToken } });
});
</script>

<template>
  <template v-if="auth.authenticated">
    <AppShell />
  </template>
  <AuthLayout v-else />
</template>
