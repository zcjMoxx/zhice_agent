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
  beforeEach(() => {
    vi.stubGlobal("fetch", vi.fn().mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.startsWith("/api/admin/roles") && init?.method === "PATCH") return Promise.resolve(response({ id: "role-dev", key: "developer", name: "Developer", description: "", is_builtin: true, permission_keys: ["audit.read"] }));
      if (url.startsWith("/api/admin/roles")) return Promise.resolve(response({ roles: [{ id: "role-dev", key: "developer", name: "Developer", description: "Developer role", is_builtin: true, permission_keys: [] }], permissions: ["audit.read"] }));
      if (url.startsWith("/api/audit/events")) return Promise.resolve(response({ events: [{ id: "audit-1", ts: "2026-07-27T00:00:00Z", action: "role.updated", decision: "allow" }], next_cursor: "", has_more: false }));
      return Promise.resolve(response({}));
    }));
  });

  async function wrapper() {
    const pinia = createPinia();
    setActivePinia(pinia);
    const auth = useAuthStore();
    auth.user = { id: "owner", username: "owner", display_name: "Owner", status: "active", roles: ["owner"], can_manage_admins: true };
    auth.permissions = ["auth.roles.read", "auth.roles.manage", "audit.read", "audit.export", "turn.read.any"];
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
    await mounted.get('.permission-group input[type="checkbox"]').setValue(true);
    await flushPromises();
    expect(fetch).toHaveBeenCalledWith("/api/admin/roles/role-dev", expect.objectContaining({ method: "PATCH" }));
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
});
