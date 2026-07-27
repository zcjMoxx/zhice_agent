import { createPinia, setActivePinia } from "pinia";
import { mount } from "@vue/test-utils";
import { describe, expect, it } from "vitest";

import { useAuthStore } from "@/stores/auth";
import { useUiStore } from "@/stores/ui";
import SettingsCenter from "./SettingsCenter.vue";

describe("SettingsCenter", () => {
  it("navigates five sections and persists an identity-scoped theme", async () => {
    const pinia = createPinia();
    setActivePinia(pinia);
    const auth = useAuthStore();
    auth.user = { id: "u-theme", username: "alice", display_name: "Alice", status: "active", roles: ["viewer"], can_manage_admins: false };
    const ui = useUiStore();
    ui.settingsSection = "general";
    const wrapper = mount(SettingsCenter, { global: { plugins: [pinia] } });

    const navigation = wrapper.findAll(".settings-nav > button");
    expect(navigation.map((item) => item.text())).toEqual(["常规", "个性化", "个人资料", "账号与安全", "渠道连接"]);
    await navigation[1].trigger("click");
    await wrapper.findAll(".theme-grid button")[2].trigger("click");

    expect(ui.theme).toBe("dark");
    expect(document.documentElement.dataset.theme).toBe("dark");
    expect(localStorage.getItem("zhice.ui.u-theme.theme")).toBe("dark");
  });
});
