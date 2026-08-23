"""SQLite schema and built-in RBAC seed data for Part 9."""

from __future__ import annotations

PERMISSIONS: dict[str, tuple[str, str]] = {
    "auth.users.read": ("List users", "auth"),
    "auth.users.manage": ("Create and update users", "auth"),
    "auth.admin.manage": ("Promote and demote administrators", "auth"),
    "auth.roles.read": ("List roles and permissions", "auth"),
    "auth.roles.manage": ("Update role permissions", "auth"),
    "session.manage.any": ("Manage all sessions", "session"),
    "chat.stop.any": ("Stop any turn", "chat"),
    "turn.read.any": ("Read all turn summaries", "turn"),
    "diagnostics.system.use": ("Use cross-user system diagnostics", "diagnostics"),
    "tool.exec.dangerous": ("Request confirmed high-risk exec", "tool"),
    "skill.sources.read": ("Read Skill source status", "skill"),
    "skill.sync": ("Synchronize Skill sources", "skill"),
    "audit.read": ("Read audit events", "audit"),
    "audit.export": ("Export audit events", "audit"),
    "workflow.use": ("Create and run personal workflows", "workflow"),
    "workflow.schedule": ("Schedule personal workflows", "workflow"),
    "workflow.notify.self": ("Send workflow notifications to self", "workflow"),
    "workflow.email.send": ("Send email through personal connections", "workflow"),
    "workflow.external.action": ("Run approved external workflow actions", "workflow"),
    "workflow.social.publish": ("Publish through approved workflow tools", "workflow"),
    "workflow.manage.any": ("Manage workflow metadata across users", "workflow"),
    "workflow.settings.manage": ("Manage global workflow settings", "workflow"),
}

ROLE_PERMISSIONS: dict[str, tuple[str, ...]] = {
    "owner": tuple(PERMISSIONS),
    "admin": tuple(
        key
        for key in (
            "auth.users.read",
            "auth.users.manage",
            "auth.roles.read",
            "session.manage.any",
            "chat.stop.any",
            "turn.read.any",
            "workflow.use",
            "workflow.schedule",
            "workflow.notify.self",
            "workflow.email.send",
            "workflow.manage.any",
        )
    ),
    "developer": (
        "workflow.use",
        "workflow.schedule",
        "workflow.notify.self",
        "workflow.email.send",
    ),
    "viewer": (
        "workflow.use",
        "workflow.schedule",
        "workflow.notify.self",
        "workflow.email.send",
    ),
    "auditor": (
        "audit.read",
        "turn.read.any",
    ),
}

ROLE_NAMES = {
    "owner": "Owner",
    "admin": "Administrator",
    "developer": "Developer",
    "viewer": "Viewer",
    "auditor": "Auditor",
}

SCHEMA_SQL = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS users (
  id TEXT PRIMARY KEY,
  username TEXT NOT NULL UNIQUE,
  display_name TEXT NOT NULL,
  password_hash TEXT NOT NULL,
  password_salt TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'active',
  is_builtin INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  last_login_at TEXT
);

