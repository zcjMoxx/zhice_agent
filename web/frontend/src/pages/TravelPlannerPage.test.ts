import { createPinia, setActivePinia } from "pinia";
import { flushPromises, mount } from "@vue/test-utils";
import { createMemoryHistory, createRouter } from "vue-router";
import { afterEach, describe, expect, it, vi } from "vitest";

import { api } from "@/api/client";
import type { TravelPlan } from "@/api/types";
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

  it("keeps the plain travel route blank instead of auto-opening an unfinished task", async () => {
    const { wrapper, router, pinia, travel } = await mountPage();
    wrapper.unmount();
    vi.mocked(api.travelWorkItems).mockResolvedValue({
      items: [{
        session_id: "travel-old-failed",
        plan_id: "",
        status: "failed",
        title: "旧的未完成任务",
        preview: "",
        updated_at: "2026-08-17T00:00:00Z",
        error_code: "TRAVEL_PLAN_NOT_FINALIZED",
      }],
    });
    const openWorkItem = vi.spyOn(travel, "openWorkItem");
    const startNew = vi.spyOn(travel, "startNew");

    const remounted = mount(TravelPlannerPage, { global: pageGlobals(pinia, router) });
    await flushPromises();

    expect(openWorkItem).not.toHaveBeenCalled();
    expect(startNew).toHaveBeenCalled();
    expect(travel.sessionId).toBe("");
    expect(travel.conversation).toEqual([]);
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

  it("scrolls to finalization progress immediately after choosing a candidate", async () => {
    const scrollIntoView = vi.fn();
    Object.defineProperty(HTMLElement.prototype, "scrollIntoView", {
      configurable: true,
      value: scrollIntoView,
    });
    const { wrapper, travel } = await mountPage({ interactiveProgress: true });
    let finishSelection: (() => void) | undefined;
    vi.spyOn(travel, "chooseCandidate").mockImplementation(
      () => new Promise<void>((resolve) => { finishSelection = resolve; }),
    );

    await wrapper.get(".test-choose-candidate").trigger("click");
    await wrapper.vm.$nextTick();

    expect(scrollIntoView).toHaveBeenCalledWith({ behavior: "smooth", block: "start" });
    finishSelection?.();
    await flushPromises();
    wrapper.unmount();
  });

  it("shows the continue action for a failed session whose selected candidate was restored", async () => {
    vi.spyOn(api, "travelDraft").mockResolvedValue({
      session_id: "travel-failed",
      phase: "planning",
      draft: {},
      handoff_question: "",
      messages: [],
    });
    vi.spyOn(api, "travelProgress").mockResolvedValue({ session_id: "travel-failed", items: [] });
    vi.spyOn(api, "travelCandidateReview").mockResolvedValue({
      session_id: "travel-failed",
      status: "selected",
      recommended_candidate_id: "candidate-a",
      selected_candidate_id: "candidate-a",
      candidates: [],
      created_at: "",
      updated_at: "",
    });
    const { wrapper, travel } = await mountPage();

    await travel.openWorkItem({
      session_id: "travel-failed",
      plan_id: "",
      status: "failed",
      title: "大理五日游",
      preview: "重庆到大理",
      updated_at: "2026-08-16T00:00:00Z",
      error_code: "TRAVEL_PLAN_NOT_FINALIZED",
    });
    await flushPromises();

    expect(wrapper.get(".travel-error-recovery button").text()).toBe("继续完成当前方案");
    wrapper.unmount();
  });

  it("continues the failed step in the same plan instead of starting a blank plan", async () => {
    const { wrapper, travel } = await mountPage();
    travel.sessionId = "travel-failed";
    travel.phase = "planning";
    travel.conversation = [{ role: "user", content: "重庆到大理五日游" }];
    travel.progressItems = [{ id: "guides", stage: "data", title: "攻略查询失败", detail: "超时", status: "error" }];
    travel.error = "上次规划未生成完整计划，请继续未完成步骤。";
    const startNew = vi.spyOn(travel, "startNew");
    const resume = vi.spyOn(travel, "resumeFailedPlanning").mockResolvedValue();
    await wrapper.vm.$nextTick();

    const continueButton = wrapper.get(".travel-error-recovery button");
    expect(continueButton.text()).toContain("继续未完成步骤");
    expect(continueButton.attributes("title")).toContain("只补做失败或未完成的步骤");

    await continueButton.trigger("click");
    await wrapper.vm.$nextTick();

    expect(resume).toHaveBeenCalledOnce();
    expect(startNew).not.toHaveBeenCalled();
    expect(travel.sessionId).toBe("travel-failed");
    expect(travel.conversation).toEqual([{ role: "user", content: "重庆到大理五日游" }]);
    expect(travel.progressItems[0]?.id).toBe("guides");
    wrapper.unmount();
  });

  it("renders the real flat train and hotel fields instead of placeholder dashes", async () => {
    const { wrapper, travel } = await mountPage();
    travel.activePlan = evidenceRichPlan();
    await wrapper.vm.$nextTick();

    expect(wrapper.text()).toContain("G177 · 北京朝阳站 → 沈阳站");
    expect(wrapper.text()).toContain("08:50 → 11:19");
    expect(wrapper.text()).toContain("¥355/人");
    expect(wrapper.text()).toContain("沈阳中街故宫玫瑰亚朵酒店");
    expect(wrapper.text()).toContain("2026-08-20 入住");
    expect(wrapper.text()).toContain("规划估算 ¥750/晚（非实时房价）");
    expect(wrapper.text()).toContain("住宿信息来源：高德地图：酒店 POI");
    expect(wrapper.text()).toContain("价格来源：规划估算，无外部实时报价");
    expect(wrapper.text()).toContain("阵雨概率较高 · 24.4–32℃ · 最高降水概率 98%");
    expect(wrapper.text()).toContain("实时查询 · Open-Meteo 天气");
    wrapper.unmount();
  });

  it("distinguishes an available Xiaohongshu search snapshot from no community result", async () => {
    const { wrapper, travel } = await mountPage();
    const plan = evidenceRichPlan();
    plan.unknowns = ["小红书仅完成搜索级快照，未逐篇抽取正文体验。"];
    travel.activePlan = plan;
    await wrapper.vm.$nextTick();

    expect(wrapper.text()).toContain("社区经验已有搜索摘要，原文仍需复核");
    expect(wrapper.text()).not.toContain("社区经验暂未补充");
    wrapper.unmount();
  });
});

