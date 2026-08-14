import { defineStore } from "pinia";

import { api, ApiError } from "@/api/client";
import type { ChatMessage, RuntimeEventData, TravelCandidateReview, TravelConversationMessage, TravelGenerationStatus, TravelPlan, TravelPlanSummary, TravelRequirementDraft, TravelWorkItem, WsEnvelope } from "@/api/types";
import { webSocket } from "@/websocket/client";
import { useModelStore } from "./models";

export type TravelProgressStage = "requirements" | "data" | "guides" | "solve" | "validate" | "complete";
export interface TravelProgressDetail {
  provider: string;
  query: string;
  summary: string;
  resultCount: number;
  items: Array<{ title: string; detail: string }>;
}
export interface TravelProgressItem { id: string; stage: TravelProgressStage; title: string; detail: string; status: "running" | "done" | "error"; result?: TravelProgressDetail; }

const PROGRESS_CACHE_PREFIX = "zhice.travel.progress";
const MAX_CACHED_PROGRESS_ITEMS = 100;

export const useTravelStore = defineStore("travel", {
  state: () => ({
    plans: [] as TravelPlanSummary[],
    workItems: [] as TravelWorkItem[],
    activePlan: null as TravelPlan | null,
    activeId: "",
    sessionId: "",
    phase: "intake" as "intake" | "planning",
    loading: false,
    generating: false,
    intakeBusy: false,
    stage: "requirements" as TravelProgressStage,
    statusText: "",
    progressItems: [] as TravelProgressItem[],
    error: "",
    unreadCompleted: false,
    unsubscribe: null as (() => void) | null,
    initializedUserId: "",
    restoreCompleted: false,
    restorePromise: null as Promise<void> | null,
    recoveryTimer: null as ReturnType<typeof setTimeout> | null,
    clarificationQuestions: [] as string[],
    candidateReview: null as TravelCandidateReview | null,
    candidateSelecting: false,
    conversation: [] as TravelConversationMessage[],
    conversationLoading: false,
    conversationError: "",
    activeDraft: null as TravelRequirementDraft | null,
    handoffQuestion: "",
    draftSaving: false,
    draftSavePromise: null as Promise<void> | null,
  }),
  actions: {
    initialize(userId: string): Promise<void> {
      const normalized = userId.trim();
      if (!normalized) return Promise.resolve();
      if (this.initializedUserId && this.initializedUserId !== normalized) this.resetForIdentity();
      if (this.initializedUserId === normalized && this.restoreCompleted) return Promise.resolve();
      this.initializedUserId = normalized;
      this.unreadCompleted = this.readUnreadCompleted();
      if (!this.unsubscribe) this.unsubscribe = webSocket.subscribe((envelope) => this.handleEnvelope(envelope));
      if (!this.restorePromise) {
        this.restorePromise = this.restoreGeneration()
          .then((settled) => { this.restoreCompleted = settled; })
          .finally(() => { this.restorePromise = null; });
      }
      return this.restorePromise;
    },
    resetForIdentity() {
      this.unsubscribe?.();
      this.unsubscribe = null;
      this.stopRecoveryPolling();
      this.plans = [];
      this.workItems = [];
      this.activePlan = null;
      this.activeId = "";
      this.sessionId = "";
      this.phase = "intake";
      this.generating = false;
      this.intakeBusy = false;
      this.stage = "requirements";
      this.statusText = "";
      this.progressItems = [];
      this.error = "";
      this.unreadCompleted = false;
      this.initializedUserId = "";
      this.restoreCompleted = false;
      this.restorePromise = null;
      this.clarificationQuestions = [];
      this.candidateReview = null;
      this.candidateSelecting = false;
      this.conversation = [];
      this.conversationLoading = false;
      this.conversationError = "";
      this.activeDraft = null;
      this.handoffQuestion = "";
      this.draftSaving = false;
      this.draftSavePromise = null;
    },
    async refresh() {
      this.loading = true;
      this.error = "";
      this.clarificationQuestions = [];
      try {
        const [plans, workItems] = await Promise.all([api.travelPlans(), api.travelWorkItems()]);
        this.plans = plans.plans;
        this.workItems = workItems.items;
      } catch (error) {
        this.error = error instanceof Error ? error.message : "无法读取旅行计划";
      } finally {
        this.loading = false;
      }
    },
    async open(id: string) {
      if (!id) return;
      this.loading = true;
      this.error = "";
      this.clarificationQuestions = [];
      this.candidateReview = null;
      this.conversation = [];
      this.conversationError = "";
      this.activeDraft = null;
      try {
        this.activePlan = (await api.travelPlan(id)).plan;
        const sourceSessionId = this.plans.find((item) => item.plan_id === id)?.source_session_id || "";
        this.activeId = id;
        this.sessionId = sourceSessionId;
        this.generating = false;
        this.loadProgress(sourceSessionId);
        this.stage = "complete";
        this.statusText = "旅行计划已完成";
        if (!this.progressItems.some((item) => item.stage === "complete" && item.status === "done")) {
          this.recordProgress({
            id: "saved-plan-complete",
            stage: "complete",
            title: "旅行计划已完成",
            detail: "完整行程已保存",
            status: "done",
          });
        }
        if (sourceSessionId) await this.loadDraft(sourceSessionId);
        if (window.location.pathname === "/travel") window.history.replaceState({}, "", `/travel?plan=${encodeURIComponent(id)}`);
      } catch (error) {
        this.error = error instanceof Error ? error.message : "无法读取旅行计划";
      } finally {
        this.loading = false;
      }
    },
    startNew() {
      this.stopRecoveryPolling();
      this.clearPersistedSessionId();
      this.activePlan = null;
      this.activeId = "";
      this.sessionId = "";
      this.phase = "intake";
      this.generating = false;
      this.intakeBusy = false;
      this.stage = "requirements";
      this.statusText = "";
      this.progressItems = [];
      this.error = "";
      this.clarificationQuestions = [];
      this.candidateReview = null;
      this.conversation = [];
      this.conversationLoading = false;
      this.conversationError = "";
      this.activeDraft = null;
      this.loadProgress("");
      this.handoffQuestion = "";
      window.history.replaceState({}, "", "/travel");
    },
    async remove(id: string) {
      const sourceSessionId = this.plans.find((item) => item.plan_id === id)?.source_session_id || "";
      await api.deleteTravelPlan(id);
      this.plans = this.plans.filter((item) => item.plan_id !== id);
      if (this.activeId === id) {
        this.activeId = "";
        this.activePlan = null;
        this.conversation = [];
        this.conversationError = "";
        window.history.replaceState({}, "", "/travel");
      }
      if (sourceSessionId && this.sessionId === sourceSessionId) {
        this.sessionId = "";
        this.clearPersistedSessionId();
      }
    },
    async removeWorkItem(item: TravelWorkItem) {
      if (item.status === "completed" && item.plan_id) {
        await this.remove(item.plan_id);
      } else {
        await api.deleteTravelWorkItem(item.session_id);
        this.workItems = this.workItems.filter((entry) => entry.session_id !== item.session_id);
        if (this.sessionId === item.session_id) this.startNew();
      }
      await this.refresh();
    },
    async openWorkItem(item: TravelWorkItem) {
      if (item.status === "completed" && item.plan_id) {
        await this.open(item.plan_id);
        return;
      }
      this.activePlan = null;
      this.activeId = "";
      this.sessionId = item.session_id;
      this.loadProgress(item.session_id);
      this.phase = "intake";
      this.error = "";
      this.candidateReview = null;
      this.clarificationQuestions = [];
      await this.loadDraft(item.session_id);
      window.history.replaceState({}, "", `/travel?session=${encodeURIComponent(item.session_id)}`);
      if (item.status === "running" || item.status === "awaiting_candidate") {
        await this.applyGenerationStatus(await api.travelGeneration(item.session_id), true);
      } else {
        this.generating = false;
        this.stage = "requirements";
        this.statusText = item.status === "failed" ? "上次规划未完成，可以补充或修正需求后继续" : "旅行需求收集中";
        if (item.status === "failed") this.error = "上次规划未生成完整计划，请检查需求后重新开始。";
      }
    },
    async saveDraft(conversation: TravelConversationMessage[], draft?: TravelRequirementDraft) {
      const messages = conversation.slice(-20).map((item) => ({ ...item, content: item.content.slice(0, 2000) }));
      if (!messages.length) return;
      const previous = this.draftSavePromise || Promise.resolve();
      const task = previous.then(async () => {
        this.draftSaving = true;
        try {
          if (!this.sessionId) this.sessionId = await webSocket.createSession("travel");
          await api.persistTravelConversation(this.sessionId, messages, draft);
          this.conversation = messages;
          if (draft) this.activeDraft = { ...draft };
          window.history.replaceState({}, "", `/travel?session=${encodeURIComponent(this.sessionId)}`);
          await this.refresh();
        } catch (error) {
          this.conversationError = error instanceof Error ? error.message : "旅行草稿保存失败";
        } finally {
          this.draftSaving = false;
        }
      });
      const queued = task.finally(() => {
        if (this.draftSavePromise === queued) this.draftSavePromise = null;
      });
      this.draftSavePromise = queued;
      return queued;
    },
    async sendIntake(message: string) {
      const text = message.trim();
      if (!text || this.intakeBusy || this.generating || this.phase !== "intake") return;
      const models = useModelStore();
      this.intakeBusy = true;
      this.conversationError = "";
      this.conversation.push({ role: "user", content: text.slice(0, 2000) });
      try {
        if (!this.sessionId) this.sessionId = await webSocket.createSession("travel");
        window.history.replaceState({}, "", `/travel?session=${encodeURIComponent(this.sessionId)}`);
        await webSocket.sendMessage(this.sessionId, text, models.current || "auto");
      } catch (error) {
        this.intakeBusy = false;
        this.conversationError = error instanceof Error ? error.message : "旅行助手暂时无法回复";
      }
    },
    async generate(message: string, conversation: TravelConversationMessage[] = [], draft?: TravelRequirementDraft) {
      const text = message.trim();
      if (!text || this.generating) return;
      const models = useModelStore();
      this.activePlan = null;
      this.activeId = "";
      window.history.replaceState({}, "", "/travel");
      this.generating = true;
      this.stage = "requirements";
      this.statusText = "正在确认旅行需求";
      this.error = "";
      this.clarificationQuestions = [];
      this.candidateReview = null;
      this.conversation = conversation.map((item) => ({ ...item }));
      if (draft) this.activeDraft = { ...draft };
      this.conversationError = "";
      this.progressItems = [{ id: "requirements", stage: "requirements", title: "已收到旅行需求", detail: "正在提取日期、人数、预算与偏好", status: "running" }];
      try {
        if (this.draftSavePromise) await this.draftSavePromise;
        if (!this.sessionId) this.sessionId = await webSocket.createSession("travel");
        this.persistProgress();
        if (!this.activeDraft) throw new Error("请先确认旅行条件");
        await api.confirmTravelPlanning(this.sessionId, this.activeDraft);
        this.phase = "planning";
        this.persistSessionId();
        await webSocket.sendMessage(this.sessionId, "我已确认以上旅行条件，请开始正式规划。", models.current || "auto");
        this.scheduleRecoveryPoll();
      } catch (error) {
        this.generating = false;
        this.clearPersistedSessionId();
        this.error = error instanceof Error ? error.message : "旅行规划请求未能启动";
        this.recordProgress({ id: `error-${Date.now()}`, stage: this.stage, title: "规划未能启动", detail: this.error, status: "error" });
      }
    },
    async loadConversation(sessionId: string) {
      this.conversationLoading = true;
      this.conversationError = "";
      try {
        const response = await api.session(sessionId);
        this.conversation = visibleTravelConversation(response.messages);
      } catch (error) {
        this.conversation = [];
        this.conversationError = error instanceof Error ? error.message : "无法读取规划对话";
      } finally {
        this.conversationLoading = false;
      }
    },
    async loadDraft(sessionId: string) {
      this.conversationLoading = true;
      this.conversationError = "";
      try {
        const snapshot = await api.travelDraft(sessionId);
        if (this.sessionId !== sessionId) return;
        this.conversation = snapshot.messages;
        this.activeDraft = Object.keys(snapshot.draft).length ? snapshot.draft as TravelRequirementDraft : null;
        this.phase = snapshot.phase || "intake";
        this.handoffQuestion = snapshot.handoff_question || "";
      } catch (error) {
        if (this.sessionId !== sessionId) return;
        this.conversation = [];
        this.activeDraft = null;
        this.conversationError = error instanceof Error ? error.message : "无法读取旅行草稿";
      } finally {
        if (this.sessionId === sessionId) this.conversationLoading = false;
      }
    },
    handleEnvelope(envelope: WsEnvelope) {
      if (envelope.event === "socket_closed" && this.intakeBusy) {
        this.intakeBusy = false;
        this.conversationError = "连接暂时中断，正在恢复旅行对话";
        void webSocket.connect()
          .then(() => this.sessionId ? this.loadDraft(this.sessionId) : undefined)
          .catch(() => undefined);
        return;
      }
      if (envelope.event === "socket_closed" && this.generating) {
        this.statusText = "连接暂时中断，正在恢复规划状态";
        this.scheduleRecoveryPoll(0);
        void webSocket.connect().catch(() => undefined);
        return;
      }
      const envelopeSessionId = String(envelope.session_id || "");
      if (envelopeSessionId && envelopeSessionId !== this.sessionId) {
        if (envelope.event === "runtime_event") {
          const backgroundEvent = (envelope.data ?? {}) as RuntimeEventData;
          const backgroundName = String(backgroundEvent.type ?? backgroundEvent.event ?? "");
          if (backgroundName === "travel.plan_ready") this.markCompletedUnread();
          if (["travel.plan_ready", "travel.clarification_required", "travel.candidate_review_required"].includes(backgroundName)) void this.refresh();
        } else if (envelope.event === "channel_status") {
          const backgroundStatus = String((envelope.data as { type?: string })?.type || "");
          if (["done", "error", "stopped"].includes(backgroundStatus)) void this.refresh();
        }
        return;
      }
      if (!this.sessionId || envelopeSessionId !== this.sessionId) return;
      if (envelope.event === "runtime_event") {
        const event = (envelope.data ?? {}) as RuntimeEventData;
        const eventName = String(event.type ?? event.event ?? "");
        const toolName = String(event.metadata?.tool_name ?? "");
        if (eventName === "travel.intake_draft_updated") {
          const draft = event.ui_metadata?.detail_data?.draft;
          if (draft) this.activeDraft = { ...draft };
          const changedFields = event.ui_metadata?.detail_data?.changed_fields;
          if (Array.isArray(changedFields) && changedFields.length) this.handoffQuestion = "";
          return;
        }
        if (eventName === "travel.main_chat_handoff") {
          this.handoffQuestion = String(event.ui_metadata?.detail_data?.question || "").trim();
          return;
        }
        if (eventName === "travel.planning_confirmed") {
          this.phase = "planning";
          this.intakeBusy = false;
          this.generating = true;
          this.stage = "requirements";
          this.statusText = "旅行条件已确认，正在开始正式规划";
          this.error = "";
          this.conversationError = "";
          this.clarificationQuestions = [];
          this.candidateReview = null;
          this.progressItems = [{
            id: "requirements",
            stage: "requirements",
            title: "旅行条件已确认",
            detail: "正在启动地图、天气、交通与旅行资料查询",
            status: "running",
          }];
          this.persistSessionId();
          this.scheduleRecoveryPoll();
          return;
        }
        if (eventName === "travel.plan_ready") {
          const planId = String(event.metadata?.plan_id ?? "");
          if (planId) {
            this.stage = "complete";
            this.statusText = "旅行计划已完成";
            this.generating = false;
            this.conversationError = "";
            this.markCompletedUnread();
            this.clearPersistedSessionId();
            this.stopRecoveryPolling();
            this.finishProgress("complete", "旅行计划已保存", "正在打开完整行程");
            void this.open(planId).then(() => this.refresh());
          }
          return;
        }
        if (eventName === "travel.clarification_required") {
          const questions = event.ui_metadata?.detail_data?.questions;
          this.clarificationQuestions = Array.isArray(questions)
            ? questions.filter((item) => typeof item === "string" && item.trim()).slice(0, 6)
            : [];
          this.generating = false;
          this.stopRecoveryPolling();
          this.clearPersistedSessionId();
          this.statusText = "还需要确认一些旅行信息";
          this.finishProgress("requirements", "等待补充旅行信息", "补充后会重新确认并开始规划");
          return;
        }
        if (eventName === "travel.candidate_review_required") {
          const data = event.ui_metadata?.detail_data;
          const candidates = Array.isArray(data?.candidates) ? data.candidates : [];
          this.candidateReview = {
            session_id: this.sessionId,
            status: "pending",
            recommended_candidate_id: String(data?.recommended_candidate_id || ""),
            selected_candidate_id: "",
            candidates,
            created_at: "",
            updated_at: "",
          };
          this.generating = false;
          this.stopRecoveryPolling();
          this.statusText = "请选择一个候选行程";
          this.finishProgress("solve", "候选行程比较完成", "选择后会继续生成完整旅行计划");
          return;
        }
        if (!this.generating) return;
        if (event.display?.visibility === "internal" || isInternalTravelTool(toolName)) return;
        if (eventName.startsWith("skill.")) this.stage = "solve";
        else if (toolName === "finalize_travel_plan") this.stage = "validate";
        else if (eventName.startsWith("tool.") && toolName.includes("xhs")) this.stage = "guides";
        else if (eventName.startsWith("tool.")) this.stage = "data";
        let title = eventName.startsWith("skill.")
          ? skillProgressTitle(eventName)
          : eventName === "tool.started"
            ? toolProgressTitle(toolName)
            : completedToolTitle(toolName, String(event.display?.title || ""));
        let detail = String(event.display?.detail || safeToolLabel(toolName, eventName) || this.statusText);
        const result = progressDetail(event);
        const toolFailed = /error|failed/.test(eventName);
        if (toolName.includes("tavily") && toolFailed && this.progressItems.some((item) =>
          item.result?.provider.toLocaleLowerCase().includes("tavily") && item.result.resultCount > 0
        )) {
          title = "Tavily 补充检索未成功";
          detail = "前面已取得的网页资料继续保留，本次补充查询超时或未完成。";
        } else if (toolName.includes("xhs") && !toolFailed && result?.resultCount === 0) {
          title = "小红书本轮未查到结果";
          detail = "将收窄为单个景点或区域关键词，再检索一次。";
        }
        this.statusText = detail || title;
        if (eventName.startsWith("tool.") || eventName.startsWith("skill.")) {
          const status = /error|failed/.test(eventName) ? "error" : /done|completed|finished/.test(eventName) ? "done" : "running";
          this.recordProgress({
            id: String(event.tool_call_id || event.skill_run_id || `${eventName}-${event.sequence || Date.now()}`),
            stage: this.stage,
            title,
            detail,
            status,
            result,
          });
        }
        return;
      }
      if (envelope.event !== "channel_status") return;
      const data = envelope.data as { type?: string; assistant?: { content?: string }; error?: { message?: string; code?: string } };
      if (this.intakeBusy) {
        if (data.type === "error") {
          this.intakeBusy = false;
          this.conversationError = data.error?.message || "旅行助手暂时无法回复";
        } else if (data.type === "done" || data.type === "stopped") {
          this.intakeBusy = false;
          const content = String(data.assistant?.content || "").trim();
          if (content && data.type === "done") this.conversation.push({ role: "assistant", content });
          if (data.type === "stopped") this.conversationError = "本次回复已停止";
          const completedSessionId = this.sessionId;
          void this.loadDraft(completedSessionId);
          void this.refresh();
        }
        return;
      }
      if (!this.generating) return;
      if (data.type === "error") {
        this.generating = false;
        this.clearPersistedSessionId();
        this.stopRecoveryPolling();
        this.error = data.error?.message || "旅行规划失败";
        this.recordProgress({ id: `error-${Date.now()}`, stage: this.stage, title: "规划未完成", detail: this.error, status: "error" });
      } else if (data.type === "done" || data.type === "stopped") {
        this.generating = false;
        this.clearPersistedSessionId();
        this.stopRecoveryPolling();
        if (this.stage !== "complete" && !this.clarificationQuestions.length) {
          if (data.type === "stopped") {
            this.statusText = "旅行规划已停止";
            this.finishProgress(this.stage, "旅行规划已停止", "可以修改需求后重新开始");
          } else {
            this.error = "旅行规划没有生成完整结果，请重试";
            this.statusText = "旅行规划未完成";
            this.recordProgress({ id: `not-finalized-${Date.now()}`, stage: this.stage, title: "规划未完成", detail: this.error, status: "error" });
          }
        }
      }
    },
    async restoreGeneration(): Promise<boolean> {
      const persisted = this.readPersistedSessionId();
      try {
        await this.applyGenerationStatus(await api.travelGeneration(persisted), true);
        void webSocket.connect().catch(() => {
          if (this.generating) this.scheduleRecoveryPoll(0);
        });
        return true;
      } catch (error) {
        if (error instanceof ApiError && error.status === 404) {
          this.clearPersistedSessionId();
          return true;
        }
        return false;
      }
    },
    async checkGenerationStatus() {
      if (!this.sessionId) return;
      try {
        await this.applyGenerationStatus(await api.travelGeneration(this.sessionId), false);
      } catch {
        if (this.generating) this.scheduleRecoveryPoll();
      }
    },
    async applyGenerationStatus(status: TravelGenerationStatus, restoring: boolean) {
      if (status.status === "running" || status.status === "pending") {
        this.sessionId = status.session_id;
        this.loadProgress(status.session_id);
        this.generating = true;
        this.persistSessionId();
        if (!this.conversation.length && !this.conversationLoading) void this.loadDraft(status.session_id);
        if (!this.progressItems.length) {
          this.stage = "requirements";
          this.progressItems = [{ id: "recovered", stage: "requirements", title: "已恢复旅行规划", detail: "后台仍在生成，正在同步最新状态", status: "running" }];
        }
        this.statusText = restoring ? "已恢复正在生成的旅行计划" : "旅行计划仍在生成";
        this.scheduleRecoveryPoll();
        return;
      }
      if (status.status === "awaiting_candidate") {
        this.sessionId = status.session_id;
        this.loadProgress(status.session_id);
        this.generating = false;
        this.persistSessionId();
        this.statusText = "请选择一个候选行程";
        try {
          this.candidateReview = await api.travelCandidateReview(status.session_id);
        } catch (error) {
          this.error = error instanceof Error ? error.message : "候选行程暂时无法恢复";
        }
        return;
      }
      this.stopRecoveryPolling();
      this.clearPersistedSessionId();
      if (status.status === "completed" && status.plan_id) {
        this.sessionId = status.session_id;
        this.generating = false;
        this.stage = "complete";
        this.statusText = "旅行计划已完成";
        this.markCompletedUnread();
        this.finishProgress("complete", "旅行计划已保存", "已恢复完整行程");
        await this.open(status.plan_id);
        await this.refresh();
        return;
      }
      if (!this.generating && !restoring) return;
      this.generating = false;
      if (status.status === "failed") this.error = status.error_code === "TRAVEL_PLAN_NOT_FINALIZED" ? "旅行规划没有生成完整结果，请重试" : "旅行规划未能完成，请重试";
      this.statusText = status.status === "stopped" ? "旅行规划已停止" : status.status === "failed" ? "旅行规划失败" : "旅行规划已结束，但没有生成完整计划";
      if (status.status !== "idle") this.finishProgress(this.stage, this.statusText, this.error || "可以修改需求后重新开始");
    },
    scheduleRecoveryPoll(delay = 2500) {
      if (!this.generating || !this.sessionId || this.recoveryTimer) return;
      this.recoveryTimer = setTimeout(() => {
        this.recoveryTimer = null;
        void this.checkGenerationStatus();
      }, delay);
    },
    stopRecoveryPolling() {
      if (this.recoveryTimer) clearTimeout(this.recoveryTimer);
      this.recoveryTimer = null;
    },
    persistenceKey() {
      return this.initializedUserId ? `zhice.travel.active.${this.initializedUserId}` : "";
    },
    persistSessionId() {
      const key = this.persistenceKey();
      if (key && this.sessionId) sessionStorage.setItem(key, this.sessionId);
    },
    readPersistedSessionId() {
      const key = this.persistenceKey();
      return key ? String(sessionStorage.getItem(key) || "") : "";
    },
    clearPersistedSessionId() {
      const key = this.persistenceKey();
      if (key) sessionStorage.removeItem(key);
    },
    unreadKey() {
      return this.initializedUserId ? `zhice.travel.unread.${this.initializedUserId}` : "";
    },
    readUnreadCompleted() {
      const key = this.unreadKey();
      return key ? localStorage.getItem(key) === "1" : false;
    },
    markCompletedUnread() {
      if (window.location.pathname === "/travel") return;
      this.unreadCompleted = true;
      const key = this.unreadKey();
      if (key) localStorage.setItem(key, "1");
    },
    markViewed() {
      this.unreadCompleted = false;
      const key = this.unreadKey();
      if (key) localStorage.removeItem(key);
    },
    recordProgress(item: TravelProgressItem) {
      this.progressItems = this.progressItems.map((entry) => entry.status === "running" && entry.id !== item.id ? { ...entry, status: "done" as const } : entry).slice(-MAX_CACHED_PROGRESS_ITEMS);
      const index = this.progressItems.findIndex((entry) => entry.id === item.id);
      if (index >= 0) this.progressItems[index] = item;
      else this.progressItems.push(item);
      this.persistProgress();
    },
    finishProgress(stage: TravelProgressStage, title: string, detail: string) {
      this.progressItems = this.progressItems.map((entry) => entry.status === "running" ? { ...entry, status: "done" as const } : entry);
      this.progressItems.push({ id: `${stage}-${Date.now()}`, stage, title, detail, status: "done" });
      this.persistProgress();
    },
    async chooseCandidate(candidateId: string) {
      if (!this.sessionId || this.candidateSelecting || this.generating) return;
      this.candidateSelecting = true;
      this.error = "";
      try {
        this.candidateReview = await api.selectTravelCandidate(this.sessionId, candidateId);
        const models = useModelStore();
        this.generating = true;
        this.stage = "validate";
        this.statusText = "正在按选定方案生成完整计划";
        this.recordProgress({
          id: `candidate-${candidateId}`,
          stage: "solve",
          title: "已选择候选行程",
          detail: `正在完善 ${candidateDisplayName(this.candidateReview, candidateId)}`,
          status: "done",
        });
        await webSocket.sendMessage(
          this.sessionId,
          `继续生成我已确认的候选方案：${candidateId}`,
          models.current || "auto",
        );
        this.scheduleRecoveryPoll();
      } catch (error) {
        this.generating = false;
        this.error = error instanceof Error ? error.message : "候选行程选择失败";
      } finally {
        this.candidateSelecting = false;
      }
    },
    loadProgress(sessionId: string) {
      this.progressItems = [];
      if (!sessionId || !this.initializedUserId) return;
      const raw = sessionStorage.getItem(progressCacheKey(this.initializedUserId, sessionId));
      if (!raw) return;
      try {
        const parsed = JSON.parse(raw);
        if (!Array.isArray(parsed)) return;
        this.progressItems = parsed.slice(-MAX_CACHED_PROGRESS_ITEMS) as TravelProgressItem[];
        const last = this.progressItems.at(-1);
        if (last) this.stage = last.stage;
      } catch {
        sessionStorage.removeItem(progressCacheKey(this.initializedUserId, sessionId));
      }
    },
    persistProgress() {
      if (!this.sessionId || !this.initializedUserId) return;
      try {
        sessionStorage.setItem(
          progressCacheKey(this.initializedUserId, this.sessionId),
          JSON.stringify(this.progressItems.slice(-MAX_CACHED_PROGRESS_ITEMS)),
        );
      } catch {
        // Progress is best-effort UI state; generation must not fail if storage is unavailable.
      }
    },
  },
});

