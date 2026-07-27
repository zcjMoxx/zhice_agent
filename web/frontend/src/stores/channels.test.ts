import { createPinia, setActivePinia } from "pinia";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { api } from "@/api/client";
import { useChannelStore } from "./channels";

vi.mock("@/api/client", () => ({
  api: { bindings: vi.fn(), weixinStatus: vi.fn() },
}));

describe("channel store", () => {
  beforeEach(() => setActivePinia(createPinia()));

  it("keeps QQ truth while Weixin degrades independently", async () => {
    vi.mocked(api.bindings).mockResolvedValue({ bindings: [{ binding_id: "qq-1", channel: "qq", display_name: "QQ", linked_at: "now" }] });
    vi.mocked(api.weixinStatus).mockRejectedValue(new Error("Weixin is unavailable"));
    const store = useChannelStore();

    await store.refresh();

    expect(store.bindings).toHaveLength(1);
    expect(store.weixin.status).toBe("unavailable");
    expect(store.error).toContain("Weixin is unavailable");
  });
});
