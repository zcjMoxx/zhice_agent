<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from "vue";
import type { TravelDay, TravelLocation } from "@/api/types";

const props = defineProps<{ days: TravelDay[] }>();
const container = ref<HTMLElement | null>(null);
const fallback = ref("");
const mapNotice = ref("");
interface AMapInstance {
  add: (overlays: unknown[]) => void;
  setFitView: (overlays: unknown[], immediate: boolean, padding: number[]) => void;
  on?: (event: string, handler: (event?: unknown) => void) => void;
  destroy?: () => void;
}
interface AMapGeocoder {
  getLocation: (address: string, callback: (status: string, result: unknown) => void) => void;
}
interface AMapApi {
  Map: new (container: HTMLElement, options: Record<string, unknown>) => AMapInstance;
  Marker: new (options: Record<string, unknown>) => unknown;
  Polyline: new (options: Record<string, unknown>) => unknown;
  Geocoder?: new (options?: Record<string, unknown>) => AMapGeocoder;
  plugin?: (plugins: string | string[], callback: () => void) => void;
}
type NumberedPoint = TravelLocation & { label: string; number: number; date: string; area: string };
type PointCandidate = Omit<NumberedPoint, keyof TravelLocation> & {
  location?: TravelLocation | null;
};
let map: AMapInstance | null = null;

const pointCandidates = computed<PointCandidate[]>(() => {
  let number = 0;
  return props.days.flatMap((day) => day.activities.map((activity) => {
    number += 1;
    return { location: activity.location, label: activity.place, number, date: day.date, area: day.city_or_area };
  }));
});
const resolvedPoints = ref<NumberedPoint[]>([]);
const points = computed<NumberedPoint[]>(() => resolvedPoints.value.length
  ? resolvedPoints.value
  : pointCandidates.value.flatMap((point) => point.location ? [{ ...point.location, label: point.label, number: point.number, date: point.date, area: point.area }] : []));
const realPaths = computed(() => props.days.flatMap((day) => day.route_segments.map((segment) => segment.path || []).filter((path) => path.length > 1)));
const referencePaths = computed(() => props.days.map((day) => points.value.filter((point) => point.date === day.date)).filter((path) => path.length > 1));
const daySummaries = computed(() => props.days.map((day) => ({
  date: day.date,
  area: day.city_or_area,
  places: day.activities.map((activity) => activity.place),
  routes: day.route_segments.map((segment) => `${segment.mode} ${segment.from} → ${segment.to} · ${formatDistance(segment.distance)} · ${formatDuration(segment.duration)}`),
})));

onMounted(async () => {
  const key = import.meta.env.VITE_AMAP_JS_API_KEY?.trim();
  if (!key) { fallback.value = "未配置高德 JS API Key；下方仍保留地点顺序、交通方式、距离和时长。"; return; }
  if (!pointCandidates.value.length) { fallback.value = "当前计划没有可绘制地点；下方仍保留文字行程。"; return; }
  try {
    const securityCode = import.meta.env.VITE_AMAP_JS_SECURITY_CODE?.trim();
    if (securityCode) (window as Window & { _AMapSecurityConfig?: Record<string, string> })._AMapSecurityConfig = { securityJsCode: securityCode };
    const AMap = await loadAmap(key);
    const missingCount = pointCandidates.value.filter((point) => !point.location).length;
    resolvedPoints.value = await resolvePoints(AMap, pointCandidates.value);
    if (!points.value.length) { fallback.value = "地点坐标暂时无法补全；下方仍保留地点顺序、交通方式、距离和时长。"; return; }
    if (missingCount) {
      const supplemented = Math.max(0, resolvedPoints.value.length - (pointCandidates.value.length - missingCount));
      mapNotice.value = supplemented
        ? `已为历史计划动态补全 ${supplemented} 个地点坐标；虚线表示当天地点先后顺序。`
        : "部分历史地点暂时无法补全坐标；已绘制其余地点，虚线表示当天地点先后顺序。";
    }
    if (!container.value) return;
    const instance = new AMap.Map(container.value, {
      zoom: 11,
      viewMode: "2D",
      mapStyle: "amap://styles/normal",
      features: ["bg", "road", "building", "point"],
      showLabel: true,
      resizeEnable: true,
    });
    map = instance;
    instance.on?.("error", () => { mapNotice.value = "高德底图未能加载，请检查浏览器 Key、安全码、域名白名单或网络；地点与路线信息仍可查看。"; });
    const overlays: unknown[] = [];
    points.value.forEach((point) => overlays.push(new AMap.Marker({
      position: [point.longitude, point.latitude],
      title: `${point.number}. ${point.label}`,
      label: { content: `${point.number}. ${escapeHtml(point.label)}`, direction: "top" },
      extData: { number: point.number, date: point.date, area: point.area },
    })));
    realPaths.value.forEach((path) => overlays.push(new AMap.Polyline({ path: coordinates(path), strokeColor: "#315f6d", strokeWeight: 6, strokeOpacity: 0.88, showDir: true })));
    if (!realPaths.value.length) referencePaths.value.forEach((path) => overlays.push(new AMap.Polyline({ path: coordinates(path), strokeColor: "#7b918e", strokeWeight: 4, strokeOpacity: 0.72, strokeStyle: "dashed" })));
    instance.add(overlays);
    instance.setFitView(overlays, false, [72, 72, 72, 72]);
  } catch { fallback.value = "地图组件加载失败；下方仍保留地点顺序、交通方式、距离和时长。"; }
});
onBeforeUnmount(() => map?.destroy?.());

