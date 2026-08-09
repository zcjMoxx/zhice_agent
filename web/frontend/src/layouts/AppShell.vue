<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref } from "vue";

import ChatPage from "@/components/ChatPage.vue";
import InteractionDialogs from "@/components/InteractionDialogs.vue";
import SessionSidebar from "@/components/SessionSidebar.vue";
import SettingsCenter from "@/components/SettingsCenter.vue";
import { useUiStore } from "@/stores/ui";

const ui = useUiStore();
const mobileViewport = ref(false);
let mobileMediaQuery: MediaQueryList | null = null;

function syncViewportMode(event?: MediaQueryList | MediaQueryListEvent) {
  const isMobile = event ? event.matches : mobileMediaQuery?.matches ?? false;
  mobileViewport.value = isMobile;
  // Keep the conversation visible on phones, while preserving the normal
  // desktop two-column layout when switching back to a large viewport.
  ui.sidebarCollapsed = isMobile;
}

onMounted(() => {
  mobileMediaQuery = window.matchMedia?.("(max-width: 720px)") || null;
  if (!mobileMediaQuery) return;
  syncViewportMode(mobileMediaQuery);
  mobileMediaQuery.addEventListener?.("change", syncViewportMode);
});
onBeforeUnmount(() => mobileMediaQuery?.removeEventListener?.("change", syncViewportMode));
</script>

<template>
  <div class="app-shell" :data-viewport="mobileViewport ? 'mobile' : 'desktop'">
    <SessionSidebar />
    <button v-if="mobileViewport && !ui.sidebarCollapsed" class="mobile-sidebar-backdrop" type="button" aria-label="关闭 Session 侧栏" @click="ui.sidebarCollapsed = true" />
    <ChatPage />
    <SettingsCenter v-if="ui.settingsOpen" />
    <InteractionDialogs />
  </div>
</template>
