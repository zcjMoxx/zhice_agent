import { describe, expect, it, vi } from "vitest";

import { ApiError, onAuthorizationFailure, request } from "./client";

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
});
