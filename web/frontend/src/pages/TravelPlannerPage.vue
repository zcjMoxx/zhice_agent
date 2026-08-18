<script setup lang="ts">
import { computed, nextTick, onMounted, ref } from "vue";
import { ArrowLeft, Compass, PanelLeftClose, PanelLeftOpen, Plus, RefreshCw, Trash2 } from "@lucide/vue";
import { useRoute, useRouter } from "vue-router";

import QuickPreferences from "@/components/QuickPreferences.vue";
import TravelBudget from "@/components/travel/TravelBudget.vue";
import TravelMap from "@/components/travel/TravelMap.vue";
import TravelPlanForm from "@/components/travel/TravelPlanForm.vue";
import TravelProgress from "@/components/travel/TravelProgress.vue";
import TravelSourcesDrawer from "@/components/travel/TravelSourcesDrawer.vue";
import TravelTimeline from "@/components/travel/TravelTimeline.vue";
import type { TravelStayRecommendation, TravelTransportOption } from "@/api/types";
import { useAuthStore } from "@/stores/auth";
import { useTravelStore } from "@/stores/travel";
import { saveChatHandoff } from "@/travel/chatHandoff";
import { reconcileTravelBudgetDisplay, travelFreshnessLabel, travelPlanningModeLabel, travelProviderLabel, travelPublicText, travelRouteSourceLabel, travelTransportModeLabel } from "@/travel/sourceLabels";

const auth = useAuthStore();
const travel = useTravelStore();
const route = useRoute();
const router = useRouter();
const plan = computed(() => travel.activePlan);
const displayedBudget = computed(() => plan.value ? reconcileTravelBudgetDisplay(plan.value.budget, plan.value.stay_recommendations) : { lower: 0, expected: 0, upper: 0, items: [] });
const travelTone = computed(() => ({
  economy: "经济实惠",
  balanced: "舒适均衡",
  comfortable: "轻松品质",
} as Record<string, string>)[travel.activeDraft?.budget_level || ""] || "未记录");
const leftCollapsed = ref(false);
const formInspectorOpen = ref(false);
const progressSection = ref<HTMLElement | null>(null);
const resultsScroll = ref<HTMLElement | null>(null);
const canResumePlanning = computed(() => Boolean(travel.error)
  && !travel.generating
  && Boolean(travel.sessionId)
  && !plan.value
  && travel.candidateReview?.status !== "selected");

onMounted(async () => {
  if (!auth.authenticated) { await router.replace("/"); return; }
  await travel.initialize(auth.user?.id || "");
  travel.markViewed();
  await travel.refresh();
  const requested = typeof route.query.plan === "string" ? route.query.plan : "";
  const requestedSession = typeof route.query.session === "string" ? route.query.session : "";
  if (requested) await travel.open(requested);
  else if (requestedSession) {
    const item = travel.workItems.find((entry) => entry.session_id === requestedSession);
    if (item) await travel.openWorkItem(item);
  } else if (!travel.generating && !travel.sessionId && !travel.activePlan && !travel.progressItems.length) {
    travel.startNew();
  }
});

async function removeWorkItem(item: import("@/api/types").TravelWorkItem) {
  const label = item.status === "completed" ? "这份旅行计划及其需求问答记录" : "这条未完成的旅行任务";
  if (!window.confirm(`确定删除${label}？`)) return;
  await travel.removeWorkItem(item);
}

async function chooseCandidate(candidateId: string) {
  const selection = travel.chooseCandidate(candidateId);
  await nextTick();
  progressSection.value?.scrollIntoView({ behavior: "smooth", block: "start" });
  await selection;
}

async function resumePlanning() {
  await travel.resumeFailedPlanning();
  await nextTick();
  progressSection.value?.scrollIntoView({ behavior: "smooth", block: "start" });
}

function workStatusLabel(status: import("@/api/types").TravelWorkStatus) {
  return ({ collecting: "需求收集中", running: "规划进行中", awaiting_candidate: "等待选择", failed: "规划未完成", completed: "已完成" })[status];
}

function text(value: unknown, fallback = "—"): string {
  if (typeof value === "string" && value.trim()) return value;
  if (typeof value === "number") return String(value);
  return fallback;
}

const money = (value: number) => new Intl.NumberFormat("zh-CN", { style: "currency", currency: "CNY", maximumFractionDigits: 0 }).format(value);

function transportValue(item: TravelTransportOption, key: string): unknown {
  const direct = item[key];
  if (direct !== undefined && direct !== null && direct !== "") return direct;
  const legacy = item.recommended_segment;
  return legacy && typeof legacy === "object" ? legacy[key] : undefined;
}

