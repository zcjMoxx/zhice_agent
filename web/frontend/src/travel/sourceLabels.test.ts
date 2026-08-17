import { describe, expect, it } from "vitest";

import {
  reconcileTravelBudgetDisplay,
  travelPlanningModeLabel,
  travelProviderLabel,
  travelPublicText,
  travelRouteSourceLabel,
  travelTransportModeLabel,
} from "@/travel/sourceLabels";

describe("travel source labels", () => {
  it("does not expose internal provider or mode enums", () => {
    expect(travelProviderLabel("AMap")).toBe("高德地图");
    expect(travelProviderLabel("Ctrip")).toBe("携程账号实查");
    expect(travelProviderLabel("ctrip-account-observation")).toBe("携程账号实查");
    expect(travelRouteSourceLabel("amap_transit")).toBe("高德公交路线");
    expect(travelRouteSourceLabel("amap_driving_fallback")).toBe("高德地图（公交未返回）");
    expect(travelTransportModeLabel("coach+bus")).toBe("城际客运 + 公交");
    expect(travelTransportModeLabel("taxi")).toBe("出租车 / 网约车");
    expect(travelRouteSourceLabel("model_estimate")).toBe("规划估算");
    expect(travelPlanningModeLabel("quick")).toBe("快速规划");
    expect(travelPlanningModeLabel("deep")).toBe("深度规划");
    expect(travelPublicText("本 Session 两次返回 transits=[]，status=not_on_sale")).toBe(
      "本次查询 两次返回 未返回可用公交方案，尚未开售",
    );
    expect(
      travelPublicText(
        "ctrip-account-observation / amap_transit / model_estimate / live_observed",
      ),
    ).toBe("携程账号实查 / 高德公交路线 / 规划估算 / 指定日期观察价");
    expect(travelPublicText("当前本 Session 已有观察价")).toBe("本次查询 已有观察价");
    expect(travelPublicText("当前 Session 已有观察价")).toBe("本次查询 已有观察价");
  });

  it("uses dated observed hotel prices in the displayed budget", () => {
    const budget = reconcileTravelBudgetDisplay(
      {
        lower: 1000,
        expected: 1791,
        upper: 2500,
        items: [
          { name: "交通", expected: 950 },
          { name: "龙门站片区住宿1晚", expected: 96 },
          { name: "老君山景区周边住宿1晚", expected: 210 },
        ],
      },
      [
        { price_status: "live_observed", observed_price_per_night_cny: 96, nights: 1 },
        { price_status: "live_observed", observed_price_per_night_cny: 169, nights: 1 },
      ] as never,
    );

    expect(budget.items.map((item) => item.expected)).toEqual([950, 96, 169]);
    expect(budget.expected).toBe(1215);
  });
});
