<script setup lang="ts">
import AdminLayout from "@/layouts/AdminLayout.vue";
import AuthLayout from "@/layouts/AuthLayout.vue";
import { useAuthStore } from "@/stores/auth";

const auth = useAuthStore();
</script>

<template>
  <AdminLayout v-if="auth.authenticated && auth.canOpenAdmin" />
  <main v-else-if="auth.authenticated" class="access-denied"><h1>无权访问管理后台</h1><p>当前账号没有任何管理栏目权限。</p><a class="primary-button" href="/">返回聊天</a></main>
  <AuthLayout v-else />
</template>