const coordinates = (path: TravelLocation[]) => path.map((point) => [point.longitude, point.latitude]);
const formatDuration = (value: number) => value >= 60 ? `${Math.floor(value / 60)} 小时 ${Math.round(value % 60)} 分` : `${Math.round(value)} 分钟`;
const formatDistance = (value: number) => value < 1 ? `${Math.round(value * 1000)} 米` : `${Number(value.toFixed(1))} 公里`;
const escapeHtml = (value: string) => value.replace(/[&<>"']/g, (character) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[character] || character);

async function resolvePoints(AMap: AMapApi, candidates: PointCandidate[]): Promise<NumberedPoint[]> {
  const resolved = candidates.flatMap((point) => point.location ? [{ ...point.location, label: point.label, number: point.number, date: point.date, area: point.area }] : []);
  const missing = candidates.filter((point) => !point.location).slice(0, 60);
  if (!missing.length || !AMap.plugin) return resolved;
  await new Promise<void>((resolve) => AMap.plugin?.("AMap.Geocoder", resolve));
  if (!AMap.Geocoder) return resolved;
  const geocoder = new AMap.Geocoder({ city: "全国" });
  for (const point of missing) {
    const location = await geocode(geocoder, `${point.area} ${point.label}`);
    if (location) resolved.push({ ...location, label: point.label, number: point.number, date: point.date, area: point.area });
  }
  return resolved.sort((left, right) => left.number - right.number);
}

function geocode(geocoder: AMapGeocoder, address: string): Promise<TravelLocation | null> {
  return new Promise((resolve) => {
    geocoder.getLocation(address, (status, result) => {
      if (status !== "complete" || !result || typeof result !== "object") { resolve(null); return; }
      const geocodes = (result as { geocodes?: Array<{ location?: unknown }> }).geocodes;
      const raw = geocodes?.[0]?.location;
      if (!raw || typeof raw !== "object") { resolve(null); return; }
      const value = raw as { lng?: number; lat?: number; getLng?: () => number; getLat?: () => number };
      const longitude = typeof value.lng === "number" ? value.lng : value.getLng?.();
      const latitude = typeof value.lat === "number" ? value.lat : value.getLat?.();
      resolve(typeof longitude === "number" && typeof latitude === "number" ? { longitude, latitude } : null);
    });
  });
}

async function loadAmap(key: string): Promise<AMapApi> {
  const existing = (window as Window & { AMap?: AMapApi }).AMap;
  if (existing) return existing;
  await new Promise<void>((resolve, reject) => {
    const script = document.createElement("script");
    script.src = `https://webapi.amap.com/maps?v=2.0&key=${encodeURIComponent(key)}`;
    script.async = true;
    script.onload = () => resolve();
    script.onerror = () => reject(new Error("AMap load failed"));
    document.head.appendChild(script);
  });
  const loaded = (window as Window & { AMap?: AMapApi }).AMap;
  if (!loaded) throw new Error("AMap unavailable");
  return loaded;
}
</script>

<template>
  <section class="travel-card travel-map-card">
    <header><span class="eyebrow">路线地图</span><h2>地点、顺序和路程一起看</h2></header>
    <div v-show="!fallback" ref="container" class="travel-map-canvas" />
    <div v-if="fallback" class="travel-map-fallback"><strong>文字路线模式</strong><p>{{ fallback }}</p></div>
    <p v-if="mapNotice" class="travel-map-notice">{{ mapNotice }}</p>
    <p v-else-if="!fallback && !realPaths.length" class="travel-map-notice">虚线只表示当天地点先后顺序，不代表实际道路导航。</p>
    <div class="travel-map-itinerary">
      <article v-for="day in daySummaries" :key="day.date">
        <header><b>{{ day.date.slice(5) }} · {{ day.area }}</b><span>{{ day.places.length }} 个地点</span></header>
        <p>{{ day.places.join(' → ') || '暂无地点' }}</p>
        <ul v-if="day.routes.length"><li v-for="route in day.routes" :key="route">{{ route }}</li></ul>
        <small v-else>当天暂无可核验的路线距离和时长</small>
      </article>
    </div>
  </section>
</template>
