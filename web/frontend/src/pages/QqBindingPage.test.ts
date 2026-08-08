import { createPinia, setActivePinia } from "pinia";
import { flushPromises, mount } from "@vue/test-utils";
import { nextTick } from "vue";
import { createMemoryHistory, createRouter } from "vue-router";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { api } from "@/api/client";
import { useAuthStore } from "@/stores/auth";
import { useChannelStore } from "@/stores/channels";
import QqBindingPage from "./QqBindingPage.vue";

const user = {
  id: "u-new",
  username: "new-user",
  display_name: "New User",
  status: "active",
  roles: ["viewer"],
  can_manage_admins: false,
};

describe("QqBindingPage", () => {
  beforeEach(() => {
    vi.useRealTimers();
    vi.spyOn(api, "bindings").mockResolvedValue({ bindings: [] });
    vi.spyOn(api, "weixinStatus").mockResolvedValue({ status: "unbound", linked_at: "" });
  });

  async function mountPage(path: string) {
    const pinia = createPinia();
    setActivePinia(pinia);
    const router = createRouter({
      history: createMemoryHistory(),
      routes: [
        { path: "/", name: "home", component: { template: "<div />" } },
        { path: "/bind/qq", name: "qq-binding", component: QqBindingPage },
      ],
    });
    await router.push(path);
    await router.isReady();
    const wrapper = mount(QqBindingPage, {
      global: {
        plugins: [pinia, router],
        stubs: {
          AuthLayout: { template: "<div class='auth-layout-stub' />" },
          QuickPreferences: { template: "<div />" },
        },
      },
    });
    return { wrapper, router, auth: useAuthStore(), channels: useChannelStore() };
  }

  it("automatically consumes the original token after a new account becomes authenticated", async () => {
    const authorize = vi.spyOn(api, "qqAuthorize").mockResolvedValue({ status: "bound", channel: "qq" });
    const { wrapper, router, auth, channels } = await mountPage("/bind/qq?token=bind-new");

    expect(wrapper.find(".auth-layout-stub").exists()).toBe(true);
    expect(authorize).not.toHaveBeenCalled();
    auth.user = user;
    await nextTick();
    await flushPromises();

    expect(authorize).toHaveBeenCalledTimes(1);
    expect(authorize).toHaveBeenCalledWith("bind-new");
    expect(channels.pendingQqToken).toBe("");
    expect(router.currentRoute.value.query.token).toBeUndefined();
    expect(wrapper.text()).toContain("QQ 绑定成功");
    expect(wrapper.text()).toContain("返回 QQ 继续和机器人聊天");
    expect(wrapper.get(".binding-primary-action").text()).toBe("关闭并返回 QQ");
    expect(wrapper.get(".binding-secondary-action").text()).toBe("进入 ZhiCe-Agent");
  });

  it("tries to close the QQ webview and shows a manual-close fallback", async () => {
    vi.useFakeTimers();
    const close = vi.spyOn(window, "close").mockImplementation(() => undefined);
    vi.spyOn(api, "qqAuthorize").mockResolvedValue({ status: "bound", channel: "qq" });
    const { wrapper, auth } = await mountPage("/bind/qq?token=bind-close");
    auth.user = user;
    await nextTick();
    await flushPromises();

    await wrapper.get(".binding-primary-action").trigger("click");
    expect(close).toHaveBeenCalledTimes(1);
    await vi.advanceTimersByTimeAsync(500);
    expect(wrapper.text()).toContain("请点击右上角关闭并返回 QQ");
  });

  it("keeps a failed token on the dedicated page and offers a large retry action", async () => {
    vi.spyOn(api, "qqAuthorize").mockRejectedValue(new Error("binding unavailable"));
    const { wrapper, router, auth, channels } = await mountPage("/bind/qq?token=bind-retry");

    auth.user = user;
    await nextTick();
    await flushPromises();

    expect(channels.pendingQqToken).toBe("bind-retry");
    expect(channels.qqAuthorizationError).toBe("binding unavailable");
    expect(router.currentRoute.value.query.token).toBe("bind-retry");
    expect(wrapper.text()).toContain("QQ 绑定未完成");
    expect(wrapper.get(".binding-primary-action").text()).toBe("重新绑定");
  });

  it("shows an explicit recovery path when the token is missing", async () => {
    const { wrapper } = await mountPage("/bind/qq");

    expect(wrapper.text()).toContain("绑定链接无效");
    expect(wrapper.text()).toContain("重新发送 /bind 获取新链接");
  });
});