async function mountPage(options: { interactiveProgress?: boolean } = {}) {
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
  vi.spyOn(api, "travelWorkItems").mockResolvedValue({ items: [] });
  const wrapper = mount(TravelPlannerPage, { global: pageGlobals(pinia, router, options) });
  await flushPromises();
  return { wrapper, router, pinia, travel };
}

function pageGlobals(
  pinia: ReturnType<typeof createPinia>,
  router: ReturnType<typeof createRouter>,
  options: { interactiveProgress?: boolean } = {},
) {
  return {
    plugins: [pinia, router],
    stubs: {
      QuickPreferences: { template: "<div />" },
      TravelPlanForm: { template: "<div />" },
      TravelProgress: options.interactiveProgress
        ? { emits: ["chooseCandidate"], template: '<button class="test-choose-candidate" @click="$emit(\'chooseCandidate\', \'candidate-a\')">选择</button>' }
        : { template: "<div />" },
      TravelBudget: { template: "<div />" },
      TravelMap: { template: "<div />" },
      TravelSourcesDrawer: { template: "<div />" },
      TravelTimeline: { template: "<div />" },
    },
  };
}

function evidenceRichPlan(): TravelPlan {
  return {
    schema_version: "1",
    plan_id: "plan-real-shape",
    owner_user_id: "user-a",
    request: {
      origin: "北京",
      destinations: ["沈阳"],
      start_date: "2026-08-20",
      end_date: "2026-08-22",
      duration_days: 3,
      travellers: [{ type: "成人", count: 2 }],
      budget_total_cny: 5000,
      planning_mode: "quick",
    },
    assumptions: [],
    freshness_summary: {},
    transport_options: [{
      name: "去程高铁",
      mode: "高铁",
      service_name: "G177",
      from: "北京朝阳站",
      to: "沈阳站",
      departure: "2026-08-20T08:50:00+08:00",
      arrival: "2026-08-20T11:19:00+08:00",
      duration_minutes: 149,
      seat: "二等座",
      price_cny_per_person: 355,
      price_cny_total: 710,
      source: "12306",
      evidence_ids: ["train"],
    }],
    stay_recommendations: [{
      hotel_name: "沈阳中街故宫玫瑰亚朵酒店",
      address: "中街路201号",
      area: "中街/故宫片区",
      location: { longitude: 123.46, latitude: 41.80 },
      check_in: "2026-08-20",
      check_out: "2026-08-22",
      nights: 2,
      observed_price_per_night_cny: null,
      planning_estimate_per_night_cny: 750,
      price_status: "planning_estimate",
      evidence_ids: ["hotel"],
      price_source_evidence_ids: [],
      reason: "步行可达核心景点",
    }],
    days: [],
    budget: { lower: 3500, expected: 4200, upper: 4900, items: [] },
    weather_summary: [{
      date: "2026-08-20",
      overview: "阵雨概率较高",
      temp_min_c: 24.4,
      temp_max_c: 32,
      precipitation_probability_max_pct: 98,
      provider: "Open-Meteo",
      freshness: "live",
    }],
    fallbacks: [],
    avoidance_tips: [],
    evidence: [{
      evidence_id: "hotel",
      source_type: "official_api",
      provider: "高德地图",
      title: "酒店 POI",
      source_url: "https://ditu.amap.com/search?query=hotel",
      published_at: "",
      retrieved_at: "2026-08-15T00:00:00Z",
      data_as_of: "",
      excerpt: "",
      facts: [],
      confidence: 0.6,
      freshness: "live",
      content_hash: "",
    }],
    unknowns: [],
    generated_at: "2026-08-15T00:00:00Z",
  };
}