function progressCacheKey(userId: string, sessionId: string): string {
  return `${PROGRESS_CACHE_PREFIX}.${encodeURIComponent(userId)}.${encodeURIComponent(sessionId)}`;
}

function candidateDisplayName(review: TravelCandidateReview | null, candidateId: string): string {
  const candidate = review?.candidates.find((item) => item.candidate_id === candidateId);
  const firstDay = candidate?.days[0];
  const places = firstDay?.places?.slice(0, 2).filter(Boolean).join("、");
  if (firstDay?.city_or_area && places) return `${firstDay.city_or_area} · ${places}`;
  if (firstDay?.city_or_area) return firstDay.city_or_area;
  if (places) return places;
  return "已选方案";
}

function safeToolLabel(name: string, eventName = "") {
  if (!name) return "";
  if (name === "finalize_travel_plan") return "校验并保存结构化计划";
  if (name === "run_skill") return "执行行程可行性与预算门控";
  const running = eventName.endsWith("started");
  if (name.includes("open-meteo")) return running ? "正在通过 Open-Meteo 核对天气" : "Open-Meteo 天气查询完成";
  if (name.includes("amap")) return running ? "正在高德地图查询地点与路线" : "高德地图查询完成";
  if (name.includes("12306")) return running ? "正在铁路 12306 核对车次" : "铁路交通查询完成";
  if (name.includes("tavily")) return running ? "正在通过 Tavily 检索公开资料" : "网页资料检索完成";
  if (name.includes("xhs")) return running ? "正在小红书只读查询旅行经验" : "社区经验查询完成";
  return "正在调用规划能力";
}
function progressTitle(stage: TravelProgressStage, toolName: string) {
  if (toolName === "finalize_travel_plan") return "校验最终计划";
  return ({ requirements: "理解旅行需求", data: "收集基础数据", guides: "整理攻略经验", solve: "优化候选行程", validate: "校验完整计划", complete: "旅行计划完成" })[stage];
}

