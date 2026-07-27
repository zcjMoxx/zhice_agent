import { createPinia } from "pinia";
import { mount } from "@vue/test-utils";
import { describe, expect, it } from "vitest";

import AuthLayout from "./AuthLayout.vue";

describe("AuthLayout", () => {
  it("switches between login and registration in the sliding surface", async () => {
    const wrapper = mount(AuthLayout, { global: { plugins: [createPinia()] } });
    expect(wrapper.text()).toContain("欢迎回来");
    await wrapper.get(".ghost-inverse").trigger("click");
    expect(wrapper.get(".auth-slider").classes()).toContain("is-register");
    expect(wrapper.text()).toContain("创建本地账号");
    expect(wrapper.find('input[autocomplete="new-password"]').exists()).toBe(true);
  });
});
