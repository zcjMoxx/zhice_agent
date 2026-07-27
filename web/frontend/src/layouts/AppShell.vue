<script setup lang="ts">
import ChatPage from "@/components/ChatPage.vue";
import InteractionDialogs from "@/components/InteractionDialogs.vue";
import SessionSidebar from "@/components/SessionSidebar.vue";
import SettingsCenter from "@/components/SettingsCenter.vue";
import { useUiStore } from "@/stores/ui";

const ui = useUiStore();
</script>

<template><div class="app-shell"><SessionSidebar /><ChatPage /><SettingsCenter v-if="ui.settingsOpen" /><InteractionDialogs /></div></template>