function toolProgressTitle(name: string) {
  if (name === "finalize_travel_plan") return "正在校验完整计划";
  if (name === "run_skill") return "正在比较候选行程";
  if (name.includes("open-meteo")) return "正在通过 Open-Meteo 核对天气";
  if (name.includes("amap")) return "正在高德地图查询地点与路线";
  if (name.includes("12306")) return "正在铁路 12306 核对车次";
  if (name.includes("tavily")) return "正在通过 Tavily 检索公开资料";
  if (name.includes("xhs")) return "正在小红书只读查询旅行经验";
  return progressTitle("data", name);
}

function completedToolTitle(name: string, backendTitle: string) {
  if (name === "finalize_travel_plan") return backendTitle || "完整计划校验完成";
  if (name === "run_skill") return backendTitle || "候选行程比较完成";
  if (name.includes("open-meteo")) return backendTitle && !backendTitle.includes("mcp__") ? backendTitle : "Open-Meteo 天气查询完成";
  if (name.includes("amap")) return backendTitle && !backendTitle.includes("mcp__") ? backendTitle : "高德地图查询完成";
  if (name.includes("12306")) return backendTitle && !backendTitle.includes("mcp__") ? backendTitle : "12306 查询完成";
  if (name.includes("tavily")) return backendTitle && !backendTitle.includes("mcp__") ? backendTitle : "Tavily 网页检索完成";
  if (name.includes("xhs")) return backendTitle && !backendTitle.includes("mcp__") ? backendTitle : "小红书只读查询完成";
  return backendTitle && !backendTitle.includes("mcp__") ? backendTitle : "规划能力执行完成";
}

