<script setup lang="ts">
import AdminLayout from "@/layouts/AdminLayout.vue";
import AuthLayout from "@/layouts/AuthLayout.vue";
import { uiText } from "@/i18n";
import { useAuthStore } from "@/stores/auth";
import { useUiStore } from "@/stores/ui";

const auth = useAuthStore();
const ui = useUiStore();
function tr(chinese: string, english: string): string { return uiText(ui.language, chinese, english); }
</script>

<template>
  <AdminLayout v-if="auth.authenticated && auth.canOpenAdmin" />
  <main v-else-if="auth.authenticated" class="access-denied"><h1>{{ tr('无权访问管理后台', 'Administration access denied') }}</h1><p>{{ tr('当前账号没有任何管理栏目权限。', 'This account has no administration permissions.') }}</p><a class="primary-button" href="/">{{ tr('返回聊天', 'Back to chat') }}</a></main>
  <AuthLayout v-else />
</template>
