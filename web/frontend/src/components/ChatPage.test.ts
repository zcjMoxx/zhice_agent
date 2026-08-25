import { createPinia, setActivePinia } from "pinia";
import { mount } from "@vue/test-utils";
import { afterEach, describe, expect, it, vi } from "vitest";

import { api } from "@/api/client";
import type { ChatMessage } from "@/api/types";
import { useAuthStore } from "@/stores/auth";
import { useModelStore } from "@/stores/models";
import { useSessionStore } from "@/stores/sessions";
import { useUiStore } from "@/stores/ui";
import { webSocket } from "@/websocket/client";
import { CHAT_HANDOFF_KEY } from "@/travel/chatHandoff";
import ChatPage from "./ChatPage.vue";

describe("ChatPage", () => {
  afterEach(() => { vi.restoreAllMocks(); });

  it("hides persisted tool calls, tool results, and empty messages from chat history", async () => {
    const pinia = createPinia();
    setActivePinia(pinia);
    const auth = useAuthStore();
    auth.user = { id: "reader", username: "reader", display_name: "Reader", status: "active", roles: ["viewer"], can_manage_admins: false };
    vi.spyOn(api, "sessions").mockResolvedValue({
      sessions: [{ session_id: "weixin-history", title: "微信天气", preview: "明天天气", updated_at: "", message_count: 4, channel: "weixin", conversation_type: "private", continuation_mode: "writable" }],
    });
    vi.spyOn(api, "session").mockResolvedValue({
      session_id: "weixin-history",
      messages: [
        { role: "user", content: "帮我查明天天气" },
        { role: "assistant", content: "", tool_calls: [{ id: "call-discovery" }] },
        { role: "tool", content: "{\"status\":\"activated\"}", tool_call_id: "call-discovery" },
        { role: "assistant", content: "明天晴，最高 30℃。" },
      ],
      metadata: {},
    });
    vi.spyOn(api, "models").mockResolvedValue({ endpoint: "local", current_model: "demo", models: ["demo"] });
    vi.spyOn(webSocket, "subscribe").mockReturnValue(() => undefined);
    vi.spyOn(webSocket, "connect").mockResolvedValue({} as WebSocket);

    const wrapper = mount(ChatPage, { global: { plugins: [pinia] } });
    await vi.waitFor(() => expect(api.session).toHaveBeenCalledWith("weixin-history"));
    await vi.waitFor(() => expect(wrapper.findAll(".message")).toHaveLength(2));

    expect(wrapper.text()).toContain("帮我查明天天气");
    expect(wrapper.text()).toContain("明天晴，最高 30℃。");
    expect(wrapper.text()).not.toContain("activated");
    expect(useSessionStore().messages.map((message) => message.role)).toEqual(["user", "assistant"]);
    wrapper.unmount();
  });

  it("restores the session reading position and does not pull history readers to the latest message", async () => {
    const pinia = createPinia();
    setActivePinia(pinia);
    const auth = useAuthStore();
    auth.user = { id: "reader", username: "reader", display_name: "阅读者", status: "active", roles: ["viewer"], can_manage_admins: false };

    const messages: ChatMessage[] = [{ role: "assistant", content: "一段很长的历史内容" }];
    let resolveSession!: (value: { session_id: string; messages: ChatMessage[]; metadata: Record<string, unknown> }) => void;
    vi.spyOn(api, "sessions").mockResolvedValue({
      sessions: [{ session_id: "s-history", title: "历史", preview: "", updated_at: "", message_count: 1, channel: "web", conversation_type: "private", continuation_mode: "writable" }],
    });
    vi.spyOn(api, "session").mockImplementation(() => new Promise((resolve) => { resolveSession = resolve; }));
    vi.spyOn(api, "models").mockResolvedValue({ endpoint: "local", current_model: "demo", models: ["demo"] });
    vi.spyOn(webSocket, "subscribe").mockReturnValue(() => undefined);
    vi.spyOn(webSocket, "connect").mockResolvedValue({} as WebSocket);

    sessionStorage.setItem("zhice.scroll.reader.s-history", "240");
    const wrapper = mount(ChatPage, { global: { plugins: [pinia] }, attachTo: document.body });
    await vi.waitFor(() => expect(api.session).toHaveBeenCalledWith("s-history"));
    expect(wrapper.find(".chat-header > .chat-heading").exists()).toBe(true);
    expect(wrapper.get(".chat-header > .quick-preferences").classes()).not.toContain("chat-heading");

    const scroller = wrapper.get(".message-scroll").element as HTMLElement;
    Object.defineProperty(scroller, "scrollHeight", { configurable: true, value: 1200 });
    Object.defineProperty(scroller, "clientHeight", { configurable: true, value: 400 });
    resolveSession({ session_id: "s-history", messages, metadata: {} });
    await vi.waitFor(() => expect(scroller.scrollTop).toBe(240));

    const sessions = useSessionStore();
    sessions.messages[0].content += "，并继续流式输出";
    await wrapper.vm.$nextTick();
    expect(scroller.scrollTop).toBe(240);

    scroller.scrollTop = 360;
    await wrapper.get(".message-scroll").trigger("scroll");
    expect(sessionStorage.getItem("zhice.scroll.reader.s-history")).toBe("360");
    wrapper.unmount();
  });

  it("keeps New Session as a draft while allowing a model choice before the first message", async () => {
    const pinia = createPinia();
    setActivePinia(pinia);
    const auth = useAuthStore();
    auth.user = { id: "starter", username: "starter", display_name: "Starter", status: "active", roles: ["viewer"], can_manage_admins: false };
    const ui = useUiStore();
    ui.startPage = "new";
    vi.spyOn(api, "sessions").mockResolvedValue({
      sessions: [{ session_id: "recent", title: "最近", preview: "", updated_at: "", message_count: 1, channel: "web", conversation_type: "private", continuation_mode: "writable" }],
    });
    const sessionSpy = vi.spyOn(api, "session");
    vi.spyOn(api, "models").mockResolvedValue({ endpoint: "local", current_model: "demo", models: ["demo", "model-b"] });
    const setModel = vi.spyOn(api, "setModel").mockResolvedValue({ endpoint: "local", current_model: "model-b", models: ["demo", "model-b"] });
    vi.spyOn(webSocket, "subscribe").mockReturnValue(() => undefined);
    vi.spyOn(webSocket, "connect").mockResolvedValue({} as WebSocket);
    const createSession = vi.spyOn(webSocket, "createSession").mockResolvedValue("fresh-session");
    const sendMessage = vi.spyOn(webSocket, "sendMessage").mockResolvedValue();

    const wrapper = mount(ChatPage, { global: { plugins: [pinia] } });
    await vi.waitFor(() => expect(api.sessions).toHaveBeenCalled());

    expect(sessionSpy).not.toHaveBeenCalled();
    expect(createSession).not.toHaveBeenCalled();
    expect(useSessionStore().activeId).toBe("");
    expect(useSessionStore().messages).toEqual([]);
    expect(wrapper.text()).toContain("今天想一起完成什么？");
    await vi.waitFor(() => expect(wrapper.get(".model-picker select").attributes("disabled")).toBeUndefined());
    await wrapper.get(".model-picker select").setValue("model-b");
    expect(setModel).not.toHaveBeenCalled();
    expect(createSession).not.toHaveBeenCalled();
    await wrapper.get(".composer textarea").setValue("你好");
    await wrapper.get(".composer").trigger("submit");
    await vi.waitFor(() => expect(createSession).toHaveBeenCalledOnce());

    expect(useSessionStore().activeId).toBe("fresh-session");
    expect(setModel).toHaveBeenCalledWith("fresh-session", "model-b");
    await vi.waitFor(() => expect(sendMessage).toHaveBeenCalledWith("fresh-session", "你好", "model-b"));
    expect(useModelStore().current).toBe("model-b");
    wrapper.unmount();
  });

  it("prefills a one-time travel handoff in a new local draft without auto-sending", async () => {
    const pinia = createPinia();
    setActivePinia(pinia);
    const auth = useAuthStore();
    auth.user = { id: "starter", username: "starter", display_name: "Starter", status: "active", roles: ["viewer"], can_manage_admins: false };
    vi.spyOn(api, "sessions").mockResolvedValue({ sessions: [{ session_id: "recent", title: "最近", preview: "", updated_at: "", message_count: 1, channel: "web", conversation_type: "private", continuation_mode: "writable" }] });
    vi.spyOn(api, "models").mockResolvedValue({ endpoint: "local", current_model: "demo", models: ["demo"] });
    vi.spyOn(webSocket, "subscribe").mockReturnValue(() => undefined);
    vi.spyOn(webSocket, "connect").mockResolvedValue({} as WebSocket);
    const createSession = vi.spyOn(webSocket, "createSession").mockResolvedValue("fresh-session");
    const sendMessage = vi.spyOn(webSocket, "sendMessage").mockResolvedValue();
    sessionStorage.setItem(CHAT_HANDOFF_KEY, "帮我写一段 Python 代码");

    const wrapper = mount(ChatPage, { global: { plugins: [pinia] } });
    await vi.waitFor(() => expect((wrapper.get(".composer textarea").element as HTMLTextAreaElement).value).toBe("帮我写一段 Python 代码"));

    expect(sessionStorage.getItem(CHAT_HANDOFF_KEY)).toBeNull();
    expect(useSessionStore().activeId).toBe("");
    expect(createSession).not.toHaveBeenCalled();
    expect(sendMessage).not.toHaveBeenCalled();
    wrapper.unmount();
  });

  it("shows a travel handoff before a slow session refresh finishes", async () => {
    const pinia = createPinia();
    setActivePinia(pinia);
    const auth = useAuthStore();
    auth.user = { id: "starter", username: "starter", display_name: "Starter", status: "active", roles: ["viewer"], can_manage_admins: false };
    vi.spyOn(api, "sessions").mockImplementation(() => new Promise(() => undefined));
    vi.spyOn(api, "models").mockResolvedValue({ endpoint: "local", current_model: "demo", models: ["demo"] });
    vi.spyOn(webSocket, "subscribe").mockReturnValue(() => undefined);
    vi.spyOn(webSocket, "connect").mockResolvedValue({} as WebSocket);
    sessionStorage.setItem(CHAT_HANDOFF_KEY, "继续讨论这段代码");

    const wrapper = mount(ChatPage, { global: { plugins: [pinia] } });
    await vi.waitFor(() => expect((wrapper.get(".composer textarea").element as HTMLTextAreaElement).value).toBe("继续讨论这段代码"));

    expect(useSessionStore().activeId).toBe("");
    wrapper.unmount();
  });
});
