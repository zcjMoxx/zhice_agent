import { createPinia, setActivePinia } from "pinia";
import { flushPromises, mount } from "@vue/test-utils";
import { nextTick } from "vue";
import { afterEach, describe, expect, it, vi } from "vitest";

import { api } from "@/api/client";
import { useAuthStore } from "@/stores/auth";
import { useChannelStore } from "@/stores/channels";
import { useUiStore } from "@/stores/ui";
import SettingsCenter from "./SettingsCenter.vue";

describe("SettingsCenter", () => {
  afterEach(() => vi.restoreAllMocks());

  it("persists identity-scoped color mode and theme family independently", async () => {
    const pinia = createPinia();
    setActivePinia(pinia);
    const auth = useAuthStore();
    auth.user = { id: "u-theme", username: "alice", display_name: "Alice", status: "active", roles: ["viewer"], can_manage_admins: false };
    const ui = useUiStore();
    ui.settingsSection = "general";
    const wrapper = mount(SettingsCenter, { global: { plugins: [pinia] } });

    const navigation = wrapper.findAll(".settings-nav > button");
    expect(navigation.map((item) => item.text())).toEqual(["常规", "个性化", "个人资料", "账号与安全", "连接与账号"]);
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

  it("shows bilingual QQ instructions and real Weixin QR terminal details", async () => {
    const pinia = createPinia();
    setActivePinia(pinia);
    const auth = useAuthStore();
    auth.user = { id: "u-channel", username: "alice", display_name: "Alice", status: "active", roles: ["viewer"], can_manage_admins: false };
    const ui = useUiStore();
    ui.settingsSection = "connections";
    ui.language = "zh-CN";
    const channels = useChannelStore();
    vi.spyOn(channels, "refresh").mockResolvedValue();
    channels.weixin = { status: "unbound", linked_at: "" };
    channels.weixinAttempt = {
      attempt_id: "wxbind-1",
      status: "waiting_scan",
      expires_at: "later",
      qr_data: "data:image/png;base64,c2FmZQ==",
      error_code: "",
    };

    const wrapper = mount(SettingsCenter, { global: { plugins: [pinia] } });

    expect(wrapper.text()).toContain("个人邮箱");
    expect(wrapper.text()).toContain("使用 SMTP 授权码连接");
    expect(wrapper.text()).not.toContain("Gmail");
    expect(wrapper.text()).not.toContain("Microsoft");
    expect(wrapper.text()).not.toContain("系统未启用");
    expect(wrapper.text()).toContain("群聊：先 @机器人，再发送生成的 /bind 命令。私聊：直接发送该命令。");
    expect(wrapper.get(".weixin-qr").attributes("src")).toBe("data:image/png;base64,c2FmZQ==");
    expect(wrapper.text()).toContain("等待微信扫码");

    ui.setLanguage("en", "u-channel");
    await nextTick();
    expect(wrapper.text()).toContain("Group chat: @mention the bot first, then send the generated /bind command. Direct chat: send the command directly.");
    expect(wrapper.text()).toContain("Waiting for Weixin scan");
  });

  it("keeps known mailbox setup simple and derives the QQ SMTP request", async () => {
    const pinia = createPinia();
    setActivePinia(pinia);
    const auth = useAuthStore();
    auth.user = { id: "u-email", username: "alice", display_name: "Alice", status: "active", roles: ["viewer"], can_manage_admins: false };
    const ui = useUiStore();
    ui.settingsSection = "connections";
    const channels = useChannelStore();
    vi.spyOn(channels, "refresh").mockResolvedValue();
    vi.spyOn(api, "workflowCapabilities").mockResolvedValue({ personal_email: { available: true, code: "SMTP_READY" } });
    vi.spyOn(api, "workflowEmailConnections").mockResolvedValue({ connections: [] });
    const createConnection = vi.spyOn(api, "createSmtpEmailConnection").mockResolvedValue({
      connection: { id: "email-1", provider: "smtp_personal", account_display: "849534549@qq.com", status: "active" },
    });

    const wrapper = mount(SettingsCenter, { global: { plugins: [pinia] } });
    await flushPromises();
    await wrapper.get(".email-provider-card button").trigger("click");

    expect(wrapper.text()).toContain("邮箱类型");
    expect(wrapper.text()).not.toContain("发件地址");
    expect(wrapper.text()).not.toContain("SMTP 服务器");
    expect(wrapper.find(".custom-smtp-settings").exists()).toBe(false);

    await wrapper.get(".mailbox-provider-select").setValue("other");
    expect(wrapper.text()).toContain("其他邮箱服务器设置");
    expect(wrapper.text()).toContain("发信服务器");
    await wrapper.get(".mailbox-provider-select").setValue("qq");
    expect(wrapper.find(".custom-smtp-settings").exists()).toBe(false);

    await wrapper.get('.smtp-connection-form input[type="email"]').setValue("849534549@qq.com");
    await wrapper.get('.smtp-connection-form input[type="password"]').setValue("smtp-app-password");
    await wrapper.get(".smtp-connection-form").trigger("submit");
    await flushPromises();

    expect(createConnection).toHaveBeenCalledWith({
      host: "smtp.qq.com",
      port: 465,
      security: "tls",
      username: "849534549@qq.com",
      app_password: "smtp-app-password",
    });
  });

  it("renders a full-width-ready retry action for a pending QQ web binding", () => {
    const pinia = createPinia();
    setActivePinia(pinia);
    const auth = useAuthStore();
    auth.user = { id: "u-bind", username: "alice", display_name: "Alice", status: "active", roles: ["viewer"], can_manage_admins: false };
    const ui = useUiStore();
    ui.settingsSection = "connections";
    const channels = useChannelStore();
    vi.spyOn(channels, "refresh").mockResolvedValue();
    channels.pendingQqToken = "bind-retry";

    const wrapper = mount(SettingsCenter, { global: { plugins: [pinia] } });

    const action = wrapper.get(".channel-bind-action");
    expect(action.text()).toBe("完成绑定");
    expect((wrapper.get(".inline-bind input").element as HTMLInputElement).value).toBe("bind-retry");
  });
});
