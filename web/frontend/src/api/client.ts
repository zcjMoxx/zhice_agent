import type {
  ApiErrorBody,
  AuditPage,
  AuthMe,
  ChannelBinding,
  ModelState,
  McpMonitorSnapshot,
  MonitorSnapshot,
  OperationsTerminal,
  PublicUser,
  RegistrationPolicy,
  Role,
  SessionSummary,
  SystemDiagnosticsSnapshot,
  SkillSourcesSnapshot,
  WeixinBindingAttempt,
  WeixinChannelStatus,
  TravelPlan,
  TravelGenerationStatus,
  TravelCandidateReview,
  TravelPlanSummary,
  XhsReadonlyAdminStatus,
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
  registrationPolicy: () => request<RegistrationPolicy>("/api/auth/registration-policy", { cache: "no-store" }),
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
  ownerRegistrationPolicy: () => request<RegistrationPolicy>("/api/admin/auth/registration-policy", { cache: "no-store" }),
  updateOwnerRegistrationPolicy: (registrationEnabled: boolean) => request<RegistrationPolicy>("/api/admin/auth/registration-policy", { method: "PATCH", body: JSON.stringify({ registration_enabled: registrationEnabled }) }),
  createUser: (payload: Record<string, unknown>) => request<PublicUser>("/api/admin/users", json(payload)),
  updateUser: (id: string, payload: Record<string, unknown>) => request<PublicUser>(`/api/admin/users/${encodeURIComponent(id)}`, { method: "PATCH", body: JSON.stringify(payload) }),
  deleteUser: (id: string, confirmation: string) => request<{ status: string }>(`/api/admin/users/${encodeURIComponent(id)}`, { method: "DELETE", body: JSON.stringify({ confirmation }) }),
  roles: () => request<{ roles: Role[]; permissions: string[] }>("/api/admin/roles"),
  updateRole: (id: string, permissionKeys: string[]) => request<Role>(`/api/admin/roles/${encodeURIComponent(id)}`, { method: "PATCH", body: JSON.stringify({ permission_keys: permissionKeys }) }),
  skillSources: () => request<SkillSourcesSnapshot>("/api/admin/skills/sources", { cache: "no-store" }),
  mcpStatus: () => request<McpMonitorSnapshot>("/api/admin/mcp/status", { cache: "no-store" }),
  xhsAdminStatus: () => request<XhsReadonlyAdminStatus>("/api/admin/mcp/xhs-readonly/status", { cache: "no-store" }),
  checkXhsAdminLogin: () => request<XhsReadonlyAdminStatus>("/api/admin/mcp/xhs-readonly/check-login", { method: "POST", cache: "no-store" }),
  startXhsAdminLogin: () => request<XhsReadonlyAdminStatus>("/api/admin/mcp/xhs-readonly/login", { method: "POST", cache: "no-store" }),
  restartXhsAdminSidecar: () => request<XhsReadonlyAdminStatus>("/api/admin/mcp/xhs-readonly/restart", { method: "POST", cache: "no-store" }),
  syncSkillSource: (source: string) => request<{ status: string }>(`/api/admin/skills/sources/${encodeURIComponent(source)}/sync`, { method: "POST" }),
  refreshSkillSourceIndex: (source: string) => request<{ status: string }>(`/api/admin/skills/sources/${encodeURIComponent(source)}/refresh-index`, { method: "POST" }),
  operationsTerminal: () => request<OperationsTerminal>("/api/admin/operations/terminal", { cache: "no-store" }),
  monitor: (query = new URLSearchParams()) => request<MonitorSnapshot>(`/api/admin/monitor?${query}`),
  diagnostics: (query: URLSearchParams) => request<SystemDiagnosticsSnapshot>(`/api/admin/diagnostics?${query}`),
  audit: (query: URLSearchParams) => request<AuditPage>(`/api/audit/events?${query}`),
  confirmation: (id: string, approved: boolean) => request(`/api/tool-confirmations/${encodeURIComponent(id)}/${approved ? "approve" : "deny"}`, { method: "POST" }),
  travelPlans: () => request<{ plans: TravelPlanSummary[] }>("/api/travel/plans", { cache: "no-store" }),
  travelWorkItems: () => request<{ items: import("./types").TravelWorkItem[] }>("/api/travel/work-items", { cache: "no-store" }),
  travelGeneration: (sessionId = "") => request<TravelGenerationStatus>(`/api/travel/generation${sessionId ? `?session_id=${encodeURIComponent(sessionId)}` : ""}`, { cache: "no-store" }),
  persistTravelConversation: (sessionId: string, messages: import("./types").TravelConversationMessage[], draft?: import("./types").TravelRequirementDraft) => request<{ session_id: string; message_count: number; status: string }>(`/api/travel/sessions/${encodeURIComponent(sessionId)}/conversation`, json({ messages, draft: draft || {} })),
  travelDraft: (sessionId: string) => request<import("./types").TravelDraftSnapshot>(`/api/travel/sessions/${encodeURIComponent(sessionId)}/draft`, { cache: "no-store" }),
  confirmTravelPlanning: (sessionId: string, draft: import("./types").TravelRequirementDraft) => request<{ session_id: string; phase: "planning"; status: string }>(`/api/travel/sessions/${encodeURIComponent(sessionId)}/confirm-planning`, json({ draft })),
  deleteTravelWorkItem: (sessionId: string) => request<{ session_id: string; status: string }>(`/api/travel/sessions/${encodeURIComponent(sessionId)}`, { method: "DELETE" }),
  travelCandidateReview: (sessionId: string) => request<TravelCandidateReview>(`/api/travel/sessions/${encodeURIComponent(sessionId)}/candidate-review`, { cache: "no-store" }),
  selectTravelCandidate: (sessionId: string, candidateId: string) => request<TravelCandidateReview>(`/api/travel/sessions/${encodeURIComponent(sessionId)}/candidate-selection`, json({ candidate_id: candidateId })),
  extractTravelRequirements: (text: string) => request<{ draft: import("./types").TravelRequirementDraft; missing_fields: string[] }>("/api/travel/requirements/extract", json({ text })),
  travelPlan: (id: string) => request<{ plan: TravelPlan }>(`/api/travel/plans/${encodeURIComponent(id)}`, { cache: "no-store" }),
  deleteTravelPlan: (id: string) => request<{ plan_id: string; status: string }>(`/api/travel/plans/${encodeURIComponent(id)}`, { method: "DELETE" }),
};
