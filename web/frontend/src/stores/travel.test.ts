import { createPinia, setActivePinia } from "pinia";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { TravelPlan, TravelRequirementDraft } from "@/api/types";
import { api } from "@/api/client";
import { useTravelStore, visibleTravelConversation } from "./travel";
import { webSocket } from "@/websocket/client";

describe("travel store", () => {
  beforeEach(() => {
    setActivePinia(createPinia());
    sessionStorage.clear();
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ plan: samplePlan() }), { status: 200, headers: { "Content-Type": "application/json" } })));
    window.history.replaceState({}, "", "/travel");
  });

  afterEach(() => vi.restoreAllMocks());

  it("loads the plan named by travel.plan_ready and closes progress", async () => {
    const travel = useTravelStore();
    travel.initializedUserId = "user-a";
    travel.sessionId = "travel-session";
    travel.generating = true;
    travel.handleEnvelope({
      event: "runtime_event",
      session_id: "travel-session",
      data: {
        type: "travel.plan_ready",
        metadata: { plan_id: "travel-plan-one" },
        display: { title: "旅行计划已生成" },
      },
    });
    await vi.waitFor(() => expect(travel.activeId).toBe("travel-plan-one"));

    expect(travel.stage).toBe("complete");
    expect(travel.generating).toBe(false);
    expect(window.location.search).toContain("travel-plan-one");
  });

  it("receives plan completion while chat is visible without changing the route", async () => {
    window.history.replaceState({}, "", "/");
    const travel = useTravelStore();
    travel.initializedUserId = "user-a";
    travel.sessionId = "travel-session";
    travel.generating = true;

    travel.handleEnvelope({
      event: "runtime_event",
      session_id: "travel-session",
      data: { type: "travel.plan_ready", metadata: { plan_id: "travel-plan-one" } },
    });
    await vi.waitFor(() => expect(travel.activeId).toBe("travel-plan-one"));

    expect(window.location.pathname).toBe("/");
    expect(travel.generating).toBe(false);
    expect(travel.unreadCompleted).toBe(true);
    expect(localStorage.getItem("zhice.travel.unread.user-a")).toBe("1");
  });

  it("maps truthful runtime stages without inventing completion", () => {
    const travel = useTravelStore();
    travel.sessionId = "travel-session";
    travel.generating = true;
    travel.handleEnvelope({ event: "runtime_event", session_id: "travel-session", data: { type: "tool.started", metadata: { tool_name: "mcp__open-meteo__get_forecast" }, display: { title: "天气查询" } } });
    expect(travel.stage).toBe("data");
    travel.handleEnvelope({ event: "runtime_event", session_id: "travel-session", data: { type: "skill.progress", display: { detail: "预算求解" } } });
    expect(travel.stage).toBe("solve");
    expect(travel.progressItems.some((item) => item.detail === "预算求解")).toBe(true);
  });

  it("hides internal orchestration and presents named source results to users", () => {
    const travel = useTravelStore();
    travel.sessionId = "travel-session";
    travel.generating = true;

    travel.handleEnvelope({ event: "runtime_event", session_id: "travel-session", data: { type: "tool.completed", tool_call_id: "load", metadata: { tool_name: "load_skills" }, display: { title: "load_skills 执行完成" } } });
    travel.handleEnvelope({
      event: "runtime_event",
      session_id: "travel-session",
      data: {
        type: "tool.started",
        tool_call_id: "amap-search",
        metadata: { tool_name: "mcp__amap-maps__maps_text_search" },
      },
    });
    expect(travel.progressItems[0]?.title).toBe("正在高德地图查询地点与路线");
    travel.handleEnvelope({
      event: "runtime_event",
      session_id: "travel-session",
      data: {
        type: "tool.completed",
        tool_call_id: "amap-search",
        metadata: { tool_name: "mcp__amap-maps__maps_text_search" },
        display: { title: "高德地图查询完成", detail: "返回 12 个结果，展示前 3 个候选" },
        ui_metadata: {
          detail_type: "search_results",
          detail_data: {
            provider: "高德地图",
            query: "大理古城周边景点",
            summary: "返回 12 个结果，展示前 3 个候选",
            result_count: 12,
            items: [
              { title: "崇圣寺三塔", detail: "大理镇三塔路" },
              { title: "洱海生态廊道", detail: "适合骑行" },
              { title: "大理古城", detail: "古城核心区" },
            ],
          },
        },
      },
    });

    expect(travel.progressItems).toHaveLength(1);
    expect(travel.progressItems[0]).toMatchObject({
      id: "amap-search",
      title: "高德地图查询完成",
      status: "done",
      result: {
        provider: "高德地图",
        query: "大理古城周边景点",
        resultCount: 12,
      },
    });
    expect(travel.progressItems[0]?.result?.items.map((item) => item.title)).toEqual(["崇圣寺三塔", "洱海生态廊道", "大理古城"]);
  });

  it("never exposes the internal 12306 MCP method in progress titles", () => {
    const travel = useTravelStore();
    travel.sessionId = "travel-session";
    travel.generating = true;

    travel.handleEnvelope({
      event: "runtime_event",
      session_id: "travel-session",
      data: {
        type: "tool.completed",
        tool_call_id: "rail-search",
        metadata: { tool_name: "mcp__12306__get-tickets" },
        display: { title: "mcp__12306__get-tickets 执行完成", detail: "返回 3 个车次" },
      },
    });

    expect(travel.progressItems[0]?.title).toBe("12306 查询完成");
    expect(travel.progressItems[0]?.title).not.toContain("mcp__");
  });

  it("returns structured clarification to the requirement conversation", () => {
    const travel = useTravelStore();
    travel.initializedUserId = "user-a";
    travel.sessionId = "travel-session";
    travel.generating = true;

    travel.handleEnvelope({
      event: "runtime_event",
      session_id: "travel-session",
      data: {
        type: "travel.clarification_required",
        ui_metadata: { detail_type: "summary", detail_data: { questions: ["预算档位？", "是否接受飞机？"] } },
        metadata: { question_count: 2 },
      },
    });

    expect(travel.generating).toBe(false);
    expect(travel.clarificationQuestions).toEqual(["预算档位？", "是否接受飞机？"]);
    expect(travel.statusText).toBe("还需要确认一些旅行信息");
  });

  it("treats a done turn without plan_ready as an error instead of completion", () => {
    const travel = useTravelStore();
    travel.sessionId = "travel-session";
    travel.generating = true;

    travel.handleEnvelope({ event: "channel_status", session_id: "travel-session", data: { type: "done" } });

    expect(travel.generating).toBe(false);
    expect(travel.statusText).toBe("旅行规划未完成");
    expect(travel.error).toBe("旅行规划没有生成完整结果，请重试");
    expect(travel.progressItems.at(-1)?.status).toBe("error");
  });

  it("keeps the complete truthful progress timeline", () => {
    const travel = useTravelStore();
    travel.sessionId = "travel-session";
    travel.generating = true;
    for (let index = 0; index < 20; index += 1) {
      travel.handleEnvelope({ event: "runtime_event", session_id: "travel-session", data: { type: "tool.started", sequence: index, tool_call_id: `call-${index}`, metadata: { tool_name: "mcp__amap__search" } } });
    }
    expect(travel.progressItems).toHaveLength(20);
    expect(travel.progressItems[0]?.id).toBe("call-0");
    expect(travel.progressItems.at(-1)?.detail).toBe("正在高德地图查询地点与路线");
  });

  it("persists progress per travel session and restores only the selected session", () => {
    const travel = useTravelStore();
    travel.initializedUserId = "user-a";
    travel.sessionId = "session-a";
    travel.recordProgress({ id: "a1", stage: "data", title: "天气查询完成", detail: "已完成", status: "done" });

    travel.loadProgress("session-b");
    expect(travel.progressItems).toEqual([]);
    travel.loadProgress("session-a");
    expect(travel.progressItems[0]?.title).toBe("天气查询完成");
  });

  it("uses a readable candidate name instead of exposing the candidate id", async () => {
    vi.spyOn(api, "selectTravelCandidate").mockResolvedValue({
      session_id: "travel-session",
      status: "selected",
      recommended_candidate_id: "classic-riverside-loop",
      selected_candidate_id: "classic-riverside-loop",
      candidates: [{
        candidate_id: "classic-riverside-loop",
        recommended: true,
        score: 1,
        days: [{ date: "2026-08-15", city_or_area: "重庆主城", places: ["三峡博物馆", "洪崖洞"] }],
        budget: { lower: 1, expected: 2, upper: 3 },
        route_minutes: 10,
        route_distance_km: 1,
        daily_intensity_scores: [1],
        evidence_coverage: 1,
        warnings: [],
      }],
      created_at: "",
      updated_at: "",
    });
    vi.spyOn(webSocket, "sendMessage").mockResolvedValue();
    const travel = useTravelStore();
    travel.initializedUserId = "user-a";
    travel.sessionId = "travel-session";
    travel.candidateReview = {
      session_id: "travel-session",
      status: "pending",
      recommended_candidate_id: "classic-riverside-loop",
      selected_candidate_id: "",
      candidates: [{
        candidate_id: "classic-riverside-loop",
        recommended: true,
        score: 1,
        days: [{ date: "2026-08-15", city_or_area: "重庆主城", places: ["三峡博物馆", "洪崖洞"] }],
        budget: { lower: 1, expected: 2, upper: 3 },
        route_minutes: 10,
        route_distance_km: 1,
        daily_intensity_scores: [1],
        evidence_coverage: 1,
        warnings: [],
      }],
      created_at: "",
      updated_at: "",
    };

    await travel.chooseCandidate("classic-riverside-loop");

    expect(travel.progressItems.at(-1)?.detail).toBe("正在完善 重庆主城 · 三峡博物馆、洪崖洞");
    expect(travel.progressItems.at(-1)?.detail).not.toContain("classic-riverside-loop");
  });

  it("starts a new blank workspace without opening the latest saved plan", () => {
    const travel = useTravelStore();
    travel.activeId = "old-plan";
    travel.activePlan = samplePlan();
    travel.statusText = "old progress";
    travel.progressItems = [{ id: "old", stage: "complete", title: "old", detail: "old", status: "done" }];

    travel.startNew();

    expect(travel.activeId).toBe("");
    expect(travel.activePlan).toBeNull();
    expect(travel.progressItems).toEqual([]);
    expect(window.location.pathname).toBe("/travel");
    expect(window.location.search).toBe("");
  });

  it("creates an isolated travel session and ignores events from other sessions", async () => {
    vi.spyOn(webSocket, "createSession").mockResolvedValue("travel-new");
    const send = vi.spyOn(webSocket, "sendMessage").mockResolvedValue();
    const confirm = vi.spyOn(api, "confirmTravelPlanning").mockResolvedValue({ session_id: "travel-new", phase: "planning", status: "confirmed" });
    const travel = useTravelStore();
    travel.activeDraft = sampleDraft();

    await travel.generate("重庆到大理五天", [
      { role: "user", content: "重庆到大理五天" },
      { role: "assistant", content: "信息已齐全" },
    ]);

    expect(webSocket.createSession).toHaveBeenCalledWith("travel");
    expect(confirm).toHaveBeenCalledWith("travel-new", sampleDraft());
    expect(send).toHaveBeenCalledWith("travel-new", "我已确认以上旅行条件，请开始正式规划。", "auto");
    travel.handleEnvelope({ event: "channel_status", session_id: "other", data: { type: "error", error: { message: "other failed" } } });
    expect(travel.generating).toBe(true);
    expect(travel.error).toBe("");
  });

  it("creates and persists a collecting draft before starting an Agent turn", async () => {
    vi.spyOn(webSocket, "createSession").mockResolvedValue("travel-draft");
    const send = vi.spyOn(webSocket, "sendMessage").mockResolvedValue();
    const persist = vi.spyOn(api, "persistTravelConversation").mockResolvedValue({ session_id: "travel-draft", message_count: 2, status: "saved" });
    vi.spyOn(api, "travelPlans").mockResolvedValue({ plans: [] });
    vi.spyOn(api, "travelWorkItems").mockResolvedValue({ items: [] });
    const travel = useTravelStore();
    const draft = sampleDraft();

    await travel.saveDraft([
      { role: "user", content: "重庆去大理" },
      { role: "assistant", content: "几号出发？" },
    ], draft);

    expect(webSocket.createSession).toHaveBeenCalledWith("travel");
    expect(persist).toHaveBeenCalledWith("travel-draft", expect.any(Array), draft);
    expect(send).not.toHaveBeenCalled();
    expect(travel.sessionId).toBe("travel-draft");
    expect(window.location.search).toContain("session=travel-draft");
  });

  it("reuses the collecting draft Session when generation is confirmed", async () => {
    const create = vi.spyOn(webSocket, "createSession").mockResolvedValue("new-session");
    const send = vi.spyOn(webSocket, "sendMessage").mockResolvedValue();
    const confirm = vi.spyOn(api, "confirmTravelPlanning").mockResolvedValue({ session_id: "travel-draft", phase: "planning", status: "confirmed" });
    const travel = useTravelStore();
    travel.sessionId = "travel-draft";
    travel.activeDraft = sampleDraft();

    await travel.generate("正式规划", [{ role: "user", content: "重庆去大理" }]);

    expect(create).not.toHaveBeenCalled();
    expect(confirm).toHaveBeenCalledWith("travel-draft", sampleDraft());
    expect(send).toHaveBeenCalledWith("travel-draft", "我已确认以上旅行条件，请开始正式规划。", "auto");
    expect(travel.phase).toBe("planning");
  });

  it("does not start the planning Agent turn when server confirmation fails", async () => {
    vi.spyOn(webSocket, "createSession").mockResolvedValue("travel-new");
    const send = vi.spyOn(webSocket, "sendMessage").mockResolvedValue();
    vi.spyOn(api, "confirmTravelPlanning").mockRejectedValue(new Error("确认失败"));
    const travel = useTravelStore();
    travel.activeDraft = sampleDraft();

    await travel.generate("重庆到大理五天", [{ role: "user", content: "重庆到大理五天" }]);

    expect(send).not.toHaveBeenCalled();
    expect(travel.generating).toBe(false);
    expect(travel.error).toBe("确认失败");
  });

  it("runs intake through WebSocket and appends the Agent natural reply", async () => {
    vi.spyOn(webSocket, "createSession").mockResolvedValue("travel-intake");
    const send = vi.spyOn(webSocket, "sendMessage").mockResolvedValue();
    vi.spyOn(api, "travelPlans").mockResolvedValue({ plans: [] });
    vi.spyOn(api, "travelWorkItems").mockResolvedValue({ items: [] });
    vi.spyOn(api, "travelDraft").mockResolvedValue({
      session_id: "travel-intake",
      phase: "intake",
      draft: {},
      handoff_question: "",
      messages: [
        { role: "user", content: "你是谁" },
        { role: "assistant", content: "我是智策旅行助手，主要帮你规划行程。" },
      ],
    });
    const travel = useTravelStore();

    await travel.sendIntake("你是谁");
    expect(travel.conversation).toEqual([{ role: "user", content: "你是谁" }]);
    expect(send).toHaveBeenCalledWith("travel-intake", "你是谁", "auto");
    expect(travel.intakeBusy).toBe(true);

    travel.handleEnvelope({
      event: "channel_status",
      session_id: "travel-intake",
      data: {
        type: "done",
        assistant: { role: "assistant", content: "我是智策旅行助手，主要帮你规划行程。" },
      },
    });

    expect(travel.intakeBusy).toBe(false);
    expect(travel.conversation.at(-1)).toEqual({
      role: "assistant",
      content: "我是智策旅行助手，主要帮你规划行程。",
    });
    await vi.waitFor(() => expect(api.travelDraft).toHaveBeenCalledWith("travel-intake"));
  });

  it("restores a persisted handoff as soon as the intake turn finishes", async () => {
    vi.spyOn(api, "travelPlans").mockResolvedValue({ plans: [] });
    vi.spyOn(api, "travelWorkItems").mockResolvedValue({ items: [] });
    vi.spyOn(api, "travelDraft").mockResolvedValue({
      session_id: "travel-intake",
      phase: "intake",
      draft: {},
      handoff_question: "编写冒泡排序算法",
      messages: [
        { role: "user", content: "编写冒泡排序算法" },
        { role: "assistant", content: "这个问题更适合回主聊天继续。" },
      ],
    });
    const travel = useTravelStore();
    travel.sessionId = "travel-intake";
    travel.intakeBusy = true;

    travel.handleEnvelope({
      event: "channel_status",
      session_id: "travel-intake",
      data: { type: "done", assistant: { role: "assistant", content: "这个问题更适合回主聊天继续。" } },
    });

    await vi.waitFor(() => expect(travel.handoffQuestion).toBe("编写冒泡排序算法"));
    expect(travel.conversation).toEqual([
      { role: "user", content: "编写冒泡排序算法" },
      { role: "assistant", content: "这个问题更适合回主聊天继续。" },
    ]);
  });

  it("keeps the main-chat handoff available while the user asks how to return", async () => {
    vi.spyOn(webSocket, "createSession").mockResolvedValue("travel-intake");
    vi.spyOn(webSocket, "sendMessage").mockResolvedValue();
    const travel = useTravelStore();
    travel.handoffQuestion = "帮我写 Python";

    await travel.sendIntake("怎么回主聊天");

    expect(travel.handoffQuestion).toBe("帮我写 Python");
  });

  it("switches from intake thinking to formal generation when confirmation is committed", () => {
    const travel = useTravelStore();
    travel.sessionId = "travel-intake";
    travel.intakeBusy = true;

    travel.handleEnvelope({
      event: "runtime_event",
      session_id: "travel-intake",
      data: {
        type: "travel.planning_confirmed",
        ui_metadata: {
          detail_type: "travel_planning_confirmed",
          detail_data: { phase: "planning" },
        },
      },
    });

    expect(travel.phase).toBe("planning");
    expect(travel.intakeBusy).toBe(false);
    expect(travel.generating).toBe(true);
    expect(travel.statusText).toContain("正式规划");
    expect(travel.progressItems[0]?.title).toBe("旅行条件已确认");
    travel.stopRecoveryPolling();
  });

  it("clears a stale handoff only when an intake event changes travel fields", () => {
    const travel = useTravelStore();
    travel.sessionId = "travel-intake";
    travel.handoffQuestion = "帮我写 Python";

    travel.handleEnvelope({
      event: "runtime_event",
      session_id: "travel-intake",
      data: {
        type: "travel.intake_draft_updated",
        ui_metadata: { detail_data: { draft: sampleDraft(), changed_fields: [] } },
      },
    });
    expect(travel.handoffQuestion).toBe("帮我写 Python");

    travel.handleEnvelope({
      event: "runtime_event",
      session_id: "travel-intake",
      data: {
        type: "travel.intake_draft_updated",
        ui_metadata: { detail_data: { draft: sampleDraft(), changed_fields: ["origin"] } },
      },
    });
    expect(travel.handoffQuestion).toBe("");
  });

  it("applies intake draft and main-chat handoff runtime events", () => {
    const travel = useTravelStore();
    travel.sessionId = "travel-intake";
    travel.intakeBusy = true;

    travel.handleEnvelope({
      event: "runtime_event",
      session_id: "travel-intake",
      data: {
        type: "travel.intake_draft_updated",
        ui_metadata: {
          detail_type: "travel_intake_draft",
          detail_data: { draft: sampleDraft(), missing_fields: [], ready: true },
        },
      },
    });
    travel.handleEnvelope({
      event: "runtime_event",
      session_id: "travel-intake",
      data: {
        type: "travel.main_chat_handoff",
        ui_metadata: {
          detail_type: "travel_main_chat_handoff",
          detail_data: { question: "帮我写 Python", topic: "编程" },
        },
      },
    });

    expect(travel.activeDraft).toEqual(sampleDraft());
    expect(travel.handoffQuestion).toBe("帮我写 Python");
  });

  it("leaves intake thinking state and attempts recovery when the socket closes", async () => {
    vi.spyOn(webSocket, "connect").mockRejectedValue(new Error("offline"));
    const travel = useTravelStore();
    travel.sessionId = "travel-intake";
    travel.intakeBusy = true;

    travel.handleEnvelope({ event: "socket_closed", data: {} });
    await Promise.resolve();

    expect(travel.intakeBusy).toBe(false);
    expect(travel.conversationError).toContain("连接暂时中断");
    expect(webSocket.connect).toHaveBeenCalledOnce();
  });

  it("loads the source Session conversation when a saved plan is opened", async () => {
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      const payload = url.includes("/api/travel/sessions/travel-source/draft")
        ? { session_id: "travel-source", phase: "planning", draft: sampleDraft(), messages: [
          { role: "user", content: "重庆出发去大理五天" },
          { role: "assistant", content: "好的，先确认一共几位出行？" },
        ] }
        : { plan: samplePlan() };
      return new Response(JSON.stringify(payload), { status: 200, headers: { "Content-Type": "application/json" } });
    }));
    const travel = useTravelStore();
    travel.plans = [{ plan_id: "travel-plan-one", owner_user_id: "user-a", source_session_id: "travel-source", source_turn_id: "turn-a", schema_version: "1", title: "大理", destination_summary: "大理", created_at: "", updated_at: "" }];

    await travel.open("travel-plan-one");

    expect(travel.conversation).toEqual([
      { role: "user", content: "重庆出发去大理五天" },
      { role: "assistant", content: "好的，先确认一共几位出行？" },
    ]);
  });

  it("filters internal continuations, tool calls, empty messages and JSON documents", () => {
    expect(visibleTravelConversation([
      { role: "user", content: "继续完成当前旅行规划。不要汇报中间步骤。" },
      { role: "assistant", content: "", tool_calls: [{ id: "call-a" }] },
      { role: "tool", content: "secret tool result" },
      { role: "assistant", content: '{"plan_id":"internal"}' },
      { role: "user", content: "我想住古城附近", metadata: { travel_visibility: "conversation", travel_phase: "requirements" } },
    ])).toEqual([{ role: "user", content: "我想住古城附近" }]);
  });

  it("clears the active requirement conversation when its plan is deleted", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ plan_id: "travel-plan-one", status: "deleted" }), { status: 200, headers: { "Content-Type": "application/json" } })));
    const travel = useTravelStore();
    travel.initializedUserId = "user-a";
    travel.plans = [{ plan_id: "travel-plan-one", owner_user_id: "user-a", source_session_id: "travel-source", source_turn_id: "turn-a", schema_version: "1", title: "大理", destination_summary: "大理", created_at: "", updated_at: "" }];
    travel.activeId = "travel-plan-one";
    travel.activePlan = samplePlan();
    travel.sessionId = "travel-source";
    travel.conversation = [{ role: "user", content: "重庆去大理" }];
    sessionStorage.setItem("zhice.travel.active.user-a", "travel-source");

    await travel.remove("travel-plan-one");

    expect(travel.plans).toEqual([]);
    expect(travel.activePlan).toBeNull();
    expect(travel.conversation).toEqual([]);
    expect(travel.sessionId).toBe("");
    expect(sessionStorage.getItem("zhice.travel.active.user-a")).toBeNull();
  });

  it("reports travel session creation failures without leaving a running state", async () => {
    vi.spyOn(webSocket, "createSession").mockRejectedValue(new Error("connection unavailable"));
    const travel = useTravelStore();

    await travel.generate("重庆到大理五天");

    expect(travel.generating).toBe(false);
    expect(travel.error).toBe("connection unavailable");
    expect(travel.progressItems.at(-1)?.status).toBe("error");
  });

  it("keeps the application subscription alive when the travel page is left", async () => {
    vi.spyOn(webSocket, "connect").mockResolvedValue({} as WebSocket);
    const subscribe = vi.spyOn(webSocket, "subscribe");
    const travel = useTravelStore();

    await travel.initialize("user-a");
    const firstSubscription = travel.unsubscribe;
    await travel.initialize("user-a");

    expect(subscribe).toHaveBeenCalledTimes(1);
    expect(travel.unsubscribe).toBe(firstSubscription);
  });

  it("detaches an active generation and opens an independent blank workspace", () => {
    const travel = useTravelStore();
    travel.initializedUserId = "user-a";
    travel.sessionId = "travel-running";
    travel.generating = true;
    travel.statusText = "正在查询天气";
    sessionStorage.setItem("zhice.travel.active.user-a", "travel-running");

    travel.startNew();

    expect(travel.sessionId).toBe("");
    expect(travel.generating).toBe(false);
    expect(travel.statusText).toBe("");
    expect(sessionStorage.getItem("zhice.travel.active.user-a")).toBeNull();
  });

  it("detaches intake thinking and ignores its late completion in the new workspace", async () => {
    const travel = useTravelStore();
    travel.initializedUserId = "user-a";
    travel.sessionId = "travel-intake-old";
    travel.intakeBusy = true;
    travel.conversation = [{ role: "user", content: "编写冒泡排序算法" }];
    const refresh = vi.spyOn(travel, "refresh").mockResolvedValue();

    travel.startNew();
    travel.handleEnvelope({
      event: "channel_status",
      session_id: "travel-intake-old",
      data: { type: "done", assistant: { role: "assistant", content: "旧计划回复" } },
    });
    await Promise.resolve();

    expect(refresh).toHaveBeenCalledOnce();
    expect(travel.sessionId).toBe("");
    expect(travel.intakeBusy).toBe(false);
    expect(travel.conversation).toEqual([]);
  });

  it("forces a saved plan to complete after loading a stale solve progress cache", async () => {
    vi.spyOn(api, "travelPlan").mockResolvedValue({ plan: samplePlan() });
    vi.spyOn(api, "travelDraft").mockResolvedValue({
      session_id: "travel-source",
      phase: "planning",
      draft: sampleDraft(),
      handoff_question: "",
      messages: [],
    });
    const travel = useTravelStore();
    travel.initializedUserId = "user-a";
    travel.plans = [{ plan_id: "travel-plan-one", owner_user_id: "user-a", source_session_id: "travel-source", source_turn_id: "turn-a", schema_version: "1", title: "大理", destination_summary: "大理", created_at: "", updated_at: "" }];
    sessionStorage.setItem("zhice.travel.progress.user-a.travel-source", JSON.stringify([{
      id: "candidate-selected",
      stage: "solve",
      title: "已选择候选行程",
      detail: "正在完善方案",
      status: "done",
    }]));

    await travel.open("travel-plan-one");

    expect(travel.stage).toBe("complete");
    expect(travel.progressItems.at(-1)).toMatchObject({
      id: "saved-plan-complete",
      stage: "complete",
      status: "done",
    });
  });

  it("refreshes background completion without replacing the new workspace", async () => {
    const travel = useTravelStore();
    travel.initializedUserId = "user-a";
    travel.sessionId = "travel-new";
    travel.conversation = [{ role: "user", content: "去昆明" }];
    const refresh = vi.spyOn(travel, "refresh").mockResolvedValue();
    const open = vi.spyOn(travel, "open").mockResolvedValue();

    travel.handleEnvelope({
      event: "runtime_event",
      session_id: "travel-background",
      data: { type: "travel.plan_ready", metadata: { plan_id: "plan-background" } },
    });
    await Promise.resolve();

    expect(refresh).toHaveBeenCalledOnce();
    expect(open).not.toHaveBeenCalled();
    expect(travel.sessionId).toBe("travel-new");
    expect(travel.conversation).toEqual([{ role: "user", content: "去昆明" }]);
  });

  it("labels a failed Tavily supplement without discarding prior results", () => {
    const travel = useTravelStore();
    travel.sessionId = "travel-a";
    travel.generating = true;
    travel.progressItems = [{
      id: "tavily-success",
      stage: "data",
      title: "Tavily 网页检索完成",
      detail: "取得资料",
      status: "done",
      result: { provider: "Tavily", query: "大理", summary: "", resultCount: 2, items: [] },
    }];

    travel.handleEnvelope({
      event: "runtime_event",
      session_id: "travel-a",
      data: {
        type: "tool.failed",
        tool_call_id: "tavily-timeout",
        metadata: { tool_name: "mcp__tavily__tavily_search" },
        display: { title: "网页资料查询失败", detail: "查询超时" },
      },
    });

    expect(travel.progressItems.at(-1)?.title).toBe("Tavily 补充检索未成功");
    expect(travel.progressItems.at(-1)?.detail).toContain("前面已取得的网页资料继续保留");
    expect(travel.progressItems[0]?.result?.resultCount).toBe(2);
  });

  it("restores a running session from safe per-user session storage", async () => {
    sessionStorage.setItem("zhice.travel.active.user-a", "travel-running");
    vi.spyOn(webSocket, "connect").mockResolvedValue({} as WebSocket);
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      const payload = url.includes("/api/travel/sessions/travel-running/draft")
        ? { session_id: "travel-running", phase: "planning", draft: sampleDraft(), messages: [{ role: "user", content: "重庆去大理" }] }
        : { status: "running", session_id: "travel-running", turn_id: "turn-a", plan_id: "", error_code: "" };
      return new Response(JSON.stringify(payload), { status: 200, headers: { "Content-Type": "application/json" } });
    }));
    const travel = useTravelStore();

    await travel.initialize("user-a");

    expect(travel.sessionId).toBe("travel-running");
    expect(travel.generating).toBe(true);
    expect(travel.progressItems[0]?.title).toBe("已恢复旅行规划");
    await vi.waitFor(() => expect(travel.conversationLoading).toBe(false));
    expect(travel.conversation).toEqual([{ role: "user", content: "重庆去大理" }]);
    travel.stopRecoveryPolling();
  });

  it("loads a completed plan discovered during refresh recovery", async () => {
    sessionStorage.setItem("zhice.travel.active.user-a", "travel-finished");
    vi.spyOn(webSocket, "connect").mockResolvedValue({} as WebSocket);
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      const payload = url.includes("/generation")
        ? { status: "completed", session_id: "travel-finished", turn_id: "turn-a", plan_id: "travel-plan-one", error_code: "" }
        : url.endsWith("/plans") ? { plans: [] } : { plan: samplePlan() };
      return new Response(JSON.stringify(payload), { status: 200, headers: { "Content-Type": "application/json" } });
    }));
    const travel = useTravelStore();

    await travel.initialize("user-a");

    expect(travel.generating).toBe(false);
    expect(travel.activeId).toBe("travel-plan-one");
    expect(travel.stage).toBe("complete");
    expect(sessionStorage.getItem("zhice.travel.active.user-a")).toBeNull();
  });

  it("keeps the session recovery hint after a transient status failure", async () => {
    sessionStorage.setItem("zhice.travel.active.user-a", "travel-running");
    vi.spyOn(webSocket, "connect").mockResolvedValue({} as WebSocket);
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ error: { status: 503, code: "TEMPORARY", message: "暂时不可用" } }), { status: 503, headers: { "Content-Type": "application/json" } })));
    const travel = useTravelStore();

    await travel.initialize("user-a");

    expect(travel.restoreCompleted).toBe(false);
    expect(sessionStorage.getItem("zhice.travel.active.user-a")).toBe("travel-running");
  });

  it("clears the unread completion after the travel workspace is viewed", () => {
    localStorage.setItem("zhice.travel.unread.user-a", "1");
    const travel = useTravelStore();
    travel.initializedUserId = "user-a";
    travel.unreadCompleted = true;

    travel.markViewed();

    expect(travel.unreadCompleted).toBe(false);
    expect(localStorage.getItem("zhice.travel.unread.user-a")).toBeNull();
  });
});

function samplePlan(): TravelPlan {
  return {
    schema_version: "1",
    plan_id: "travel-plan-one",
    owner_user_id: "user-a",
    request: { origin: "重庆", destinations: ["大理"], start_date: "2026-10-01", end_date: "2026-10-02", duration_days: 2, travellers: [{ type: "学生", count: 2 }], budget_total_cny: 5000, planning_mode: "quick" },
    assumptions: [], freshness_summary: {}, transport_options: [], stay_recommendations: [], days: [],
    budget: { lower: 1, expected: 2, upper: 3, items: [] }, weather_summary: [], fallbacks: [], avoidance_tips: [], evidence: [], unknowns: [], generated_at: "2026-09-28T00:00:00Z",
  };
}

function sampleDraft(): TravelRequirementDraft {
  return {
    intent: "travel_requirement", intent_topic: "", origin: "重庆", destinations: ["大理"],
    start_date: "2026-10-01", end_date: "2026-10-03", traveller_type: "", traveller_count: 2,
    budget_total_cny: null, budget_level: "", transport_preferences: [], stay_preferences: [],
    interest_tags: [], pace: "", planning_mode: "", hard_constraints: [],
  };
}
