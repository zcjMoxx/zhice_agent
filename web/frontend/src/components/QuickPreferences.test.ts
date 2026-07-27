import { createPinia, setActivePinia } from "pinia";
import { mount } from "@vue/test-utils";
import { beforeEach, describe, expect, it } from "vitest";

import { useAuthStore } from "@/stores/auth";
import { useUiStore } from "@/stores/ui";
import QuickPreferences from "./QuickPreferences.vue";

describe("QuickPreferences", () => {
  beforeEach(() => { localStorage.clear(); });

  it("switches language and theme from compact header controls", async () => {
    const pinia = createPinia();
    setActivePinia(pinia);
    const auth = useAuthStore();
    auth.user = { id: "u-quick", username: "alice", display_name: "Alice", status: "active", roles: ["viewer"], can_manage_admins: false };
    const ui = useUiStore();
    ui.language = "zh-CN";
    ui.colorMode = "light";
    ui.themeFamily = "aurora";
    ui.applyTheme();
    const wrapper = mount(QuickPreferences, { global: { plugins: [pinia] } });

    const buttons = wrapper.findAll(".quick-preference");
    await buttons[0].trigger("click");
    await buttons[1].trigger("click");

    expect(ui.language).toBe("en");
    expect(ui.colorMode).toBe("dark");
    expect(ui.themeFamily).toBe("aurora");
    expect(localStorage.getItem("zhice.ui.u-quick.language")).toBe("en");
    expect(localStorage.getItem("zhice.ui.u-quick.colorMode")).toBe("dark");
    expect(localStorage.getItem("zhice.ui.u-quick.themeFamily")).toBeNull();
  });
});
