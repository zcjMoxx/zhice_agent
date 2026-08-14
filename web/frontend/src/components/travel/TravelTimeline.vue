<script setup lang="ts">
import type { TravelDay } from "@/api/types";
defineProps<{ days: TravelDay[] }>();
const money = (value: number) => new Intl.NumberFormat("zh-CN", { style: "currency", currency: "CNY", maximumFractionDigits: 0 }).format(value);
</script>

<template>
  <section class="travel-card travel-timeline-card">
    <header><span class="eyebrow">每日时间线</span><h2>把路上的时间也算进去</h2></header>
    <article v-for="(day, index) in days" :key="day.date" class="travel-day">
      <div class="travel-day-heading"><span>Day {{ index + 1 }}</span><div><h3>{{ day.city_or_area }}</h3><small>{{ day.date }} · 强度 {{ day.intensity_score.toFixed(1) }}/10 · {{ money(day.daily_budget) }}</small></div></div>
      <div class="travel-activities">
        <div v-for="activity in day.activities" :key="`${activity.start}-${activity.place}`" class="travel-activity">
          <time>{{ activity.start }}<br />{{ activity.end }}</time>
          <span class="travel-activity-dot" />
          <div><strong>{{ activity.place }}</strong><p>{{ activity.reason }}</p><small v-if="activity.opening_hours">开放时间：{{ activity.opening_hours }}</small></div>
        </div>
      </div>
      <div v-if="day.route_segments.length" class="travel-route-list">
        <div v-for="(segment, routeIndex) in day.route_segments" :key="routeIndex"><b>{{ segment.mode }}</b><span>{{ segment.from }} → {{ segment.to }}</span><small>{{ segment.duration }} 分钟 · {{ segment.distance }} 公里 · {{ segment.source }}</small></div>
      </div>
      <div class="travel-day-notes">
        <p v-if="day.meal_suggestions.length"><b>吃什么：</b>{{ day.meal_suggestions.join('、') }}</p>
        <p v-if="day.weather_adjustment"><b>天气调整：</b>{{ day.weather_adjustment }}</p>
        <p v-if="day.fallback_plan"><b>替代方案：</b>{{ day.fallback_plan }}</p>
      </div>
    </article>
  </section>
</template>