function transportRoute(item: TravelTransportOption): string {
  if (item.summary) return item.summary;
  const from = text(transportValue(item, "from"), "");
  const to = text(transportValue(item, "to"), "");
  const service = text(transportValue(item, "service_name") || transportValue(item, "train_number"), "");
  return [service, from && to ? `${from} → ${to}` : from || to].filter(Boolean).join(" · ") || "路线信息待补充";
}

function transportMeta(item: TravelTransportOption): string {
  const pieces: string[] = [];
  const departure = shortDateTime(transportValue(item, "departure"));
  const arrival = shortDateTime(transportValue(item, "arrival"));
  if (departure || arrival) pieces.push(`${departure || "—"} → ${arrival || "—"}`);
  const duration = Number(transportValue(item, "duration_minutes"));
  if (Number.isFinite(duration) && duration > 0) pieces.push(duration >= 60 ? `${Math.floor(duration / 60)}小时${duration % 60}分` : `${duration}分钟`);
  const seat = text(transportValue(item, "seat"), "");
  if (seat) pieces.push(seat);
  const perPerson = Number(transportValue(item, "price_cny_per_person") ?? transportValue(item, "fare_reference_cny_per_person"));
  if (Number.isFinite(perPerson) && perPerson > 0) pieces.push(`${money(perPerson)}/人`);
  const total = Number(transportValue(item, "price_cny_total") ?? transportValue(item, "price_cny_total_for_2"));
  if (Number.isFinite(total) && total > 0) pieces.push(`合计 ${money(total)}`);
  const source = text(transportValue(item, "source"), "");
  if (source) pieces.push(`来源：${travelRouteSourceLabel(source)}`);
  return pieces.join(" · ") || "详见每日路线与来源记录";
}

function shortDateTime(value: unknown): string {
  const raw = text(value, "");
  const match = raw.match(/(?:T|\s)(\d{2}:\d{2})/);
  return match?.[1] || (/^\d{2}:\d{2}$/.test(raw) ? raw : "");
}

function stayDateLine(item: TravelStayRecommendation): string {
  const pieces = [];
  if (item.check_in) pieces.push(`${item.check_in} 入住`);
  if (item.check_out) pieces.push(`${item.check_out} 退房`);
  if (Number(item.nights) > 0) pieces.push(`${item.nights} 晚`);
  return pieces.join(" · ") || "入住日期待确认";
}

function stayPriceLine(item: TravelStayRecommendation): string {
  const observed = Number(item.observed_price_per_night_cny);
  if (Number.isFinite(observed) && observed > 0) return `指定日期观察价 ${money(observed)}/晚`;
  const estimate = Number(item.planning_estimate_per_night_cny);
  if (Number.isFinite(estimate) && estimate > 0) return `规划估算 ${money(estimate)}/晚（非实时房价）`;
  return "本次没有取得可核验房价";
}

function evidenceLabels(ids: string[]): string[] {
  return ids
    .map((id) => plan.value?.evidence.find((entry) => entry.evidence_id === id))
    .filter(Boolean)
    .map((entry) => `${travelProviderLabel(entry?.provider)}：${entry?.title || "住宿信息"}`);
}

function stayIdentitySourceLine(item: TravelStayRecommendation): string {
  const ids = Array.isArray(item.evidence_ids) ? item.evidence_ids : [];
  const labels = evidenceLabels(ids);
  return labels.length ? `住宿信息来源：${labels.join("；")}` : "住宿信息来源待复核";
}

function stayPriceSourceLine(item: TravelStayRecommendation): string {
  const ids = Array.isArray(item.price_source_evidence_ids) ? item.price_source_evidence_ids : [];
  const labels = evidenceLabels(ids);
  if (labels.length) return `价格来源：${labels.join("；")}`;
  if (item.price_status === "planning_estimate") return "价格来源：规划估算，无外部实时报价";
  return "没有指定日期价格来源";
}

function weatherSummaryLine(item: Record<string, unknown>): string {
  const overview = text(item.summary || item.condition || item.overview, "");
  const minimum = Number(item.temp_min_c ?? item.temperature_min_c);
  const maximum = Number(item.temp_max_c ?? item.temperature_max_c);
  const temperature = Number.isFinite(minimum) && Number.isFinite(maximum)
    ? `${minimum}–${maximum}℃`
    : "";
  const rainProbability = Number(item.precipitation_probability_max_pct);
  const rain = Number.isFinite(rainProbability) ? `最高降水概率 ${rainProbability}%` : "";
  return [overview, temperature, rain].filter(Boolean).join(" · ") || "天气数据待补充";
}

