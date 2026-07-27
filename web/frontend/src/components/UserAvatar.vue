<script setup lang="ts">
import { computed } from "vue";

const props = defineProps<{ name: string }>();
const initials = computed(() => {
  const value = props.name.trim();
  if (!value) return "U";
  const words = value.split(/\s+/).filter(Boolean);
  if (words.length > 1 && Array.from(value).every((character) => character.charCodeAt(0) < 128)) return `${words[0][0]}${words.at(-1)?.[0]}`.toUpperCase();
  const chars = Array.from(value.replace(/\s/g, ""));
  return chars.slice(0, 2).join("").toUpperCase();
});
</script>

<template><span class="user-avatar" aria-hidden="true">{{ initials }}</span></template>
