<script setup lang="ts">
import { ChevronDown } from "@lucide/vue";
import { computed, onBeforeUnmount, onMounted, ref, watch } from "vue";

import type { TravelCandidateReview } from "@/api/types";
import type { TravelProgressItem, TravelProgressStage } from "@/stores/travel";
import { travelProviderLabel } from "@/travel/sourceLabels";

const props = defineProps<{ stage: TravelProgressStage; statusText?: string; active?: boolean; items?: TravelProgressItem[]; candidateReview?: TravelCandidateReview | null; candidateBusy?: boolean; travelTone?: string }>();
const emit = defineEmits<{ chooseCandidate: [candidateId: string] }>();
const expanded = ref(Boolean(props.active || props.candidateReview));
const decisionExpanded = ref(false);
const now = ref(Date.now());
let elapsedTimer: ReturnType<typeof setInterval> | null = null;
const stages: Array<{ key: TravelProgressStage; label: string }> = [
  { key: "requirements", label: "需求" },
  { key: "data", label: "基础数据" },
  { key: "guides", label: "攻略" },
  { key: "solve", label: "求解" },
  { key: "validate", label: "校验" },
  { key: "complete", label: "完成" },
];
const index = () => stages.findIndex((item) => item.key === props.stage);
const finalizationItem = computed(() => (props.items || [])
  .filter((item) => (
    item.status === "running"
    && item.startedAt
    && (item.id.startsWith("finalizing-") || item.id.startsWith("retry-finalizing-"))
  ))
  .reduce<TravelProgressItem | undefined>((latest, item) => (
    !latest || (item.startedAt || 0) > (latest.startedAt || 0) ? item : latest
  ), undefined));
const elapsedText = computed(() => {
  const startedAt = finalizationItem.value?.startedAt;
  if (!startedAt || !props.active) return "";
  const seconds = Math.max(0, Math.floor((now.value - startedAt) / 1000));
  if (seconds < 60) return `已等待 ${seconds} 秒`;
  return `已等待 ${Math.floor(seconds / 60)} 分 ${seconds % 60} 秒`;
});
const laneDefinitions = [
  { key: "lodging", label: "住宿与房价", waiting: "等待携程日期价格" },
  { key: "transport", label: "交通路线", waiting: "等待车次与市内路线" },
  { key: "validation", label: "最终校验", waiting: "待资料补齐后校验" },
] as const;
const laneState = (lane: typeof laneDefinitions[number]["key"]) => {
  const item = [...(props.items || [])].reverse().find((entry) => entry.lane === lane);
  if (!item) return { status: "waiting", detail: laneDefinitions.find((entry) => entry.key === lane)?.waiting || "等待开始" };
  return {
    status: item.status,
    detail: item.status === "running" ? item.title : item.detail || item.title,
  };
};
onMounted(() => { elapsedTimer = setInterval(() => { now.value = Date.now(); }, 1000); });
onBeforeUnmount(() => { if (elapsedTimer) clearInterval(elapsedTimer); });
watch(() => props.active, (active) => { if (active) expanded.value = true; });
watch(() => props.candidateReview, (review) => { if (review) expanded.value = true; });
watch(() => props.stage, (stage) => { if (stage === "complete" && !props.active) expanded.value = false; });
const candidateTitle = (candidate: TravelCandidateReview["candidates"][number]) => {
  if (candidate.strategy_label) return candidate.strategy_label;
  const first = candidate.days[0];
  const places = first?.places.slice(0, 2).join("、") || "行程方案";
  return first?.city_or_area ? `${first.city_or_area} · ${places}` : places;
};
const selectedCandidate = computed(() => props.candidateReview?.candidates.find(
  (candidate) => candidate.candidate_id === props.candidateReview?.selected_candidate_id,
) || null);
const selectionWasAutomatic = computed(() => (
  props.candidateReview?.status === "selected" && props.candidateReview.candidates.length === 1
));
const candidateWarningLabels: Record<string, string> = {
  DAILY_INTENSITY_HIGH: "个别日期强度偏高",
  DAILY_ROUTE_DISTANCE_HIGH: "个别日期路程偏长",
  EVIDENCE_COVERAGE_LOW: "部分信息仍需复核",
  OPENING_HOURS_CONFLICT: "部分到访时间需复核营业安排",
  ACTIVITY_TIME_INVALID: "部分活动时间需调整",
  ACTIVITY_OVERLAP: "部分活动时间存在重叠",
  DAILY_TIME_LIMIT_EXCEEDED: "个别日期安排偏满",
  CROSS_CITY_ROUTE_MISSING: "跨区域接驳仍需补充",
  ROUTE_BACKTRACK: "部分路线存在折返",
  DAILY_INTENSITY_EXCEEDED: "个别日期强度过高",
  BUDGET_LOWER_EXCEEDS_HARD_LIMIT: "最低预算可能超过上限",
  BUDGET_EXPECTED_TIGHTENED_TO_HARD_LIMIT: "已按总预算压缩餐饮与市内交通余量",
};
const candidateWarnings = (warnings: string[]) => [...new Set(warnings.map((item) => {
  const normalized = String(item || "").trim();
  if (!normalized) return "";
  if (candidateWarningLabels[normalized]) return candidateWarningLabels[normalized];
  return /^[A-Z][A-Z0-9_]+$/.test(normalized) ? "部分安排仍需复核" : normalized;
}).filter(Boolean))].join("；");
</script>

