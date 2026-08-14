<script setup lang="ts">
import { ChevronDown } from "@lucide/vue";
import { ref, watch } from "vue";

import type { TravelCandidateReview } from "@/api/types";
import type { TravelProgressItem, TravelProgressStage } from "@/stores/travel";

const props = defineProps<{ stage: TravelProgressStage; statusText?: string; active?: boolean; items?: TravelProgressItem[]; candidateReview?: TravelCandidateReview | null; candidateBusy?: boolean }>();
const emit = defineEmits<{ chooseCandidate: [candidateId: string] }>();
const expanded = ref(Boolean(props.active || props.candidateReview));
const stages: Array<{ key: TravelProgressStage; label: string }> = [
  { key: "requirements", label: "需求" },
  { key: "data", label: "基础数据" },
  { key: "guides", label: "攻略" },
  { key: "solve", label: "求解" },
  { key: "validate", label: "校验" },
  { key: "complete", label: "完成" },
];
const index = () => stages.findIndex((item) => item.key === props.stage);
watch(() => props.active, (active) => { if (active) expanded.value = true; });
watch(() => props.candidateReview, (review) => { if (review) expanded.value = true; });
watch(() => props.stage, (stage) => { if (stage === "complete" && !props.active) expanded.value = false; });
const candidateTitle = (candidate: TravelCandidateReview["candidates"][number]) => {
  if (!/^[a-z0-9_-]+$/i.test(candidate.candidate_id)) return candidate.candidate_id;
  const first = candidate.days[0];
  const places = first?.places.slice(0, 2).join("、") || "行程方案";
  return first?.city_or_area ? `${first.city_or_area} · ${places}` : places;
};
</script>

<template>
  <section v-if="active || statusText || items?.length || candidateReview?.candidates.length" class="travel-progress" aria-live="polite">
    <button class="travel-progress-toggle" type="button" :aria-expanded="expanded" @click="expanded = !expanded">
      <span><strong>规划过程</strong><small>{{ statusText || '等待开始' }}</small></span>
      <ChevronDown :size="18" :class="{ rotated: expanded }" />
    </button>
    <div v-show="expanded" class="travel-progress-body">
      <div class="travel-progress-track">
      <div v-for="(item, itemIndex) in stages" :key="item.key" :class="['travel-progress-step', { done: itemIndex < index() || stage === 'complete', active: item.key === stage }]">
        <span>{{ itemIndex + 1 }}</span><b>{{ item.label }}</b>
      </div>
      </div>
      <p v-if="active || statusText">{{ statusText || '正在生成旅行计划…' }}</p>
      <ol v-if="items?.length" class="travel-progress-events">
        <li v-for="item in items" :key="item.id" :data-status="item.status">
          <span></span><div>
            <strong>{{ item.title }}</strong><small>{{ item.detail }}</small>
            <section v-if="item.result" class="travel-progress-result">
              <header>
                <div><b>{{ item.result.provider }}</b><small>筛选结果</small></div>
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
      <section v-if="candidateReview?.candidates.length" class="travel-candidate-review" aria-label="候选行程">
        <header><div><strong>先选一个行程方向</strong><small>这是粗略骨架，确认后再补全交通、住宿、天气和逐日细节。</small></div></header>
        <div class="travel-candidate-grid">
          <article v-for="candidate in candidateReview.candidates" :key="candidate.candidate_id" :class="['travel-candidate-card', { recommended: candidate.candidate_id === candidateReview.recommended_candidate_id }]">
            <header><div><b>{{ candidateTitle(candidate) }}</b><span v-if="candidate.candidate_id === candidateReview.recommended_candidate_id">推荐</span></div><strong>约 ¥{{ Math.round(candidate.budget.expected) }}</strong></header>
            <ol><li v-for="day in candidate.days" :key="`${candidate.candidate_id}-${day.date}`"><b>{{ day.date.slice(5) }} · {{ day.city_or_area }}</b><small>{{ day.places.join('、') }}</small></li></ol>
            <div class="travel-candidate-metrics"><span>预算 ¥{{ Math.round(candidate.budget.lower) }}–{{ Math.round(candidate.budget.upper) }}</span><span>路程 {{ Math.round(candidate.route_minutes) }} 分钟</span><span>证据 {{ Math.round(candidate.evidence_coverage * 100) }}%</span></div>
            <p v-if="candidate.warnings.length">{{ candidate.warnings.map((item) => item === 'DAILY_INTENSITY_HIGH' ? '个别日期强度偏高' : item === 'EVIDENCE_COVERAGE_LOW' ? '部分信息仍需复核' : item).join('；') }}</p>
            <button type="button" :disabled="candidateBusy || active" @click="emit('chooseCandidate', candidate.candidate_id)">{{ candidate.candidate_id === candidateReview.recommended_candidate_id ? '采用推荐方案' : '选择这个方案' }}</button>
          </article>
        </div>
      </section>
    </div>
  </section>
</template>
