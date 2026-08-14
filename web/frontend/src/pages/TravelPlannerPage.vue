<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import { ArrowLeft, Compass, PanelLeftClose, PanelLeftOpen, Plus, RefreshCw, Trash2 } from "@lucide/vue";
import { useRoute, useRouter } from "vue-router";

import QuickPreferences from "@/components/QuickPreferences.vue";
import TravelBudget from "@/components/travel/TravelBudget.vue";
import TravelMap from "@/components/travel/TravelMap.vue";
import TravelPlanForm from "@/components/travel/TravelPlanForm.vue";
import TravelProgress from "@/components/travel/TravelProgress.vue";
import TravelSourcesDrawer from "@/components/travel/TravelSourcesDrawer.vue";
import TravelTimeline from "@/components/travel/TravelTimeline.vue";
import { useAuthStore } from "@/stores/auth";
import { useTravelStore } from "@/stores/travel";
import { saveChatHandoff } from "@/travel/chatHandoff";

const auth = useAuthStore();
const travel = useTravelStore();
const route = useRoute();
const router = useRouter();
const plan = computed(() => travel.activePlan);
const leftCollapsed = ref(false);
const formInspectorOpen = ref(false);

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
    const unfinished = travel.workItems.find((entry) => entry.status !== "completed");
    if (unfinished) await travel.openWorkItem(unfinished);
    else travel.startNew();
  }
});

async function removeWorkItem(item: import("@/api/types").TravelWorkItem) {
  const label = item.status === "completed" ? "这份旅行计划及其需求问答记录" : "这条未完成的旅行任务";
  if (!window.confirm(`确定删除${label}？`)) return;
  await travel.removeWorkItem(item);
}

function workStatusLabel(status: import("@/api/types").TravelWorkStatus) {
  return ({ collecting: "需求收集中", running: "规划进行中", awaiting_candidate: "等待选择", failed: "规划未完成", completed: "已完成" })[status];
}

function text(value: unknown, fallback = "—"): string {
  if (typeof value === "string" && value.trim()) return value;
  if (typeof value === "number") return String(value);
  return fallback;
}

function unknownSummary(value: string): { title: string; detail: string } {
  const normalized = String(value || "").replace(/https?:\/\/\S+/gi, "").replace(/\bmcp__\S+/gi, "").replace(/\bevidence\b/gi, "来源记录").replace(/\s+/g, " ").trim();
  if (/open-meteo|天气|预报/i.test(normalized)) return { title: "天气预报暂未确认", detail: "本次天气服务查询失败或日期不在可靠预报窗口内。建议出发前 1–2 天重新查询，再调整室内外顺序。" };
  if (/12306|车票|铁路|余票/i.test(normalized)) return { title: "车次与票价需临近出发复核", detail: "当前查询不能作为最终余票或票价承诺；请在开售后或出发前重新查询 12306。" };
  if (/高德|地图|poi|路线/i.test(normalized)) return { title: "部分地点或路线缺少可打开来源", detail: "现有结果仍可用于位置和行程顺序参考，实际导航距离、耗时和开放状态请出发前复核。" };
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
        <div class="travel-results-scroll">
          <TravelPlanForm :busy="travel.generating" :intake-busy="travel.intakeBusy" :clarification-questions="travel.clarificationQuestions" :restored-conversation="travel.conversation" :restored-draft="travel.activeDraft" :handoff-question="travel.handoffQuestion" :history-mode="Boolean(plan)" @intake-message="travel.sendIntake" @submit="travel.generate" @details-change="formInspectorOpen = $event" @handoff-chat="handoffToChat" @dismiss-handoff="travel.handoffQuestion = ''" />
          <p v-if="travel.conversationLoading" class="travel-conversation-inline-status">正在恢复需求问答…</p>
          <p v-else-if="travel.conversationError" class="travel-conversation-inline-status error">需求问答暂时无法恢复：{{ travel.conversationError }}。完整计划仍可正常查看。</p>
          <TravelProgress :stage="travel.stage" :status-text="travel.statusText" :active="travel.generating" :items="travel.progressItems" :candidate-review="travel.candidateReview" :candidate-busy="travel.candidateSelecting" @choose-candidate="travel.chooseCandidate" />
          <div v-if="travel.error" class="form-error">{{ travel.error }}</div>
          <template v-if="plan">
          <section class="travel-plan-hero travel-card">
            <div><span class="eyebrow">TravelPlanV1 · {{ plan.request.planning_mode }}</span><h1>{{ plan.request.origin }} → {{ plan.request.destinations.join(' / ') }}</h1><p>{{ plan.request.start_date }} 至 {{ plan.request.end_date }} · {{ plan.request.duration_days }} 天 · {{ plan.request.travellers.reduce((sum, item) => sum + item.count, 0) }} 人</p></div>
            <div class="travel-plan-actions"><span>{{ plan.generated_at }}</span><TravelSourcesDrawer :evidence="plan.evidence" /></div>
          </section>

          <section class="travel-comparison-grid">
            <article class="travel-card"><header><span class="eyebrow">交通方案</span><h2>对比与取舍</h2></header><div class="travel-option-list"><div v-for="(item, index) in plan.transport_options" :key="index"><strong>{{ text(item.title || item.name || item.mode, `方案 ${index + 1}`) }}</strong><p>{{ text(item.reason || item.summary) }}</p><small>{{ text(item.duration) }} · {{ text(item.price || item.price_note) }} · {{ text(item.status) }}</small></div></div></article>
            <article class="travel-card"><header><span class="eyebrow">住宿区域</span><h2>位置优先，不冒充房态</h2></header><div class="travel-option-list"><div v-for="(item, index) in plan.stay_recommendations" :key="index"><strong>{{ text(item.area || item.name, `区域 ${index + 1}`) }}</strong><p>{{ text(item.reason || item.summary) }}</p><small>{{ text(item.price_note, 'POI 只证明位置与类别，价格和房态需预订前复核') }}</small></div></div></article>
          </section>

          <TravelMap :days="plan.days" />
          <TravelTimeline :days="plan.days" />
          <TravelBudget :budget="plan.budget" />

          <section class="travel-comparison-grid">
            <article class="travel-card"><header><span class="eyebrow">天气与雨天替代</span><h2>预报和历史气候分开看</h2></header><div class="travel-weather-list"><div v-for="(item, index) in plan.weather_summary" :key="index"><strong>{{ text(item.date || item.period, `时段 ${index + 1}`) }}</strong><p>{{ text(item.summary || item.condition) }}</p><small :data-freshness="text(item.freshness, 'unknown')">{{ text(item.freshness, 'unknown') }} · {{ text(item.provider) }}</small></div></div><ul><li v-for="item in plan.fallbacks" :key="item">{{ item }}</li></ul></article>
            <article class="travel-card"><header><span class="eyebrow">避坑与小众体验</span><h2>经验不冒充事实</h2></header><ul class="travel-tip-list"><li v-for="item in plan.avoidance_tips" :key="item"><Compass :size="16" />{{ item }}</li></ul></article>
          </section>

          <section class="travel-card travel-unknowns"><header><span class="eyebrow">未知项与预订前复核</span><h2>这些信息仍需要重新查询</h2></header><ul><li v-for="item in plan.unknowns" :key="item"><input type="checkbox" /><span><b>{{ unknownSummary(item).title }}</b><small>{{ unknownSummary(item).detail }}</small></span></li></ul><details v-if="plan.assumptions.length"><summary>查看规划假设</summary><ul><li v-for="item in plan.assumptions" :key="item">{{ item }}</li></ul></details></section>
          </template>
        </div>
      </main>
    </div>
  </div>
</template>
