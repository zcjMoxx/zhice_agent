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
            provider: "高德地图",
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
});
