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

export interface SkillSummary {
  qualified_name: string;
  source: string;
  name: string;
  description: string;
  executable: boolean;
}

export interface SkillSourceStatus {
  source: string;
  enabled: boolean;
  sync_enabled: boolean;
  configured_target: string;
  current_commit: string;
  last_sync_started_at: string;
  last_sync_finished_at: string;
  last_success_at: string;
  last_status: string;
  health: string;
  skill_count: number;
  load_error_count: number;
  last_error_code: string;
  last_error_message_safe: string;
}

export interface SkillSourcesSnapshot {
  status: string;
  sources: SkillSourceStatus[];
  skills: SkillSummary[];
}

export interface OperationsTerminal {
  enabled: boolean;
  configured: boolean;
  url: string;
  presentation: "new_tab" | "embed" | "both";
  mode?: "local_process" | "local_docker" | "server_docker" | "";
  target_type?: "process" | "container" | "";
  target_name?: string;
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

export interface ChannelBinding {
  binding_id: string;
  channel: string;
  display_name: string;
  linked_at: string;
}

export interface WeixinChannelStatus {
  status: string;
  linked_at: string;
}

export interface WeixinBindingAttempt {
  attempt_id: string;
  status: string;
  expires_at: string;
  qr_data: string;
  error_code: string;
}

export interface SystemDiagnosticsSnapshot {
  status: string;
  window_minutes: number;
  filters: Record<string, unknown>;
  summary: Record<string, number>;
  incidents: Record<string, unknown>[];
  timeline: Record<string, unknown>[];
  limitations: string[];
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
  display?: { title?: string; detail?: string; visibility?: string };
  scope?: { agent_id?: string; task_id?: string; task_name?: string };
  agent_id?: string;
  task_id?: string;
  root_session_id?: string;
  root_turn_id?: string;
  skill_run_id?: string;
  parent_event_id?: string;
  tool_call_id?: string;
  tool_call_record_id?: string;
  metadata?: { percent?: number; code?: string; skill_name?: string };
}

export interface WsEnvelope {
  event: string;
  data: unknown;
  session_id?: string;
  turn_id?: string;
}