function unknownSummary(value: string): { title: string; detail: string } {
  const normalized = String(value || "").replace(/https?:\/\/\S+/gi, "").replace(/\bmcp__\S+/gi, "").replace(/\bevidence\b/gi, "来源记录").replace(/\s+/g, " ").trim();
  if (/open-meteo|天气|预报/i.test(normalized)) return { title: "天气预报暂未确认", detail: "本次天气服务查询失败或日期不在可靠预报窗口内。建议出发前 1–2 天重新查询，再调整室内外顺序。" };
  if (/12306|车票|铁路|余票/i.test(normalized)) return { title: "车次与票价需临近出发复核", detail: "当前查询不能作为最终余票或票价承诺；请在开售后或出发前重新查询 12306。" };
  if (/高德|地图|poi|路线/i.test(normalized)) return { title: "部分地点或路线缺少可打开来源", detail: "现有结果仍可用于位置和行程顺序参考，实际导航距离、耗时和开放状态请出发前复核。" };
  if (/小红书|公开笔记|社区|避坑/i.test(normalized) && /搜索级|摘要|未逐篇|未抽取正文/i.test(normalized)) return { title: "社区经验已有搜索摘要，原文仍需复核", detail: "本次已取得公开笔记标题和筛选摘要，但没有逐篇读取完整正文；不要把摘要当成已核实事实。" };
  if (/小红书|公开笔记|社区|避坑/i.test(normalized)) return { title: "社区经验暂未补充", detail: "本次只读搜索未得到可展示笔记。可减少组合关键词，按单个景点或住宿区域重新查询。" };
  if (/住宿|酒店|房价|房态/i.test(normalized)) return { title: "住宿价格与房态待确认", detail: "当前只推荐住宿区域，不承诺实时房价或房态；确定预算后再到预订平台复核。" };
  return { title: normalized.slice(0, 80) || "有一项信息待复核", detail: "该信息尚无足够可靠的实时来源，建议预订或出发前重新查询。" };
}

async function handoffToChat(question: string) {
  saveChatHandoff(question);
  await router.push("/");
}
</script>

