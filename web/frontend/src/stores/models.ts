import { defineStore } from "pinia";

import { api } from "@/api/client";

export const useModelStore = defineStore("models", {
  state: () => ({ endpoint: "", current: "", models: [] as string[], loading: false }),
  actions: {
    async refresh(sessionId: string) {
      if (!sessionId) { this.endpoint = ""; this.current = ""; this.models = []; return; }
      this.loading = true;
      try {
        const state = await api.models(sessionId);
        this.endpoint = state.endpoint;
        this.current = state.current_model;
        this.models = state.models;
      } finally { this.loading = false; }
    },
    async select(sessionId: string, model: string) {
      const state = await api.setModel(sessionId, model);
      this.endpoint = state.endpoint;
      this.current = state.current_model;
      this.models = state.models;
    },
  },
});
