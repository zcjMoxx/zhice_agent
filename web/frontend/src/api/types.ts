export interface ApiErrorBody {
  status: number;
  code: string;
  message: string;
  request_id: string;
  details: Record<string, unknown>;
}

export interface PublicUser {
  id: string;
  username: string;
  display_name: string;
  status: string;
  roles: string[];
  can_manage_admins: boolean;
}

export interface AuthMe {
  user: PublicUser;
  permissions: string[];
}

export interface SessionSummary {
  session_id: string;
  preview: string;
  updated_at: string;
  message_count: number;
  title: string;
  channel: string;
  conversation_type: string;
  continuation_mode: string;
}

export interface ChatMessage {
  role: string;
  content: string;
  name?: string | null;
  tool_call_id?: string | null;
  tool_calls?: Record<string, unknown>[];
  metadata?: Record<string, unknown>;
  turn_id?: string | null;
  turn_index?: number | null;
  parent_turn_id?: string | null;
  pending?: boolean;
  runtime?: RuntimeUiState;
}

export interface ModelState { endpoint: string; current_model: string; models: string[] }

export interface Role {
  id: string;
  key: string;
  name: string;
  description: string;
  is_builtin: boolean;
  permission_keys: string[];
}

export interface AuditPage {
  events: Record<string, unknown>[];
  next_cursor?: string;
  has_more?: boolean;
}

export interface CapabilityStatus {
  name: string;
  state: "available" | "disabled" | "degraded" | "unavailable" | string;
  code: string;
  message: string;
  hint?: string;
  details?: Record<string, unknown>;
}

export interface MonitorSnapshot {
  gateway: Record<string, unknown>;
  capabilities: Record<string, CapabilityStatus>;
  activity: {
    recent_turns: Record<string, unknown>[];
    recent_tools: Record<string, unknown>[];
    summary: Record<string, number>;
  };
}

export interface RuntimeUiState {
  sequence: number;
  title: string;
  phase: string;
  status: string;
  childTasks: Record<string, { sequence: number; title: string; status: string }>;
}

export interface RuntimeEventData {
  session_id?: string;
  turn_id?: string;
  sequence?: number;
  type?: string;
  event?: string;
  status?: string;
  display?: { title?: string; detail?: string };
  scope?: { agent_id?: string; task_id?: string; task_name?: string };
  agent_id?: string;
  task_id?: string;
  root_session_id?: string;
  root_turn_id?: string;
}

export interface WsEnvelope {
  event: string;
  data: unknown;
  session_id?: string;
  turn_id?: string;
}
