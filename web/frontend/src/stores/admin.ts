import { defineStore } from "pinia";

import { api } from "@/api/client";
import type { MonitorSnapshot, PublicUser, Role, SystemDiagnosticsSnapshot } from "@/api/types";

export const useAdminStore = defineStore("admin", {
  state: () => ({
    users: [] as PublicUser[],
    roles: [] as Role[],
    permissions: [] as string[],
    monitor: null as MonitorSnapshot | null,
    diagnostics: null as SystemDiagnosticsSnapshot | null,
    auditEvents: [] as Record<string, unknown>[],
    auditCursor: "",
    auditHasMore: false,
    loading: false,
    error: "",
  }),
  actions: {
    async loadUsers() { this.users = (await api.users()).users; },
    async loadRoles() { const result = await api.roles(); this.roles = result.roles; this.permissions = result.permissions; },
    async updateRole(id: string, keys: string[]) {
      const updated = await api.updateRole(id, keys);
      this.roles = this.roles.map((role) => role.id === id ? updated : role);
    },
    async loadMonitor() { this.monitor = await api.monitor(); },
    async loadDiagnostics(filters: Record<string, string> = {}) {
      this.diagnostics = await api.diagnostics(new URLSearchParams({ minutes: "60", limit: "100", ...filters }));
    },
    async loadAudit(filters: Record<string, string> = {}, append = false) {
      const query = new URLSearchParams({ limit: "50", ...filters });
      if (append && this.auditCursor) query.set("cursor", this.auditCursor);
      const result = await api.audit(query);
      this.auditEvents = append ? [...this.auditEvents, ...result.events] : result.events;
      this.auditCursor = result.next_cursor ?? "";
      this.auditHasMore = Boolean(result.has_more);
    },
  },
});
