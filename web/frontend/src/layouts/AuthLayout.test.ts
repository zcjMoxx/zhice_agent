import { createPinia, setActivePinia } from "pinia";
import { flushPromises, mount } from "@vue/test-utils";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { useAuthStore } from "@/stores/auth";
import { useUiStore } from "@/stores/ui";
import AuthLayout from "./AuthLayout.vue";

describe("AuthLayout", () => {
  const publicRecord = {
    code: "00000000000000",
    label: "测试公安备案00000000000000号",
    url: "https://beian.mps.gov.cn/#/query/webSearch?code=00000000000000",
  };

  beforeEach(() => {
    vi.stubGlobal("fetch", vi.fn().mockImplementation((input: RequestInfo | URL) => {
      const url = String(input);
      const payload = url.endsWith("/api/site")
        ? { public_security_record: publicRecord }
        : url.includes("/api/auth/username-availability")
          ? { available: true }
          : { registration_enabled: true };
      return Promise.resolve(new Response(JSON.stringify(payload), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }));
    }));
  });

  function mountLayout(options: { setup?: boolean; language?: "zh-CN" | "en" } = {}) {
    const pinia = createPinia();
    setActivePinia(pinia);
    useAuthStore().registrationEnabled = true;
    useAuthStore().publicSecurityRecord = publicRecord;
    useUiStore().language = options.language ?? "zh-CN";
    return mount(AuthLayout, {
      props: { setup: options.setup ?? false },
      global: { plugins: [pinia] },
    });
  }

  it("switches between login and registration in the sliding surface", async () => {
    const wrapper = mountLayout();
    expect(wrapper.text()).toContain("欢迎回来");
    await wrapper.get(".ghost-inverse").trigger("click");
    expect(wrapper.get(".auth-slider").classes()).toContain("is-register");
    expect(wrapper.text()).toContain("创建账号");
    expect(wrapper.find('input[autocomplete="new-password"]').exists()).toBe(true);
    expect(wrapper.get('input[autocomplete="username"]').attributes("placeholder")).toBeUndefined();
  });

  it("shows registration validation against the backend credential rules", async () => {
    const wrapper = mountLayout();
    await wrapper.get(".ghost-inverse").trigger("click");
    const username = wrapper.get('input[autocomplete="username"]');
    const passwords = wrapper.findAll('input[autocomplete="new-password"]');

    await username.setValue("张三");
    expect(username.classes()).toContain("is-invalid");
    expect(wrapper.text()).toContain("仅支持字母、数字、点、下划线和连字符");
    await username.setValue("zhangsan");
    expect(wrapper.text()).toContain("检查中");
    await new Promise((resolve) => setTimeout(resolve, 320));
    await flushPromises();
    expect(username.classes()).toContain("is-valid");

    await passwords[0].setValue("short");
    expect(passwords[0].classes()).toContain("is-invalid");
    await passwords[0].setValue("password-123");
    await passwords[1].setValue("password-456");
    expect(wrapper.text()).toContain("与新密码不一致");
    await passwords[1].setValue("password-123");
    expect(wrapper.text()).toContain("两次密码一致");
    expect(wrapper.get(".auth-submit").attributes("disabled")).toBeUndefined();
  });

  it("checks an occupied username before submit and does not expose internal codes", async () => {
    vi.stubGlobal("fetch", vi.fn().mockImplementation((input: RequestInfo | URL) => {
      const url = String(input);
      const payload = url.endsWith("/api/site")
        ? { public_security_record: publicRecord }
        : url.includes("/api/auth/username-availability")
          ? { available: false }
          : { registration_enabled: true };
      return Promise.resolve(new Response(JSON.stringify(payload), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }));
    }));
    const wrapper = mountLayout();
    await wrapper.get(".ghost-inverse").trigger("click");
    const username = wrapper.get('input[autocomplete="username"]');

    await username.setValue("owner");
    expect(wrapper.text()).toContain("检查中");
    await new Promise((resolve) => setTimeout(resolve, 320));
    await flushPromises();

    expect(wrapper.text()).toContain("已存在");
    expect(wrapper.text()).toContain("该账号已被使用，请换一个");
    expect(username.classes()).toContain("is-invalid");
    expect(wrapper.get(".auth-submit").attributes("disabled")).toBeDefined();
    expect(wrapper.text()).not.toContain("USER_USERNAME_ALREADY_EXISTS");
  });

  it("maps a registration race failure to friendly copy without its internal identifier", async () => {
    vi.stubGlobal("fetch", vi.fn().mockImplementation((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith("/api/site")) return Promise.resolve(new Response(JSON.stringify({ public_security_record: publicRecord }), { status: 200, headers: { "Content-Type": "application/json" } }));
      if (url.includes("/api/auth/username-availability")) return Promise.resolve(new Response(JSON.stringify({ available: true }), { status: 200, headers: { "Content-Type": "application/json" } }));
      if (url.endsWith("/api/auth/register")) return Promise.resolve(new Response(JSON.stringify({ error: { status: 409, code: "USER_USERNAME_ALREADY_EXISTS", message: "username already exists", request_id: "req-test", details: {} } }), { status: 409, headers: { "Content-Type": "application/json" } }));
      return Promise.resolve(new Response(JSON.stringify({ registration_enabled: true }), { status: 200, headers: { "Content-Type": "application/json" } }));
    }));
    const wrapper = mountLayout();
    await wrapper.get(".ghost-inverse").trigger("click");
    await wrapper.get('input[autocomplete="username"]').setValue("new-user");
    await new Promise((resolve) => setTimeout(resolve, 320));
    await flushPromises();
    const passwords = wrapper.findAll('input[autocomplete="new-password"]');
    await passwords[0].setValue("password-123");
    await passwords[1].setValue("password-123");
    await wrapper.get("form").trigger("submit");
    await flushPromises();

    expect(wrapper.get(".form-error").text()).toBe("该账号已存在，请换一个");
    expect(wrapper.text()).not.toContain("USER_USERNAME_ALREADY_EXISTS");
  });

  it("keeps the login eye button out of the tab order", async () => {
    const wrapper = mountLayout();
    const input = wrapper.get('input[autocomplete="current-password"]');
    const toggle = wrapper.get(".password-field button");

    expect(input.attributes("type")).toBe("password");
    expect(toggle.attributes("tabindex")).toBe("-1");
    await toggle.trigger("click");
    expect(input.attributes("type")).toBe("text");
  });

  it("toggles registration passwords independently", async () => {
    const wrapper = mountLayout();
    await wrapper.get(".ghost-inverse").trigger("click");
    const inputs = wrapper.findAll('input[autocomplete="new-password"]');
    const toggles = wrapper.findAll(".password-field button");

    expect(inputs).toHaveLength(2);
    expect(toggles).toHaveLength(2);
    expect(toggles.every((toggle) => toggle.attributes("tabindex") === "-1")).toBe(true);
    await toggles[0].trigger("click");
    expect(inputs.map((input) => input.attributes("type"))).toEqual(["text", "password"]);
    await toggles[1].trigger("click");
    expect(inputs.map((input) => input.attributes("type"))).toEqual(["text", "text"]);
  });

  it("toggles owner password and setup credential independently", async () => {
    const wrapper = mountLayout({ setup: true });
    const inputs = wrapper.findAll(".password-field input");
    const toggles = wrapper.findAll(".password-field button");

    expect(inputs).toHaveLength(2);
    expect(toggles).toHaveLength(2);
    expect(toggles.every((toggle) => toggle.attributes("tabindex") === "-1")).toBe(true);
    await toggles[1].trigger("click");
    expect(inputs.map((input) => input.attributes("type"))).toEqual(["password", "text"]);
  });

  it("uses the Pine Mist Dawn login palette name and product copy", () => {
    expect(mountLayout().text()).toContain("松雾晨光");
    expect(mountLayout().text()).toContain("让每一次对话，都离完成更近一步。");
    expect(mountLayout().text()).toContain("登录你的 ZhiCe-Agent 账号");
    expect(mountLayout({ language: "en" }).text()).toContain("Pine Mist Dawn");
  });

  it("shows the public security filing at the bottom of every authentication flow", () => {
    const wrapper = mountLayout();
    const link = wrapper.get(".public-security-record a");

    expect(wrapper.get(".auth-surface").find(".public-security-record").exists()).toBe(true);
    expect(link.text()).toBe(publicRecord.label);
    expect(link.attributes("href")).toBe(publicRecord.url);
    expect(link.attributes("target")).toBe("_blank");
    expect(link.attributes("rel")).toBe("noreferrer");
    expect(link.get("img").attributes("src")).toBe("/static/beian-icon.png");
  });

  it("hides the public security filing when runtime site configuration is absent", async () => {
    const wrapper = mountLayout();
    useAuthStore().publicSecurityRecord = null;
    await wrapper.vm.$nextTick();

    expect(wrapper.find(".public-security-record").exists()).toBe(false);
  });

  it("uses the compact dedicated copy for QQ binding authentication", () => {
    const pinia = createPinia();
    setActivePinia(pinia);
    useAuthStore().registrationEnabled = true;
    const wrapper = mount(AuthLayout, {
      props: { flow: "qq-binding" },
      global: { plugins: [pinia] },
    });

    expect(wrapper.get(".auth-slider").classes()).toContain("is-channel-binding");
    expect(wrapper.text()).toContain("登录并绑定 QQ");
    expect(wrapper.text()).toContain("完成后会自动绑定，无需再进入设置。");
    expect(wrapper.get(".auth-submit").text()).toBe("登录并继续");
    expect(wrapper.get(".mobile-mode-switch span").text()).toBe("没有账号？");
    expect(wrapper.get(".mobile-mode-switch strong").text()).toBe("立即创建");
  });

  it("fails closed and hides registration in default and QQ binding flows", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(
      JSON.stringify({ registration_enabled: false }),
      { status: 200, headers: { "Content-Type": "application/json" } },
    )));
    const pinia = createPinia();
    setActivePinia(pinia);
    const normal = mount(AuthLayout, { global: { plugins: [pinia] } });
    await flushPromises();

    expect(normal.find(".ghost-inverse").exists()).toBe(false);
    expect(normal.find(".mobile-mode-switch").exists()).toBe(false);
    expect(normal.text()).not.toContain("创建账号");

    const binding = mount(AuthLayout, {
      props: { flow: "qq-binding" },
      global: { plugins: [pinia] },
    });
    await flushPromises();
    expect(binding.find(".ghost-inverse").exists()).toBe(false);
    expect(binding.find(".mobile-mode-switch").exists()).toBe(false);
    expect(binding.text()).toContain("登录并绑定 QQ");
  });
});
