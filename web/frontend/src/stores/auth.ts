import { defineStore } from "pinia";

import { api, ApiError, onAuthorizationFailure } from "@/api/client";
import type { PublicUser } from "@/api/types";

export const useAuthStore = defineStore("auth", {
  state: () => ({
    user: null as PublicUser | null,
    permissions: [] as string[],
    initialized: false,
    loading: false,
    error: "",
  }),
  getters: {
    authenticated: (state) => Boolean(state.user),
    isOwner: (state) => state.user?.roles.includes("owner") ?? false,
    can: (state) => (permission: string) => state.permissions.includes(permission),
    canOpenAdmin(): boolean {
      return ["auth.users.read", "auth.roles.read", "turn.read.any", "diagnostics.system.use", "audit.read"].some((permission) => this.permissions.includes(permission));
    },
  },
  actions: {
    async fetchCurrentUser() {
      this.loading = true;
      try {
        const me = await api.me();
        this.user = me.user;
        this.permissions = me.permissions;
      } catch (error) {
        if (!(error instanceof ApiError && error.status === 401)) this.error = error instanceof Error ? error.message : "无法读取账号";
        this.user = null;
        this.permissions = [];
      } finally {
        this.initialized = true;
        this.loading = false;
      }
    },
    async login(username: string, password: string) {
      this.error = "";
      await api.login(username, password);
      await this.fetchCurrentUser();
    },
    async register(username: string, password: string) {
      this.error = "";
      await api.register(username, password);
      await this.fetchCurrentUser();
    },
    async bootstrap(setupToken: string, password: string) {
      await api.bootstrap(setupToken, password);
      await this.fetchCurrentUser();
    },
    async logout() {
      try { await api.logout(); } finally {
        this.user = null;
        this.permissions = [];
      }
    },
    async updateProfile(displayName: string) {
      const me = await api.updateProfile(displayName);
      this.user = me.user;
      this.permissions = me.permissions;
    },
    async changePassword(currentPassword: string, newPassword: string) {
      await api.changePassword(currentPassword, newPassword);
      this.user = null;
      this.permissions = [];
    },
  },
});

export function installAuthorizationRefresh(): void {
  onAuthorizationFailure(async (status) => {
    if (status !== 401) return;
    const auth = useAuthStore();
    auth.user = null;
    auth.permissions = [];
  });
}
