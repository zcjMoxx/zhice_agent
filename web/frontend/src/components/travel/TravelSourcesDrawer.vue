<script setup lang="ts">
import { computed, ref } from "vue";
import { ExternalLink, X } from "@lucide/vue";
import type { TravelEvidence } from "@/api/types";
import { needsTravelRefresh } from "@/travel/freshness";
import { travelFreshnessLabel, travelProviderLabel } from "@/travel/sourceLabels";

const props = defineProps<{ evidence: TravelEvidence[] }>();
const open = ref(false);
const safeEvidence = computed(() => props.evidence.map((item) => ({
  ...item,
  safeUrl: safeUrl(item.source_url),
  needsRefresh: needsTravelRefresh(item),
})));
function safeUrl(value: string): string {
  try { const parsed = new URL(value); return parsed.protocol === "http:" || parsed.protocol === "https:" ? parsed.href : ""; }
  catch { return ""; }
}
const label: Record<string, string> = { official_api: "官方接口", live_query: "实时查询", official_page: "官方页面", web_article: "网页攻略", social_post: "个体体验", model_estimate: "规划估算" };
</script>

<template>
  <button class="travel-source-trigger" type="button" @click="open = true">查看 {{ evidence.length }} 条来源与时效</button>
  <Teleport to="body">
    <div v-if="open" class="travel-source-backdrop" @click.self="open = false">
      <aside class="travel-source-drawer" aria-label="来源与时效">
        <header><div><span class="eyebrow">证据抽屉</span><h2>来源、查询时间与时效</h2></div><button class="icon-button" type="button" aria-label="关闭" @click="open = false"><X :size="19" /></button></header>
        <p class="travel-source-note">社交内容只作为个体体验；页面不重新发布完整正文、图片或视频。</p>
        <div class="travel-source-list">
          <article v-for="item in safeEvidence" :key="item.evidence_id">
            <div class="travel-source-meta"><span :data-freshness="item.freshness">{{ travelFreshnessLabel(item.freshness) }}</span><b>{{ label[item.source_type] || '其他来源' }}</b><em v-if="item.needsRefresh">需要重新查询</em><small>{{ travelProviderLabel(item.provider) }}</small></div>
            <h3>{{ item.title }}</h3><p>{{ item.excerpt }}</p>
            <dl><dt>发布</dt><dd>{{ item.published_at || '未提供' }}</dd><dt>查询</dt><dd>{{ item.retrieved_at }}</dd><dt>数据时点</dt><dd>{{ item.data_as_of || '未提供' }}</dd><dt>可信度</dt><dd>{{ Math.round(item.confidence * 100) }}%</dd></dl>
            <a v-if="item.safeUrl" :href="item.safeUrl" target="_blank" rel="noopener noreferrer"><ExternalLink :size="14" />打开原链接</a>
          </article>
        </div>
      </aside>
    </div>
  </Teleport>
</template>