CREATE TABLE IF NOT EXISTS auth_sessions (
  id TEXT PRIMARY KEY,
  user_id TEXT NOT NULL REFERENCES users(id),
  token_hash TEXT NOT NULL UNIQUE,
  created_at TEXT NOT NULL,
  expires_at TEXT NOT NULL,
  revoked_at TEXT,
  user_agent_preview TEXT NOT NULL DEFAULT '',
  remote_addr_preview TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS auth_settings (
  id INTEGER PRIMARY KEY CHECK (id = 1),
  registration_enabled INTEGER NOT NULL DEFAULT 0
    CHECK (registration_enabled IN (0, 1)),
  updated_at TEXT NOT NULL,
  updated_by_user_id TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS external_identities (
  id TEXT PRIMARY KEY,
  user_id TEXT NOT NULL REFERENCES users(id),
  channel TEXT NOT NULL,
  external_tenant_id TEXT NOT NULL DEFAULT '',
  external_user_id TEXT NOT NULL,
  external_display_name TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL DEFAULT 'active',
  linked_at TEXT NOT NULL,
  last_seen_at TEXT,
  metadata_json TEXT NOT NULL DEFAULT '{}',
  UNIQUE(channel, external_tenant_id, external_user_id)
);

CREATE TABLE IF NOT EXISTS user_notification_endpoints (
  id TEXT PRIMARY KEY,
  user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  type TEXT NOT NULL CHECK (type IN ('email')),
  address TEXT NOT NULL,
  verified_at TEXT,
  status TEXT NOT NULL DEFAULT 'pending'
    CHECK (status IN ('pending', 'active', 'revoked')),
  is_default INTEGER NOT NULL DEFAULT 0 CHECK (is_default IN (0, 1)),
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  UNIQUE(user_id, type, address)
);

CREATE INDEX IF NOT EXISTS idx_notification_endpoints_user
ON user_notification_endpoints(user_id, status, is_default);

CREATE TABLE IF NOT EXISTS channel_accounts (
  id TEXT PRIMARY KEY,
  channel TEXT NOT NULL,
  account_key TEXT NOT NULL,
  owner_user_id TEXT NOT NULL REFERENCES users(id),
  external_account_id TEXT NOT NULL,
  external_user_id TEXT NOT NULL,
  credential_ref TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'active',
  linked_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  last_seen_at TEXT,
  UNIQUE(channel, account_key),
  UNIQUE(channel, owner_user_id),
  UNIQUE(channel, external_account_id),
  UNIQUE(channel, external_user_id)
);

CREATE TABLE IF NOT EXISTS external_identity_link_tokens (
  id TEXT PRIMARY KEY,
  token_hash TEXT NOT NULL UNIQUE,
  user_id TEXT NOT NULL REFERENCES users(id),
  channel TEXT NOT NULL,
  account_key TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'pending',
  created_at TEXT NOT NULL,
  expires_at TEXT NOT NULL,
  consumed_at TEXT
);

CREATE TABLE IF NOT EXISTS external_identity_authorization_requests (
  id TEXT PRIMARY KEY,
  token_hash TEXT NOT NULL UNIQUE,
  channel TEXT NOT NULL,
  account_key TEXT NOT NULL,
  external_user_id TEXT NOT NULL,
  external_display_name TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL DEFAULT 'pending',
  created_at TEXT NOT NULL,
  expires_at TEXT NOT NULL,
  consumed_at TEXT,
  user_id TEXT REFERENCES users(id)
);

CREATE TABLE IF NOT EXISTS roles (
  id TEXT PRIMARY KEY,
  key TEXT NOT NULL UNIQUE,
  name TEXT NOT NULL,
  description TEXT NOT NULL DEFAULT '',
  is_builtin INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS permissions (
  key TEXT PRIMARY KEY,
  description TEXT NOT NULL,
  category TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS user_roles (
  user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  role_id TEXT NOT NULL REFERENCES roles(id) ON DELETE CASCADE,
  PRIMARY KEY (user_id, role_id)
);

CREATE TABLE IF NOT EXISTS user_permissions (
  user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  permission_key TEXT NOT NULL REFERENCES permissions(key) ON DELETE CASCADE,
  granted_at TEXT NOT NULL,
  PRIMARY KEY (user_id, permission_key)
);

CREATE TABLE IF NOT EXISTS role_permissions (
  role_id TEXT NOT NULL REFERENCES roles(id) ON DELETE CASCADE,
  permission_key TEXT NOT NULL REFERENCES permissions(key) ON DELETE CASCADE,
  PRIMARY KEY (role_id, permission_key)
);

CREATE TABLE IF NOT EXISTS session_index (
  session_id TEXT PRIMARY KEY,
  owner_user_id TEXT NOT NULL REFERENCES users(id),
  channel TEXT NOT NULL DEFAULT 'web',
  conversation_type TEXT NOT NULL DEFAULT '',
  external_chat_id TEXT NOT NULL DEFAULT '',
  external_thread_id TEXT NOT NULL DEFAULT '',
  title TEXT NOT NULL DEFAULT '',
  preview TEXT NOT NULL DEFAULT '',
  message_count INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  archived_at TEXT
);

CREATE TABLE IF NOT EXISTS channel_conversations (
  id TEXT PRIMARY KEY,
  channel TEXT NOT NULL,
  account_key TEXT NOT NULL,
  conversation_type TEXT NOT NULL,
  external_conversation_id TEXT NOT NULL,
  external_thread_id TEXT NOT NULL DEFAULT '',
  owner_user_id TEXT NOT NULL REFERENCES users(id),
  current_session_id TEXT NOT NULL REFERENCES session_index(session_id),
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  UNIQUE(
    channel, account_key, conversation_type, external_conversation_id,
    external_thread_id, owner_user_id
  )
);

CREATE TABLE IF NOT EXISTS channel_event_receipts (
  channel TEXT NOT NULL,
  account_key TEXT NOT NULL,
  event_id TEXT NOT NULL,
  message_id TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL,
  first_seen_at TEXT NOT NULL,
  finished_at TEXT,
  error_code TEXT NOT NULL DEFAULT '',
  PRIMARY KEY(channel, account_key, event_id)
);

CREATE TABLE IF NOT EXISTS weixin_outbound_messages (
  delivery_id TEXT PRIMARY KEY,
  account_key TEXT NOT NULL,
  event_id TEXT NOT NULL,
  peer TEXT NOT NULL,
  context_token_ref TEXT NOT NULL,
  chunk_index INTEGER NOT NULL,
  text TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'pending',
  attempt_count INTEGER NOT NULL DEFAULT 0,
  last_error_code TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  sent_at TEXT,
  UNIQUE(account_key, event_id, chunk_index)
);

CREATE TABLE IF NOT EXISTS turn_runs (
  turn_id TEXT PRIMARY KEY,
  session_id TEXT NOT NULL,
  turn_index INTEGER,
  actor_user_id TEXT NOT NULL REFERENCES users(id),
  auth_session_id TEXT NOT NULL DEFAULT '',
  request_id TEXT NOT NULL DEFAULT '',
  channel TEXT NOT NULL,
  status TEXT NOT NULL,
  started_at TEXT NOT NULL,
  finished_at TEXT,
  duration_ms INTEGER,
  error_code TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS tool_call_records (
  id TEXT PRIMARY KEY,
  session_id TEXT NOT NULL,
  turn_id TEXT NOT NULL,
  tool_call_id TEXT NOT NULL,
  tool_name TEXT NOT NULL,
  actor_user_id TEXT NOT NULL REFERENCES users(id),
  cwd TEXT NOT NULL DEFAULT '',
  command_preview TEXT NOT NULL DEFAULT '',
  args_preview TEXT NOT NULL DEFAULT '',
  args_hash TEXT NOT NULL DEFAULT '',
  risk_level TEXT NOT NULL DEFAULT 'low',
  risk_category TEXT NOT NULL DEFAULT 'safe',
  decision TEXT NOT NULL,
  decision_code TEXT NOT NULL DEFAULT '',
  permission_key TEXT NOT NULL DEFAULT '',
  confirmation_status TEXT NOT NULL DEFAULT '',
  started_at TEXT NOT NULL,
  finished_at TEXT,
  duration_seconds REAL,
  is_error INTEGER NOT NULL DEFAULT 0,
  result_code TEXT NOT NULL DEFAULT '',
  exit_code INTEGER,
  timeout_seconds INTEGER,
  stdout_tail TEXT NOT NULL DEFAULT '',
  stderr_tail TEXT NOT NULL DEFAULT '',
  output_truncated INTEGER NOT NULL DEFAULT 0,
  output_preview TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS tool_confirmations (
  id TEXT PRIMARY KEY,
  tool_call_record_id TEXT NOT NULL REFERENCES tool_call_records(id),
  actor_user_id TEXT NOT NULL REFERENCES users(id),
  session_id TEXT NOT NULL,
  turn_id TEXT NOT NULL,
  tool_name TEXT NOT NULL,
  risk_level TEXT NOT NULL,
  command_preview TEXT NOT NULL DEFAULT '',
  args_hash TEXT NOT NULL,
  status TEXT NOT NULL,
  requested_at TEXT NOT NULL,
  expires_at TEXT NOT NULL,
  decided_at TEXT,
  decision_actor_user_id TEXT REFERENCES users(id)
);

CREATE TABLE IF NOT EXISTS audit_events (
  id TEXT PRIMARY KEY,
  ts TEXT NOT NULL,
  actor_user_id TEXT,
  auth_session_id TEXT NOT NULL DEFAULT '',
  request_id TEXT NOT NULL DEFAULT '',
  channel TEXT NOT NULL DEFAULT '',
  action TEXT NOT NULL,
  resource_type TEXT NOT NULL,
  resource_id TEXT NOT NULL DEFAULT '',
  session_id TEXT NOT NULL DEFAULT '',
  turn_id TEXT NOT NULL DEFAULT '',
  tool_call_record_id TEXT NOT NULL DEFAULT '',
  route TEXT NOT NULL DEFAULT '',
  status_code INTEGER,
  decision TEXT NOT NULL DEFAULT '',
  reason_code TEXT NOT NULL DEFAULT '',
  risk_category TEXT NOT NULL DEFAULT '',
  metadata_json TEXT NOT NULL DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_auth_sessions_user ON auth_sessions(user_id, expires_at);
CREATE INDEX IF NOT EXISTS idx_user_permissions_user ON user_permissions(user_id);
CREATE INDEX IF NOT EXISTS idx_external_identities_user ON external_identities(user_id, channel);
CREATE INDEX IF NOT EXISTS idx_channel_accounts_owner
  ON channel_accounts(owner_user_id, channel, status);
CREATE INDEX IF NOT EXISTS idx_external_link_tokens_user
  ON external_identity_link_tokens(user_id, channel, status);
CREATE INDEX IF NOT EXISTS idx_external_authorization_status
  ON external_identity_authorization_requests(channel, account_key, status, expires_at);
CREATE INDEX IF NOT EXISTS idx_session_index_owner ON session_index(owner_user_id, updated_at);
CREATE INDEX IF NOT EXISTS idx_session_index_channel ON session_index(channel, external_chat_id, updated_at);
CREATE INDEX IF NOT EXISTS idx_channel_conversations_owner
  ON channel_conversations(owner_user_id, updated_at);
CREATE INDEX IF NOT EXISTS idx_channel_receipts_seen
  ON channel_event_receipts(channel, account_key, first_seen_at);
CREATE INDEX IF NOT EXISTS idx_weixin_outbound_pending
  ON weixin_outbound_messages(account_key, status, created_at, chunk_index);
CREATE INDEX IF NOT EXISTS idx_turn_runs_session ON turn_runs(session_id, started_at);
CREATE INDEX IF NOT EXISTS idx_turn_runs_actor ON turn_runs(actor_user_id, started_at);
CREATE INDEX IF NOT EXISTS idx_tool_calls_turn ON tool_call_records(session_id, turn_id);
CREATE INDEX IF NOT EXISTS idx_tool_calls_actor ON tool_call_records(actor_user_id, started_at);
CREATE INDEX IF NOT EXISTS idx_audit_ts ON audit_events(ts);
CREATE INDEX IF NOT EXISTS idx_audit_actor ON audit_events(actor_user_id, ts);
CREATE INDEX IF NOT EXISTS idx_audit_session_turn ON audit_events(session_id, turn_id);
"""
