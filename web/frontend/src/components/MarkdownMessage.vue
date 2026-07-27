<script setup lang="ts">
import DOMPurify from "dompurify";
import { marked } from "marked";
import markedKatex from "marked-katex-extension";
import { computed } from "vue";

import "katex/dist/katex.min.css";

const props = defineProps<{ content: string }>();
marked.setOptions({ breaks: true, gfm: true });
marked.use(markedKatex({
  throwOnError: false,
  strict: "ignore",
  trust: false,
  macros: { "\\bm": "\\boldsymbol{#1}" },
}));
const html = computed(() => DOMPurify.sanitize(marked.parse(props.content || "") as string, { USE_PROFILES: { html: true } }));
</script>

<template><div class="markdown-body" v-html="html" /></template>
