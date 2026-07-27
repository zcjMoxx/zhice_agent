import { defineStore } from "pinia";

export type ThemePreference = "system" | "light" | "dark";

function mediaDark(): boolean { return window.matchMedia?.("(prefers-color-scheme: dark)").matches ?? false; }
let systemThemeListenerInstalled = false;

export const useUiStore = defineStore("ui", {
  state: () => ({
    theme: "system" as ThemePreference,
    sidebarCollapsed: false,
    settingsOpen: false,
    settingsSection: "general",
    accountMenuOpen: false,
    density: "comfortable",
    contentWidth: "standard",
    startPage: "chat",
  }),
  getters: {
    resolvedTheme: (state) => state.theme === "system" ? (mediaDark() ? "dark" : "light") : state.theme,
  },
  actions: {
    load(userId = "pre-auth") {
      const prefix = `zhice.ui.${userId}.`;
      this.theme = (localStorage.getItem(prefix + "theme") as ThemePreference) || "system";
      this.density = localStorage.getItem(prefix + "density") || "comfortable";
      this.contentWidth = localStorage.getItem(prefix + "contentWidth") || "standard";
      this.startPage = localStorage.getItem(prefix + "startPage") || "chat";
      this.applyTheme();
      if (!systemThemeListenerInstalled && window.matchMedia) {
        window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => {
          if (this.theme === "system") this.applyTheme();
        });
        systemThemeListenerInstalled = true;
      }
    },
    setTheme(theme: ThemePreference, userId = "pre-auth") {
      this.theme = theme;
      localStorage.setItem(`zhice.ui.${userId}.theme`, theme);
      this.applyTheme();
    },
    persist(userId: string) {
      const prefix = `zhice.ui.${userId}.`;
      localStorage.setItem(prefix + "density", this.density);
      localStorage.setItem(prefix + "contentWidth", this.contentWidth);
      localStorage.setItem(prefix + "startPage", this.startPage);
      document.documentElement.dataset.density = this.density;
    },
    applyTheme() {
      document.documentElement.dataset.theme = this.resolvedTheme;
      document.documentElement.style.colorScheme = this.resolvedTheme;
    },
    openSettings(section = "general") {
      this.settingsSection = section;
      this.settingsOpen = true;
      this.accountMenuOpen = false;
    },
  },
});
