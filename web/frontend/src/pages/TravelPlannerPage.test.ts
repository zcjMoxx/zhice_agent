import { createPinia, setActivePinia } from "pinia";
import { flushPromises, mount } from "@vue/test-utils";
import { createMemoryHistory, createRouter } from "vue-router";
import { afterEach, describe, expect, it, vi } from "vitest";

import { api } from "@/api/client";
import { useAuthStore } from "@/stores/auth";
import { useTravelStore } from "@/stores/travel";
import TravelPlannerPage from "./TravelPlannerPage.vue";

describe("TravelPlannerPage generation continuity", () => {
  afterEach(() => vi.restoreAllMocks());

  it("keeps an in-progress workspace when the page is entered again", async () => {
    const { wrapper, router, pinia, travel } = await mountPage();
    travel.sessionId = "travel-running";
    travel.generating = true;
    travel.statusText = "正在查询路线";
    travel.progressItems = [{ id: "route", stage: "data", title: "路线查询", detail: "正在查询路线", status: "running" }];
    const startNew = vi.spyOn(travel, "startNew");

    wrapper.unmount();
    const remounted = mount(TravelPlannerPage, { global: pageGlobals(pinia, router) });
    await flushPromises();

    expect(startNew).not.toHaveBeenCalled();
    expect(travel.sessionId).toBe("travel-running");
    expect(travel.generating).toBe(true);
    expect(travel.statusText).toBe("正在查询路线");
    remounted.unmount();
    travel.stopRecoveryPolling();
  });

  it("opens an explicitly selected saved plan instead of the active workspace", async () => {
    const { wrapper, router, pinia, travel } = await mountPage();
    const open = vi.spyOn(travel, "open").mockResolvedValue();

    wrapper.unmount();
    await router.replace("/travel?plan=plan-selected");
    await router.isReady();
    const remounted = mount(TravelPlannerPage, { global: pageGlobals(pinia, router) });
    await flushPromises();

    expect(open).toHaveBeenCalledWith("plan-selected");
    remounted.unmount();
  });

  it("keeps new-plan available during background generation and intake thinking", async () => {
    const { wrapper, travel } = await mountPage();
    travel.generating = true;
    await wrapper.vm.$nextTick();

    expect(wrapper.get(".travel-new-button").attributes("disabled")).toBeUndefined();
    expect(wrapper.get(".travel-new-button").attributes("title")).toContain("后台生成");

    travel.intakeBusy = true;
    await wrapper.vm.$nextTick();
    expect(wrapper.get(".travel-new-button").attributes("disabled")).toBeUndefined();
    expect(wrapper.get(".travel-new-button").attributes("title")).toContain("原计划");
    wrapper.unmount();
  });
});

async function mountPage() {
  const pinia = createPinia();
  setActivePinia(pinia);
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: "/", component: { template: "<div />" } },
      { path: "/travel", component: TravelPlannerPage },
    ],
  });
  await router.push("/travel");
  await router.isReady();
  const auth = useAuthStore();
  auth.user = { id: "user-a", username: "user-a", display_name: "User A", status: "active", roles: ["viewer"], can_manage_admins: false };
  auth.initialized = true;
  const travel = useTravelStore();
  travel.initializedUserId = "user-a";
  travel.restoreCompleted = true;
  vi.spyOn(api, "travelPlans").mockResolvedValue({ plans: [] });
  const wrapper = mount(TravelPlannerPage, { global: pageGlobals(pinia, router) });
  await flushPromises();
  return { wrapper, router, pinia, travel };
}

function pageGlobals(pinia: ReturnType<typeof createPinia>, router: ReturnType<typeof createRouter>) {
  return {
    plugins: [pinia, router],
    stubs: {
      QuickPreferences: { template: "<div />" },
      TravelPlanForm: { template: "<div />" },
      TravelProgress: { template: "<div />" },
      TravelBudget: { template: "<div />" },
      TravelMap: { template: "<div />" },
      TravelSourcesDrawer: { template: "<div />" },
      TravelTimeline: { template: "<div />" },
    },
  };
}
