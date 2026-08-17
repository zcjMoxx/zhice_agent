<script setup lang="ts">
import { CheckCircle2, ListPlus, Send, Sparkles, X } from "@lucide/vue";
import { computed, reactive, ref, watch } from "vue";

import type { TravelConversationMessage, TravelRequirementDraft } from "@/api/types";
import MarkdownMessage from "@/components/MarkdownMessage.vue";

type RequirementMessage = TravelConversationMessage;

const emit = defineEmits<{ submit: [message: string, conversation: TravelConversationMessage[], draft: TravelRequirementDraft]; intakeMessage: [message: string]; detailsChange: [open: boolean]; handoffChat: [question: string]; dismissHandoff: [] }>();
const props = withDefaults(defineProps<{
  busy?: boolean;
  intakeBusy?: boolean;
  clarificationQuestions?: string[];
  restoredConversation?: TravelConversationMessage[];
  restoredDraft?: TravelRequirementDraft | null;
  handoffQuestion?: string;
  historyMode?: boolean;
}>(), { clarificationQuestions: () => [], restoredConversation: () => [], restoredDraft: null, handoffQuestion: "" });

const detailsOpen = ref(false);
const detailsSource = ref<"manual" | "model">("manual");
const startDateDefault = ref(false);
const endDateDefault = ref(false);
const conversation = ref<RequirementMessage[]>([]);
const hasDraft = ref(false);
const tonePickerRevealed = ref(false);
const form = reactive({
  naturalInput: "",
  origin: "",
  destinations: "",
  startDate: "",
  endDate: "",
  travellerType: "",
  travellerCount: null as number | null,
  budget: null as number | null,
  budgetLevel: "",
  transport: "",
  stay: "",
  interests: "",
  pace: "",
  mode: "",
  constraints: "",
});
const toneOptions = [
  { value: "经济实惠", title: "经济实惠", price: "住宿约 ¥100–250/晚", detail: "公共交通优先，把预算留给更多体验" },
  { value: "舒适均衡", title: "舒适均衡", price: "住宿约 ¥250–450/晚", detail: "控制折返，必要时短途打车" },
  { value: "轻松品质", title: "轻松品质", price: "住宿约 ¥450–700/晚", detail: "减少换乘和长距离步行，节奏更松" },
] as const;

const durationDays = computed(() => {
  const start = Date.parse(`${form.startDate}T00:00:00Z`);
  const end = Date.parse(`${form.endDate}T00:00:00Z`);
  if (!Number.isFinite(start) || !Number.isFinite(end) || end < start) return 0;
  return Math.floor((end - start) / 86400000) + 1;
});
const missingFields = computed(() => [
  !form.origin.trim() && "出发地",
  !form.destinations.trim() && "目的地",
  !form.startDate && "开始日期",
  !form.endDate && "结束日期",
  (!form.travellerCount || form.travellerCount < 1) && "人数",
  !form.budgetLevel && "旅行基调",
  form.startDate && form.endDate && !durationDays.value && "有效日期范围",
].filter(Boolean) as string[]);
const readyToGenerate = computed(() => missingFields.value.length === 0);
const conversationReady = computed(() => hasDraft.value && readyToGenerate.value);
const tonePrerequisitesReady = computed(() => [
  form.origin.trim(),
  form.destinations.trim(),
  form.startDate,
  form.endDate,
  form.travellerCount && form.travellerCount > 0 ? "人数" : "",
  durationDays.value > 0 ? "日期" : "",
].every(Boolean));
const tonePrompted = computed(() => (
  !props.historyMode
  && !form.budgetLevel
  && tonePrerequisitesReady.value
  && conversation.value.some((message) => message.role === "assistant")
));
const showTonePicker = computed(() => (
  !props.historyMode
  && !props.busy
  && !props.intakeBusy
  && !form.budgetLevel
  && tonePrerequisitesReady.value
  && (tonePickerRevealed.value || tonePrompted.value)
));
watch(detailsOpen, (open) => emit("detailsChange", open));
watch(
  () => props.restoredConversation,
  (messages) => {
    conversation.value = messages.map((message) => ({ ...message }));
    hasDraft.value = Boolean(props.restoredDraft);
    form.naturalInput = "";
    if (!messages.length) tonePickerRevealed.value = false;
  },
  { deep: true, immediate: true },
);
watch(
  () => props.restoredDraft,
  (draft) => {
    if (!draft) {
      if (!conversation.value.length) resetForm();
      return;
    }
    applyDraft(draft);
    hasDraft.value = true;
  },
  { deep: true, immediate: true },
);
watch(
  () => props.clarificationQuestions,
  (questions) => {
    const normalized = questions.filter((item) => item.trim()).slice(0, 6);
    if (!normalized.length) return;
    conversation.value.push({ role: "assistant", content: friendlyQuestions(normalized) });
    hasDraft.value = false;
  },
  { deep: true },
);

