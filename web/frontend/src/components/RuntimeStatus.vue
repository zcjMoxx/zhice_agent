<script setup lang="ts">
import { Bot, LoaderCircle } from "@lucide/vue";
import type { RuntimeUiState } from "@/api/types";

defineProps<{ state: RuntimeUiState }>();
</script>

<template>
  <div v-if="state.title || Object.keys(state.childTasks).length" class="runtime-status">
    <div v-if="state.title"><LoaderCircle class="spin" :size="15" /><span>{{ state.title }}</span></div>
    <div v-for="(task, key) in state.childTasks" :key="key" class="child-runtime"><Bot :size="14" /><span>{{ task.title }}</span></div>
  </div>
</template>
