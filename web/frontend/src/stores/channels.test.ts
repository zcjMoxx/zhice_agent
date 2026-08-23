import { createPinia, setActivePinia } from "pinia";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError, api } from "@/api/client";
import { useChannelStore } from "./channels";

vi.mock("@/api/client", async (importOriginal) => {
  const original = await importOriginal<typeof import("@/api/client")>();
  return {
    ...original,
    api: {
      bindings: vi.fn(),
      weixinStatus: vi.fn(),
      startWeixin: vi.fn(),
      pollWeixin: vi.fn(),
      cancelWeixin: vi.fn(),
      qqCode: vi.fn(),
      unlinkBinding: vi.fn(),
      unlinkWeixin: vi.fn(),
      reconnectWeixin: vi.fn(),
    },
  };
});

const waitingAttempt = {
  attempt_id: "wxbind-1",
  status: "waiting_scan",
  expires_at: "later",
  qr_data: "data:image/png;base64,c2FmZQ==",
  error_code: "",
};

describe("channel store", () => {
  beforeEach(() => {
    setActivePinia(createPinia());
    vi.clearAllMocks();
  });
  afterEach(() => vi.useRealTimers());

  it("keeps QQ truth while Weixin degrades independently", async () => {
    vi.mocked(api.bindings).mockResolvedValue({ bindings: [{ binding_id: "qq-1", channel: "qq", display_name: "QQ", linked_at: "now" }] });
    vi.mocked(api.weixinStatus).mockRejectedValue(new Error("Weixin is unavailable"));
    const store = useChannelStore();

    await store.refresh();

    expect(store.bindings).toHaveLength(1);
    expect(store.weixin.status).toBe("unavailable");
    expect(store.error).toContain("Weixin is unavailable");
  });

  it("treats a disabled Weixin channel as availability state instead of a global error", async () => {
    vi.mocked(api.bindings).mockResolvedValue({ bindings: [] });
    vi.mocked(api.weixinStatus).mockRejectedValue(new ApiError(
      503,
      "CHANNEL_WEIXIN_UNAVAILABLE",
      "Weixin channel is disabled or unavailable",
    ));
    const store = useChannelStore();

    await store.refresh();

    expect(store.weixin.status).toBe("unavailable");
    expect(store.error).toBe("");
  });

  it("polls real pending statuses and preserves the connected terminal state", async () => {
    vi.useFakeTimers();
    vi.mocked(api.startWeixin).mockResolvedValue(waitingAttempt);
    vi.mocked(api.pollWeixin).mockResolvedValue({ ...waitingAttempt, status: "connected", qr_data: "" });
    vi.mocked(api.bindings).mockResolvedValue({ bindings: [] });
    vi.mocked(api.weixinStatus).mockResolvedValue({ status: "active", linked_at: "now" });
    const store = useChannelStore();

    await store.startWeixin();
    await vi.advanceTimersByTimeAsync(1500);

    expect(api.pollWeixin).toHaveBeenCalledWith("wxbind-1");
    expect(store.weixinAttempt?.status).toBe("connected");
    expect(store.weixin.status).toBe("active");
    expect(store.weixinError).toBeNull();
  });

  it("keeps an attempt retryable when polling fails with an API error", async () => {
    vi.mocked(api.pollWeixin).mockRejectedValue(new ApiError(503, "CHANNEL_WEIXIN_UNAVAILABLE", "Sidecar unavailable"));
    const store = useChannelStore();
    store.weixinAttempt = waitingAttempt;

    await store.pollWeixinNow();

    expect(store.weixinAttempt).toEqual(waitingAttempt);
    expect(store.weixinError).toEqual({ code: "CHANNEL_WEIXIN_UNAVAILABLE", message: "Sidecar unavailable" });
    expect(store.weixinBusy).toBe(false);
  });

  it("retains failed and cancelled terminal responses with their error state", async () => {
    vi.mocked(api.startWeixin).mockResolvedValue({
      ...waitingAttempt,
      status: "verification_failed",
      qr_data: "",
      error_code: "WEIXIN_VERIFY_CODE_REQUIRED",
    });
    vi.mocked(api.cancelWeixin).mockResolvedValue({ ...waitingAttempt, status: "cancelled", qr_data: "" });
    const store = useChannelStore();

    await store.startWeixin();
    expect(store.weixinAttempt?.error_code).toBe("WEIXIN_VERIFY_CODE_REQUIRED");

    store.weixinAttempt = waitingAttempt;
    await store.cancelWeixin();
    expect(store.weixinAttempt?.status).toBe("cancelled");
  });

  it("surfaces QQ and Weixin mutation failures instead of rejecting silently", async () => {
    vi.mocked(api.qqCode).mockRejectedValue(new ApiError(503, "QQ_CODE_FAILED", "绑定码服务不可用"));
    vi.mocked(api.unlinkWeixin).mockRejectedValue(new ApiError(503, "WEIXIN_UNLINK_FAILED", "微信解绑失败"));
    const store = useChannelStore();

    await store.generateQqCode();
    expect(store.error).toBe("绑定码服务不可用");
    expect(store.busy).toBe(false);

    await store.unlinkWeixin();
    expect(store.weixinError).toEqual({ code: "WEIXIN_UNLINK_FAILED", message: "微信解绑失败" });
    expect(store.weixinBusy).toBe(false);
  });
});
