import { createPinia, setActivePinia } from "pinia";
import { flushPromises, mount } from "@vue/test-utils";
import { createMemoryHistory, createRouter } from "vue-router";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { useAuthStore } from "@/stores/auth";
import AdminLayout from "./AdminLayout.vue";

function response(payload: unknown): Response {
  return new Response(JSON.stringify(payload), { status: 200, headers: { "Content-Type": "application/json" } });
}

describe("AdminLayout", () => {
  const roles = [
    { id: "role-admin", key: "admin", name: "Administrator", description: "Administrator role", is_builtin: true, permission_keys: ["audit.read"] },
    { id: "role-auditor", key: "auditor", name: "Auditor", description: "Auditor role", is_builtin: true, permission_keys: ["audit.read"] },
    { id: "role-dev", key: "developer", name: "Developer", description: "Developer role", is_builtin: true, permission_keys: [] },
    { id: "role-owner", key: "owner", name: "Owner", description: "System owner", is_builtin: true, permission_keys: ["audit.read"] },
    { id: "role-viewer", key: "viewer", name: "Viewer", description: "Viewer role", is_builtin: true, permission_keys: [] },
  ];

  beforeEach(() => {
    vi.stubGlobal("fetch", vi.fn().mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.startsWith("/api/admin/roles") && init?.method === "PATCH") return Promise.resolve(response({ id: "role-dev", key: "developer", name: "Developer", description: "", is_builtin: true, permission_keys: ["audit.read"] }));
      if (url.startsWith("/api/admin/roles")) return Promise.resolve(response({ roles, permissions: ["audit.read"] }));
      if (url.startsWith("/api/admin/diagnostics")) return Promise.resolve(response({ status: "ok", window_minutes: 60, filters: {}, summary: { incidents: 1 }, incidents: [{ incident_id: "inc-1", component: "llm", code: "RATE_LIMITED", subject: "primary", count: 2, last_seen_at: "2026-07-29T00:00:00Z", rule: "same_component_code_subject_within_query_window" }], timeline: [{ evidence_id: "evt-1", ts: "2026-07-29T00:00:00Z", component: "llm", event: "llm.error", code: "RATE_LIMITED" }], limitations: [] }));
      if (url.startsWith("/api/admin/monitor")) return Promise.resolve(response({ gateway: { status: "ok", current_model: "default/model" }, capabilities: {}, activity: { summary: {}, recent_turns: [], recent_tools: [] } }));
      if (url.startsWith("/api/audit/events")) return Promise.resolve(response({ events: [{ id: "audit-1", ts: "2026-07-27T00:00:00Z", action: "role.updated", decision: "allow" }], next_cursor: "", has_more: false }));
      return Promise.resolve(response({}));
    }));
  });

  async function wrapper(userRoles = ["owner"]) {
    const pinia = createPinia();
    setActivePinia(pinia);
    const auth = useAuthStore();
    auth.user = { id: "actor", username: "actor", display_name: "Actor", status: "active", roles: userRoles, can_manage_admins: userRoles.includes("owner") };
    auth.permissions = ["auth.roles.read", "auth.roles.manage", "audit.read", "audit.export", "turn.read.any", "diagnostics.system.use"];
    const router = createRouter({ history: createMemoryHistory(), routes: [{ path: "/", component: { template: "<div />" } }] });
    const mounted = mount(AdminLayout, { global: { plugins: [pinia, router] } });
    await flushPromises();
    return mounted;
  }

  it("shows Chinese role capabilities while keeping technical keys collapsed", async () => {
    const mounted = await wrapper();
    await mounted.findAll(".admin-sidebar nav button").find((button) => button.text() === "角色与权限")!.trigger("click");
    await flushPromises();
    expect(mounted.text()).toContain("所有登录用户的基础能力");
    expect(mounted.text()).toContain("查看安全审计");
    expect(mounted.find(".technical-details").attributes("open")).toBeUndefined();
    await mounted.findAll(".role-list button").find((button) => button.text().includes("开发者"))!.trigger("click");
    await mounted.get('.permission-group input[type="checkbox"]').setValue(true);
    await flushPromises();
    expect(fetch).toHaveBeenCalledWith("/api/admin/roles/role-dev", expect.objectContaining({ method: "PATCH" }));
  });

  it("locks system role permissions in both UI and event handling", async () => {
    const mounted = await wrapper();
    await mounted.findAll(".admin-sidebar nav button").find((button) => button.text() === "角色与权限")!.trigger("click");
    await flushPromises();

    expect(mounted.get(".role-lock").text()).toContain("系统固定，权限不可修改");
    expect(mounted.get('.permission-group input[type="checkbox"]').attributes("disabled")).toBeDefined();
    const patchCallsBefore = vi.mocked(fetch).mock.calls.filter(([, init]) => init?.method === "PATCH").length;
    await mounted.get('.permission-group input[type="checkbox"]').trigger("change");
    await flushPromises();
    const patchCallsAfter = vi.mocked(fetch).mock.calls.filter(([, init]) => init?.method === "PATCH").length;
    expect(patchCallsAfter).toBe(patchCallsBefore);
  });

  it("orders roles by authority and lets Owner update administrator permissions", async () => {
    const mounted = await wrapper();
    await mounted.findAll(".admin-sidebar nav button").find((button) => button.text() === "角色与权限")!.trigger("click");
    await flushPromises();

    expect(mounted.findAll(".role-list button").map((button) => button.find("strong").text())).toEqual([
      "系统所有者", "管理员", "开发者", "审计员", "普通用户",
    ]);
    await mounted.findAll(".role-list button").find((button) => button.text().includes("管理员"))!.trigger("click");
    expect(mounted.find(".role-lock").exists()).toBe(false);
    expect(mounted.get('.permission-group input[type="checkbox"]').attributes("disabled")).toBeUndefined();
    await mounted.get('.permission-group input[type="checkbox"]').setValue(false);
    await flushPromises();
    expect(fetch).toHaveBeenCalledWith("/api/admin/roles/role-admin", expect.objectContaining({ method: "PATCH" }));
  });

  it("keeps administrator role permissions read-only for non-Owner actors", async () => {
    const mounted = await wrapper(["admin"]);
    await mounted.findAll(".admin-sidebar nav button").find((button) => button.text() === "角色与权限")!.trigger("click");
    await flushPromises();
    await mounted.findAll(".role-list button").find((button) => button.text().includes("管理员"))!.trigger("click");

    expect(mounted.get(".role-lock").text()).toContain("仅系统所有者可修改");
    expect(mounted.get('.permission-group input[type="checkbox"]').attributes("disabled")).toBeDefined();
  });

  it("filters and expands security audit details", async () => {
    const mounted = await wrapper();
    await mounted.findAll(".admin-sidebar nav button").find((button) => button.text() === "安全审计")!.trigger("click");
    await flushPromises();
    expect(mounted.text()).toContain("role.updated");
    await mounted.get(".audit-list details").trigger("click");
    expect(mounted.get(".audit-list details").element).toBeTruthy();
    expect(mounted.get('.audit-filters a[href*="/api/audit/events/export"]').element).toBeTruthy();
  });

  it("shows deterministic incidents and the redacted system timeline", async () => {
    const mounted = await wrapper();
    await mounted.findAll(".admin-sidebar nav button").find((button) => button.text() === "系统监控")!.trigger("click");
    await flushPromises();

    expect(mounted.text()).toContain("RATE_LIMITED");
    expect(mounted.text()).toContain("same_component_code_subject_within_query_window");
    expect(mounted.text()).toContain("llm.error");
    expect(fetch).toHaveBeenCalledWith(expect.stringContaining("/api/admin/diagnostics?"), expect.anything());
  });
});
