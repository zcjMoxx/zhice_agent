import { defineStore } from "pinia";

import { api } from "@/api/client";
import type { WorkflowDefinitionV1, WorkflowDetail, WorkflowRun, WorkflowSummary } from "@/api/types";

function defaultDefinition(name = "未命名工作流"): WorkflowDefinitionV1 {
  return {
    schema_version: 1,
    name,
    description: "",
    timezone: Intl.DateTimeFormat().resolvedOptions().timeZone || "Asia/Shanghai",
    nodes: [{ id: "trigger", type: "schedule_trigger", title: "开始", position: { x: 80, y: 120 }, config: { trigger_type: "manual", schedule_mode: "manual" } }],
    edges: [],
    required_permissions: ["workflow.use"],
    connection_ids: [],
  };
}

export const useWorkflowStore = defineStore("workflows", {
  state: () => ({
    items: [] as WorkflowSummary[],
    current: null as WorkflowDetail | null,
    runs: [] as WorkflowRun[],
    runDetail: null as WorkflowRun | null,
    loading: false,
    saving: false,
  }),
  actions: {
    async loadAll() {
      this.loading = true;
      try { this.items = (await api.workflows()).items || []; }
      finally { this.loading = false; }
    },
    async open(id: string) {
      this.current = await api.workflow(id);
      this.runDetail = null;
      await this.loadRuns();
    },
    async create(definition: WorkflowDefinitionV1 = defaultDefinition()) {
      this.current = await api.createWorkflow(definition);
      await this.loadAll();
      this.runs = [];
      this.runDetail = null;
    },
    async save(definition: WorkflowDefinitionV1) {
      if (!this.current) return;
      this.saving = true;
      try {
        const workflowId = this.current.workflow_id;
        try {
          this.current = await api.saveWorkflowDraft(workflowId, definition, this.current.version);
        } catch (error) {
          if (!error || typeof error !== "object" || (error as { code?: string }).code !== "WORKFLOW_DRAFT_CONFLICT") throw error;
          const latest = await api.workflow(workflowId);
          const rebasedDefinition: WorkflowDefinitionV1 = {
            ...definition,
            workflow_id: latest.workflow_id,
            owner_user_id: latest.owner_user_id,
            version: latest.version,
            status: latest.status,
          };
          this.current = await api.saveWorkflowDraft(workflowId, rebasedDefinition, latest.version);
        }
        await this.loadAll();
        return this.current;
      } finally { this.saving = false; }
    },
    async publish(definition: WorkflowDefinitionV1) {
      if (!this.current) return;
      await this.save(definition);
      if (this.current && (this.current.active_version == null || this.current.has_unpublished_changes)) {
        this.current = await api.publishWorkflow(this.current.workflow_id);
        await this.loadAll();
      }
      return this.current;
    },
    async togglePaused() {
      if (!this.current) return;
      if (this.current.status === "paused") await api.resumeWorkflow(this.current.workflow_id);
      else await api.pauseWorkflow(this.current.workflow_id);
      this.current = await api.workflow(this.current.workflow_id);
      await this.loadAll();
    },
    async runNow(definition: WorkflowDefinitionV1) {
      if (!this.current) return;
      await this.save(definition);
      if (!this.current) return;
      const run = await api.runWorkflowDraft(this.current.workflow_id);
      await this.loadRuns();
      await this.openRun(run.run_id);
    },
    async loadRuns() {
      if (!this.current) return;
      this.runs = ((await api.workflowRuns(this.current.workflow_id)).items || []).map((run) => ({ ...run, run_id: run.run_id || run.id || "" }));
    },
    async openRun(runId: string) {
      this.runDetail = await api.workflowRun(runId);
    },
    async toggleRun(runId: string) {
      if ((this.runDetail?.id || this.runDetail?.run_id) === runId) {
        this.runDetail = null;
        return;
      }
      await this.openRun(runId);
    },
    async remove() {
      if (!this.current) return;
      await api.deleteWorkflow(this.current.workflow_id);
      this.current = null;
      this.runs = [];
      this.runDetail = null;
      await this.loadAll();
    },
    close() {
      this.current = null;
      this.runs = [];
      this.runDetail = null;
    },
  },
});

export { defaultDefinition };
