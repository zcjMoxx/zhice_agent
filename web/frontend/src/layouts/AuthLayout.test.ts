import { createPinia, setActivePinia } from "pinia";
import { mount } from "@vue/test-utils";
import { describe, expect, it } from "vitest";

import { useUiStore } from "@/stores/ui";
import AuthLayout from "./AuthLayout.vue";

describe("AuthLayout", () => {
  function mountLayout(options: { setup?: boolean; language?: "zh-CN" | "en" } = {}) {
    const pinia = createPinia();
    setActivePinia(pinia);
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
    expect(wrapper.text()).toContain("创建本地账号");
    expect(wrapper.find('input[autocomplete="new-password"]').exists()).toBe(true);
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
});