function isInternalTravelTool(name: string) {
  return ["discover_tools", "load_skills", "request_travel_clarification"].includes(name);
}

function skillProgressTitle(eventName: string) {
  if (eventName.endsWith("failed")) return "候选行程筛选未通过";
  if (eventName.endsWith("completed")) return "候选行程比较完成";
  return "正在比较候选行程";
}

function progressDetail(event: RuntimeEventData): TravelProgressDetail | undefined {
  if (event.ui_metadata?.detail_type !== "search_results") return undefined;
  const data = event.ui_metadata.detail_data;
  if (!data) return undefined;
  const provider = String(data.provider || "").trim();
  const query = String(data.query || "").trim();
  const summary = String(data.summary || "").trim();
  const items = Array.isArray(data.items)
    ? data.items
      .filter((item) => item && typeof item.title === "string" && item.title.trim())
      .slice(0, 5)
      .map((item) => ({ title: String(item.title).trim(), detail: String(item.detail || "").trim() }))
    : [];
  if (!provider && !query && !summary && !items.length) return undefined;
  return {
    provider,
    query,
    summary,
    resultCount: Number.isFinite(data.result_count) ? Math.max(0, Number(data.result_count)) : items.length,
    items,
  };
}

export function visibleTravelConversation(messages: ChatMessage[]): TravelConversationMessage[] {
  const tagged: TravelConversationMessage[] = [];
  const legacy: TravelConversationMessage[] = [];
  for (const message of messages) {
    if (message.role !== "user" && message.role !== "assistant") continue;
    if (message.tool_calls?.length) continue;
    let content = String(message.content || "").trim();
    if (!content || isJsonDocument(content)) continue;
    const requirementMessage = message.metadata?.travel_visibility === "conversation"
      && message.metadata?.travel_phase === "requirements";
    if (requirementMessage) {
      tagged.push({ role: message.role, content: content.slice(0, 4000) });
      continue;
    }
    if (message.role === "user") {
      if (content.startsWith("继续完成当前旅行规划。")) continue;
      content = originalRequirement(content);
      if (!content) continue;
      legacy.push({ role: "user", content: content.slice(0, 4000) });
    }
  }
  return (tagged.length ? tagged : legacy).slice(-30);
}

function originalRequirement(content: string): string {
  const marker = "用户需求对话：";
  const index = content.lastIndexOf(marker);
  if (index >= 0) return content.slice(index + marker.length).trim();
  if (content.startsWith("请为我生成一份可保存、可在旅行专属页面查看的智能旅行计划。")) return "";
  return content;
}

function isJsonDocument(content: string): boolean {
  if (!content.startsWith("{") && !content.startsWith("[")) return false;
  try {
    const parsed = JSON.parse(content);
    return Boolean(parsed && typeof parsed === "object");
  } catch {
    return false;
  }
}
