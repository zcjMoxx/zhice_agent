import { defineStore } from "pinia";

import { api } from "@/api/client";
import type { MonitorSnapshot, OperationsTerminal, PublicUser, RegistrationPolicy, Role, SkillSourcesSnapshot, SystemDiagnosticsSnapshot } from "@/api/types";

export const useAdminStore = defineStore("admin", {
  state: () => ({
    users: [] as PublicUser[],
    roles: [] as Role[],
    permissions: [] as string[],
    skillSources: null as SkillSourcesSnapshot | null,
    operationsTerminal: null as OperationsTerminal | null,
    registrationPolicy: null as RegistrationPolicy | null,
    registrationPolicyBusy: false,
    skillActionSource: "",
    monitor: null as MonitorSnapshot | null,
    diagnostics: null as SystemDiagnosticsSnapshot | null,
    auditEvents: [] as Record<string, unknown>[],
    auditCursor: "",
    auditHasMore: false,
    auditPageCursors: [""] as string[],
    auditPageIndex: 0,
    loading: false,
    error: "",
  }),
  actions: {
    async loadUsers() { this.users = (await api.users()).users; },
    async loadRegistrationPolicy() { this.registrationPolicy = await api.ownerRegistrationPolicy(); },
    async updateRegistrationPolicy(enabled: boolean) {
      this.registrationPolicyBusy = true;
      try { this.registrationPolicy = await api.updateOwnerRegistrationPolicy(enabled); }
      finally { this.registrationPolicyBusy = false; }
    },
    async loadRoles() { const result = await api.roles(); this.roles = result.roles; this.permissions = result.permissions; },
    async updateRole(id: string, keys: string[]) {
      const updated = await api.updateRole(id, keys);
      this.roles = this.roles.map((role) => role.id === id ? updated : role);
    },
    async loadSkillSources() { this.skillSources = await api.skillSources(); },
    async syncSkillSource(source: string) {
      this.skillActionSource = source;
      try { await api.syncSkillSource(source); await this.loadSkillSources(); }
      finally { this.skillActionSource = ""; }
    },
    async refreshSkillSourceIndex(source: string) {
      this.skillActionSource = source;
      try { await api.refreshSkillSourceIndex(source); await this.loadSkillSources(); }
      finally { this.skillActionSource = ""; }
    },
    async loadOperationsTerminal() { this.operationsTerminal = await api.operationsTerminal(); },
    async loadMonitor(status = "error") {
      const query = new URLSearchParams({ limit: "50" });
      if (status) query.set("status", status);
      this.monitor = await api.monitor(query);
    },
    async loadDiagnostics(filters: Record<string, string> = {}) {
      this.diagnostics = await api.diagnostics(new URLSearchParams({ minutes: "1440", limit: "100", ...filters }));
    },
    async loadAudit(filters: Record<string, string> = {}, direction: "reset" | "next" | "previous" = "reset") {
      if (direction === "reset") {
        this.auditPageCursors = [""];
        this.auditPageIndex = 0;
      } else if (direction === "next") {
        if (!this.auditCursor) return;
        this.auditPageCursors = [...this.auditPageCursors.slice(0, this.auditPageIndex + 1), this.auditCursor];
        this.auditPageIndex += 1;
      } else if (this.auditPageIndex > 0) {
        this.auditPageIndex -= 1;
      } else return;
      const query = new URLSearchParams({ limit: "50", ...filters });
      const pageCursor = this.auditPageCursors[this.auditPageIndex] || "";
      if (pageCursor) query.set("cursor", pageCursor);
      const result = await api.audit(query);
      this.auditEvents = result.events;
      this.auditCursor = result.next_cursor ?? "";
      this.auditHasMore = Boolean(result.has_more);
    },
  },
});
