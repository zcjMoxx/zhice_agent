import { defineStore } from "pinia";

import { api, ApiError } from "@/api/client";
import type { RuntimeUiState, WsEnvelope } from "@/api/types";
import { applyRuntimeEvent, emptyRuntimeState } from "@/runtime-events/reducer";
import { webSocket } from "@/websocket/client";
import { useModelStore } from "./models";
import { useSessionStore } from "./sessions";

interface ActiveTurn { sessionId: string; turnId: string }
interface Confirmation { id: string; tool_name?: string; risk_level?: string; command_preview?: string }
interface Elicitation { interaction_id: string; server_id?: string; message?: string; requested_schema?: Record<string, unknown>; url?: string }

export const useChatStore = defineStore("chat", {
  state: () => ({
    sending: false,
    activeTurn: null as ActiveTurn | null,
    runtime: emptyRuntimeState() as RuntimeUiState,
    confirmation: null as Confirmation | null,
    elicitation: null as Elicitation | null,
    error: "",
    initialized: false,
    unsubscribe: null as (() => void) | null,
  }),
  actions: {
    initialize() {
      if (this.initialized) return;
      this.unsubscribe = webSocket.subscribe((envelope) => this.handleEnvelope(envelope));
      this.initialized = true;
      void webSocket.connect().catch(() => undefined);
    },
    async send(content: string) {
      const sessions = useSessionStore();
      const models = useModelStore();
      const text = content.trim();
      if (!text || this.sending || !sessions.writable) return;
      if (!sessions.activeId) await sessions.create();
      const sessionId = sessions.activeId;
      sessions.messages.push({ role: "user", content: text });
      sessions.messages.push({ role: "assistant", content: "", pending: true, runtime: emptyRuntimeState() });
      this.sending = true;
      this.error = "";
      this.runtime = emptyRuntimeState();
      this.activeTurn = { sessionId, turnId: "" };
      try { await webSocket.sendMessage(sessionId, text, models.current || "auto"); }
      catch (error) { this.fail(error instanceof Error ? error.message : "发送失败"); }
    },
    async stop() {
      if (this.activeTurn) await webSocket.stop(this.activeTurn.sessionId);
    },
    async decideConfirmation(approved: boolean) {
      if (!this.confirmation) return;
      await api.confirmation(this.confirmation.id, approved);
      this.confirmation = null;
    },
    async respondToElicitation(action: "accept" | "cancel", response: Record<string, unknown> | null) {
      if (!this.elicitation || !this.activeTurn) return;
      await webSocket.respondToElicitation(this.activeTurn.sessionId, this.elicitation.interaction_id, action, response);
      this.elicitation = null;
    },
    handleEnvelope(envelope: WsEnvelope) {
      const sessions = useSessionStore();
      if (envelope.event === "socket_closed") {
        if (this.sending) this.fail("WebSocket 连接已关闭");
        return;
      }
      if (envelope.event === "tool_confirmation_required") {
        this.confirmation = envelope.data as Confirmation;
        return;
      }
      if (envelope.event === "mcp_elicitation_requested") {
        this.elicitation = envelope.data as Elicitation;
        return;
      }
      if (envelope.event === "runtime_event" && this.activeTurn) {
        const sessions = useSessionStore();
        const pending = [...sessions.messages].reverse().find((message) => message.pending);
        const updated = applyRuntimeEvent(
          this.runtime,
          envelope,
          this.activeTurn.turnId,
          this.activeTurn.sessionId,
        );
        if (updated !== this.runtime) {
          this.runtime = updated;
          if (pending) pending.runtime = updated;
        }
        return;
      }
      if (!this.activeTurn || envelope.session_id !== this.activeTurn.sessionId) return;
      const pending = [...sessions.messages].reverse().find((message) => message.pending);
      if (envelope.event === "channel_text" && pending) {
        pending.content += String(envelope.data ?? "");
        return;
      }
      if (envelope.event !== "channel_status") return;
      const status = envelope.data as { type?: string; turn_id?: string; assistant?: { content?: string }; error?: { message?: string; code?: string } };
      if (status.type === "accepted") {
        this.activeTurn.turnId = String(status.turn_id ?? envelope.turn_id ?? "");
        return;
      }
      if (status.type === "done" || status.type === "stopped") {
        if (pending) {
          pending.pending = false;
          pending.runtime = undefined;
          if (!pending.content) pending.content = String(status.assistant?.content ?? (status.type === "stopped" ? "已停止" : ""));
        }
        this.finish();
        void sessions.refresh();
        return;
      }
      if (status.type === "error") this.fail(status.error?.message || "请求失败");
    },
    finish() {
      this.sending = false;
      this.activeTurn = null;
      this.runtime = emptyRuntimeState();
    },
    fail(message: string) {
      const sessions = useSessionStore();
      const pending = [...sessions.messages].reverse().find((item) => item.pending);
      if (pending) { pending.pending = false; pending.content = pending.content || `请求失败：${message}`; }
      this.error = message;
      this.finish();
    },
  },
});

export function errorMessage(error: unknown): string {
  if (error instanceof ApiError) return `${error.message} (${error.code})`;
  return error instanceof Error ? error.message : "操作失败";
}