<template>
  <div class="travel-page" :class="{ 'travel-left-collapsed': leftCollapsed, 'travel-inspector-open': formInspectorOpen }">
    <header class="travel-topbar glass-panel">
      <a class="brand-lockup compact" href="/" @click.prevent="router.push('/')"><img :src="'/static/zhice-logo-a.png'" alt="" /><strong>ZhiCe Travel</strong></a>
      <nav><button type="button" @click="router.push('/')"><ArrowLeft :size="16" />返回聊天</button><QuickPreferences /></nav>
    </header>

    <div class="travel-workspace">
      <aside class="travel-sidebar" :class="{ collapsed: leftCollapsed }">
        <button v-if="leftCollapsed" class="travel-sidebar-expand icon-button" type="button" aria-label="展开我的计划" @click="leftCollapsed = false"><PanelLeftOpen :size="18" /></button>
        <section v-else class="travel-saved">
          <header><div><span class="eyebrow">我的计划</span><h2>最近生成</h2></div><div class="travel-saved-actions"><button class="icon-button" type="button" aria-label="刷新" @click="travel.refresh"><RefreshCw :size="16" /></button><button class="icon-button" type="button" aria-label="收起我的计划" @click="leftCollapsed = true"><PanelLeftClose :size="16" /></button></div></header>
          <button class="travel-new-button" type="button" :title="travel.intakeBusy ? '上一条回复会继续留在原计划中' : travel.generating ? '当前计划会继续在后台生成' : ''" @click="travel.startNew"><Plus :size="16" />新建旅行计划</button>
          <p v-if="travel.loading && !travel.workItems.length">正在读取…</p>
          <p v-else-if="!travel.workItems.length" class="travel-empty-copy">还没有旅行计划或草稿。</p>
          <button v-for="item in travel.workItems" :key="item.session_id" :class="['travel-saved-row', { active: item.session_id === travel.sessionId || item.plan_id === travel.activeId }]" type="button" @click="travel.openWorkItem(item)">
            <span><strong>{{ item.title }}</strong><small><em :data-status="item.status">{{ workStatusLabel(item.status) }}</em> · {{ item.updated_at.slice(0, 10) }}</small></span>
            <span role="button" tabindex="0" aria-label="删除旅行任务" @click.stop="removeWorkItem(item)" @keydown.enter.stop="removeWorkItem(item)"><Trash2 :size="15" /></span>
          </button>
        </section>
      </aside>

      <main class="travel-results">
        <div ref="resultsScroll" class="travel-results-scroll">
          <TravelPlanForm :busy="travel.generating || Boolean(travel.candidateReview)" :intake-busy="travel.intakeBusy" :clarification-questions="travel.clarificationQuestions" :restored-conversation="travel.conversation" :restored-draft="travel.activeDraft" :handoff-question="travel.handoffQuestion" :history-mode="Boolean(plan)" @intake-message="travel.sendIntake" @submit="travel.generate" @details-change="formInspectorOpen = $event" @handoff-chat="handoffToChat" @dismiss-handoff="travel.handoffQuestion = ''" />
          <p v-if="travel.conversationLoading" class="travel-conversation-inline-status">正在恢复需求问答…</p>
          <p v-else-if="travel.conversationError" class="travel-conversation-inline-status error">需求问答暂时无法恢复：{{ travel.conversationError }}。完整计划仍可正常查看。</p>
          <div ref="progressSection" class="travel-progress-anchor">
            <TravelProgress :stage="travel.stage" :status-text="travel.statusText" :active="travel.generating" :items="travel.progressItems" :candidate-review="travel.candidateReview" :candidate-busy="travel.candidateSelecting" :travel-tone="travelTone" @choose-candidate="chooseCandidate" />
          </div>
          <div v-if="travel.error" class="form-error travel-error-recovery">
            <span>{{ travel.error }}</span>
            <button v-if="travel.candidateReview?.status === 'selected' && !travel.generating" class="primary-button" type="button" @click="travel.retrySelectedCandidate">继续完成当前方案</button>
            <button v-else-if="canResumePlanning" class="primary-button" type="button" title="保留当前计划和已完成结果，只补做失败或未完成的步骤" @click="resumePlanning"><RefreshCw :size="15" />继续未完成步骤</button>
          </div>
          <template v-if="plan">
          <section class="travel-plan-hero travel-card">
            <div><span class="eyebrow">旅行规划 · {{ travelPlanningModeLabel(plan.request.planning_mode) }}</span><h1>{{ plan.request.origin }} → {{ plan.request.destinations.join(' / ') }}</h1><p>{{ plan.request.start_date }} 至 {{ plan.request.end_date }} · {{ plan.request.duration_days }} 天 · {{ plan.request.travellers.reduce((sum, item) => sum + item.count, 0) }} 人</p></div>
            <div class="travel-plan-actions"><span>{{ plan.generated_at }}</span><TravelSourcesDrawer :evidence="plan.evidence" /></div>
          </section>

          <section class="travel-comparison-grid">
            <article class="travel-card"><header><span class="eyebrow">交通方案</span><h2>车次、时间与价格</h2></header><div class="travel-option-list"><div v-for="(item, index) in plan.transport_options" :key="index"><strong>{{ text(item.title || item.name, travelTransportModeLabel(item.mode) || `方案 ${index + 1}`) }}</strong><p>{{ transportRoute(item) }}</p><small>{{ transportMeta(item) }}</small></div></div></article>
            <article class="travel-card"><header><span class="eyebrow">住宿建议</span><h2>酒店、日期与价格依据</h2></header><div class="travel-option-list"><div v-for="(item, index) in plan.stay_recommendations" :key="index"><strong>{{ text(item.hotel_name || item.suggested_poi || item.name || item.area, `住宿 ${index + 1}`) }}</strong><p>{{ text(item.address || item.area) }} · {{ stayDateLine(item) }}</p><small class="travel-stay-price">{{ stayPriceLine(item) }}</small><small>{{ stayIdentitySourceLine(item) }}</small><small>{{ stayPriceSourceLine(item) }}</small><p>{{ travelPublicText(item.reason || item.summary) }}</p></div></div></article>
          </section>

          <TravelMap :days="plan.days" />
          <TravelTimeline :days="plan.days" />
          <TravelBudget :budget="displayedBudget" />

          <section class="travel-comparison-grid">
            <article class="travel-card"><header><span class="eyebrow">天气与雨天替代</span><h2>预报和历史气候分开看</h2></header><div class="travel-weather-list"><div v-for="(item, index) in plan.weather_summary" :key="index"><strong>{{ text(item.date || item.period, `时段 ${index + 1}`) }}</strong><p>{{ weatherSummaryLine(item) }}</p><small :data-freshness="text(item.freshness, 'unknown')">{{ travelFreshnessLabel(item.freshness) }} · {{ travelProviderLabel(item.provider) }}</small></div></div><ul><li v-for="item in plan.fallbacks" :key="item">{{ travelPublicText(item) }}</li></ul></article>
            <article class="travel-card"><header><span class="eyebrow">避坑与小众体验</span><h2>经验不冒充事实</h2></header><ul class="travel-tip-list"><li v-for="item in plan.avoidance_tips" :key="item"><Compass :size="16" />{{ item }}</li></ul></article>
          </section>

          <section class="travel-card travel-unknowns"><header><span class="eyebrow">未知项与预订前复核</span><h2>这些信息仍需要重新查询</h2></header><ul><li v-for="item in plan.unknowns" :key="item"><input type="checkbox" /><span><b>{{ unknownSummary(item).title }}</b><small>{{ unknownSummary(item).detail }}</small></span></li></ul><details v-if="plan.assumptions.length"><summary>查看规划假设</summary><ul><li v-for="item in plan.assumptions" :key="item">{{ travelPublicText(item) }}</li></ul></details></section>
          </template>
        </div>
      </main>
    </div>
  </div>
</template>