function openManualDetails() {
  if (props.busy || props.intakeBusy) return;
  detailsSource.value = hasDraft.value ? "model" : "manual";
  if (!hasDraft.value) applyBeijingDateDefaults();
  detailsOpen.value = true;
}

function openReviewDetails() {
  if (props.busy || props.intakeBusy) return;
  detailsSource.value = "model";
  detailsOpen.value = true;
}

function sendNaturalMessage() {
  const text = form.naturalInput.trim();
  if (!text || props.busy || props.intakeBusy || props.historyMode) return;
  form.naturalInput = "";
  const toneContext = form.budgetLevel ? `（旅行基调：${form.budgetLevel}）` : "";
  emit("intakeMessage", `${text}${toneContext}`);
}

function friendlyQuestions(questions: string[]) {
  if (questions.length === 1) return questions[0];
  return `好呀，再告诉我这几件事就可以继续规划了：\n${questions.map((question, index) => `${index + 1}. ${question}`).join("\n")}`;
}

function handleComposerKeydown(event: KeyboardEvent) {
  if (event.key !== "Enter" || event.shiftKey || event.isComposing) return;
  event.preventDefault();
  void sendNaturalMessage();
}

function applyDraft(draft: TravelRequirementDraft) {
  form.origin = draft.origin;
  form.destinations = draft.destinations.join(", ");
  form.startDate = draft.start_date;
  form.endDate = draft.end_date;
  startDateDefault.value = false;
  endDateDefault.value = false;
  form.travellerType = draft.traveller_type;
  form.travellerCount = draft.traveller_count;
  form.budget = draft.budget_total_cny;
  form.budgetLevel = ({ economy: "经济实惠", balanced: "舒适均衡", comfortable: "轻松品质" } as Record<string, string>)[draft.budget_level] || "";
  form.transport = draft.transport_preferences.join(", ");
  form.stay = draft.stay_preferences.join(", ");
  form.interests = draft.interest_tags.join(", ");
  form.pace = draft.pace;
  form.mode = draft.planning_mode;
  form.constraints = draft.hard_constraints.join(", ");
}

function resetForm() {
  form.naturalInput = "";
  form.origin = "";
  form.destinations = "";
  form.startDate = "";
  form.endDate = "";
  form.travellerType = "";
  form.travellerCount = null;
  form.budget = null;
  form.budgetLevel = "";
  form.transport = "";
  form.stay = "";
  form.interests = "";
  form.pace = "";
  form.mode = "";
  form.constraints = "";
  startDateDefault.value = false;
  endDateDefault.value = false;
}

function selectTone(value: string) {
  if (props.busy || props.intakeBusy || !tonePrerequisitesReady.value) return;
  form.budgetLevel = value;
  tonePickerRevealed.value = false;
  conversation.value = [
    ...conversation.value.filter((message, index, items) => !(
      message.role === "assistant"
      && index === items.length - 1
      && /旅行基调|经济实惠|舒适均衡|轻松品质/.test(message.content)
    )),
    { role: "user", content: `已选择旅行基调：${value}` },
  ];
  confirmAndSubmit();
}

function applyBeijingDateDefaults() {
  const today = beijingToday();
  if (!form.startDate) {
    form.startDate = today;
    startDateDefault.value = true;
  }
  if (!form.endDate || form.endDate < form.startDate) {
    form.endDate = form.startDate;
    endDateDefault.value = true;
  }
}

function updateStartDate(event: Event) {
  const value = (event.target as HTMLInputElement).value;
  form.startDate = value;
  startDateDefault.value = false;
  if (!form.endDate || form.endDate < value) {
    form.endDate = value;
    endDateDefault.value = true;
  }
}

function updateEndDate(event: Event) {
  form.endDate = (event.target as HTMLInputElement).value;
  endDateDefault.value = false;
}

