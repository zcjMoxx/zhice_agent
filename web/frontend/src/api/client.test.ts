import { describe, expect, it, vi } from "vitest";

import { api, ApiError, onAuthorizationFailure, request } from "./client";

describe("API client", () => {
  it("preserves stable backend errors and refreshes authorization", async () => {
    const refresh = vi.fn();
    onAuthorizationFailure(refresh);
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify({ error: { status: 401, code: "AUTH_REQUIRED", message: "Sign in", request_id: "req-1", details: {} } }), { status: 401, headers: { "Content-Type": "application/json" } })));
    const failure = await request("/api/sessions").catch((error: unknown) => error);
    expect(failure).toBeInstanceOf(ApiError);
    expect(failure).toMatchObject({ status: 401, code: "AUTH_REQUIRED", requestId: "req-1" });
    expect(refresh).toHaveBeenCalledWith(401);
  });

  it("encodes fixed Skill source path segments", async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({ status: "refreshed" }), { status: 200, headers: { "Content-Type": "application/json" } }));
    vi.stubGlobal("fetch", fetchMock);
    await api.refreshSkillSourceIndex("official source");
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/admin/skills/sources/official%20source/refresh-index",
      expect.objectContaining({ method: "POST" }),
    );
  });

  it("sends workflow definitions directly and uses owner-scoped run routes", async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({ workflow_id: "wf-1", status: "draft" }), { status: 200, headers: { "Content-Type": "application/json" } }));
    vi.stubGlobal("fetch", fetchMock);
    await api.createWorkflow({
      schema_version: 1,
      name: "Daily summary",
      description: "",
      timezone: "Asia/Shanghai",
      nodes: [{ id: "trigger", type: "schedule_trigger", position: { x: 0, y: 0 }, config: { trigger_type: "manual" } }],
      edges: [],
    });
    const [, requestInit] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(JSON.parse(String(requestInit.body))).toMatchObject({ schema_version: 1, name: "Daily summary" });
    expect(JSON.parse(String(requestInit.body))).not.toHaveProperty("definition");
  });
});
