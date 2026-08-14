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

export interface TravelConversationMessage {
  role: "user" | "assistant";
  content: string;
}

export interface TravelDraftSnapshot {
  session_id: string;
  messages: TravelConversationMessage[];
  draft: TravelRequirementDraft | Record<string, never>;
  phase: "intake" | "planning";
  handoff_question: string;
}

export type TravelWorkStatus = "collecting" | "running" | "awaiting_candidate" | "failed" | "completed";

export interface TravelWorkItem {
  session_id: string;
  plan_id: string;
  status: TravelWorkStatus;
  title: string;
  preview: string;
  updated_at: string;
  error_code: string;
}

export interface RegistrationPolicy {
  registration_enabled: boolean;
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

export interface McpServerMonitor {
  server_id: string;
  state: string;
  tool_count: number;
  error_code: string;
  call_count: number;
  success_count: number;
  failure_count: number;
  cancelled_count: number;
  last_tool_error_code: string;
  last_connection_state: string;
  last_connection_at: number;
  last_connection_reason_code: string;
  oauth_state: string;
}

export interface McpMonitorSnapshot {
  status: string;
  catalog_version: number;
  generated_at: number;
  active_calls: number;
  catalog_refresh_count: number;
  list_changed_count: number;
  reconnect_count: number;
  servers: McpServerMonitor[];
}

export interface XhsReadonlyAdminStatus {
  server_id: "xhs-readonly";
  state: "unknown" | "authenticated" | "auth_required" | "login_pending" | "unavailable";
  code: string;
  message: string;
  enabled: boolean;
  login_supported: boolean;
  login_in_progress: boolean;
  restart_supported: boolean;
  cookie_updated_at: string;
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
  ui_metadata?: {
    detail_type?: string;
    detail_data?: {
      questions?: string[];
      provider?: string;
      query?: string;
      summary?: string;
      result_count?: number;
      items?: Array<{ title?: string; detail?: string }>;
      session_id?: string;
      status?: string;
      recommended_candidate_id?: string;
      selected_candidate_id?: string;
      candidates?: TravelCandidate[];
      draft?: TravelRequirementDraft;
      missing_fields?: string[];
      changed_fields?: string[];
      ready?: boolean;
      question?: string;
      topic?: string;
    };
  };
  scope?: { agent_id?: string; task_id?: string; task_name?: string };
  agent_id?: string;
  task_id?: string;
  root_session_id?: string;
  root_turn_id?: string;
  skill_run_id?: string;
  parent_event_id?: string;
  tool_call_id?: string;
  tool_call_record_id?: string;
  metadata?: { percent?: number; code?: string; skill_name?: string; plan_id?: string; tool_name?: string; question_count?: number; ready?: boolean; missing_count?: number; topic?: string };
}

export type TravelFreshness = "live" | "snapshot" | "historical" | "estimate" | "unknown";
export type TravelSourceType = "official_api" | "live_query" | "official_page" | "web_article" | "social_post" | "model_estimate";

export interface TravelPlanSummary {
  plan_id: string;
  owner_user_id: string;
  source_session_id: string;
  source_turn_id: string;
  schema_version: string;
  title: string;
  destination_summary: string;
  created_at: string;
  updated_at: string;
}

export interface TravelGenerationStatus {
  status: "idle" | "pending" | "running" | "awaiting_candidate" | "completed" | "finished" | "failed" | "stopped";
  session_id: string;
  turn_id: string;
  plan_id: string;
  error_code: string;
}

export interface TravelCandidateDay {
  date: string;
  city_or_area: string;
  places: string[];
}

export interface TravelCandidate {
  candidate_id: string;
  recommended: boolean;
  score: number;
  days: TravelCandidateDay[];
  budget: { lower: number; expected: number; upper: number };
  route_minutes: number;
  route_distance_km: number;
  daily_intensity_scores: number[];
  evidence_coverage: number;
  warnings: string[];
}

export interface TravelCandidateReview {
  session_id: string;
  status: "pending" | "selected";
  recommended_candidate_id: string;
  selected_candidate_id: string;
  candidates: TravelCandidate[];
  created_at: string;
  updated_at: string;
}

export interface TravelRequirementDraft {
  intent: "travel_requirement" | "assistant_greeting" | "assistant_identity" | "assistant_capabilities" | "planner_help" | "unrelated";
  intent_topic: "" | "dates" | "travellers" | "budget" | "preferences" | "data_sources" | "models" | "workflow";
  origin: string;
  destinations: string[];
  start_date: string;
  end_date: string;
  traveller_type: string;
  traveller_count: number | null;
  budget_total_cny: number | null;
  budget_level: "" | "economy" | "balanced" | "comfortable";
  transport_preferences: string[];
  stay_preferences: string[];
  interest_tags: string[];
  pace: "" | "relaxed" | "balanced" | "intensive";
  planning_mode: "" | "quick" | "deep";
  hard_constraints: string[];
}

export interface TravelEvidence {
  evidence_id: string;
  source_type: TravelSourceType;
  provider: string;
  title: string;
  source_url: string;
  published_at: string;
  retrieved_at: string;
  data_as_of: string;
  excerpt: string;
  facts: string[];
  confidence: number;
  freshness: TravelFreshness;
  content_hash: string;
}

export interface TravelLocation { longitude: number; latitude: number }

export interface TravelActivity {
  start: string;
  end: string;
  place: string;
  reason: string;
  evidence_ids: string[];
  opening_hours?: string;
  location?: TravelLocation | null;
}

export interface TravelRouteSegment {
  mode: string;
  from: string;
  to: string;
  duration: number;
  distance: number;
  source: string;
  evidence_ids: string[];
  path?: TravelLocation[];
}

export interface TravelDay {
  date: string;
  city_or_area: string;
  activities: TravelActivity[];
  route_segments: TravelRouteSegment[];
  meal_suggestions: string[];
  daily_budget: number;
  weather_adjustment: string;
  fallback_plan: string;
  intensity_score: number;
}

export interface TravelPlan {
  schema_version: string;
  plan_id: string;
  owner_user_id: string;
  request: {
    origin: string;
    destinations: string[];
    start_date: string;
    end_date: string;
    duration_days: number;
    travellers: Array<{ type: string; count: number }>;
    budget_total_cny: number | null;
    planning_mode: "quick" | "deep";
    [key: string]: unknown;
  };
  assumptions: string[];
  freshness_summary: Record<string, unknown> | unknown[];
  transport_options: Array<Record<string, unknown>>;
  stay_recommendations: Array<Record<string, unknown>>;
  days: TravelDay[];
  budget: { lower: number; expected: number; upper: number; items: Array<Record<string, unknown>> };
  weather_summary: Array<Record<string, unknown>>;
  fallbacks: string[];
  avoidance_tips: string[];
  evidence: TravelEvidence[];
  unknowns: string[];
  generated_at: string;
}

export interface WsEnvelope {
  event: string;
  data: unknown;
  session_id?: string;
  turn_id?: string;
}