function beijingToday() {
  const parts = new Intl.DateTimeFormat("en-US", {
    timeZone: "Asia/Shanghai",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).formatToParts(new Date());
  const value = Object.fromEntries(parts.map((part) => [part.type, part.value]));
  return `${value.year}-${value.month}-${value.day}`;
}

function chineseDate(value: string) {
  const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(value);
  return match ? `${match[1]}年${Number(match[2])}月${Number(match[3])}日` : "请选择日期";
}

function confirmAndSubmit() {
  if (props.busy || props.intakeBusy) return;
  if (!readyToGenerate.value || !form.travellerCount) { detailsOpen.value = true; return; }
  detailsOpen.value = false;
  const reviewedConversation = conversation.value.length
    ? conversation.value.slice(-20).map((item) => ({ ...item, content: item.content.slice(0, 2000) }))
    : [{ role: "user" as const, content: manualRequirementSummary() }];
  emit("submit", manualRequirementSummary(), reviewedConversation, draftFromForm());
}

function draftFromForm(): TravelRequirementDraft {
  const budgetLevels: Record<string, TravelRequirementDraft["budget_level"]> = {
    经济实惠: "economy",
    舒适均衡: "balanced",
    轻松品质: "comfortable",
  };
  return {
    intent: "travel_requirement",
    intent_topic: "",
    origin: form.origin.trim(),
    destinations: form.destinations.split(/[,，、/]/).map((item) => item.trim()).filter(Boolean),
    start_date: form.startDate,
    end_date: form.endDate,
    traveller_type: form.travellerType.trim(),
    traveller_count: form.travellerCount,
    budget_total_cny: form.budget,
    budget_level: budgetLevels[form.budgetLevel] || "",
    transport_preferences: form.transport.split(/[,，、]/).map((item) => item.trim()).filter(Boolean),
    stay_preferences: form.stay.split(/[,，、]/).map((item) => item.trim()).filter(Boolean),
    interest_tags: form.interests.split(/[,，、]/).map((item) => item.trim()).filter(Boolean),
    pace: form.pace as TravelRequirementDraft["pace"],
    planning_mode: form.mode as TravelRequirementDraft["planning_mode"],
    hard_constraints: form.constraints.split(/[,，、]/).map((item) => item.trim()).filter(Boolean),
  };
}

function manualRequirementSummary() {
  return `我已确认旅行条件：${form.origin.trim()}出发，前往${form.destinations.trim()}，${form.startDate} 至 ${form.endDate}，共 ${durationDays.value} 天，${form.travellerCount} 人；旅行基调为${form.budgetLevel}。`;
}
</script>

<template>
  <form class="travel-form travel-composer" @submit.prevent="sendNaturalMessage">
    <header class="travel-composer-header">
      <div><span class="eyebrow"><Sparkles :size="14" /> {{ historyMode ? '旅行需求' : '新建计划' }}</span><h2>{{ historyMode ? '生成这份计划时的需求问答' : '用一句话描述你的旅行' }}</h2><p>{{ historyMode ? '这段记录与当前计划一起保存。' : '信息不清楚时我会继续询问，确认后才开始规划。' }}</p></div>
      <button v-if="!historyMode" class="travel-supplement-button" type="button" :disabled="busy || intakeBusy" :aria-expanded="detailsOpen" @click="openManualDetails"><ListPlus :size="15" />补充数据</button>
    </header>

    <div v-if="conversation.length" class="travel-requirement-dialog" aria-live="polite">
      <div v-for="(message, index) in conversation" :key="index" :class="['travel-requirement-message', message.role]">
        <span>{{ message.role === 'user' ? '你' : '旅行助手' }}</span>
        <p v-if="message.role === 'user'" class="travel-requirement-bubble">{{ message.content }}</p>
        <MarkdownMessage v-else class="travel-requirement-bubble" :content="message.content" />
      </div>
      <section v-if="showTonePicker" class="travel-tone-picker" aria-label="旅行基调">
        <header><div><strong>最后选择这次旅行的基调</strong><small>其他必要条件已经齐全；选择后会立即开始规划。</small></div></header>
        <div>
          <button v-for="tone in toneOptions" :key="tone.value" type="button" :class="{ selected: form.budgetLevel === tone.value }" :aria-pressed="form.budgetLevel === tone.value" :disabled="busy || intakeBusy" @click="selectTone(tone.value)">
            <strong>{{ tone.title }}</strong><span>{{ tone.price }}</span><small>{{ tone.detail }}</small>
          </button>
        </div>
        <p class="travel-tone-optional">还有精确预算、交通、住宿、兴趣、节奏或硬约束？请先点“补充信息”；不填会按所选基调采用合理默认。</p>
      </section>
      <div v-if="conversationReady && !historyMode && !busy && !intakeBusy" class="travel-requirement-ready">
        <CheckCircle2 :size="18" />
        <span>关键信息已经齐全</span>
        <button type="button" @click="openReviewDetails">补充信息</button>
        <button class="primary-button" type="button" :disabled="busy || intakeBusy" @click="confirmAndSubmit">确认并开始规划</button>
      </div>
      <div v-if="handoffQuestion" class="travel-chat-handoff">
        <strong>这个问题更适合在智策 Agent 主聊天中交流</strong>
        <span>返回后会把原问题放进输入框，由你确认后再发送。</span>
        <div><button class="primary-button" type="button" @click="emit('handoffChat', handoffQuestion)">携带问题返回主聊天</button><button type="button" @click="emit('dismissHandoff')">继续规划旅行</button></div>
      </div>
    </div>

    <div v-if="!historyMode" class="travel-chat-input">
      <textarea v-model="form.naturalInput" rows="3" :placeholder="conversation.length ? '回答上面的问题，或继续补充、修正旅行信息…' : '例如：国庆期间，重庆出发到云南大理游玩 5 天'" @keydown="handleComposerKeydown" />
      <button
        class="primary-button"
        type="button"
        :disabled="busy || intakeBusy || !form.naturalInput.trim()"
        aria-label="发送旅行需求"
        :title="!form.naturalInput.trim() ? '输入旅行需求后发送' : '发送旅行需求'"
        @click="sendNaturalMessage"
      >
        <Send :size="18" />
      </button>
    </div>
    <div v-if="intakeBusy" class="travel-extracting">旅行助手正在思考…</div>

    <aside v-show="detailsOpen" class="travel-form-details" aria-label="旅行条件表单">
      <header><div><span class="eyebrow">生成前确认</span><h2>{{ detailsSource === 'manual' ? '手动填写旅行条件' : '检查旅行条件' }}</h2></div><button class="icon-button" type="button" aria-label="收起旅行条件" @click="detailsOpen = false"><X :size="17" /></button></header>
      <p>{{ detailsSource === 'manual' ? '请填写旅行信息，也可以补充预算和偏好。确认无误后开始规划。' : '以下内容来自刚才的问答。你可以补充或修改，确认后才开始规划。' }}</p>
      <div class="travel-form-grid">
        <label class="travel-field"><span>出发地</span><input v-model="form.origin" required /></label>
        <label class="travel-field"><span>目的地</span><input v-model="form.destinations" required /></label>
        <label class="travel-field"><span>开始日期</span><span :class="['travel-date-field', { defaulted: startDateDefault }]"><span>{{ chineseDate(form.startDate) }}</span><input :value="form.startDate" type="date" required aria-label="开始日期" @input="updateStartDate" /></span></label>
        <label class="travel-field"><span>结束日期</span><span :class="['travel-date-field', { defaulted: endDateDefault }]"><span>{{ chineseDate(form.endDate) }}</span><input :value="form.endDate" type="date" :min="form.startDate" required aria-label="结束日期" @input="updateEndDate" /></span></label>
        <label class="travel-field"><span>人群（可空）</span><input v-model="form.travellerType" placeholder="未说明时按旅行者处理" /></label>
        <label class="travel-field"><span>人数</span><input v-model.number="form.travellerCount" type="number" min="1" max="50" required /></label>
        <label class="travel-field"><span>精确总预算（可空）</span><input v-model.number="form.budget" type="number" min="100" step="100" placeholder="人民币" /></label>
        <label class="travel-field"><span>旅行基调</span><select v-model="form.budgetLevel" required><option value="">请选择</option><option>经济实惠</option><option>舒适均衡</option><option>轻松品质</option></select></label>
        <label class="travel-field"><span>交通偏好</span><input v-model="form.transport" /></label>
        <label class="travel-field"><span>住宿偏好</span><input v-model="form.stay" /></label>
        <label class="travel-field"><span>旅行节奏（可选）</span><select v-model="form.pace"><option value="">系统采用均衡</option><option value="relaxed">轻松</option><option value="balanced">均衡</option><option value="intensive">充实</option></select></label>
        <label class="travel-field"><span>规划模式（可选）</span><select v-model="form.mode"><option value="">系统采用快速模式</option><option value="quick">快速模式</option><option value="deep">深度模式</option></select></label>
        <label class="travel-field travel-field-wide"><span>兴趣</span><input v-model="form.interests" /></label>
        <label class="travel-field travel-field-wide"><span>硬约束</span><input v-model="form.constraints" /></label>
      </div>
      <div v-if="missingFields.length" class="travel-review-missing">开始前只需确认：{{ missingFields.join('、') }}</div>
      <div class="travel-inspector-actions"><button type="button" @click="detailsOpen = false">暂不规划</button><button class="primary-button" type="button" :disabled="busy || intakeBusy || !readyToGenerate" @click="confirmAndSubmit">{{ busy ? "正在启动…" : intakeBusy ? "正在更新条件…" : "确认并开始规划" }}</button></div>
    </aside>
    <footer v-if="!historyMode">
      <span></span>
      <small>生成可执行计划 · 来源与时效可核验</small>
      <strong>{{ intakeBusy ? "正在交流…" : busy ? "正在规划…" : conversationReady ? "等待确认" : "确认后开始" }}</strong>
    </footer>
  </form>
</template>
