import type { TravelStayRecommendation } from "@/api/types";

const providerLabels: Record<string, string> = {
  "amap": "高德地图",
  "amap-maps": "高德地图",
  "amap_search": "高德地图",
  "amap_detail": "高德地图",
  "amap_transit": "高德地图",
  "12306": "12306",
  "tavily": "Tavily 网页检索",
  "ctrip-account-observation": "携程账号实查",
  "ctrip": "携程账号实查",
  "hotel-browser": "携程账号实查",
  "model_estimate": "规划估算",
  "open-meteo": "Open-Meteo 天气",
  "open_meteo": "Open-Meteo 天气",
  "xiaohongshu-readonly": "小红书只读",
  "xhs-readonly": "小红书只读",
};

const routeSourceLabels: Record<string, string> = {
  "amap": "高德地图",
  "amap_transit": "高德公交路线",
  "amap_walking": "高德步行路线",
  "amap_driving": "高德驾车路线",
  "amap_driving_fallback": "高德地图（公交未返回）",
  "model_estimate": "规划估算",
  "planning_estimate": "规划估算",
};

const freshnessLabels: Record<string, string> = {
  live: "实时查询",
  snapshot: "查询快照",
  historical: "历史同期",
  estimate: "规划估算",
  unknown: "待确认",
};

export function travelProviderLabel(value: unknown): string {
  const raw = String(value || "").trim();
  if (!raw) return "来源";
  return providerLabels[raw.toLocaleLowerCase()] || (/\p{Script=Han}/u.test(raw) ? raw : "外部数据源");
}

export function travelRouteSourceLabel(value: unknown): string {
  const raw = String(value || "").trim();
  if (!raw) return "来源待确认";
  return routeSourceLabels[raw.toLocaleLowerCase()] || travelProviderLabel(raw);
}

export function travelSegmentSourceLabel(sourceValue: unknown, modeValue: unknown): string {
  const source = String(sourceValue || "").trim().toLocaleLowerCase();
  const mode = String(modeValue || "");
  if (source === "amap_transit" && /打车|出租|网约|接驳/.test(mode)) {
    return "高德地图（公交未返回）";
  }
  return travelRouteSourceLabel(sourceValue);
}

export function travelTransportModeLabel(value: unknown): string {
  const raw = String(value || "").trim();
  if (!raw) return "交通";
  const normalized = raw.toLocaleLowerCase().replace(/\s+/g, "");
  const labels: Record<string, string> = {
    bus: "公交",
    metro: "地铁",
    subway: "地铁",
    taxi: "出租车 / 网约车",
    walk: "步行",
    walking: "步行",
    driving: "驾车",
    car: "驾车",
    coach: "城际客运",
    "coach+bus": "城际客运 + 公交",
    "bus+metro": "公交 + 地铁",
    "metro+bus": "地铁 + 公交",
  };
  return labels[normalized] || (/\p{Script=Han}/u.test(raw) ? raw : "组合交通");
}

export function travelFreshnessLabel(value: unknown): string {
  const raw = String(value || "").trim();
  if (!raw) return "待确认";
  return freshnessLabels[raw.toLocaleLowerCase()] || (/\p{Script=Han}/u.test(raw) ? raw : "待确认");
}

export function travelPlanningModeLabel(value: unknown): string {
  const raw = String(value || "").trim().toLocaleLowerCase();
  if (raw === "deep") return "深度规划";
  if (raw === "quick") return "快速规划";
  return "旅行规划";
}

export function travelPublicText(value: unknown): string {
  return String(value || "")
    .replace(/当前\s*(?:本\s+)?Session/gi, "本次查询")
    .replace(/本\s+Session/gi, "本次查询")
    .replace(/\bSession\b/gi, "本次查询")
    .replace(/\btransits\s*=\s*\[\]/gi, "未返回可用公交方案")
    .replace(/\bnot_on_sale\b/gi, "尚未开售")
    .replace(/\bsale_open_date\b/gi, "起售日期")
    .replace(/\bstatus\s*=\s*/gi, "")
    .replace(/\bctrip-account-observation\b/gi, "携程账号实查")
    .replace(/\bamap_transit\b/gi, "高德公交路线")
    .replace(/\bamap_walking\b/gi, "高德步行路线")
    .replace(/\bamap_driving_fallback\b/gi, "高德地图（公交未返回）")
    .replace(/\bmodel_estimate\b/gi, "规划估算")
    .replace(/\bplanning_estimate\b/gi, "规划估算")
    .replace(/\blive_observed\b/gi, "指定日期观察价");
}

export function reconcileTravelBudgetDisplay(
  budget: { lower: number; expected: number; upper: number; items: Array<Record<string, unknown>> },
  stays: TravelStayRecommendation[],
) {
  const displayed = { ...budget, items: budget.items.map((item) => ({ ...item })) };
  const observed = stays
    .filter((stay) => ["live_observed", "snapshot_observed"].includes(String(stay.price_status || "")))
    .map((stay) => Number(stay.observed_price_per_night_cny) * Math.max(1, Number(stay.nights) || 1))
    .filter((price) => Number.isFinite(price) && price > 0);
  const lodging = displayed.items.filter((item) => /住宿|酒店|民宿|旅馆/.test(String(item.name || item.category || "")));
  if (observed.length && observed.length === lodging.length) {
    lodging.forEach((item, index) => { item.expected = observed[index]; });
    const expected = displayed.items.reduce((total, item) => total + Number(item.expected || 0), 0);
    if (Number.isFinite(expected) && expected > 0) displayed.expected = expected;
  }
  return displayed;
}
