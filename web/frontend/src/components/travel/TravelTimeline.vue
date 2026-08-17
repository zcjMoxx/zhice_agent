<script setup lang="ts">
import type { TravelDay } from "@/api/types";
import { travelSegmentSourceLabel, travelTransportModeLabel } from "@/travel/sourceLabels";
defineProps<{ days: TravelDay[] }>();
const money = (value: number) => new Intl.NumberFormat("zh-CN", { style: "currency", currency: "CNY", maximumFractionDigits: 0 }).format(value);
const walkingDistance = (meters: number) => meters < 1000
  ? `${Math.round(meters)} 米`
  : `${Number((meters / 1000).toFixed(1))} 公里`;
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
        <div v-for="(segment, routeIndex) in day.route_segments" :key="routeIndex">
          <b>{{ travelTransportModeLabel(segment.mode) }}</b><span>{{ segment.from }} → {{ segment.to }}</span><small>{{ segment.duration }} 分钟 · {{ segment.distance }} 公里 · {{ travelSegmentSourceLabel(segment.source, segment.mode) }}</small>
          <ul v-if="segment.transit_legs?.length" class="travel-transit-legs">
            <li v-for="(leg, legIndex) in segment.transit_legs" :key="`${routeIndex}-${legIndex}`">
              <strong>{{ leg.line_name }}</strong><span>{{ leg.departure_stop }} → {{ leg.arrival_stop }}</span><small v-if="leg.via_stops.length">途经 {{ leg.via_stops.join('、') }}</small>
            </li>
          </ul>
          <small v-if="Number(segment.walking_distance) > 0">步行接驳 {{ walkingDistance(Number(segment.walking_distance)) }}</small>
        </div>
      </div>
      <div class="travel-day-notes">
        <p v-if="day.meal_suggestions.length"><b>吃什么：</b>{{ day.meal_suggestions.join('、') }}</p>
        <p v-if="day.weather_adjustment"><b>天气调整：</b>{{ day.weather_adjustment }}</p>
        <p v-if="day.fallback_plan"><b>替代方案：</b>{{ day.fallback_plan }}</p>
      </div>
    </article>
  </section>
</template>
