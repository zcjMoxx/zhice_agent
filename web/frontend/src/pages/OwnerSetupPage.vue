<script setup lang="ts">
import { useRouter } from "vue-router";

import AuthLayout from "@/layouts/AuthLayout.vue";

const router = useRouter();
</script>

<template><AuthLayout setup @authenticated="router.replace('/')" /></template>
