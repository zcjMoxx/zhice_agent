import { defineStore } from "pinia";

import { api } from "@/api/client";
import type { ChatMessage, SessionSummary } from "@/api/types";
import { webSocket } from "@/websocket/client";
import { useAuthStore } from "./auth";

export const useSessionStore = defineStore("sessions", {
  state: () => ({
    items: [] as SessionSummary[],
    activeId: "",
    messages: [] as ChatMessage[],
    metadata: {} as Record<string, unknown>,
    loading: false,
    search: "",
  }),
  getters: {
    filtered: (state) => state.items.filter((item) => `${item.title} ${item.preview}`.toLowerCase().includes(state.search.toLowerCase())),
    active: (state) => state.items.find((item) => item.session_id === state.activeId) ?? null,
    writable(): boolean { return !this.active || this.active.continuation_mode === "writable"; },
  },
  actions: {
    async refresh(openFirst = false) {
      this.loading = true;
      try {
        this.items = (await api.sessions()).sessions;
        if (openFirst && !this.activeId && this.items[0]) await this.open(this.items[0].session_id);
      } finally { this.loading = false; }
    },
    async open(id: string) {
      const state = await api.session(id);
      this.activeId = id;
      this.messages = state.messages;
      this.metadata = state.metadata;
      const userId = useAuthStore().user?.id || "local";
      localStorage.setItem(`zhice.lastSession.${userId}`, id);
    },
    async create() {
      const id = await webSocket.createSession();
      const placeholder: SessionSummary = { session_id: id, title: "新对话", preview: "", updated_at: new Date().toISOString(), message_count: 0, channel: "web", conversation_type: "private", continuation_mode: "writable" };
      this.items = [placeholder, ...this.items.filter((item) => item.session_id !== id)];
      this.activeId = id;
      this.messages = [];
      this.metadata = {};
      return id;
    },
    async rename(id: string, title: string) {
      await api.renameSession(id, title);
      const item = this.items.find((session) => session.session_id === id);
      if (item) item.title = title.trim();
    },
    async remove(id: string) {
      await api.deleteSession(id);
      this.items = this.items.filter((item) => item.session_id !== id);
      if (this.activeId === id) {
        this.activeId = "";
        this.messages = [];
        if (this.items[0]) await this.open(this.items[0].session_id);
      }
    },
    async forkActive() {
      if (!this.activeId) return;
      const fork = await api.forkSession(this.activeId);
      await this.refresh();
      await this.open(fork.session_id);
    },
  },
});
