<script setup lang="ts">
import { Languages, Moon, Sun } from "@lucide/vue";

import { uiText } from "@/i18n";
import { useAuthStore } from "@/stores/auth";
import { useUiStore } from "@/stores/ui";

const auth = useAuthStore();
const ui = useUiStore();

function tr(chinese: string, english: string): string { return uiText(ui.language, chinese, english); }
function userId(): string { return auth.user?.id || "pre-auth"; }
</script>

<template>
  <div class="quick-preferences" :aria-label="tr('界面快捷设置', 'Quick appearance settings')">
    <button
      class="quick-preference"
      type="button"
      :aria-label="tr('切换界面语言', 'Switch interface language')"
      :data-tooltip="ui.language === 'zh-CN' ? 'Switch to English' : '切换为简体中文'"
      @click="ui.toggleLanguage(userId())"
    >
      <Languages :size="17" />
      <span>{{ ui.language === 'zh-CN' ? 'EN' : '中' }}</span>
    </button>
    <button
      class="quick-preference"
      type="button"
      :aria-label="tr('切换明暗主题', 'Toggle light or dark theme')"
      :data-tooltip="ui.resolvedTheme === 'dark' ? tr('切换为浅色', 'Switch to light') : tr('切换为暗色', 'Switch to dark')"
      @click="ui.toggleTheme(userId())"
    >
      <Sun v-if="ui.resolvedTheme === 'dark'" :size="17" />
      <Moon v-else :size="17" />
    </button>
  </div>
</template>
