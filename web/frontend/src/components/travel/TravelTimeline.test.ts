import { mount } from "@vue/test-utils";
import { describe, expect, it } from "vitest";

import type { TravelDay } from "@/api/types";
import TravelTimeline from "./TravelTimeline.vue";

describe("TravelTimeline", () => {
  it("shows verified transit lines and boarding stops", () => {
    const days: TravelDay[] = [{
      date: "2026-08-20",
      city_or_area: "沈阳老城",
      activities: [{ start: "09:00", end: "11:00", place: "沈阳故宫", reason: "参观", evidence_ids: [], location: { longitude: 123.45, latitude: 41.79 } }],
      route_segments: [{
        mode: "地铁",
        from: "沈阳站",
        to: "中街",
        duration: 25,
        distance: 6,
        source: "amap_transit",
        evidence_ids: [],
        path: [],
        walking_distance: 742,
        transit_legs: [{ mode: "地铁", line_name: "地铁1号线", departure_stop: "沈阳站", arrival_stop: "中街站", via_stops: ["太原街", "青年大街"] }],
      }],
      meal_suggestions: [],
      daily_budget: 500,
      weather_adjustment: "",
      fallback_plan: "",
      intensity_score: 5,
    }];

    const wrapper = mount(TravelTimeline, { props: { days } });

    expect(wrapper.text()).toContain("地铁1号线");
    expect(wrapper.text()).toContain("沈阳站 → 中街站");
    expect(wrapper.text()).toContain("途经 太原街、青年大街");
    expect(wrapper.text()).toContain("步行接驳 742 米");
  });

  it("converts walking distances of one kilometre or more", () => {
    const days: TravelDay[] = [{
      date: "2026-08-20",
      city_or_area: "沈阳老城",
      activities: [{ start: "09:00", end: "11:00", place: "沈阳故宫", reason: "参观", evidence_ids: [], location: { longitude: 123.45, latitude: 41.79 } }],
      route_segments: [{ mode: "步行", from: "A", to: "B", duration: 20, distance: 1.2, source: "amap_walking", evidence_ids: [], path: [], walking_distance: 1200, transit_legs: [] }],
      meal_suggestions: [],
      daily_budget: 500,
      weather_adjustment: "",
      fallback_plan: "",
      intensity_score: 5,
    }];

    const wrapper = mount(TravelTimeline, { props: { days } });

    expect(wrapper.text()).toContain("步行接驳 1.2 公里");
    expect(wrapper.text()).toContain("高德步行路线");
    expect(wrapper.text()).not.toContain("amap_walking");
  });

  it("does not label a no-transit taxi fallback as a verified bus route", () => {
    const days: TravelDay[] = [{
      date: "2026-09-21",
      city_or_area: "栾川老君山",
      activities: [],
      route_segments: [{ mode: "景区接驳/短途打车", from: "栾川汽车站", to: "老君山", duration: 25, distance: 8.1, source: "amap_transit", evidence_ids: [], path: [], transit_legs: [] }],
      meal_suggestions: [], daily_budget: 300, weather_adjustment: "", fallback_plan: "", intensity_score: 5,
    }];

    const wrapper = mount(TravelTimeline, { props: { days } });

    expect(wrapper.text()).toContain("高德地图（公交未返回）");
    expect(wrapper.text()).not.toContain("高德公交路线");
  });

  it("translates internal transport mode enums and driving fallback source", () => {
    const days: TravelDay[] = [{
      date: "2026-09-21", city_or_area: "栾川老君山", activities: [],
      route_segments: [{ mode: "taxi", from: "老君山游客中心", to: "洛阳汽车站", duration: 128, distance: 150.1, source: "amap_driving_fallback", evidence_ids: [], path: [], transit_legs: [] }],
      meal_suggestions: [], daily_budget: 300, weather_adjustment: "", fallback_plan: "", intensity_score: 8,
    }];

    const wrapper = mount(TravelTimeline, { props: { days } });

    expect(wrapper.text()).toContain("出租车 / 网约车");
    expect(wrapper.text()).toContain("高德地图（公交未返回）");
    expect(wrapper.text()).not.toContain("taxi");
    expect(wrapper.text()).not.toContain("amap_driving_fallback");
  });
});
