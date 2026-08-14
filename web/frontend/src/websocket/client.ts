import type { WsEnvelope } from "@/api/types";

type Listener = (envelope: WsEnvelope) => void;

export class ZhiCeWebSocket {
  private socket: WebSocket | null = null;
  private connecting: Promise<WebSocket> | null = null;
  private listeners = new Set<Listener>();
  private sessionWaiters: Array<(id: string) => void> = [];

  subscribe(listener: Listener): () => void {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  }

  async connect(): Promise<WebSocket> {
    if (this.socket?.readyState === WebSocket.OPEN) return this.socket;
    if (this.connecting) return this.connecting;
    const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
    const socket = new WebSocket(`${protocol}//${window.location.host}/ws`);
    this.socket = socket;
    this.connecting = new Promise((resolve, reject) => {
      socket.addEventListener("open", () => {
        socket.send(JSON.stringify({ type: "hello", client: "web" }));
        this.connecting = null;
        resolve(socket);
      }, { once: true });
      socket.addEventListener("error", () => {
        this.connecting = null;
        reject(new Error("WebSocket 连接失败"));
      }, { once: true });
    });
    socket.addEventListener("message", (message) => this.handleMessage(message));
    socket.addEventListener("close", () => {
      this.socket = null;
      this.connecting = null;
      this.listeners.forEach((listener) => listener({ event: "socket_closed", data: {} }));
    });
    return this.connecting;
  }

  async createSession(application: "chat" | "travel" = "chat"): Promise<string> {
    const socket = await this.connect();
    return new Promise((resolve) => {
      this.sessionWaiters.push(resolve);
      socket.send(JSON.stringify({ type: "new_session", application }));
    });
  }

  async sendMessage(sessionId: string, content: string, model: string): Promise<void> {
    const socket = await this.connect();
    socket.send(JSON.stringify({ type: "message", session_id: sessionId, content, model }));
  }

  async stop(sessionId: string): Promise<void> {
    const socket = await this.connect();
    socket.send(JSON.stringify({ type: "stop", session_id: sessionId }));
  }

  async respondToElicitation(sessionId: string, interactionId: string, action: "accept" | "cancel", response: Record<string, unknown> | null): Promise<void> {
    const socket = await this.connect();
    socket.send(JSON.stringify({ type: "mcp_elicitation_response", session_id: sessionId, interaction_id: interactionId, action, response }));
  }

  close(): void {
    this.socket?.close();
    this.socket = null;
  }

  private handleMessage(message: MessageEvent<string>): void {
    let envelope: WsEnvelope;
    try { envelope = JSON.parse(message.data) as WsEnvelope; }
    catch { return; }
    if (envelope.event === "session_created") {
      const data = envelope.data as { session_id?: string };
      const waiter = this.sessionWaiters.shift();
      if (waiter) waiter(String(data.session_id ?? envelope.session_id ?? ""));
    }
    this.listeners.forEach((listener) => listener(envelope));
  }
}

export const webSocket = new ZhiCeWebSocket();
