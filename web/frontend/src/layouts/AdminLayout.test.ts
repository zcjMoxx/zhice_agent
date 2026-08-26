import { createPinia, setActivePinia } from "pinia";
import { flushPromises, mount } from "@vue/test-utils";
import { createMemoryHistory, createRouter } from "vue-router";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { useAuthStore } from "@/stores/auth";
import { useAdminStore } from "@/stores/admin";
import AdminLayout from "./AdminLayout.vue";

function response(payload: unknown): Response {
  return new Response(JSON.stringify(payload), { status: 200, headers: { "Content-Type": "application/json" } });
}

function mcpServer(serverId: string, overrides: Record<string, unknown> = {}) {
  return {
    server_id: serverId,
    state: "ready",
    tool_count: 2,
    error_code: "",
    call_count: 2,
    success_count: 2,
    failure_count: 0,
    cancelled_count: 0,
    last_tool_error_code: "",
    last_connection_state: "ready",
    last_connection_at: 1,
    last_connection_reason_code: "",
    oauth_state: "disabled",
    ...overrides,
  };
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
    vi.stubGlobal("open", vi.fn());
    vi.stubGlobal("fetch", vi.fn().mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.startsWith("/api/admin/auth/registration-policy") && init?.method === "PATCH") {
        const body = JSON.parse(String(init.body));
        return Promise.resolve(response({ registration_enabled: body.registration_enabled }));
      }
      if (url.startsWith("/api/admin/auth/registration-policy")) return Promise.resolve(response({ registration_enabled: false }));
      if (url.startsWith("/api/admin/users/user-disabled") && init?.method === "DELETE") return Promise.resolve(response({ status: "deleted" }));
      if (url.startsWith("/api/admin/users")) return Promise.resolve(response({ users: [
        { id: "actor", username: "actor", display_name: "Actor", status: "active", roles: ["owner"], can_manage_admins: true },
        { id: "user-disabled", username: "old-user", display_name: "Old User", status: "disabled", roles: ["viewer"], can_manage_admins: false },
      ] }));
      if (url.startsWith("/api/admin/roles") && init?.method === "PATCH") return Promise.resolve(response({ id: "role-dev", key: "developer", name: "Developer", description: "", is_builtin: true, permission_keys: ["audit.read"] }));
      if (url.startsWith("/api/admin/roles")) return Promise.resolve(response({ roles, permissions: ["audit.read"] }));
      if (url.startsWith("/api/admin/skills/sources/official/sync") && init?.method === "POST") return Promise.resolve(response({ status: "synchronized" }));
      if (url.startsWith("/api/admin/skills/sources/official/refresh-index") && init?.method === "POST") return Promise.resolve(response({ status: "refreshed" }));
      if (url.startsWith("/api/admin/skills/sources")) return Promise.resolve(response({ status: "ok", sources: [{ source: "official", enabled: true, sync_enabled: true, configured_target: "master", current_commit: "abc123", last_sync_started_at: "2026-08-09T00:00:00Z", last_sync_finished_at: "2026-08-09T00:00:01Z", last_success_at: "2026-08-09T00:00:01Z", last_status: "up_to_date", health: "healthy", skill_count: 1, load_error_count: 0, last_error_code: "", last_error_message_safe: "" }], skills: [{ qualified_name: "official/weather", source: "official", name: "weather", description: "天气报告", executable: true }] }));
      if (url.startsWith("/api/admin/external-platforms/xhs/check-login")) return Promise.resolve(response({ platform_id: "xhs", state: "authenticated", code: "OK", message: "logged in", enabled: true, login_supported: true, login_in_progress: false, restart_supported: true, cookie_updated_at: "2026-08-14T04:42:18Z" }));
      if (url.startsWith("/api/admin/external-platforms/xhs/login")) return Promise.resolve(response({ platform_id: "xhs", state: "login_pending", code: "XHS_LOGIN_STARTED", message: "started", enabled: true, login_supported: true, login_in_progress: true, restart_supported: true, cookie_updated_at: "2026-08-14T04:42:18Z" }));
      if (url.startsWith("/api/admin/mcp/xhs-readonly/restart")) return Promise.resolve(response({ platform_id: "xhs", state: "unknown", code: "XHS_RESTARTED", message: "restarted", enabled: true, login_supported: true, login_in_progress: false, restart_supported: true, cookie_updated_at: "2026-08-14T04:42:18Z" }));
      if (url.startsWith("/api/admin/external-platforms/xhs/status")) return Promise.resolve(response({ platform_id: "xhs", state: "unknown", code: "XHS_AUTH_NOT_CHECKED", message: "not checked", enabled: true, login_supported: true, login_in_progress: false, restart_supported: true, cookie_updated_at: "2026-08-14T04:42:18Z" }));
      if (url.startsWith("/api/admin/external-platforms/ctrip/credentials") && init?.method === "PUT") return Promise.resolve(response({ platform_id: "ctrip", state: "login_pending", code: "HOTEL_LOGIN_STARTED", message: "started", credential_store_supported: true, credential_configured: true, account_hint: "tr***@example.com", credentials_updated_at: "2026-08-14T05:00:00Z", browser_supported: true, login_supported: true, login_in_progress: true, login_mode: "password_with_manual_verification_fallback", last_checked_at: "" }));
      if (url.startsWith("/api/admin/external-platforms/ctrip/credentials") && init?.method === "DELETE") return Promise.resolve(response({ platform_id: "ctrip", state: "not_configured", code: "HOTEL_CREDENTIALS_NOT_CONFIGURED", message: "deleted", credential_store_supported: true, credential_configured: false, account_hint: "", credentials_updated_at: "", browser_supported: true, login_supported: true, login_in_progress: false, login_mode: "password_with_manual_verification_fallback", last_checked_at: "" }));
      if (url.startsWith("/api/admin/external-platforms/ctrip/check-login")) return Promise.resolve(response({ platform_id: "ctrip", state: "authenticated", code: "OK", message: "logged in", credential_store_supported: true, credential_configured: true, account_hint: "tr***@example.com", credentials_updated_at: "2026-08-14T05:00:00Z", browser_supported: true, check_in_progress: false, login_supported: true, login_in_progress: false, login_mode: "password_headless", last_checked_at: "2026-08-26T08:00:00Z" }));
      if (url.startsWith("/api/admin/external-platforms/ctrip/login")) return Promise.resolve(response({ platform_id: "ctrip", state: "login_pending", code: "HOTEL_LOGIN_STARTED", message: "started", credential_store_supported: true, credential_configured: true, account_hint: "tr***@example.com", credentials_updated_at: "2026-08-14T05:00:00Z", browser_supported: true, login_supported: true, login_in_progress: true, login_mode: "password_with_manual_verification_fallback", last_checked_at: "" }));
      if (url.startsWith("/api/admin/external-platforms/ctrip/status")) return Promise.resolve(response({ platform_id: "ctrip", state: "not_configured", code: "HOTEL_CREDENTIALS_NOT_CONFIGURED", message: "not configured", credential_store_supported: true, credential_configured: false, account_hint: "", credentials_updated_at: "", browser_supported: true, login_supported: true, login_in_progress: false, login_mode: "password_with_manual_verification_fallback", last_checked_at: "" }));
      if (url.startsWith("/api/admin/mcp/status")) return Promise.resolve(response({ status: "ok", catalog_version: 4, generated_at: 1, active_calls: 1, catalog_refresh_count: 2, list_changed_count: 1, reconnect_count: 3, servers: [mcpServer("tavily", { state: "degraded", error_code: "MCP_TRANSPORT_ERROR", call_count: 5, success_count: 3, failure_count: 1, cancelled_count: 1, last_tool_error_code: "MCP_TOOL_TIMEOUT", last_connection_state: "degraded", last_connection_reason_code: "MCP_TRANSPORT_ERROR" }), mcpServer("open-meteo"), mcpServer("amap-maps"), mcpServer("12306"), mcpServer("xhs-readonly", { tool_count: 3 })] }));
      if (url.startsWith("/api/admin/operations/terminal")) return Promise.resolve(response({ enabled: true, configured: true, url: "https://ops.example.test", presentation: "both", mode: "server_docker", target_type: "container", target_name: "zhice-agent" }));
      if (url.startsWith("/api/admin/diagnostics")) return Promise.resolve(response({ status: "ok", window_minutes: 1440, filters: {}, summary: { incidents: 1 }, incidents: [{ incident_id: "inc-1", component: "agent", code: "WEIXIN_TOKEN_STALE", subject: "", count: 1, first_seen_at: "2026-07-29T00:00:00Z", last_seen_at: "2026-07-29T00:00:00Z", rule: "same_component_code_subject_within_query_window", evidence: [{ evidence_id: "evt-1", ts: "2026-07-29T00:00:00Z", component: "agent", event: "channel.weixin.reconnect_required", code: "WEIXIN_TOKEN_STALE", error_message: "The Weixin token is stale", request_id: "req-1" }] }], timeline: [{ evidence_id: "evt-1", ts: "2026-07-29T00:00:00Z", component: "agent", event: "channel.weixin.reconnect_required", code: "WEIXIN_TOKEN_STALE", is_error: true, error_message: "The Weixin token is stale", request_id: "req-1" }, { evidence_id: "evt-2", ts: "2026-07-29T00:00:01Z", component: "gateway", event: "channel.ready", code: "", is_error: false }, { evidence_id: "evt-3", ts: "2026-07-29T00:00:02Z", component: "tool", event: "tool.done", code: "MCP_OK", is_error: false }], limitations: [] }));
      if (url.startsWith("/api/admin/monitor")) return Promise.resolve(response({ gateway: { status: "ok", current_model: "default/model" }, capabilities: { mcp: { name: "MCP", state: "available", message: "MCP ready", code: "MCP_READY" }, travel: { name: "Travel", state: "available", message: "Travel ready", code: "TRAVEL_READY" } }, activity: { summary: {}, recent_turns: [
        { turn_id: "turn-sec", request_id: "req-turn-sec", session_id: "session-error", session_title: "排查模型错误", actor_user_id: "actor", actor_username: "actor", actor_display_name: "Actor", status: "error", error_code: "GATEWAY_RESTART_INTERRUPTED", channel: "web", started_at: "2026-08-08T14:00:00Z", duration_ms: null },
        { turn_id: "turn-min", session_id: "session-minute", session_title: "一分钟任务", actor_user_id: "actor", actor_username: "actor", actor_display_name: "Actor", status: "completed", channel: "web", started_at: "2026-08-08T14:00:00Z", duration_ms: 60_000 },
        { turn_id: "turn-hour", session_id: "session-hour", session_title: "一小时任务", actor_user_id: "actor", actor_username: "actor", actor_display_name: "Actor", status: "completed", channel: "web", started_at: "2026-08-08T14:00:00Z", duration_ms: 3_600_000 },
      ], recent_tools: [] } }));
      if (url.startsWith("/api/audit/events")) return Promise.resolve(response({ events: [{ id: "audit-1", ts: "2026-07-27T00:00:00Z", action: "role.updated", decision: "allow" }], next_cursor: "2026-07-27T00:00:00Z|audit-1", has_more: true }));
      return Promise.resolve(response({}));
    }));
  });

  async function wrapper(userRoles = ["owner"]) {
    const pinia = createPinia();
    setActivePinia(pinia);
    const auth = useAuthStore();
    auth.user = { id: "actor", username: "actor", display_name: "Actor", status: "active", roles: userRoles, can_manage_admins: userRoles.includes("owner") };
    auth.permissions = ["auth.users.read", "auth.users.manage", "auth.roles.read", "auth.roles.manage", "audit.read", "audit.export", "turn.read.any", "diagnostics.system.use", "skill.sources.read", "skill.sync"];
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

  it("shows MCP server health and reconnect statistics beside Skills without treating unused OAuth as disabled", async () => {
    const mounted = await wrapper();
    await mounted.findAll(".admin-sidebar nav button").find((button) => button.text() === "MCP 与 Skills")!.trigger("click");
    await flushPromises();

    expect(mounted.get(".mcp-monitor-section").text()).toContain("MCP 服务监控");
    expect(mounted.get(".mcp-server-card").text()).toContain("tavily");
    expect(mounted.get(".mcp-server-card").text()).toContain("MCP_TRANSPORT_ERROR");
    expect(mounted.get(".mcp-server-card").text()).toContain("API Key");
    const amapCard = mounted.findAll(".mcp-server-card").find((card) => card.text().includes("amap-maps"))!;
    expect(amapCard.text()).toContain("API Key");
    const weatherCard = mounted.findAll(".mcp-server-card").find((card) => card.text().includes("open-meteo"))!;
    expect(weatherCard.text()).toContain("无需认证");
    expect(mounted.get(".mcp-server-card").text()).not.toContain("disabled");
    expect(mounted.findAll(".mcp-summary-grid strong")[0].text()).toBe("5");
    expect(mounted.get(".mcp-monitor-section").text()).not.toContain("hotel-browser");
    expect(mounted.get(".mcp-monitor-section").text()).not.toContain("携程账号登录");
    expect(mounted.get(".mcp-monitor-section").text()).toContain("3");
    expect(fetch).toHaveBeenCalledWith("/api/admin/mcp/status", expect.anything());
  });

  it("keeps Xiaohongshu technical controls in MCP and moves login to Owner platform accounts", async () => {
    const mounted = await wrapper();
    await mounted.findAll(".admin-sidebar nav button").find((button) => button.text() === "MCP 与 Skills")!.trigger("click");
    await flushPromises();

    const xhsCard = mounted.findAll(".mcp-server-card").find((card) => card.text().includes("xhs-readonly"))!;
    expect(xhsCard.text()).toContain("扫码 / Cookie");
    expect(xhsCard.text()).not.toContain("无需 OAuth");
    expect(xhsCard.text()).toContain("重启 MCP 服务");
    expect(xhsCard.text()).not.toContain("检查登录");
    const xhsAccount = mounted.findAll(".platform-account-card").find((card) => card.text().includes("小红书账号登录"))!;
    expect(xhsAccount.text()).toContain("扫码 / Cookie");
    expect(xhsAccount.text()).toContain("已登录");
    expect(mounted.find(".admin-action-feedback").exists()).toBe(false);
    expect(fetch).toHaveBeenCalledWith(
      "/api/admin/external-platforms/xhs/check-login",
      expect.objectContaining({ method: "POST" }),
    );
    await xhsAccount.findAll(".platform-account-actions button")[1].trigger("click");
    await flushPromises();
    expect(fetch).toHaveBeenCalledWith(
      "/api/admin/external-platforms/xhs/login",
      expect.objectContaining({ method: "POST" }),
    );
    mounted.unmount();

    const adminMounted = await wrapper(["admin"]);
    await adminMounted.findAll(".admin-sidebar nav button").find((button) => button.text() === "MCP 与 Skills")!.trigger("click");
    await flushPromises();
    expect(adminMounted.find(".external-platform-section").exists()).toBe(false);
    adminMounted.unmount();
  });

  it("shows equal platform-account cards without treating Ctrip as an MCP server", async () => {
    const mounted = await wrapper();
    await mounted.findAll(".admin-sidebar nav button").find((button) => button.text() === "MCP 与 Skills")!.trigger("click");
    await flushPromises();

    const platformSection = mounted.get(".external-platform-section");
    expect(platformSection.text()).toContain("外部平台账号");
    const accountCards = platformSection.findAll(".platform-account-card");
    expect(accountCards).toHaveLength(2);
    expect(accountCards[0].text()).toContain("小红书账号登录");
    const card = accountCards[1];
    expect(card.text()).toContain("携程账号登录");
    expect(mounted.get(".mcp-server-grid").text()).not.toContain("hotel-browser");
    expect(mounted.get(".mcp-server-grid").text()).not.toContain("携程");
    expect(card.text()).toContain("账号登录");
    expect(card.classes()).toContain("platform-account-card");
    const inputs = card.findAll(".platform-credential-form input");
    await inputs[0].setValue("traveller@example.com");
    await inputs[1].setValue("secret-password");
    await card.get(".platform-credential-form").trigger("submit");
    await flushPromises();

    expect(fetch).toHaveBeenCalledWith(
      "/api/admin/external-platforms/ctrip/credentials",
      expect.objectContaining({ method: "PUT" }),
    );
    expect((inputs[1].element as HTMLInputElement).value).toBe("");
    expect(card.text()).not.toContain("secret-password");
    expect(card.find(".platform-credential-form").exists()).toBe(false);
    expect(card.text()).toContain("管理账号");
    const admin = useAdminStore();
    admin.hotelBrowserStatus = {
      ...admin.hotelBrowserStatus!,
      state: "authenticated",
      code: "OK",
      check_in_progress: false,
      login_in_progress: false,
      login_mode: "password_headless",
    };
    await flushPromises();
    await card.findAll(".platform-account-actions button").find((button) => button.text().includes("检查登录"))!.trigger("click");
    await flushPromises();
    expect(fetch).toHaveBeenCalledWith(
      "/api/admin/external-platforms/ctrip/check-login",
      expect.objectContaining({ method: "POST" }),
    );
    await card.findAll(".platform-account-actions button").find((button) => button.text().includes("管理账号"))!.trigger("click");
    expect(card.find(".platform-credential-form").exists()).toBe(true);
    expect(card.text()).toContain("删除凭据");
    mounted.unmount();
  });

  it("keeps MCP server count truthful and hides Owner-only platform accounts", async () => {
    const mounted = await wrapper(["admin"]);
    await mounted.findAll(".admin-sidebar nav button").find((button) => button.text() === "MCP 与 Skills")!.trigger("click");
    await flushPromises();

    expect(mounted.find(".external-platform-section").exists()).toBe(false);
    expect(mounted.findAll(".mcp-summary-grid strong")[0].text()).toBe("5");
    mounted.unmount();
  });

  it("lets only Owner control public registration from account management", async () => {
    const mounted = await wrapper();
    await mounted.findAll(".admin-sidebar nav button").find((button) => button.text() === "账号管理")!.trigger("click");
    await flushPromises();

    const policy = mounted.get(".registration-policy-card");
    expect(policy.text()).toContain("允许新用户注册");
    expect(policy.text()).toContain("已关闭");
    await policy.get('input[type="checkbox"]').setValue(true);
    await flushPromises();
    expect(fetch).toHaveBeenCalledWith(
      "/api/admin/auth/registration-policy",
      expect.objectContaining({ method: "PATCH", body: JSON.stringify({ registration_enabled: true }) }),
    );
    expect(policy.text()).toContain("已开放");

    const adminMounted = await wrapper(["admin"]);
    await adminMounted.findAll(".admin-sidebar nav button").find((button) => button.text() === "账号管理")!.trigger("click");
    await flushPromises();
    expect(adminMounted.find(".registration-policy-card").exists()).toBe(false);
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
    expect(mounted.findAll(".admin-sidebar nav button").some((button) => button.text() === "高级设置")).toBe(false);
    await mounted.findAll(".admin-sidebar nav button").find((button) => button.text() === "安全审计")!.trigger("click");
    await flushPromises();
    expect(mounted.text()).toContain("安全审计");
    expect(mounted.text()).toContain("role.updated");
    expect(mounted.findAll('.audit-filter-field select').at(0)?.text()).toContain("登录");
    expect(mounted.findAll('.audit-filter-field select').at(0)?.text()).not.toContain("登录成功");
    expect(mounted.findAll('.audit-filter-field select').at(1)?.text()).toContain("Actor (@actor)");
    expect(mounted.findAll('.audit-filter-field select').at(2)?.text()).toContain("成功");
    await mounted.get(".audit-list details").trigger("click");
    expect(mounted.get(".audit-list details").element).toBeTruthy();
    expect(mounted.get('.audit-filters a[href*="/api/audit/events/export"]').element).toBeTruthy();
    const pagination = mounted.get(".audit-pagination");
    await pagination.findAll("button")[1].trigger("click");
    await flushPromises();
    expect(pagination.text()).toContain("第 2 页");
    expect(fetch).toHaveBeenCalledWith(expect.stringContaining("cursor=2026-07-27T00%3A00%3A00Z%7Caudit-1"), expect.anything());
    await pagination.findAll("button")[0].trigger("click");
    await flushPromises();
    expect(pagination.text()).toContain("第 1 页");
  });

  it("shows deterministic incidents and the redacted system timeline", async () => {
    const mounted = await wrapper();
    await mounted.findAll(".admin-sidebar nav button").find((button) => button.text() === "运行诊断")!.trigger("click");
    await flushPromises();

    expect(mounted.text()).toContain("微信连接凭据已失效");
    expect(mounted.text()).toContain("WEIXIN_TOKEN_STALE");
    expect(mounted.text()).toContain("微信账号需要重新连接");
    expect(fetch).toHaveBeenCalledWith(expect.stringContaining("/api/admin/diagnostics?"), expect.anything());
    expect(fetch).toHaveBeenCalledWith(expect.stringContaining("minutes=1440"), expect.anything());
    expect(fetch).toHaveBeenCalledWith(expect.stringContaining("/api/admin/monitor?limit=50&status=error"), expect.anything());
    expect(mounted.text()).toContain("排查模型错误");
    expect(mounted.text()).toContain("GATEWAY_RESTART_INTERRUPTED");
    const diagnosticSelects = mounted.findAll(".diagnostic-filters select");
    expect(diagnosticSelects[0].text()).toContain("Actor (@actor)");
    expect(diagnosticSelects[1].text()).toContain("排查模型错误");
    expect(diagnosticSelects[2].text()).toContain("Agent 运行时");
    expect(diagnosticSelects[3].text()).toContain("WEIXIN_TOKEN_STALE");
    expect(diagnosticSelects[5].text()).toContain("最近 24 小时");
    await mounted.get(".incident-list > details > summary").trigger("click");
    expect(mounted.text()).toContain("这不表示无人连接");
    expect(mounted.text()).toContain("管理员无法代替其他用户");
    expect(mounted.text()).not.toContain("前往渠道连接");
    expect(mounted.get(".timeline-event").classes()).toContain("error");
    expect(mounted.text()).not.toContain("查看证据");
    await mounted.get(".timeline-event .table-row").trigger("click");
    expect(mounted.text()).toContain("req-1");
    expect(mounted.text()).toContain("用于在诊断结果和脱敏日志中唯一定位");
    expect(mounted.get(".timeline-event").classes()).toContain("open");
    expect(mounted.findAll(".timeline-event")).toHaveLength(1);
    await mounted.get(".diagnostic-timeline-heading select").setValue("all");
    expect(mounted.findAll(".timeline-event")).toHaveLength(3);
    expect(mounted.findAll(".timeline-event")[1].classes()).toContain("normal");
    expect(mounted.findAll(".timeline-event")[1].text()).toContain("正常");
    expect(mounted.findAll(".timeline-event")[2].classes()).toContain("normal");
    expect(mounted.findAll(".timeline-event")[2].text()).toContain("MCP_OK");
    expect(mounted.text()).toContain("渠道已就绪");
    expect(mounted.text()).toContain("channel.ready");
    await diagnosticSelects[2].setValue("agent");
    await mounted.get(".diagnostic-filters").trigger("submit");
    await flushPromises();
    expect(fetch).toHaveBeenCalledWith(expect.stringContaining("component=agent"), expect.anything());
    expect(mounted.text()).toContain("诊断已更新");
    const run = mounted.findAll(".run-record")[0];
    expect(run.get(".record-toggle").text()).toContain("查看诊断");
    await run.get(".record-toggle").trigger("click");
    expect(run.classes()).toContain("open");
    expect(run.get(".record-toggle").text()).toContain("收起");
    expect(run.text()).toContain("session-error");
    expect(run.text()).toContain("turn-sec");
    expect(run.text()).toContain("req-turn-sec");
    expect(run.text()).toContain("Gateway 在完成前退出或重启");
    expect(run.text()).not.toContain("0.00 毫秒");
    expect(mounted.text()).toContain("1.00 分钟");
    expect(mounted.text()).toContain("1.00 小时");
  });

  it("prevents credential autofill and requires username confirmation for permanent deletion", async () => {
    const mounted = await wrapper();
    await mounted.findAll(".admin-sidebar nav button").find((button) => button.text() === "账号管理")!.trigger("click");
    await flushPromises();

    expect(mounted.get(".admin-create-form").attributes("autocomplete")).toBe("off");
    expect(mounted.get('input[name="admin-new-password"]').attributes("autocomplete")).toBe("new-password");
    await mounted.get(".danger-text-button").trigger("click");
    expect(mounted.text()).toContain("永久删除账号？");
    const confirmation = mounted.get('input[name="delete-user-confirmation"]');
    await confirmation.setValue("user001");
    await mounted.get(".dialog-card").trigger("submit");
    expect(mounted.get('[role="alert"]').text()).toContain("账号不一致，请重新输入");
    expect(vi.mocked(fetch).mock.calls.some(([url, init]) => String(url).includes("user-disabled") && init?.method === "DELETE")).toBe(false);

    await confirmation.setValue("old-user");
    expect(mounted.find('[role="alert"]').exists()).toBe(false);
    await mounted.get(".dialog-card").trigger("submit");
    await flushPromises();
    expect(fetch).toHaveBeenCalledWith(
      "/api/admin/users/user-disabled",
      expect.objectContaining({ method: "DELETE", body: JSON.stringify({ confirmation: "old-user" }) }),
    );
  });

  it("shows safe Skill source status and executes fixed source actions", async () => {
    const mounted = await wrapper();
    await mounted.findAll(".admin-sidebar nav button").find((button) => button.text() === "MCP 与 Skills")!.trigger("click");
    await flushPromises();

    expect(mounted.text()).toContain("official");
    expect(mounted.text()).toContain("abc123");
    expect(mounted.text()).not.toContain("raw secret stderr");
    await mounted.findAll(".skill-source-actions button").find((button) => button.text() === "查看 Skills")!.trigger("click");
    expect(mounted.text()).toContain("official/weather");
    expect(mounted.text()).toContain("可执行");
    await mounted.findAll(".skill-source-actions button").find((button) => button.text() === "同步")!.trigger("click");
    await flushPromises();
    expect(fetch).toHaveBeenCalledWith("/api/admin/skills/sources/official/sync", expect.objectContaining({ method: "POST" }));
    await mounted.findAll(".skill-source-actions button").find((button) => button.text() === "刷新索引")!.trigger("click");
    await flushPromises();
    expect(fetch).toHaveBeenCalledWith("/api/admin/skills/sources/official/refresh-index", expect.objectContaining({ method: "POST" }));
  });

  it("keeps server operations Owner-only and falls back from iframe to a new window", async () => {
    const mounted = await wrapper();
    await mounted.findAll(".admin-sidebar nav button").find((button) => button.text() === "服务器运维")!.trigger("click");
    await flushPromises();

    expect(mounted.text()).toContain("ZhiCe 独立运维控制面");
    expect(mounted.text()).toContain("服务器 Docker");
    expect(mounted.text()).toContain("zhice-agent");
    expect(mounted.text()).toContain(window.location.origin);
    const operationButtons = mounted.findAll(".operations-actions button");
    expect(operationButtons.find((button) => button.text() === "独立窗口打开")!.classes()).toContain("operations-action-button");
    expect(operationButtons.find((button) => button.text() === "页面内嵌")!.classes()).toContain("operations-secondary-button");
    await mounted.findAll(".operations-actions button").find((button) => button.text() === "页面内嵌")!.trigger("click");
    expect(mounted.get(".operations-close-button").text()).toBe("关闭投影");
    const frame = mounted.get(".operations-frame-wrap iframe");
    expect(frame.attributes("src")).toBe("https://ops.example.test");
    await frame.trigger("error");
    expect(mounted.text()).toContain("页面内嵌不可用");
    expect(window.open).toHaveBeenCalledWith("https://ops.example.test", "_blank", "noopener,noreferrer");

    const adminMounted = await wrapper(["admin"]);
    expect(adminMounted.findAll(".admin-sidebar nav button").some((button) => button.text() === "服务器运维")).toBe(false);
    expect(adminMounted.findAll(".admin-sidebar nav button").some((button) => button.text() === "MCP 与 Skills")).toBe(true);
  });

  it("keeps MCP out of runtime capability diagnostics while retaining other capabilities", async () => {
    const mounted = await wrapper();
    await mounted.findAll(".admin-sidebar nav button").find((button) => button.text() === "运行诊断")!.trigger("click");
    await flushPromises();

    expect(mounted.get(".capability-grid").text()).toContain("Travel");
    expect(mounted.get(".capability-grid").text()).not.toContain("MCP");
  });

  it("drills overview failures and incidents into their diagnostic sections", async () => {
    const failuresMounted = await wrapper();
    const failures = failuresMounted.get('[data-overview-target="failures"]');
    expect(failures.element.tagName).toBe("BUTTON");
    expect(failures.attributes("aria-label")).toBe("查看近期失败运行");
    await failures.trigger("click");
    await flushPromises();
    expect(failuresMounted.find("#monitor-runs").exists()).toBe(true);
    expect(failuresMounted.get(".recent-runs-section select").element).toHaveProperty("value", "error");

    const incidentsMounted = await wrapper();
    const incidents = incidentsMounted.get('[data-overview-target="incidents"]');
    expect(incidents.attributes("aria-label")).toBe("查看当前事故证据");
    await incidents.trigger("click");
    await flushPromises();
    expect(incidentsMounted.find("#monitor-incidents").exists()).toBe(true);
    expect(vi.mocked(fetch).mock.calls.some(([url]) => String(url).includes("minutes=60"))).toBe(true);
  });
});
