import { defineStore } from "pinia";

import { api } from "@/api/client";

export const useModelStore = defineStore("models", {
  state: () => ({ endpoint: "", current: "", models: [] as string[], loading: false, requestVersion: 0 }),
  actions: {
    async refresh(sessionId: string) {
      const version = ++this.requestVersion;
      this.loading = true;
      try {
        const state = await api.models(sessionId);
        if (version !== this.requestVersion) return;
        this.endpoint = state.endpoint;
        this.current = state.current_model;
        this.models = state.models;
      } finally {
        if (version === this.requestVersion) this.loading = false;
      }
    },
    async select(sessionId: string, model: string) {
      if (!sessionId) {
        if (this.models.includes(model)) this.current = model;
        return;
      }
      const version = ++this.requestVersion;
      this.loading = true;
      try {
        const state = await api.setModel(sessionId, model);
        if (version !== this.requestVersion) return;
        this.endpoint = state.endpoint;
        this.current = state.current_model;
        this.models = state.models;
      } finally {
        if (version === this.requestVersion) this.loading = false;
      }
    },
  },
});
