import { mount } from "@vue/test-utils";
import { afterEach, describe, expect, it, vi } from "vitest";
import { nextTick } from "vue";

import type { TravelDay } from "@/api/types";
import TravelMap from "./TravelMap.vue";

describe("TravelMap", () => {
  afterEach(() => {
    vi.unstubAllEnvs();
    vi.unstubAllGlobals();
  });

  it("keeps the textual route available when the map key is absent", async () => {
    const wrapper = mount(TravelMap, { props: { days: sampleDays() } });
    await nextTick();

    expect(wrapper.text()).toContain("仍保留地点顺序、交通方式、距离和时长");
    expect(wrapper.text()).toContain("古城");
    expect(wrapper.text()).toContain("步行 A → B");
    expect(wrapper.text()).toContain("2 公里");
  });

  it("geocodes missing historical locations and draws map overlays", async () => {
    vi.stubEnv("VITE_AMAP_JS_API_KEY", "test-key");
    const add = vi.fn();
    const setFitView = vi.fn();
    const Marker = vi.fn(function Marker() { return {}; });
    const Polyline = vi.fn(function Polyline() { return {}; });
    const Map = vi.fn(function Map() { return { add, setFitView, destroy: vi.fn() }; });
    const Geocoder = vi.fn(function Geocoder() {
      return {
        getLocation: (_address: string, callback: (status: string, result: unknown) => void) =>
          callback("complete", { geocodes: [{ location: { lng: 100.16, lat: 25.69 } }] }),
      };
    });
    vi.stubGlobal("AMap", {
      Map,
      Marker,
      Polyline,
      Geocoder,
      plugin: (_plugin: string, callback: () => void) => callback(),
    });
    const days = sampleDays();
    days[0].activities[0].location = null;

    const wrapper = mount(TravelMap, { props: { days } });
    await vi.waitFor(() => expect(Marker).toHaveBeenCalledOnce());

    expect(Geocoder).toHaveBeenCalledOnce();
    expect(add).toHaveBeenCalled();
    expect(setFitView).toHaveBeenCalled();
    expect(wrapper.text()).toContain("已为历史计划动态补全 1 个地点坐标");
  });
});

function sampleDays(): TravelDay[] {
  return [{
    date: "2026-10-01",
    city_or_area: "大理",
    activities: [{ start: "09:00", end: "11:00", place: "古城", reason: "步行", evidence_ids: [], location: { longitude: 100.16, latitude: 25.69 } }],
    route_segments: [{ mode: "步行", from: "A", to: "B", duration: 20, distance: 2, source: "高德", evidence_ids: [], path: [] }],
    meal_suggestions: [],
    daily_budget: 300,
    weather_adjustment: "",
    fallback_plan: "",
    intensity_score: 3,
  }];
}
