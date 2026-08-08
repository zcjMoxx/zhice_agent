import { createPinia } from "pinia";
import { flushPromises, mount } from "@vue/test-utils";
import { createMemoryHistory, createRouter } from "vue-router";
import { describe, expect, it } from "vitest";

import HomePage from "./HomePage.vue";

describe("HomePage legacy QQ binding link", () => {
  it("redirects the old channel_bind query to the dedicated binding page", async () => {
    const pinia = createPinia();
    const router = createRouter({
      history: createMemoryHistory(),
      routes: [
        { path: "/", name: "home", component: HomePage },
        { path: "/bind/qq", name: "qq-binding", component: { template: "<div />" } },
      ],
    });
    await router.push("/?channel_bind=legacy-token&next=chat");
    await router.isReady();

    mount(HomePage, {
      global: {
        plugins: [pinia, router],
        stubs: {
          AppShell: { template: "<div />" },
          AuthLayout: { template: "<div />" },
        },
      },
    });
    await flushPromises();

    expect(router.currentRoute.value.name).toBe("qq-binding");
    expect(router.currentRoute.value.query.token).toBe("legacy-token");
    expect(router.currentRoute.value.query.next).toBe("chat");
    expect(router.currentRoute.value.query.channel_bind).toBeUndefined();
  });
});
