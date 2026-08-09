import type {
  ApiErrorBody,
  AuditPage,
  AuthMe,
  ChannelBinding,
  ModelState,
  MonitorSnapshot,
  PublicUser,
  Role,
  SessionSummary,
  SystemDiagnosticsSnapshot,
  WeixinBindingAttempt,
  WeixinChannelStatus,
} from "./types";

export class ApiError extends Error {
  constructor(
    public readonly status: number,
    public readonly code: string,
    message: string,
    public readonly requestId = "",
    public readonly details: Record<string, unknown> = {},
  ) {
    super(message);
  }
}

let authorizationFailureHandler: ((status: number) => void | Promise<void>) | undefined;

export function onAuthorizationFailure(handler: (status: number) => void | Promise<void>): void {
  authorizationFailureHandler = handler;
}

export async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const response = await fetch(path, {
    credentials: "same-origin",
    ...init,
    headers: init.body ? { "Content-Type": "application/json", ...init.headers } : init.headers,
  });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    const body = (payload.error ?? {}) as Partial<ApiErrorBody>;
    if ((response.status === 401 || response.status === 403) && authorizationFailureHandler) {
      await authorizationFailureHandler(response.status);
    }
    throw new ApiError(
      Number(body.status ?? response.status),
      body.code ?? "REQUEST_FAILED",
      body.message ?? "请求失败",
      body.request_id ?? response.headers.get("X-Request-ID") ?? "",
      body.details ?? {},
    );
  }
  return payload as T;
}

const json = (body: unknown): RequestInit => ({ method: "POST", body: JSON.stringify(body) });

export const api = {
  me: () => request<AuthMe>("/api/auth/me"),
  login: (username: string, password: string) => request<{ status: string; user: PublicUser }>("/api/auth/login", json({ username, password })),
  register: (username: string, password: string) => request<{ status: string; user: PublicUser }>("/api/auth/register", json({ username, password })),
  bootstrap: (setupToken: string, password: string) => request<{ status: string; user: PublicUser }>("/api/auth/bootstrap", json({ setup_token: setupToken, password })),
  logout: () => request<{ status: string }>("/api/auth/logout", { method: "POST" }),
  updateProfile: (displayName: string) => request<AuthMe>("/api/auth/profile", { method: "PATCH", body: JSON.stringify({ display_name: displayName }) }),
  changePassword: (currentPassword: string, newPassword: string) => request<{ status: string }>("/api/auth/password", json({ current_password: currentPassword, new_password: newPassword })),
  sessions: () => request<{ sessions: SessionSummary[] }>("/api/sessions"),
  session: (id: string) => request<{ session_id: string; messages: import("./types").ChatMessage[]; metadata: Record<string, unknown> }>(`/api/sessions/${encodeURIComponent(id)}`),
  renameSession: (id: string, title: string) => request(`/api/sessions/${encodeURIComponent(id)}`, { method: "PATCH", body: JSON.stringify({ title }) }),
  deleteSession: (id: string) => request(`/api/sessions/${encodeURIComponent(id)}`, { method: "DELETE" }),
  forkSession: (id: string) => request<SessionSummary>(`/api/sessions/${encodeURIComponent(id)}/fork`, { method: "POST" }),
  models: (sessionId: string) => request<ModelState>(`/api/models?session_id=${encodeURIComponent(sessionId)}`),
  setModel: (sessionId: string, model: string) => request<ModelState>("/api/model/preference", json({ session_id: sessionId, model })),
  bindings: () => request<{ bindings: ChannelBinding[] }>("/api/channels/bindings"),
  qqCode: () => request<{ code: string; command: string; expires_at: string }>("/api/channels/qq/link-code", { method: "POST" }),
  qqAuthorize: (token: string) => request("/api/channels/qq/authorize", json({ token })),
  unlinkBinding: (id: string) => request(`/api/channels/bindings/${encodeURIComponent(id)}`, { method: "DELETE" }),
  weixinStatus: () => request<WeixinChannelStatus>("/api/channels/weixin", { cache: "no-store" }),
  startWeixin: () => request<WeixinBindingAttempt>("/api/channels/weixin/binding-attempts", { method: "POST", cache: "no-store" }),
  pollWeixin: (id: string) => request<WeixinBindingAttempt>(`/api/channels/weixin/binding-attempts/${encodeURIComponent(id)}`, { cache: "no-store" }),
  cancelWeixin: (id: string) => request<WeixinBindingAttempt>(`/api/channels/weixin/binding-attempts/${encodeURIComponent(id)}`, { method: "DELETE", cache: "no-store" }),
  unlinkWeixin: () => request("/api/channels/weixin/binding", { method: "DELETE" }),
  reconnectWeixin: () => request("/api/channels/weixin/reconnect", { method: "POST" }),
  users: () => request<{ users: PublicUser[] }>("/api/admin/users"),
  createUser: (payload: Record<string, unknown>) => request<PublicUser>("/api/admin/users", json(payload)),
  updateUser: (id: string, payload: Record<string, unknown>) => request<PublicUser>(`/api/admin/users/${encodeURIComponent(id)}`, { method: "PATCH", body: JSON.stringify(payload) }),
  deleteUser: (id: string, confirmation: string) => request<{ status: string }>(`/api/admin/users/${encodeURIComponent(id)}`, { method: "DELETE", body: JSON.stringify({ confirmation }) }),
  roles: () => request<{ roles: Role[]; permissions: string[] }>("/api/admin/roles"),
  updateRole: (id: string, permissionKeys: string[]) => request<Role>(`/api/admin/roles/${encodeURIComponent(id)}`, { method: "PATCH", body: JSON.stringify({ permission_keys: permissionKeys }) }),
  monitor: (query = new URLSearchParams()) => request<MonitorSnapshot>(`/api/admin/monitor?${query}`),
  diagnostics: (query: URLSearchParams) => request<SystemDiagnosticsSnapshot>(`/api/admin/diagnostics?${query}`),
  audit: (query: URLSearchParams) => request<AuditPage>(`/api/audit/events?${query}`),
  confirmation: (id: string, approved: boolean) => request(`/api/tool-confirmations/${encodeURIComponent(id)}/${approved ? "approve" : "deny"}`, { method: "POST" }),
};
