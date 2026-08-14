<script setup lang="ts">
defineProps<{ budget: { lower: number; expected: number; upper: number; items: Array<Record<string, unknown>> } }>();
const money = (value: number) => new Intl.NumberFormat("zh-CN", { style: "currency", currency: "CNY", maximumFractionDigits: 0 }).format(value);
const text = (value: unknown) => typeof value === "string" || typeof value === "number" ? String(value) : "—";
</script>

<template>
  <section class="travel-card travel-budget-card">
    <header><span class="eyebrow">预算区间</span><h2>先看范围，再做预订复核</h2></header>
    <div class="travel-budget-range">
      <article><small>较低</small><strong>{{ money(budget.lower) }}</strong></article>
      <article class="expected"><small>预期</small><strong>{{ money(budget.expected) }}</strong></article>
      <article><small>上浮</small><strong>{{ money(budget.upper) }}</strong></article>
    </div>
    <div v-if="budget.items.length" class="travel-budget-items">
      <div v-for="(item, index) in budget.items" :key="index"><span>{{ text(item.name || item.category) }}</span><b>{{ money(Number(item.expected || 0)) }}</b></div>
    </div>
  </section>
</template>

