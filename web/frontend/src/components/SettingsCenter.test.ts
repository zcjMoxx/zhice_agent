import { createPinia, setActivePinia } from "pinia";
import { mount } from "@vue/test-utils";
import { describe, expect, it } from "vitest";

import { useAuthStore } from "@/stores/auth";
import { useUiStore } from "@/stores/ui";
import SettingsCenter from "./SettingsCenter.vue";

describe("SettingsCenter", () => {
  it("persists identity-scoped color mode and theme family independently", async () => {
    const pinia = createPinia();
    setActivePinia(pinia);
    const auth = useAuthStore();
    auth.user = { id: "u-theme", username: "alice", display_name: "Alice", status: "active", roles: ["viewer"], can_manage_admins: false };
    const ui = useUiStore();
    ui.settingsSection = "general";
    const wrapper = mount(SettingsCenter, { global: { plugins: [pinia] } });

    const navigation = wrapper.findAll(".settings-nav > button");
    expect(navigation.map((item) => item.text())).toEqual(["常规", "个性化", "个人资料", "账号与安全", "渠道连接"]);
    expect(wrapper.findAll(".setting-row")[2].findAll("option").map((item) => item.text())).toEqual(["聊天", "新会话"]);
    await navigation[1].trigger("click");
    expect(wrapper.text()).not.toContain("聊天内容宽度");
    expect(wrapper.findAll(".appearance-mode-grid button")).toHaveLength(3);
    expect(wrapper.findAll(".theme-grid button")).toHaveLength(6);
    await wrapper.findAll(".appearance-mode-grid button")[2].trigger("click");
    await wrapper.findAll(".theme-grid button")[3].trigger("click");

    expect(ui.colorMode).toBe("dark");
    expect(ui.themeFamily).toBe("sage");
    expect(document.documentElement.dataset.theme).toBe("dark");
    expect(document.documentElement.dataset.themeFamily).toBe("sage");
    expect(localStorage.getItem("zhice.ui.u-theme.colorMode")).toBe("dark");
    expect(localStorage.getItem("zhice.ui.u-theme.themeFamily")).toBe("sage");
  });

  it("switches to English and persists the identity-scoped language", async () => {
    const pinia = createPinia();
    setActivePinia(pinia);
    const auth = useAuthStore();
    auth.user = { id: "u-language", username: "alice", display_name: "Alice", status: "active", roles: ["viewer"], can_manage_admins: false };
    const ui = useUiStore();
    ui.settingsSection = "general";
    const wrapper = mount(SettingsCenter, { global: { plugins: [pinia] } });

    await wrapper.get('.setting-row select').setValue("en");

    expect(ui.language).toBe("en");
    expect(document.documentElement.lang).toBe("en");
    expect(localStorage.getItem("zhice.ui.u-language.language")).toBe("en");
    expect(wrapper.text()).toContain("Personalization");
  });

  it("migrates the removed last-Session preference to chat", () => {
    localStorage.setItem("zhice.ui.u-start.language", "zh-CN");
    localStorage.setItem("zhice.ui.u-start.startPage", "last");
    const pinia = createPinia();
    setActivePinia(pinia);
    const ui = useUiStore();

    ui.load("u-start");

    expect(ui.startPage).toBe("chat");
  });

  it("migrates the legacy theme key while keeping Obsidian as the default family", () => {
    localStorage.setItem("zhice.ui.u-legacy.theme", "dark");
    const pinia = createPinia();
    setActivePinia(pinia);
    const ui = useUiStore();

    ui.load("u-legacy");

    expect(ui.colorMode).toBe("dark");
    expect(ui.themeFamily).toBe("obsidian");
    expect(localStorage.getItem("zhice.ui.u-legacy.colorMode")).toBe("dark");
    expect(localStorage.getItem("zhice.ui.u-legacy.theme")).toBeNull();
  });
});
