import { createPinia, setActivePinia } from "pinia";
import { createMemoryHistory, createRouter } from "vue-router";
import { flushPromises, mount } from "@vue/test-utils";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ApiError, api } from "@/api/client";
import { useAuthStore } from "@/stores/auth";
import { useSessionStore } from "@/stores/sessions";
import SessionSidebar from "./SessionSidebar.vue";

describe("SessionSidebar", () => {
  afterEach(() => { document.body.innerHTML = ""; vi.restoreAllMocks(); });

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
    expect(wrapper.get(".account-area").text()).toContain("系统所有者");
    await wrapper.get(".ellipsis-button").trigger("click");
    expect(wrapper.text()).toContain("重命名");
    document.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape" }));
    await wrapper.vm.$nextTick();
    expect(wrapper.find(".session-menu").exists()).toBe(false);
    wrapper.unmount();
  });

  it("closes the account menu after clicking outside", async () => {
    const pinia = createPinia();
    setActivePinia(pinia);
    const auth = useAuthStore();
    auth.user = { id: "u1", username: "owner", display_name: "系统所有者", status: "active", roles: ["owner"], can_manage_admins: true };
    const router = createRouter({ history: createMemoryHistory(), routes: [{ path: "/", component: { template: "<div />" } }] });
    const wrapper = mount(SessionSidebar, { global: { plugins: [pinia, router] }, attachTo: document.body });

    await wrapper.get(".account-trigger").trigger("click");
    expect(wrapper.find(".account-menu").exists()).toBe(true);
    document.body.dispatchEvent(new Event("pointerdown", { bubbles: true }));
    await wrapper.vm.$nextTick();
    expect(wrapper.find(".account-menu").exists()).toBe(false);
    wrapper.unmount();
  });

  it("normalizes legacy CLI and marks only QQ groups as read-only", () => {
    const pinia = createPinia();
    setActivePinia(pinia);
    const auth = useAuthStore();
    auth.user = { id: "u1", username: "owner", display_name: "系统所有者", status: "active", roles: ["owner"], can_manage_admins: true };
    const sessions = useSessionStore();
    sessions.items = [
      { session_id: "cli", title: "CLI", preview: "", updated_at: "", message_count: 1, channel: "cli_legacy", conversation_type: "private", continuation_mode: "writable" },
      { session_id: "qq-group", title: "群聊", preview: "", updated_at: "", message_count: 1, channel: "qq", conversation_type: "group", continuation_mode: "fork_only" },
      { session_id: "qq-private", title: "私聊", preview: "", updated_at: "", message_count: 1, channel: "qq", conversation_type: "private", continuation_mode: "writable" },
      { session_id: "weixin", title: "微信", preview: "", updated_at: "", message_count: 1, channel: "weixin", conversation_type: "private", continuation_mode: "writable" },
    ];
    const router = createRouter({ history: createMemoryHistory(), routes: [{ path: "/", component: { template: "<div />" } }] });
    const wrapper = mount(SessionSidebar, { global: { plugins: [pinia, router] } });

    expect(wrapper.text()).toContain("CLI");
    expect(wrapper.text()).not.toContain("LEGACY");
    expect(wrapper.text()).toContain("QQ群 · 只读来源");
    expect(wrapper.text()).toContain("QQ · 可继续");
    expect(wrapper.text()).toContain("微信 · 可继续");
  });

  it("opens a local draft without creating an empty Session", async () => {
    const pinia = createPinia();
    setActivePinia(pinia);
    const auth = useAuthStore();
    auth.user = { id: "u1", username: "owner", display_name: "系统所有者", status: "active", roles: ["owner"], can_manage_admins: true };
    const sessions = useSessionStore();
    sessions.items = [{ session_id: "existing", title: "已有会话", preview: "", updated_at: "", message_count: 2, channel: "web", conversation_type: "private", continuation_mode: "writable" }];
    sessions.activeId = "existing";
    sessions.messages = [{ role: "user", content: "已有内容" }];
    const router = createRouter({ history: createMemoryHistory(), routes: [{ path: "/", component: { template: "<div />" } }] });
    const wrapper = mount(SessionSidebar, { global: { plugins: [pinia, router] } });

    await wrapper.get(".new-chat").trigger("click");

    expect(sessions.activeId).toBe("");
    expect(sessions.messages).toEqual([]);
    expect(sessions.items.map((item) => item.session_id)).toEqual(["existing"]);
  });

  it("shows a visible error and re-enables delete when deletion fails", async () => {
    const pinia = createPinia();
    setActivePinia(pinia);
    const auth = useAuthStore();
    auth.user = { id: "u1", username: "owner", display_name: "系统所有者", status: "active", roles: ["owner"], can_manage_admins: true };
    const sessions = useSessionStore();
    sessions.items = [{ session_id: "weixin-1", title: "微信会话", preview: "", updated_at: "", message_count: 2, channel: "weixin", conversation_type: "private", continuation_mode: "writable" }];
    vi.spyOn(api, "deleteSession").mockRejectedValue(new ApiError(503, "SESSION_DELETE_FAILED", "删除暂时失败"));
    const router = createRouter({ history: createMemoryHistory(), routes: [{ path: "/", component: { template: "<div />" } }] });
    const wrapper = mount(SessionSidebar, { global: { plugins: [pinia, router] } });

    await wrapper.get(".ellipsis-button").trigger("click");
    await wrapper.get(".session-menu .danger-text").trigger("click");
    await wrapper.get(".danger-button").trigger("click");
    expect(wrapper.get(".danger-button").attributes("disabled")).toBeDefined();
    await flushPromises();

    expect(wrapper.get('[role="alert"]').text()).toContain("删除暂时失败");
    expect(wrapper.get(".danger-button").attributes("disabled")).toBeUndefined();
    expect(wrapper.text()).toContain("微信会话");
  });

});
