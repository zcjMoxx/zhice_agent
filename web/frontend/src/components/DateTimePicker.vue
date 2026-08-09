<script setup lang="ts">
import { CalendarClock, ChevronLeft, ChevronRight, X } from "@lucide/vue";
import { computed, ref } from "vue";

import type { UiLanguage } from "@/i18n";

const props = withDefaults(defineProps<{ modelValue: string; label: string; language: UiLanguage; minValue?: string }>(), { minValue: "" });
const emit = defineEmits<{ "update:modelValue": [value: string] }>();

const open = ref(false);
const panel = ref<"calendar" | "month">("calendar");
const viewYear = ref(0);
const viewMonth = ref(0);
const selectedDay = ref(1);
const hour = ref(0);
const minute = ref(0);

const chinese = computed(() => props.language === "zh-CN");
const monthTitle = computed(() => chinese.value
  ? `${viewYear.value} 年 ${viewMonth.value} 月`
  : new Intl.DateTimeFormat("en", { month: "long", year: "numeric" }).format(new Date(viewYear.value, viewMonth.value - 1, 1))
);
const weekLabels = computed(() => chinese.value ? ["日", "一", "二", "三", "四", "五", "六"] : ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"]);
const monthLabels = computed(() => chinese.value ? Array.from({ length: 12 }, (_, index) => `${index + 1} 月`) : Array.from({ length: 12 }, (_, index) => new Intl.DateTimeFormat("en", { month: "short" }).format(new Date(2020, index, 1))));
const days = computed(() => {
  const leading = new Date(viewYear.value, viewMonth.value - 1, 1).getDay();
  const count = new Date(viewYear.value, viewMonth.value, 0).getDate();
  return [...Array<null>(leading).fill(null), ...Array.from({ length: count }, (_, index) => index + 1)];
});
const displayValue = computed(() => props.modelValue ? props.modelValue.replace("T", " ") : (chinese.value ? "请选择日期和时间" : "Select date and time"));
const selectedValue = computed(() => {
  const pad = (part: number) => String(part).padStart(2, "0");
  return `${viewYear.value}-${pad(viewMonth.value)}-${pad(selectedDay.value)}T${pad(hour.value)}:${pad(minute.value)}`;
});
const beforeMinimum = computed(() => Boolean(props.minValue && selectedValue.value < props.minValue));

function show() {
  const initial = props.modelValue && (!props.minValue || props.modelValue >= props.minValue) ? props.modelValue : props.minValue;
  const parsed = initial ? new Date(initial) : new Date();
  const source = Number.isNaN(parsed.getTime()) ? new Date() : parsed;
  viewYear.value = source.getFullYear();
  viewMonth.value = source.getMonth() + 1;
  selectedDay.value = source.getDate();
  hour.value = source.getHours();
  minute.value = source.getMinutes();
  panel.value = "calendar";
  open.value = true;
}

function resetToCurrent() {
  const now = new Date();
  const minimum = props.minValue ? new Date(props.minValue) : null;
  const source = minimum && minimum.getTime() > now.getTime() ? minimum : now;
  viewYear.value = source.getFullYear();
  viewMonth.value = source.getMonth() + 1;
  selectedDay.value = source.getDate();
  hour.value = source.getHours();
  minute.value = source.getMinutes();
  panel.value = "calendar";
}

function moveMonth(offset: number) {
  const next = new Date(viewYear.value, viewMonth.value - 1 + offset, 1);
  viewYear.value = next.getFullYear();
  viewMonth.value = next.getMonth() + 1;
  selectedDay.value = Math.min(selectedDay.value, new Date(viewYear.value, viewMonth.value, 0).getDate());
}

function confirm() {
  if (beforeMinimum.value) return;
  emit("update:modelValue", selectedValue.value);
  open.value = false;
}

function chooseMonth(month: number) {
  viewMonth.value = month;
  selectedDay.value = Math.min(selectedDay.value, new Date(viewYear.value, viewMonth.value, 0).getDate());
  panel.value = "calendar";
}

function dayBeforeMinimum(day: number): boolean {
  if (!props.minValue) return false;
  const pad = (part: number) => String(part).padStart(2, "0");
  return `${viewYear.value}-${pad(viewMonth.value)}-${pad(day)}` < props.minValue.slice(0, 10);
}

function monthBeforeMinimum(month: number): boolean {
  if (!props.minValue) return false;
  return `${viewYear.value}-${String(month).padStart(2, "0")}` < props.minValue.slice(0, 7);
}
</script>

<template>
  <label class="audit-date-filter">
    <span>{{ label }}</span>
    <button class="date-picker-trigger" type="button" @click="show"><CalendarClock :size="17" /><span>{{ displayValue }}</span></button>
  </label>

  <div v-if="open" class="modal-backdrop date-picker-backdrop" @click.self="open = false">
    <section class="date-picker-dialog" role="dialog" aria-modal="true" :aria-label="chinese ? '选择日期和时间' : 'Select date and time'">
      <button class="date-picker-close" type="button" :aria-label="chinese ? '关闭' : 'Close'" @click="open = false"><X :size="19" /></button>
      <header class="date-picker-header">
        <button type="button" :aria-label="panel === 'calendar' ? (chinese ? '上个月' : 'Previous month') : (chinese ? '上一年' : 'Previous year')" @click="panel === 'calendar' ? moveMonth(-1) : viewYear--"><ChevronLeft :size="20" /></button>
        <button class="date-picker-title" type="button" @click="panel = panel === 'calendar' ? 'month' : 'calendar'">{{ panel === 'calendar' ? monthTitle : viewYear }}</button>
        <button type="button" :aria-label="panel === 'calendar' ? (chinese ? '下个月' : 'Next month') : (chinese ? '下一年' : 'Next year')" @click="panel === 'calendar' ? moveMonth(1) : viewYear++"><ChevronRight :size="20" /></button>
      </header>
      <template v-if="panel === 'calendar'">
        <div class="date-picker-week"><span v-for="item in weekLabels" :key="item">{{ item }}</span></div>
        <div class="date-picker-days">
          <span v-for="(day, index) in days" :key="`${viewYear}-${viewMonth}-${index}`">
            <button v-if="day" type="button" :class="{ active: selectedDay === day }" :disabled="dayBeforeMinimum(day)" @click="selectedDay = day">{{ day }}</button>
          </span>
        </div>
      </template>
      <div v-else class="date-picker-months">
        <button v-for="(month, index) in monthLabels" :key="month" type="button" :class="{ active: viewMonth === index + 1 }" :disabled="monthBeforeMinimum(index + 1)" @click="chooseMonth(index + 1)">{{ month }}</button>
      </div>
      <div v-if="panel === 'calendar'" class="date-picker-time">
        <CalendarClock :size="18" />
        <label><span>{{ chinese ? '时' : 'Hour' }}</span><select v-model.number="hour"><option v-for="item in 24" :key="item - 1" :value="item - 1">{{ String(item - 1).padStart(2, '0') }}</option></select></label>
        <b>:</b>
        <label><span>{{ chinese ? '分' : 'Minute' }}</span><select v-model.number="minute"><option v-for="item in 60" :key="item - 1" :value="item - 1">{{ String(item - 1).padStart(2, '0') }}</option></select></label>
      </div>
      <p v-if="panel === 'calendar' && beforeMinimum" class="date-picker-error">{{ chinese ? '结束时间不能早于开始时间' : 'End time cannot be earlier than start time' }}</p>
      <div class="date-picker-actions">
        <button type="button" @click="resetToCurrent">{{ chinese ? '清除' : 'Clear' }}</button>
        <button class="primary-button" type="button" :disabled="beforeMinimum" @click="confirm">{{ chinese ? '确定' : 'Confirm' }}</button>
      </div>
    </section>
  </div>
</template>
