import { createPinia, setActivePinia } from "pinia";
import { createMemoryHistory, createRouter } from "vue-router";
import { mount } from "@vue/test-utils";
import { describe, expect, it } from "vitest";

import { useAuthStore } from "@/stores/auth";
import { useSessionStore } from "@/stores/sessions";
import SessionSidebar from "./SessionSidebar.vue";

describe("SessionSidebar", () => {
  it("opens the three-dot menu and closes it with Escape", async () => {
    const pinia = createPinia();
    setActivePinia(pinia);
    const auth = useAuthStore();
    auth.user = { id: "u1", username: "owner", display_name: "系统所有者", status: "active", roles: ["owner"], can_manage_admins: true };
    const sessions = useSessionStore();
    sessions.items = [{ session_id: "s1", title: "设计讨论", preview: "", updated_at: "", message_count: 99, channel: "web", conversation_type: "private", continuation_mode: "writable" }];
    const router = createRouter({ history: createMemoryHistory(), routes: [{ path: "/", component: { template: "<div />" } }] });
    const wrapper = mount(SessionSidebar, { global: { plugins: [pinia, router] }, attachTo: document.body });
    expect(wrapper.text()).not.toContain("99");
    await wrapper.get(".ellipsis-button").trigger("click");
    expect(wrapper.text()).toContain("重命名");
    document.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape" }));
    await wrapper.vm.$nextTick();
    expect(wrapper.find(".session-menu").exists()).toBe(false);
    wrapper.unmount();
  });
});