<template>
  <section v-if="active || statusText || items?.length || candidateReview?.candidates.length" class="travel-progress" aria-live="polite">
    <button class="travel-progress-toggle" type="button" :aria-expanded="expanded" @click="expanded = !expanded">
      <span><strong>规划过程</strong><small>{{ statusText || '等待开始' }}</small></span>
      <ChevronDown :size="18" :class="{ rotated: expanded }" />
    </button>
    <section v-if="selectedCandidate" class="travel-candidate-decision" aria-label="已选行程方案">
      <div class="travel-candidate-decision-main">
        <span>✓</span><div><small>{{ selectionWasAutomatic ? '无需方案取舍' : '已选择方案' }}</small><strong>{{ candidateTitle(selectedCandidate) }}</strong><p>{{ selectedCandidate.core_tradeoff || (selectionWasAutomatic ? '时间充足，主要兴趣点可以完整覆盖。' : '正在按所选方向完善住宿与路线。') }}</p></div>
      </div>
      <p v-if="selectedCandidate.warnings.length" class="travel-candidate-decision-warning">{{ candidateWarnings(selectedCandidate.warnings) }}</p>
      <div class="travel-candidate-decision-meta"><span>{{ travelTone || '旅行基调未记录' }}</span><span>约 ¥{{ Math.round(selectedCandidate.budget.expected) }}</span><span>通勤 {{ Math.round(selectedCandidate.route_minutes) }} 分钟</span></div>
      <button v-if="candidateReview && candidateReview.candidates.length > 1" type="button" :aria-expanded="decisionExpanded" @click="decisionExpanded = !decisionExpanded">{{ decisionExpanded ? '收起方案对比' : '查看原方案对比' }}</button>
      <div v-if="decisionExpanded && candidateReview" class="travel-candidate-grid readonly">
        <article v-for="candidate in candidateReview.candidates" :key="candidate.candidate_id" :class="['travel-candidate-card', { recommended: candidate.candidate_id === candidateReview.selected_candidate_id }]">
          <header><div><b>{{ candidateTitle(candidate) }}</b><span v-if="candidate.candidate_id === candidateReview.selected_candidate_id">已选</span></div><strong>约 ¥{{ Math.round(candidate.budget.expected) }}</strong></header>
          <p v-if="candidate.core_tradeoff">{{ candidate.core_tradeoff }}</p>
          <div class="travel-candidate-metrics"><span>预算 ¥{{ Math.round(candidate.budget.lower) }}–{{ Math.round(candidate.budget.upper) }}</span><span>路程 {{ Math.round(candidate.route_minutes) }} 分钟</span><span>日均强度 {{ (candidate.daily_intensity_scores.reduce((sum, value) => sum + value, 0) / Math.max(candidate.daily_intensity_scores.length, 1)).toFixed(1) }}</span></div>
        </article>
      </div>
    </section>
    <div v-show="expanded" class="travel-progress-body">
      <div class="travel-progress-track">
      <div v-for="(item, itemIndex) in stages" :key="item.key" :class="['travel-progress-step', { done: itemIndex < index() || stage === 'complete', active: item.key === stage }]">
        <span>{{ itemIndex + 1 }}</span><b>{{ item.label }}</b>
      </div>
      </div>
      <p v-if="active || statusText">{{ statusText || '正在生成旅行计划…' }}</p>
      <div v-if="elapsedText" class="travel-progress-wait"><strong>{{ elapsedText }}</strong><span>后台仍在工作，完成后会自动展示，无需刷新。</span></div>
      <section v-if="active && stage === 'validate'" class="travel-finalization-lanes" aria-label="所选方案完善进度">
        <article v-for="lane in laneDefinitions" :key="lane.key" :data-status="laneState(lane.key).status">
          <span></span><div><strong>{{ lane.label }}</strong><small>{{ laneState(lane.key).detail }}</small></div>
        </article>
      </section>
      <ol v-if="items?.length" class="travel-progress-events">
        <li v-for="item in items" :key="item.id" :data-status="item.status">
          <span></span><div>
            <strong>{{ item.title }}</strong><small>{{ item.detail }}</small>
            <section v-if="item.result" class="travel-progress-result">
              <header>
                <div><b>{{ travelProviderLabel(item.result.provider) }}</b><small>筛选结果</small></div>
                <em v-if="item.result.resultCount">{{ item.result.resultCount }} 条</em>
              </header>
              <div v-if="item.result.query" class="travel-progress-result-query"><span>查询：</span><strong>{{ item.result.query }}</strong></div>
              <p v-if="item.result.summary">{{ item.result.summary }}</p>
              <ul v-if="item.result.items.length">
                <li v-for="result in item.result.items" :key="`${result.title}-${result.detail}`">
                  <strong>{{ result.title }}</strong><small v-if="result.detail">{{ result.detail }}</small>
                </li>
              </ul>
            </section>
          </div>
        </li>
      </ol>
      <section v-if="candidateReview?.status === 'pending' && candidateReview.candidates.length" class="travel-candidate-review" aria-label="候选行程">
        <header><div><strong>先选一个行程方向</strong><small>这是粗略骨架，确认后再补全交通、住宿、天气和逐日细节。</small></div></header>
        <div class="travel-candidate-grid">
          <article v-for="candidate in candidateReview.candidates" :key="candidate.candidate_id" :class="['travel-candidate-card', { recommended: candidate.candidate_id === candidateReview.recommended_candidate_id }]">
            <header><div><b>{{ candidateTitle(candidate) }}</b><span v-if="candidate.candidate_id === candidateReview.recommended_candidate_id">推荐</span></div><strong>约 ¥{{ Math.round(candidate.budget.expected) }}</strong></header>
            <p v-if="candidate.core_tradeoff" class="travel-candidate-tradeoff">{{ candidate.core_tradeoff }}</p>
            <ol><li v-for="day in candidate.days" :key="`${candidate.candidate_id}-${day.date}`"><b>{{ day.date.slice(5) }} · {{ day.city_or_area }}</b><small>{{ day.places.join('、') }}</small></li></ol>
            <div class="travel-candidate-metrics"><span>预算 ¥{{ Math.round(candidate.budget.lower) }}–{{ Math.round(candidate.budget.upper) }}</span><span>路程 {{ Math.round(candidate.route_minutes) }} 分钟</span><span>日均强度 {{ (candidate.daily_intensity_scores.reduce((sum, value) => sum + value, 0) / Math.max(candidate.daily_intensity_scores.length, 1)).toFixed(1) }}</span><span>证据 {{ Math.round(candidate.evidence_coverage * 100) }}%</span></div>
            <p v-if="candidate.warnings.length">{{ candidateWarnings(candidate.warnings) }}</p>
            <button type="button" :disabled="candidateBusy || active" @click="emit('chooseCandidate', candidate.candidate_id)">{{ candidate.candidate_id === candidateReview.recommended_candidate_id ? '采用推荐方案' : '选择这个方案' }}</button>
          </article>
        </div>
      </section>
    </div>
  </section>
</template>
