<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from "vue";
import type { TravelDay, TravelLocation, TravelRouteSegment } from "@/api/types";
import { travelTransportModeLabel } from "@/travel/sourceLabels";

const props = defineProps<{ days: TravelDay[] }>();
const container = ref<HTMLElement | null>(null);
const fallback = ref("");
const mapNotice = ref("");
const selectedDate = ref(props.days[0]?.date || "");

interface AMapInstance {
  add: (overlays: unknown[]) => void;
  clearMap?: () => void;
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
type PointCandidate = Omit<NumberedPoint, keyof TravelLocation> & { location?: TravelLocation | null };

let map: AMapInstance | null = null;
let mapApi: AMapApi | null = null;
let renderSequence = 0;

const selectedDay = computed(() => props.days.find((day) => day.date === selectedDate.value) || props.days[0] || null);
const pointCandidates = computed<PointCandidate[]>(() => (selectedDay.value?.activities || []).map((activity, index) => ({
  location: activity.location,
  label: activity.place,
  number: index + 1,
  date: selectedDay.value?.date || "",
  area: selectedDay.value?.city_or_area || "",
})));
const resolvedPoints = ref<NumberedPoint[]>([]);
const points = computed<NumberedPoint[]>(() => resolvedPoints.value.length
  ? resolvedPoints.value
  : pointCandidates.value.flatMap((point) => point.location ? [{ ...point.location, label: point.label, number: point.number, date: point.date, area: point.area }] : []));
const realPaths = computed(() => (selectedDay.value?.route_segments || []).map((segment) => segment.path || []).filter((path) => path.length > 1));
const referencePaths = computed(() => points.value.length > 1 ? [points.value] : []);
const daySummary = computed(() => {
  const day = selectedDay.value;
  return day ? {
    date: day.date,
    area: day.city_or_area,
    places: day.activities.map((activity) => activity.place),
    routes: day.route_segments.map(routeSummary),
  } : null;
});

watch(() => props.days.map((day) => day.date).join("|"), () => {
  if (!props.days.some((day) => day.date === selectedDate.value)) selectedDate.value = props.days[0]?.date || "";
});
watch(selectedDate, () => { void drawSelectedDay(); });

onMounted(async () => {
  const key = import.meta.env.VITE_AMAP_JS_API_KEY?.trim();
  if (!key) {
    fallback.value = "未配置高德 JS API Key；下方仍保留当天地点顺序、交通方式、距离和时长。";
    return;
  }
  try {
    const securityCode = import.meta.env.VITE_AMAP_JS_SECURITY_CODE?.trim();
    if (securityCode) (window as Window & { _AMapSecurityConfig?: Record<string, string> })._AMapSecurityConfig = { securityJsCode: securityCode };
    mapApi = await loadAmap(key);
    if (!container.value) return;
    map = new mapApi.Map(container.value, {
      zoom: 11,
      viewMode: "2D",
      mapStyle: "amap://styles/normal",
      features: ["bg", "road", "building", "point"],
      showLabel: true,
      resizeEnable: true,
    });
    map.on?.("error", () => { mapNotice.value = "高德底图未能加载，请检查浏览器 Key、安全码、域名白名单或网络；当天地点与路线信息仍可查看。"; });
    await drawSelectedDay();
  } catch {
    fallback.value = "地图组件加载失败；下方仍保留当天地点顺序、交通方式、距离和时长。";
  }
});
onBeforeUnmount(() => map?.destroy?.());

async function drawSelectedDay() {
  const sequence = ++renderSequence;
  resolvedPoints.value = [];
  mapNotice.value = "";
  map?.clearMap?.();
  if (!map || !mapApi || !selectedDay.value) return;
  if (!pointCandidates.value.length) {
    mapNotice.value = "当天计划没有可绘制地点；文字行程仍可查看。";
    return;
  }
  const missingCount = pointCandidates.value.filter((point) => !point.location).length;
  const resolved = await resolvePoints(mapApi, pointCandidates.value);
  if (sequence !== renderSequence) return;
  resolvedPoints.value = resolved;
  if (!points.value.length) {
    mapNotice.value = "当天地点坐标暂时无法补全；文字行程仍可查看。";
    return;
  }
  if (missingCount) {
    const supplemented = Math.max(0, resolved.length - (pointCandidates.value.length - missingCount));
    mapNotice.value = supplemented
      ? `已为当天历史计划动态补全 ${supplemented} 个地点坐标；虚线表示当天地点先后顺序。`
      : "当天部分历史地点暂时无法补全坐标；已绘制其余地点。";
  }
  const overlays: unknown[] = [];
  points.value.forEach((point) => overlays.push(new mapApi!.Marker({
    position: [point.longitude, point.latitude],
    title: `${point.number}. ${point.label}`,
    label: { content: `${point.number}. ${escapeHtml(point.label)}`, direction: "top" },
    extData: { number: point.number, date: point.date, area: point.area },
  })));
  realPaths.value.forEach((path) => overlays.push(new mapApi!.Polyline({ path: coordinates(path), strokeColor: "#315f6d", strokeWeight: 6, strokeOpacity: 0.88, showDir: true })));
  if (!realPaths.value.length) referencePaths.value.forEach((path) => overlays.push(new mapApi!.Polyline({ path: coordinates(path), strokeColor: "#7b918e", strokeWeight: 4, strokeOpacity: 0.72, strokeStyle: "dashed" })));
  map.add(overlays);
  if (overlays.length) map.setFitView(overlays, false, [72, 72, 72, 72]);
}

function routeSummary(segment: TravelRouteSegment): string {
  const legs = (segment.transit_legs || []).map((leg) => `${leg.line_name} ${leg.departure_stop} → ${leg.arrival_stop}`).join("；");
  const route = legs || `${travelTransportModeLabel(segment.mode)} ${segment.from} → ${segment.to}`;
  return `${route} · ${formatDistance(segment.distance)} · ${formatDuration(segment.duration)}`;
}

const coordinates = (path: TravelLocation[]) => path.map((point) => [point.longitude, point.latitude]);
const formatDuration = (value: number) => value >= 60 ? `${Math.floor(value / 60)} 小时 ${Math.round(value % 60)} 分` : `${Math.round(value)} 分钟`;
const formatDistance = (value: number) => value < 1 ? `${Math.round(value * 1000)} 米` : `${Number(value.toFixed(1))} 公里`;
const escapeHtml = (value: string) => value.replace(/[&<>"']/g, (character) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[character] || character);

async function resolvePoints(AMap: AMapApi, candidates: PointCandidate[]): Promise<NumberedPoint[]> {
  const resolved = candidates.flatMap((point) => point.location ? [{ ...point.location, label: point.label, number: point.number, date: point.date, area: point.area }] : []);
  const missing = candidates.filter((point) => !point.location).slice(0, 20);
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
    <header><span class="eyebrow">路线地图</span><h2>按天查看地点与路程</h2></header>
    <div class="travel-map-day-tabs" role="tablist" aria-label="选择地图日期">
      <button v-for="(day, index) in days" :key="day.date" type="button" role="tab" :aria-selected="selectedDate === day.date" :class="{ active: selectedDate === day.date }" @click="selectedDate = day.date">
        <b>第 {{ index + 1 }} 天</b><span>{{ day.date.slice(5) }} · {{ day.city_or_area }}</span>
      </button>
    </div>
    <div v-show="!fallback" ref="container" class="travel-map-canvas" />
    <div v-if="fallback" class="travel-map-fallback"><strong>文字路线模式</strong><p>{{ fallback }}</p></div>
    <p v-if="mapNotice" class="travel-map-notice">{{ mapNotice }}</p>
    <p v-else-if="!fallback && !realPaths.length" class="travel-map-notice">虚线只表示所选日期的地点先后顺序，不代表实际道路导航。</p>
    <div v-if="daySummary" class="travel-map-itinerary">
      <article :key="daySummary.date">
        <header><b>{{ daySummary.date.slice(5) }} · {{ daySummary.area }}</b><span>{{ daySummary.places.length }} 个地点</span></header>
        <p>{{ daySummary.places.join(' → ') || '暂无地点' }}</p>
        <ul v-if="daySummary.routes.length"><li v-for="route in daySummary.routes" :key="route">{{ route }}</li></ul>
        <small v-else>当天暂无可核验的路线距离和时长</small>
      </article>
    </div>
  </section>
</template>
