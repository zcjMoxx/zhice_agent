import { createPinia, setActivePinia } from "pinia";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { api } from "@/api/client";
import { useWorkflowStore } from "./workflows";

vi.mock("@/api/client", () => ({
  api: {
    workflows: vi.fn(),
    saveWorkflowDraft: vi.fn(),
    publishWorkflow: vi.fn(),
    runWorkflow: vi.fn(),
    workflowRuns: vi.fn(),
    workflowRun: vi.fn(),
    workflow: vi.fn(),
  },
}));

describe("workflow store", () => {
  beforeEach(() => {
    setActivePinia(createPinia());
    vi.clearAllMocks();
    vi.mocked(api.workflows).mockResolvedValue({ items: [] });
  });

  it("reloads persisted timestamps and opens the completed run after run now", async () => {
    const definition = {
      schema_version: 1 as const,
      workflow_id: "workflow-1",
      owner_user_id: "user-1",
      name: "天气摘要（已修改）",
      description: "",
      timezone: "Asia/Shanghai",
      version: 1,
      status: "active" as const,
      nodes: [],
      edges: [],
      required_permissions: ["workflow.use"],
      connection_ids: [],
    };
    vi.mocked(api.saveWorkflowDraft).mockResolvedValue({
      ...definition,
      version: 2,
      active_version: 1,
      has_unpublished_changes: true,
    });
    vi.mocked(api.publishWorkflow).mockResolvedValue({
      ...definition,
      version: 2,
      active_version: 2,
      has_unpublished_changes: false,
    });
    vi.mocked(api.runWorkflow).mockResolvedValue({
      run_id: "run-1",
      workflow_id: "workflow-1",
      status: "succeeded",
    });
    vi.mocked(api.workflowRuns).mockResolvedValue({
      items: [{
        id: "run-1",
        run_id: "run-1",
        workflow_id: "workflow-1",
        status: "succeeded",
        started_at: "2026-08-21T17:14:03Z",
      }],
    });
    vi.mocked(api.workflowRun).mockResolvedValue({
      id: "run-1",
      run_id: "run-1",
      workflow_id: "workflow-1",
      status: "succeeded",
      nodes: [],
    });

    const store = useWorkflowStore();
    store.current = {
      schema_version: 1,
      workflow_id: "workflow-1",
      owner_user_id: "user-1",
      name: "天气摘要",
      description: "",
      timezone: "Asia/Shanghai",
      status: "active",
      version: 1,
      active_version: 1,
      has_unpublished_changes: false,
      nodes: [],
      edges: [],
      required_permissions: ["workflow.use"],
      connection_ids: [],
    };

    await store.runNow(definition);

    expect(api.saveWorkflowDraft).toHaveBeenCalledWith("workflow-1", definition, 1);
    expect(api.publishWorkflow).toHaveBeenCalledWith("workflow-1");
    expect(vi.mocked(api.saveWorkflowDraft).mock.invocationCallOrder[0]).toBeLessThan(vi.mocked(api.publishWorkflow).mock.invocationCallOrder[0]);
    expect(vi.mocked(api.publishWorkflow).mock.invocationCallOrder[0]).toBeLessThan(vi.mocked(api.runWorkflow).mock.invocationCallOrder[0]);
    expect(api.workflowRuns).toHaveBeenCalledWith("workflow-1");
    expect(api.workflowRun).toHaveBeenCalledWith("run-1");
    expect(store.runs[0]?.started_at).toBe("2026-08-21T17:14:03Z");
    expect(store.runDetail?.run_id).toBe("run-1");
  });

  it("keeps the current canvas and retries once on a newer server version", async () => {
    const current = {
      schema_version: 1 as const,
      workflow_id: "workflow-1",
      owner_user_id: "user-1",
      name: "每日天气建议",
      description: "",
      timezone: "Asia/Shanghai",
      version: 1,
      status: "active" as const,
      nodes: [{ id: "trigger", type: "schedule_trigger" as const, title: "每天定时", position: { x: 80, y: 120 }, config: { time_of_day: "21:56" } }],
      edges: [],
      required_permissions: ["workflow.use"],
      connection_ids: [],
      active_version: 1,
      has_unpublished_changes: false,
    };
    const latest = { ...current, version: 2, active_version: 2 };
    const saved = { ...current, version: 3, active_version: 2, has_unpublished_changes: true };
    vi.mocked(api.saveWorkflowDraft)
      .mockRejectedValueOnce({ code: "WORKFLOW_DRAFT_CONFLICT" })
      .mockResolvedValueOnce(saved);
    vi.mocked(api.workflow).mockResolvedValue(latest);

    const store = useWorkflowStore();
    store.current = current;
    await store.save(current);

    expect(api.workflow).toHaveBeenCalledWith("workflow-1");
    expect(api.saveWorkflowDraft).toHaveBeenNthCalledWith(2, "workflow-1", expect.objectContaining({
      version: 2,
      nodes: expect.arrayContaining([expect.objectContaining({ config: expect.objectContaining({ time_of_day: "21:56" }) })]),
    }), 2);
    expect(store.current?.version).toBe(3);
  });
});
