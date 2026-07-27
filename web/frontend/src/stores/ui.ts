import { defineStore } from "pinia";

import type { UiLanguage } from "@/i18n";

export type ColorModePreference = "system" | "light" | "dark";
export type ThemeFamily = "classic" | "obsidian" | "ocean" | "sage" | "aurora" | "amber";
export type StartPagePreference = "chat" | "new";

function mediaDark(): boolean { return window.matchMedia?.("(prefers-color-scheme: dark)").matches ?? false; }
function isColorMode(value: string | null): value is ColorModePreference { return value === "system" || value === "light" || value === "dark"; }
function isThemeFamily(value: string | null): value is ThemeFamily { return value === "classic" || value === "obsidian" || value === "ocean" || value === "sage" || value === "aurora" || value === "amber"; }
let systemThemeListenerInstalled = false;

export const useUiStore = defineStore("ui", {
  state: () => ({
    colorMode: "system" as ColorModePreference,
    themeFamily: "obsidian" as ThemeFamily,
    language: "zh-CN" as UiLanguage,
    sidebarCollapsed: false,
    settingsOpen: false,
    settingsSection: "general",
    accountMenuOpen: false,
    density: "comfortable",
    startPage: "chat" as StartPagePreference,
  }),
  getters: {
    resolvedTheme: (state) => state.colorMode === "system" ? (mediaDark() ? "dark" : "light") : state.colorMode,
  },
  actions: {
    load(userId = "pre-auth") {
      const prefix = `zhice.ui.${userId}.`;
      const storedColorMode = localStorage.getItem(prefix + "colorMode");
      const legacyTheme = localStorage.getItem(prefix + "theme");
      this.colorMode = isColorMode(storedColorMode) ? storedColorMode : isColorMode(legacyTheme) ? legacyTheme : "system";
      this.themeFamily = isThemeFamily(localStorage.getItem(prefix + "themeFamily")) ? localStorage.getItem(prefix + "themeFamily") as ThemeFamily : "obsidian";
      if (!isColorMode(storedColorMode) && isColorMode(legacyTheme)) {
        localStorage.setItem(prefix + "colorMode", legacyTheme);
        localStorage.removeItem(prefix + "theme");
      }
      this.language = (localStorage.getItem(prefix + "language") as UiLanguage) || "zh-CN";
      this.density = localStorage.getItem(prefix + "density") || "comfortable";
      localStorage.removeItem(prefix + "contentWidth");
      this.startPage = localStorage.getItem(prefix + "startPage") === "new" ? "new" : "chat";
      this.applyTheme();
      this.applyLanguage();
      if (!systemThemeListenerInstalled && window.matchMedia) {
        window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => {
          if (this.colorMode === "system") this.applyTheme();
        });
        systemThemeListenerInstalled = true;
      }
    },
    setColorMode(colorMode: ColorModePreference, userId = "pre-auth") {
      this.colorMode = colorMode;
      localStorage.setItem(`zhice.ui.${userId}.colorMode`, colorMode);
      this.applyTheme();
    },
    setThemeFamily(themeFamily: ThemeFamily, userId = "pre-auth") {
      this.themeFamily = themeFamily;
      localStorage.setItem(`zhice.ui.${userId}.themeFamily`, themeFamily);
      this.applyTheme();
    },
    toggleTheme(userId = "pre-auth") {
      this.setColorMode(this.resolvedTheme === "dark" ? "light" : "dark", userId);
    },
    setLanguage(language: UiLanguage, userId = "pre-auth") {
      this.language = language;
      localStorage.setItem(`zhice.ui.${userId}.language`, language);
      this.applyLanguage();
    },
    toggleLanguage(userId = "pre-auth") {
      this.setLanguage(this.language === "zh-CN" ? "en" : "zh-CN", userId);
    },
    persist(userId: string) {
      const prefix = `zhice.ui.${userId}.`;
      localStorage.setItem(prefix + "density", this.density);
      localStorage.setItem(prefix + "startPage", this.startPage);
      document.documentElement.dataset.density = this.density;
    },
    applyTheme() {
      document.documentElement.dataset.theme = this.resolvedTheme;
      document.documentElement.dataset.themeFamily = this.themeFamily;
      document.documentElement.style.colorScheme = this.resolvedTheme;
    },
    applyLanguage() {
      document.documentElement.lang = this.language;
    },
    openSettings(section = "general") {
      this.settingsSection = section;
      this.settingsOpen = true;
      this.accountMenuOpen = false;
    },
  },
});
