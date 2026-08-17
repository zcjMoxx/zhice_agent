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
export type TravelProgressLane = "lodging" | "transport" | "validation";
export interface TravelProgressItem { id: string; stage: TravelProgressStage; title: string; detail: string; status: "running" | "done" | "error"; result?: TravelProgressDetail; lane?: TravelProgressLane; startedAt?: number; }

const PROGRESS_CACHE_PREFIX = "zhice.travel.progress";
const MAX_CACHED_PROGRESS_ITEMS = 100;
const STAGE_RANK: Record<TravelProgressStage, number> = {
  requirements: 0,
  data: 1,
  guides: 2,
  solve: 3,
  validate: 4,
  complete: 5,
};

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
    autoCandidateContinuationPending: "",
    conversation: [] as TravelConversationMessage[],
    conversationLoading: false,
    conversationError: "",
    activeDraft: null as TravelRequirementDraft | null,
    handoffQuestion: "",
    draftSaving: false,
    draftSavePromise: null as Promise<void> | null,
    workspaceVersion: 0,
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
      this.workspaceVersion += 1;
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
      this.autoCandidateContinuationPending = "";
      this.candidateSelecting = false;
      this.autoCandidateContinuationPending = "";
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
    async open(id: string, sourceSessionIdHint = "") {
      if (!id) return;
      const workspaceVersion = ++this.workspaceVersion;
      this.loading = true;
      this.error = "";
      this.clarificationQuestions = [];
      this.candidateReview = null;
      this.autoCandidateContinuationPending = "";
      this.conversation = [];
      this.conversationError = "";
      this.activeDraft = null;
      try {
        const plan = (await api.travelPlan(id)).plan;
        if (this.workspaceVersion !== workspaceVersion) return;
        this.activePlan = plan;
        const sourceSessionId = sourceSessionIdHint
          || this.plans.find((item) => item.plan_id === id)?.source_session_id
          || "";
        this.activeId = id;
        this.sessionId = sourceSessionId;
        this.generating = false;
        await this.restoreProgress(sourceSessionId);
        if (this.workspaceVersion !== workspaceVersion) return;
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
        if (sourceSessionId) {
          try {
            this.candidateReview = await api.travelCandidateReview(sourceSessionId);
          } catch {
            this.candidateReview = null;
          }
        }
        if (window.location.pathname === "/travel") window.history.replaceState({}, "", `/travel?plan=${encodeURIComponent(id)}`);
      } catch (error) {
        this.error = error instanceof Error ? error.message : "无法读取旅行计划";
      } finally {
        this.loading = false;
      }
    },
    startNew() {
      this.workspaceVersion += 1;
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
      this.autoCandidateContinuationPending = "";
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
        await this.open(item.plan_id, item.session_id);
        return;
      }
      const workspaceVersion = ++this.workspaceVersion;
      this.activePlan = null;
      this.activeId = "";
      this.sessionId = item.session_id;
      this.advanceStage("requirements", true);
      this.phase = "intake";
      this.error = "";
      this.candidateReview = null;
      this.clarificationQuestions = [];
      await this.loadDraft(item.session_id);
      if (this.workspaceVersion !== workspaceVersion) return;
      window.history.replaceState({}, "", `/travel?session=${encodeURIComponent(item.session_id)}`);
      if (item.status === "running" || item.status === "awaiting_candidate") {
        const status = await api.travelGeneration(item.session_id);
        if (this.workspaceVersion !== workspaceVersion) return;
        await this.applyGenerationStatus(status, true, workspaceVersion);
      } else {
        await this.restoreProgress(item.session_id);
        if (this.workspaceVersion !== workspaceVersion) return;
        this.generating = false;
        if (item.status === "failed") {
          const selectedCandidateRestored = await this.restoreFailedCandidateReview(item.session_id, workspaceVersion);
          if (this.workspaceVersion !== workspaceVersion) return;
          if (!selectedCandidateRestored) {
            this.stage = "requirements";
            this.statusText = "上次规划未完成，可以补充或修正需求后继续";
            this.error = "上次规划未生成完整计划，请检查需求后重新开始。";
          }
        } else {
          this.stage = "requirements";
          this.statusText = "旅行需求收集中";
        }
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
      this.advanceStage("requirements", true);
      this.statusText = "正在确认旅行需求";
      this.error = "";
      this.clarificationQuestions = [];
      this.candidateReview = null;
      this.autoCandidateContinuationPending = "";
      this.conversation = conversation.map((item) => ({ ...item }));
      if (draft) this.activeDraft = { ...draft };
      this.conversationError = "";
      this.progressItems = [{ id: "requirements", stage: "requirements", title: "已收到旅行需求", detail: "正在提取日期、人数、预算与偏好", status: "running" }];
      try {
        if (conversation.length && draft) await this.saveDraft(conversation, draft);
        else if (this.draftSavePromise) await this.draftSavePromise;
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
          if (["travel.plan_ready", "travel.clarification_required", "travel.candidate_review_required", "travel.candidate_review_auto_selected"].includes(backgroundName)) void this.refresh();
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
          this.advanceStage("requirements", true);
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
            const sourceSessionId = this.sessionId;
            this.stage = "complete";
            this.statusText = "旅行计划已完成";
            this.generating = false;
            this.conversationError = "";
            this.markCompletedUnread();
            this.clearPersistedSessionId();
            this.stopRecoveryPolling();
            this.finishProgress("complete", "旅行计划已保存", "正在打开完整行程");
            void this.open(planId, sourceSessionId).then(() => this.refresh());
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
        if (["travel.candidate_review_required", "travel.candidate_review_auto_selected"].includes(eventName)) {
          const data = event.ui_metadata?.detail_data;
          const candidates = Array.isArray(data?.candidates) ? data.candidates : [];
          const automatic = eventName === "travel.candidate_review_auto_selected";
          this.candidateReview = {
            session_id: this.sessionId,
            status: automatic ? "selected" : "pending",
            recommended_candidate_id: String(data?.recommended_candidate_id || ""),
            selected_candidate_id: automatic
              ? String(data?.selected_candidate_id || data?.recommended_candidate_id || "")
              : "",
            candidates,
            created_at: "",
            updated_at: "",
          };
          if (automatic) {
            this.autoCandidateContinuationPending = this.candidateReview.selected_candidate_id;
            this.advanceStage("validate");
            this.statusText = "时间充足，已直接进入完整规划";
            this.recordProgress({
              id: `candidate-auto-${this.candidateReview.selected_candidate_id}`,
              stage: "solve",
              title: "无需额外方案取舍",
              detail: "主要兴趣点可以完整覆盖，已自动采用唯一有效方案",
              status: "done",
            });
          } else {
            this.generating = false;
            this.stopRecoveryPolling();
            this.advanceStage("solve");
            this.statusText = "请选择一个候选行程";
            this.finishProgress("solve", "候选行程比较完成", "选择后会继续生成完整旅行计划");
          }
          return;
        }
        if (!this.generating) return;
        const selectedCandidateFinalization = this.candidateReview?.status === "selected"
          && this.stage === "validate"
          && toolName === "delegate_tasks";
        if (selectedCandidateFinalization) {
          const finished = /completed|done|finished/.test(eventName);
          const failed = /error|failed/.test(eventName);
          const status: TravelProgressItem["status"] = failed ? "error" : finished ? "done" : "running";
          const callId = String(event.tool_call_id || "selected-candidate");
          this.recordProgress({
            id: `finalization-lodging-${callId}`,
            stage: "validate",
            title: status === "running" ? "正在核对住宿与房价" : status === "done" ? "住宿与房价资料已汇总" : "住宿资料未完整取得",
            detail: status === "running" ? "正在查询具体酒店身份和指定日期价格" : status === "done" ? "已完成住宿来源补齐，准备最终校验" : "保留已取得结果并进入最终校验",
            status,
            lane: "lodging",
            startedAt: status === "running" ? Date.now() : undefined,
          });
          this.recordProgress({
            id: `finalization-transport-${callId}`,
            stage: "validate",
            title: status === "running" ? "正在核对公共交通路线" : status === "done" ? "公共交通路线已汇总" : "部分路线未完整取得",
            detail: status === "running" ? "正在补齐线路号、上下车站和远郊往返" : status === "done" ? "已完成路线来源补齐，准备最终校验" : "保留已取得线路并进入最终校验",
            status,
            lane: "transport",
            startedAt: status === "running" ? Date.now() : undefined,
          });
          if (status === "done") {
            this.recordProgress({
              id: `finalization-validation-${callId}`,
              stage: "validate",
              title: "正在生成并校验完整计划",
              detail: "住宿与交通资料已汇总，正在组装最终行程并执行结构校验",
              status: "running",
              lane: "validation",
              startedAt: Date.now(),
            });
          }
          this.statusText = status === "running"
            ? "正在并行补齐住宿与交通路线"
            : "住宿与交通资料已汇总，正在生成最终计划";
          return;
        }
        if (event.display?.visibility === "internal") {
          const progressId = String(event.tool_call_id || event.skill_run_id || "");
          if (progressId && /completed|failed|error|done|finished/.test(eventName)) {
            const remaining = this.progressItems.filter((item) => item.id !== progressId);
            if (remaining.length !== this.progressItems.length) {
              this.progressItems = remaining;
              this.persistProgress();
            }
          }
          return;
        }
        if (isInternalTravelTool(toolName)) return;
        if (eventName.startsWith("skill.")) this.advanceStage("solve");
        else if (toolName === "finalize_travel_plan") this.advanceStage("validate");
        else if (eventName.startsWith("tool.") && toolName.includes("xhs")) this.advanceStage("guides");
        else if (eventName.startsWith("tool.")) this.advanceStage("data");
        let title = eventName.startsWith("skill.")
          ? skillProgressTitle(eventName)
          : eventName === "tool.started"
            ? toolProgressTitle(toolName)
            : completedToolTitle(toolName, String(event.display?.title || ""));
        let detail = String(event.display?.detail || safeToolLabel(toolName, eventName) || this.statusText);
        const result = progressDetail(event);
        const toolFailed = /error|failed/.test(eventName);
        const toolCode = String(event.metadata?.code || "");
        if (toolName === "search_travel_hotels" && toolFailed) {
          title = "携程酒店房价未取得";
          detail = ["HOTEL_MANUAL_VERIFICATION_REQUIRED", "HOTEL_LOGIN_VERIFICATION_TIMEOUT", "HOTEL_AUTH_REQUIRED"].includes(toolCode)
            ? "携程需要重新验证，本轮只使用明确标注的规划估算，不冒充实时房价。"
            : "本次未取得指定日期房价，已有住宿地点信息继续保留。";
        } else if (toolName.includes("tavily") && toolFailed && this.progressItems.some((item) =>
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
            lane: progressLane(toolName),
            startedAt: status === "running" ? Date.now() : undefined,
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
        this.error = data.error?.message || "旅行规划失败";
        this.statusText = "正在确认旅行规划的最终状态";
        void this.checkGenerationStatus();
      } else if (data.type === "done" || data.type === "stopped") {
        if (data.type === "done" && this.autoCandidateContinuationPending) {
          this.autoCandidateContinuationPending = "";
          this.generating = false;
          this.stopRecoveryPolling();
          void this.retrySelectedCandidate();
          return;
        }
        this.statusText = data.type === "stopped" ? "正在确认旅行规划已停止" : "正在确认旅行规划结果";
        void this.checkGenerationStatus();
      }
    },
    async restoreGeneration(): Promise<boolean> {
      const persisted = this.readPersistedSessionId();
      const workspaceVersion = this.workspaceVersion;
      try {
        const status = await api.travelGeneration(persisted);
        if (this.workspaceVersion !== workspaceVersion) return true;
        await this.applyGenerationStatus(status, true, workspaceVersion);
        if (this.workspaceVersion !== workspaceVersion) return true;
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
      const sessionId = this.sessionId;
      const workspaceVersion = this.workspaceVersion;
      try {
        const status = await api.travelGeneration(sessionId);
        if (this.sessionId !== sessionId || this.workspaceVersion !== workspaceVersion) return;
        await this.applyGenerationStatus(status, false, workspaceVersion);
      } catch {
        if (this.generating) this.scheduleRecoveryPoll();
      }
    },
    async applyGenerationStatus(status: TravelGenerationStatus, restoring: boolean, workspaceVersion?: number) {
      const expectedWorkspaceVersion = workspaceVersion ?? this.workspaceVersion;
      if (this.workspaceVersion !== expectedWorkspaceVersion) return;
      if (status.status === "running" || status.status === "pending") {
        this.sessionId = status.session_id;
        if (restoring) await this.restoreProgress(status.session_id);
        else this.loadProgress(status.session_id);
        if (this.workspaceVersion !== expectedWorkspaceVersion) return;
        this.generating = true;
        this.persistSessionId();
        if (!this.conversation.length && !this.conversationLoading) void this.loadDraft(status.session_id);
        this.error = "";
        const selectedFinalization = await this.restoreSelectedCandidateFinalization(status.session_id, status.error_code);
        if (this.workspaceVersion !== expectedWorkspaceVersion) return;
        if (!this.progressItems.length && !selectedFinalization) {
          this.stage = "requirements";
          this.progressItems = [{ id: "recovered", stage: "requirements", title: "已恢复旅行规划", detail: "后台仍在生成，正在同步最新状态", status: "running" }];
        }
        if (!selectedFinalization) {
          this.statusText = restoring ? "已恢复正在生成的旅行计划" : "旅行计划仍在生成";
        }
        this.scheduleRecoveryPoll();
        return;
      }
      if (status.status === "awaiting_candidate") {
        this.sessionId = status.session_id;
        if (restoring) await this.restoreProgress(status.session_id);
        else this.loadProgress(status.session_id);
        if (this.workspaceVersion !== expectedWorkspaceVersion) return;
        this.generating = false;
        this.persistSessionId();
        this.statusText = "请选择一个候选行程";
        try {
          const candidateReview = await api.travelCandidateReview(status.session_id);
          if (this.workspaceVersion !== expectedWorkspaceVersion) return;
          this.candidateReview = candidateReview;
        } catch (error) {
          this.error = error instanceof Error ? error.message : "候选行程暂时无法恢复";
        }
        return;
      }
      this.stopRecoveryPolling();
      this.clearPersistedSessionId();
      if (status.status === "completed" && status.plan_id) {
        this.sessionId = status.session_id;
        if (restoring) await this.restoreProgress(status.session_id);
        else this.loadProgress(status.session_id);
        if (this.workspaceVersion !== expectedWorkspaceVersion) return;
        this.generating = false;
        this.stage = "complete";
        this.statusText = "旅行计划已完成";
        this.markCompletedUnread();
        if (!this.progressItems.some((item) => item.stage === "complete" && item.status === "done")) {
          this.finishProgress("complete", "旅行计划已保存", "已恢复完整行程");
        }
        await this.open(status.plan_id, status.session_id);
        await this.refresh();
        return;
      }
      if (status.status === "failed") {
        this.sessionId = status.session_id;
        if (restoring) await this.restoreProgress(status.session_id);
        if (this.workspaceVersion !== expectedWorkspaceVersion) return;
        this.generating = false;
        const selectedCandidateRestored = await this.restoreFailedCandidateReview(status.session_id, expectedWorkspaceVersion);
        if (this.workspaceVersion !== expectedWorkspaceVersion) return;
        if (selectedCandidateRestored) return;
      }
      this.generating = false;
      if (status.status === "failed") this.error = status.error_code === "TRAVEL_PLAN_NOT_FINALIZED" ? "旅行规划没有生成完整结果，请重试" : "旅行规划未能完成，请重试";
      this.statusText = status.status === "stopped" ? "旅行规划已停止" : status.status === "failed" ? "旅行规划失败" : "旅行规划已结束，但没有生成完整计划";
      if (status.status === "failed") {
        this.progressItems = this.progressItems.map((entry) => entry.status === "running" ? { ...entry, status: "error" as const } : entry);
        this.recordProgress({ id: `failed-${Date.now()}`, stage: this.stage, title: this.statusText, detail: this.error, status: "error" });
      } else if (status.status !== "idle") {
        this.finishProgress(this.stage, this.statusText, this.error || "可以修改需求后重新开始");
      }
    },
    async restoreFailedCandidateReview(sessionId: string, workspaceVersion?: number): Promise<boolean> {
      const expectedWorkspaceVersion = workspaceVersion ?? this.workspaceVersion;
      let review: TravelCandidateReview;
      try {
        review = await api.travelCandidateReview(sessionId);
      } catch {
        return false;
      }
      if (this.workspaceVersion !== expectedWorkspaceVersion) return false;
      if (review.status !== "selected" || !review.selected_candidate_id) return false;
      this.candidateReview = review;
      this.phase = "planning";
      this.advanceStage("validate");
      this.statusText = "上次最终校验未完成，可以继续完善已选方案";
      this.error = "已选方案仍然保留，点击继续即可从最终校验阶段接着完成。";
      return true;
    },
    async restoreSelectedCandidateFinalization(sessionId: string, errorCode = ""): Promise<boolean> {
      let review = this.candidateReview;
      if (review?.session_id !== sessionId || review.status !== "selected") {
        try {
          review = await api.travelCandidateReview(sessionId);
        } catch {
          return false;
        }
      }
      if (!review || review.status !== "selected" || !review.selected_candidate_id) return false;
      this.candidateReview = review;
      this.advanceStage("validate");
      const candidateId = review.selected_candidate_id;
      const routeRepairing = errorCode === "TRAVEL_ROUTE_EVIDENCE_MISSING";
      if (!this.progressItems.some((item) => item.id === `finalizing-${candidateId}`)) {
        this.recordProgress({
          id: `finalizing-${candidateId}`,
          stage: "validate",
          title: "正在完善所选方案",
          detail: routeRepairing ? "正在补齐缺失的公共交通路线并重新校验" : "正在并行补齐住宿价格、交通路线并执行最终校验",
          status: "running",
          startedAt: Date.now(),
        });
      }
      const laneDetails: Array<{ lane: TravelProgressLane; title: string; detail: string }> = routeRepairing
        ? [{ lane: "transport", title: "正在补齐缺失的公共交通路线", detail: "正在改用景区可达入口，必要时核对高德驾车兜底" }]
        : [
            { lane: "lodging", title: "正在核对住宿与房价", detail: "正在查询具体酒店身份和指定日期价格" },
            { lane: "transport", title: "正在核对公共交通路线", detail: "正在补齐线路号、上下车站和远郊往返" },
      ];
      for (const lane of laneDetails) {
        const recoveredId = routeRepairing
          ? `finalization-${lane.lane}-repair-${candidateId}`
          : `finalization-${lane.lane}-recovered-${candidateId}`;
        if (this.progressItems.some((item) => item.id === recoveredId)) continue;
        if (!routeRepairing && this.progressItems.some((item) => item.lane === lane.lane)) continue;
        this.recordProgress({
          id: recoveredId,
          stage: "validate",
          title: lane.title,
          detail: lane.detail,
          status: "running",
          lane: lane.lane,
          startedAt: Date.now(),
        });
      }
      const finalizationDone = !routeRepairing && ["lodging", "transport"].every((lane) =>
        this.progressItems.some((item) => item.lane === lane && item.status === "done")
      );
      this.statusText = routeRepairing
        ? "正在补齐缺失的公共交通路线"
        : finalizationDone
        ? "住宿与交通资料已汇总，正在生成最终计划"
        : "正在并行补齐住宿价格与公共交通路线";
      return true;
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
    advanceStage(next: TravelProgressStage, reset = false) {
      if (reset || STAGE_RANK[next] > STAGE_RANK[this.stage]) this.stage = next;
    },
    recordProgress(item: TravelProgressItem) {
      this.advanceStage(item.stage);
      // Recovery placeholders are replaced by the live finalization aggregate
      // as soon as the delegate event arrives; keep one row per Lane.
      if (item.lane && item.stage === "validate" && (
        item.id.startsWith("finalization-") || item.lane === "validation"
      )) {
        this.progressItems = this.progressItems.filter((entry) => (
          entry.lane !== item.lane || entry.id === item.id
        ));
      }
      this.progressItems = this.progressItems.slice(-MAX_CACHED_PROGRESS_ITEMS);
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
      this.generating = true;
      this.error = "";
      const displayName = candidateDisplayName(this.candidateReview, candidateId);
      const selectionProgressId = `candidate-${candidateId}`;
      const finalizationProgressId = `finalizing-${candidateId}`;
      let selectionConfirmed = false;
      this.statusText = "正在确认所选方案";
      this.recordProgress({
        id: selectionProgressId,
        stage: "solve",
        title: "正在确认候选行程",
        detail: displayName,
        status: "running",
        startedAt: Date.now(),
      });
      try {
        this.candidateReview = await api.selectTravelCandidate(this.sessionId, candidateId);
        selectionConfirmed = true;
        const models = useModelStore();
        this.advanceStage("validate");
        this.statusText = "正在并行补齐住宿价格、交通路线并执行最终校验";
        this.recordProgress({
          id: selectionProgressId,
          stage: "solve",
          title: "已选择候选行程",
          detail: `正在完善 ${candidateDisplayName(this.candidateReview, candidateId)}`,
          status: "done",
        });
        this.recordProgress({
          id: finalizationProgressId,
          stage: "validate",
          title: "正在完善所选方案",
          detail: "正在并行补齐住宿价格、交通路线并执行最终校验",
          status: "running",
          startedAt: Date.now(),
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
        this.recordProgress({
          id: selectionConfirmed ? finalizationProgressId : selectionProgressId,
          stage: this.stage,
          title: selectionConfirmed ? "所选方案暂未完成" : "候选方案确认失败",
          detail: this.error,
          status: "error",
        });
      } finally {
        this.candidateSelecting = false;
      }
    },
    async retrySelectedCandidate() {
      const candidateId = this.candidateReview?.selected_candidate_id;
      if (!this.sessionId || !candidateId || this.generating || this.candidateSelecting) return;
      this.generating = true;
      this.error = "";
      this.advanceStage("validate");
      this.statusText = "正在继续完善所选方案";
      this.recordProgress({
        id: `retry-finalizing-${candidateId}-${Date.now()}`,
        stage: "validate",
        title: "正在继续完成旅行计划",
        detail: "正在恢复子任务资料并重新执行最终校验",
        status: "running",
        lane: "validation",
        startedAt: Date.now(),
      });
      try {
        const models = useModelStore();
        await webSocket.sendMessage(
          this.sessionId,
          `继续完成我已确认的候选方案：${candidateId}`,
          models.current || "auto",
        );
        this.persistSessionId();
        this.scheduleRecoveryPoll();
      } catch (error) {
        this.generating = false;
        this.error = error instanceof Error ? error.message : "旅行规划重试失败";
        this.recordProgress({
          id: `retry-finalizing-error-${candidateId}-${Date.now()}`,
          stage: "validate",
          title: "继续规划未能启动",
          detail: this.error,
          status: "error",
          lane: "validation",
        });
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
        const highest = highestProgressStage(this.progressItems);
        if (highest) this.stage = highest;
      } catch {
        sessionStorage.removeItem(progressCacheKey(this.initializedUserId, sessionId));
      }
    },
    async restoreProgress(sessionId: string) {
      this.loadProgress(sessionId);
      if (!sessionId) return;
      const localItems = this.progressItems.map((item) => ({ ...item }));
      try {
        const history = await api.travelProgress(sessionId);
        if (this.sessionId !== sessionId) return;
        this.progressItems = mergeProgressItems(history.items as TravelProgressItem[], localItems);
        const highest = highestProgressStage(this.progressItems);
        if (highest) this.stage = highest;
        this.persistProgress();
      } catch {
        // The local cache remains usable when durable history is temporarily unavailable.
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

function highestProgressStage(items: TravelProgressItem[]): TravelProgressStage | undefined {
  return items.reduce<TravelProgressStage | undefined>((highest, item) => (
    highest === undefined || STAGE_RANK[item.stage] > STAGE_RANK[highest]
      ? item.stage
      : highest
  ), undefined);
}

function mergeProgressItems(serverItems: TravelProgressItem[], localItems: TravelProgressItem[]): TravelProgressItem[] {
  const serverHasCompletion = serverItems.some((item) => item.stage === "complete" && item.status === "done");
  const serverIds = new Set(serverItems.map((item) => item.id));
  const merged = [...serverItems];
  for (const item of localItems) {
    if (serverHasCompletion && item.stage === "complete") continue;
    if (serverIds.has(item.id)) continue;
    merged.push(item);
  }
  return merged.slice(-MAX_CACHED_PROGRESS_ITEMS);
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
  if (name === "delegate_tasks") return "并行旅行研究正在汇总";
  const running = eventName.endsWith("started");
  if (name === "search_travel_hotels") return running ? "正在通过携程查询指定日期房价" : "携程酒店房价查询完成";
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
  if (name === "delegate_tasks") return "正在并行收集旅行资料";
  if (name === "search_travel_hotels") return "正在通过携程查询指定日期房价";
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
  if (name === "delegate_tasks") return "并行旅行资料已汇总";
  if (name === "search_travel_hotels") return "携程酒店房价查询完成";
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

function progressLane(name: string): TravelProgressLane | undefined {
  const normalized = name.toLocaleLowerCase();
  if (name === "search_travel_hotels" || normalized.includes("hotel") || normalized.includes("ctrip")) return "lodging";
  if (name === "finalize_travel_plan") return "validation";
  if (normalized.includes("12306") || normalized.includes("amap") || normalized.includes("route") || normalized.includes("transit")) return "transport";
  return undefined;
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
