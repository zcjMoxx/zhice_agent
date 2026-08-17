import { mount } from "@vue/test-utils";
import { describe, expect, it } from "vitest";

import TravelProgress from "./TravelProgress.vue";

describe("TravelProgress", () => {
  it("renders a user-facing source query and bounded selected results", async () => {
    const wrapper = mount(TravelProgress, {
      props: {
        stage: "data",
        active: true,
        statusText: "返回 12 个结果，展示前 3 个候选",
        items: [{
          id: "amap-search",
          stage: "data",
          title: "高德地图查询完成",
          detail: "返回 12 个结果，展示前 3 个候选",
          status: "done",
          result: {
            provider: "amap_transit",
            query: "大理古城周边景点",
            summary: "从返回结果中展示与当前行程相关的候选",
            resultCount: 12,
            items: [
              { title: "崇圣寺三塔", detail: "大理镇三塔路" },
              { title: "洱海生态廊道", detail: "适合骑行" },
              { title: "大理古城", detail: "古城核心区" },
            ],
          },
        }],
      },
    });

    expect(wrapper.text()).toContain("高德地图查询完成");
    expect(wrapper.text()).toContain("高德地图");
    expect(wrapper.text()).not.toContain("amap_transit");
    expect(wrapper.text()).toContain("查询：大理古城周边景点");
    expect(wrapper.text()).toContain("12 条");
    expect(wrapper.text()).toContain("崇圣寺三塔");
    expect(wrapper.text()).toContain("洱海生态廊道");
    expect(wrapper.text()).not.toContain("mcp__");
    expect(wrapper.text()).not.toContain("load_skills");
  });

  it("renders feasible candidate cards and emits a real selection", async () => {
    const wrapper = mount(TravelProgress, {
      props: {
        stage: "solve",
        active: false,
        candidateReview: {
          session_id: "travel-a",
          status: "pending",
          recommended_candidate_id: "slow-city",
          selected_candidate_id: "",
          candidates: [{
            candidate_id: "slow-city",
            recommended: true,
            score: 102,
            days: [{ date: "2026-10-01", city_or_area: "郑州", places: ["河南博物院", "二七广场"] }],
            budget: { lower: 1600, expected: 2400, upper: 3200 },
            route_minutes: 180,
            route_distance_km: 60,
            daily_intensity_scores: [7.2],
            evidence_coverage: 0.8,
            warnings: [],
          }],
          created_at: "",
          updated_at: "",
        },
      },
    });

    expect(wrapper.text()).toContain("采用推荐方案");
    expect(wrapper.text()).toContain("河南博物院、二七广场");
    await wrapper.get(".travel-candidate-card button").trigger("click");
    expect(wrapper.emitted("chooseCandidate")?.[0]).toEqual(["slow-city"]);
  });

  it("keeps an already selected review as a compact expandable decision record", async () => {
    const wrapper = mount(TravelProgress, {
      props: {
        stage: "validate",
        active: false,
        candidateReview: {
          session_id: "travel-a",
          status: "selected",
          recommended_candidate_id: "xi'an-balanced-lite-b",
          selected_candidate_id: "xi'an-balanced-lite-b",
          candidates: [{
            candidate_id: "xi'an-balanced-lite-b",
            recommended: true,
            score: 90,
            days: [{ date: "2026-09-15", city_or_area: "西安城内", places: ["回民街", "西安城墙"] }],
            budget: { lower: 2490, expected: 3380, upper: 4480 },
            route_minutes: 230,
            route_distance_km: 60,
            daily_intensity_scores: [7.2],
            evidence_coverage: 0.67,
            warnings: ["BUDGET_EXPECTED_TIGHTENED_TO_HARD_LIMIT"],
            strategy_label: "经典覆盖",
            core_tradeoff: "重点保留西安城墙；减少远郊通勤",
          }, {
            candidate_id: "xi'an-slow",
            recommended: false,
            score: 85,
            days: [{ date: "2026-09-15", city_or_area: "西安城内", places: ["陕西历史博物馆"] }],
            budget: { lower: 2200, expected: 3000, upper: 3900 },
            route_minutes: 150,
            route_distance_km: 35,
            daily_intensity_scores: [5.8],
            evidence_coverage: 0.67,
            warnings: [],
            strategy_label: "舒适慢游",
            core_tradeoff: "减少景点数量，降低通勤和步行强度",
          }],
          created_at: "",
          updated_at: "",
        },
      },
    });

    expect(wrapper.text()).toContain("经典覆盖");
    expect(wrapper.text()).toContain("重点保留西安城墙");
    expect(wrapper.text()).not.toContain("xi'an-balanced-lite-b");
    expect(wrapper.text()).toContain("已按总预算压缩餐饮与市内交通余量");
    expect(wrapper.text()).not.toContain("选择这个方案");
    await wrapper.get(".travel-candidate-decision > button").trigger("click");
    expect(wrapper.findAll(".travel-candidate-grid.readonly .travel-candidate-card")).toHaveLength(2);
    expect(wrapper.text()).toContain("舒适慢游");
  });

  it("does not expose internal optimizer warning codes on candidate cards", () => {
    const wrapper = mount(TravelProgress, {
      props: {
        stage: "solve",
        active: false,
        candidateReview: {
          session_id: "travel-a",
          status: "pending",
          recommended_candidate_id: "slow-city",
          selected_candidate_id: "",
          candidates: [{
            candidate_id: "slow-city",
            recommended: true,
            score: 90,
            days: [{ date: "2026-10-01", city_or_area: "郑州", places: ["河南博物院"] }],
            budget: { lower: 1600, expected: 2400, upper: 3200 },
            route_minutes: 180,
            route_distance_km: 60,
            daily_intensity_scores: [7.2],
            evidence_coverage: 0.8,
            warnings: ["OPENING_HOURS_CONFLICT", "UNEXPECTED_INTERNAL_CODE"],
          }],
          created_at: "",
          updated_at: "",
        },
      },
    });

    expect(wrapper.text()).toContain("部分到访时间需复核营业安排");
    expect(wrapper.text()).toContain("部分安排仍需复核");
    expect(wrapper.text()).not.toContain("OPENING_HOURS_CONFLICT");
    expect(wrapper.text()).not.toContain("UNEXPECTED_INTERNAL_CODE");
  });

  it("marks every progress step done when a saved plan is complete", () => {
    const wrapper = mount(TravelProgress, {
      props: {
        stage: "complete",
        active: false,
        statusText: "旅行计划已完成",
      },
    });

    const steps = wrapper.findAll(".travel-progress-step");
    expect(steps).toHaveLength(6);
    expect(steps.every((step) => step.classes().includes("done"))).toBe(true);
    expect(steps.at(-1)?.classes()).toContain("active");
  });

  it("shows elapsed time and independent finalization lanes after selection", () => {
    const wrapper = mount(TravelProgress, {
      props: {
        stage: "validate",
        active: true,
        statusText: "正在完善所选方案",
        items: [
          { id: "finalizing-a", stage: "validate", title: "上一次完善所选方案", detail: "旧回合", status: "error", startedAt: Date.now() - 600_000 },
          { id: "retry-finalizing-a-1", stage: "validate", title: "正在继续完成旅行计划", detail: "后台仍在工作", status: "running", startedAt: Date.now() - 65_000 },
          { id: "hotel-a", stage: "validate", title: "携程酒店房价查询完成", detail: "找到 3 家", status: "done", lane: "lodging" },
          { id: "route-a", stage: "validate", title: "正在高德地图查询地点与路线", detail: "路线查询中", status: "running", lane: "transport" },
        ],
      },
    });

    expect(wrapper.text()).toContain("已等待 1 分 5 秒");
    expect(wrapper.text()).toContain("住宿与房价");
    expect(wrapper.text()).toContain("交通路线");
    expect(wrapper.text()).toContain("最终校验");
    expect(wrapper.text()).toContain("无需刷新");
  });

  it("uses the newest running finalization timestamp after restored items are reordered", () => {
    const now = Date.now();
    const wrapper = mount(TravelProgress, {
      props: {
        stage: "validate",
        active: true,
        items: [
          { id: "retry-finalizing-a-new", stage: "validate", title: "本次重试", detail: "继续完善", status: "running", startedAt: now - 12_000 },
          { id: "finalizing-a-old", stage: "validate", title: "旧轮次", detail: "历史记录后置", status: "running", startedAt: now - 1_032_000 },
        ],
      },
    });

    expect(wrapper.text()).toContain("已等待 12 秒");
    expect(wrapper.text()).not.toContain("17 分");
  });

  it("does not keep timing a completed or failed finalization", () => {
    const wrapper = mount(TravelProgress, {
      props: {
        stage: "validate",
        active: true,
        items: [
          { id: "finalizing-a", stage: "validate", title: "旧轮次", detail: "已经结束", status: "error", startedAt: Date.now() - 600_000 },
        ],
      },
    });

    expect(wrapper.text()).not.toContain("已等待");
  });
});
