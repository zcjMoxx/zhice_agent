import { describe, expect, it, vi } from "vitest";

import { ZhiCeWebSocket } from "./client";

class MockSocket extends EventTarget {
  static OPEN = 1;
  readyState = 0;
  sent: string[] = [];

  constructor(public readonly url: string) {
    super();
    queueMicrotask(() => {
      this.readyState = MockSocket.OPEN;
      this.dispatchEvent(new Event("open"));
    });
  }

  send(payload: string): void { this.sent.push(payload); }
  close(): void { this.readyState = 3; this.dispatchEvent(new Event("close")); }
}

describe("ZhiCeWebSocket", () => {
  it("sends the browser hello profile and reconnects after close", async () => {
    const sockets: MockSocket[] = [];
    vi.stubGlobal("WebSocket", class extends MockSocket {
      constructor(url: string) { super(url); sockets.push(this); }
      static OPEN = MockSocket.OPEN;
    });
    const client = new ZhiCeWebSocket();
    const closed = vi.fn();
    client.subscribe((event) => { if (event.event === "socket_closed") closed(); });

    await client.connect();
    expect(JSON.parse(sockets[0].sent[0])).toEqual({ type: "hello", client: "web" });
    sockets[0].close();
    expect(closed).toHaveBeenCalledOnce();
    await client.sendMessage("session-a", "hello", "model-a");

    expect(sockets).toHaveLength(2);
    expect(JSON.parse(sockets[1].sent.at(-1)!)).toEqual({ type: "message", session_id: "session-a", content: "hello", model: "model-a" });
  });

  it("labels travel application sessions explicitly", async () => {
    const sockets: MockSocket[] = [];
    vi.stubGlobal("WebSocket", class extends MockSocket {
      constructor(url: string) { super(url); sockets.push(this); }
      static OPEN = MockSocket.OPEN;
    });
    const client = new ZhiCeWebSocket();
    const pending = client.createSession("travel");
    await vi.waitFor(() => expect(sockets[0].sent).toHaveLength(2));

    expect(JSON.parse(sockets[0].sent[1])).toEqual({ type: "new_session", application: "travel" });
    sockets[0].dispatchEvent(new MessageEvent("message", { data: JSON.stringify({ event: "session_created", data: { session_id: "travel-one" } }) }));
    await expect(pending).resolves.toBe("travel-one");
  });
});
