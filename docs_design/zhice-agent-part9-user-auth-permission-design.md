# 智策 Agent 第九部分详细设计文档：用户、登录与权限执行边界

> 关联规范：`AGENTS.md`
>
> 文档类型：阶段活文档。本文档始终按当前代码和当前阶段口径维护。
>
> 承接文档：`docs_design/zhice-agent-part8-gateway-agent-logging-design.md`
>
> 设计依据：`docs_design/2026-07-06-next-stage-sequencing-design.md`、`docs_design/2026-07-08-user-auth-permission-boundary-design.md`、`docs_design/2026-07-10-session-model-preference-scope-design.md`、`docs_design/2026-07-10-owner-admin-delegation-design.md`、`docs_design/2026-07-11-password-change-reauthentication-design.md`、`docs_design/2026-07-16-authenticated-user-baseline-capabilities-design.md`
>
> 当前状态：第九部分身份与特权边界已落地。登录用户默认拥有账号自身、本人 Session、聊天、模型、安全工具、已安装 Skill、诊断和本人 Memory 等基础能力；RBAC 只保留跨用户管理、系统管理、审计、危险执行和全局 Skill 同步等特权。Owner 是 CLI 本地 workspace operator 在 Web 端的登录身份：认证表示不同，但共用全局 workspace、sessions、metadata 和 Memory，不拥有独立的 `contexts/users/{owner_id}`。当前用户管理支持已停用非 Owner 账号的用户名二次确认永久删除；普通自助注册默认关闭并由 Owner 独占控制。Vue 工程化前端已由 Part 16 落地；通用审批流、OAuth/SSO 和多租户仍不在当前范围。

---

## 1. 背景

第七部分已经把一次用户请求收敛成稳定 turn：

- `Message` 有 `turn_id`、`turn_index`、`parent_turn_id`。
- `JsonlSessionStore` 会把 turn 字段作为 JSONL 顶层字段读写。
- `AgentLoop.run_turn(..., turn_id=...)` 支持复用 Web / SSE / WS 侧生成的 turn id。
- `WebSocket /ws` 的 accepted、channel_text、done、stopped、error 已围绕同一个 `turn_id` 传递。

第八部分已经把运行日志补齐到本地可观测形态：

- `AgentLoop` / `WebRuntime` / tool dispatch 运行日志都带 `session_id` 和 `turn_id`。
- 终端日志使用 `[YYYY-MM-DD HH:MM:SS] | LEVEL | component.event | fields`。
- workspace trace 写入 `${ZHICE_AGENT_WORKSPACE}/logs/log-YYYY-MM-DD.jsonl`。

这些能力让后续权限审计有了稳定运行边界。现在缺口集中在身份和执行授权：

- Web API 只按 `session_id` 读写，任何本地请求都能列出、读取、重命名、删除所有 session。
- WebSocket 只区分 `web` / `external` command profile，不知道连接背后的用户是谁。
- `WebRuntime._active_turns` 当前按 `session_id` 保存 active turn，缺少 owner 维度。
- `/model` 和 Web 模型选择是进程级偏好，不区分用户。
- `JsonlSessionStore` 的 sidecar metadata 只保存 title，不保存 owner。
- `ToolRegistry.execute(name, args)` 当前没有 actor、session、turn、channel、confirmation 状态。
- `ExecTool` 只知道 workspace 和命令参数，危险命令由 `shell_policy.validate_command()` 静态拦截。
- 第八部分日志可以关联 turn，但还不是安全审计日志，也没有 actor、permission、decision 字段。

因此第九部分不是“加一个登录页”这么简单，而是要先定义：

```text
User -> Session -> Turn -> ToolCall / AuditLog
```

这条链路。当前代码已经按本文档完成简单本地用户系统、权限管理界面和工具执行管控第一版。

---

## 2. 目标

1. 明确本地第一版用户系统的实体关系：user、role、permission、auth session、session 模型偏好、用户上下文目录、session index、turn run、tool call、tool confirmation、audit event。
2. 引入最小 RBAC 权限模型，支持用户、角色、权限 key 和默认角色。
3. 让 Web API / WebSocket 从匿名本地接口变成带登录态的本地接口。
4. 让 session 列表、读取、重命名、删除按 owner 或管理权限过滤。
5. 让 `AgentLoop` 在 tool dispatch 前能拿到执行上下文，但不在 core 层硬编码用户业务。
6. 让 `exec` 从纯静态拦截演进为“基础安全策略 + 权限检查 + 必要时用户确认 + 审计”。
7. 保持 JSONL 作为聊天消息真值，不在第九部分实现阶段强制迁移历史消息到数据库。
8. 定义 SQLite schema 草案，落在 workspace `state/` 运行状态目录下，不提交真实用户数据或 token。
9. 定义登录页、用户管理页、角色权限页、审计入口和危险操作确认 UI 的第一版范围。
10. 定义兼容策略：CLI 继续保留现有全局 JSONL session 路径；Owner Web 只复用该全局路径并自动补 session index，普通用户暂不接管 CLI 历史。
11. 定义用户可见诊断能力，让用户能通过 Agent 查询自己近期失败原因，而不是直接暴露 raw trace。
12. 定义测试矩阵和验收标准，避免权限逻辑只靠人工点 UI 验证。
13. 让模型偏好按 session 隔离并持久化；`/model reset` 恢复当前 session 的系统默认，`/new` 创建不继承旧偏好的新 session。

---

## 3. 范围边界

### 3.1 本阶段设计包含

- 本地用户名密码登录。
- 首个拥有 `admin` 角色的用户初始化。
- cookie-based Web 登录态。
- 可撤销 auth session。
- 用户、角色、权限 key 和默认角色。
- Session 级模型偏好、模型查看/切换权限和 turn 级模型解析。
- 用户上下文目录、session index、CLI 全局 session 保留和 Owner 索引对账策略。
- Web API / WebSocket 鉴权和 401 / 403 错误口径。
- Tool execution context 和 tool permission policy 协议边界。
- `exec` 风险分类、权限检查、确认和审计策略。
- SQLite auth/audit schema 草案。
- Web 登录页、用户菜单、用户管理、角色权限、审计和确认 UI 范围。
- CLI 本地操作者策略。
- 单元测试、API 测试和少量 Web 静态交互验证建议。

### 3.2 第九部分第一版实现不包含

- OAuth、SSO、企业 IdP 或扫码登录。
- 复杂组织架构、部门、团队空间和租户模型。
- 多 workspace 隔离。
- 生产公网部署安全方案。
- 审批流、多人会签或异步工单。
- 细粒度文件 ACL。
- 完整前端工程化、Vue/Vite 多页面重构。
- 密钥管理系统或系统 keyring 集成。
- 把全部会话消息迁移到数据库。
- 完整日志查询平台。
- 外部 IM / 协作平台真实接入。

第一版仍是本地开发服务。即便有登录，也不能把 `zcagent gateway --host 0.0.0.0` 解释成生产可公网暴露。

---

## 4. 实现前代码边界与当前落点

本节原先用于记录 Part 9 开工前的第八部分代码基线。当前实现已经通过第 14 节列出的协议和模块完成收敛；以下“当前”描述应理解为本次改造前的入口和问题来源，不再代表最新代码状态。

### 4.1 Web 边界

当前 HTTP routes 位于 `agent/app/api/routes.py`：

```text
GET    /api/sessions
GET    /api/sessions/{session_id}
PATCH  /api/sessions/{session_id}
DELETE /api/sessions/{session_id}
POST   /api/chat
POST   /api/chat/stream
GET    /api/models
POST   /api/model/preference
```

这些接口当前只从 `request.app.state.runtime` 取 `WebRuntime`，没有 `actor`。

当前 WebSocket 位于 `agent/app/api/ws.py`：

- 握手后直接 `accept()`。
- `hello` frame 只解析 `client=web|external`。
- message / stop / heartbeat 都只按 `session_id` 操作。
- external client 可以通过 `/stop`、`/history`、`/exit` 获得额外命令能力，但没有认证。

第九部分实现阶段应把 actor 解析放在 app shell，不让 route 直接读写底层 user 表。

### 4.2 Session 边界

当前 `SessionStore` 协议只处理消息：

```python
load(session_id) -> SessionState
append(session_id, messages)
clear(session_id)
rename(session_id, title)
delete(session_id)
list_sessions() -> list[SessionSummary]
```

当前 `JsonlSessionStore` 使用：

```text
${ZHICE_AGENT_WORKSPACE}/contexts/sessions/{session_id}.jsonl
${ZHICE_AGENT_WORKSPACE}/contexts/sessions_meta/{session_id}.json
```

第九部分引入用户后，Web 和外部渠道的 JSONL 会话写入用户上下文目录：

```text
${ZHICE_AGENT_WORKSPACE}/contexts/users/{user_id}/sessions/{session_id}.jsonl
${ZHICE_AGENT_WORKSPACE}/contexts/users/{user_id}/sessions_meta/{session_id}.json
```

CLI 仍是本地 no-login 管理/开发入口，继续使用当前全局路径：

```text
${ZHICE_AGENT_WORKSPACE}/contexts/sessions/{session_id}.jsonl
${ZHICE_AGENT_WORKSPACE}/contexts/sessions_meta/{session_id}.json
```

sidecar metadata 当前只保存 title。第九部分实现阶段在不改变 JSONL 消息真值的前提下，为它增加 session 模型偏好字段；owner 逻辑仍不能硬塞进 JSONL store。更合适的边界是新增 app/runtime 层 session access service 和独立 SessionModelPreferenceStore：

```text
SessionAccessService
  -> UserContextResolver 解析 actor 的用户上下文目录
  -> SessionIndex 查询 session 索引、标题、渠道和归档状态
  -> SessionStore 读写已授权会话目录下的 JSONL
  -> SessionModelPreferenceStore 读写当前 session 的模型 metadata
  -> AuditSink 记录 session 操作
```

这样 `JsonlSessionStore` 继续只做消息持久化。

### 4.2.1 Workspace 和用户上下文目录

第九部分采用以下运行目录口径：

```text
${ZHICE_AGENT_WORKSPACE}/
  config/
    llm_endpoints.json
    config.yml  # skills 等统一运行配置
  prompts/
  extends/
  logs/
    YYYY-MM-DD/
      log-YYYY-MM-DD.jsonl
  state/
    auth.sqlite3
  contexts/
    sessions/                 # CLI 与 Owner Web 会话
    sessions_meta/            # CLI 与 Owner Web 会话元数据
    shared/
      readonly/
    users/                     # 只存普通用户，不存 Owner
      {user_id}/
        sessions/             # 非 Owner 用户会话
        sessions_meta/        # 非 Owner 用户会话元数据
        files/
```

规则：

- `contexts/users/{user_id}` 是普通用户文件工作区的权限边界；Owner 不进入该目录。
- `contexts/users/{user_id}/files` 是该用户默认可写工作目录，`exec` 和未来文件写入工具默认只在这里工作。
- Owner 的 Web 会话只复用 CLI 的 `contexts/sessions` 和 `contexts/sessions_meta`，不回退到 Owner 用户目录；普通用户仍使用自己的 `contexts/users/{user_id}/sessions*`。
- Owner 是 CLI workspace operator 的 Web 登录投影，除认证记录、session index 和 audit actor 外不形成第二套物理存储身份。
- `sessions/` 和 `sessions_meta/` 是系统维护的对话记录与元数据目录，普通工具默认不可写，避免误改会话历史。
- `contexts/shared/readonly` 是普通用户可读的公共资料区，不能包含其它用户敏感信息；拥有 `admin` 角色的用户可维护。
- `logs/` 是系统级运行 trace，不放进用户目录。
- `state/auth.sqlite3` 是运行状态数据库，不属于配置模板。
- `{user_id}` 必须是稳定内部用户 id，不能使用可改名的 username。

> 当前实现：`FilesystemUserContextResolver.resolve(..., use_workspace_context=True)` 直接返回 workspace 根目录和全局 `contexts/sessions*`，不会创建或使用 `contexts/users/{owner_id}`。参数名明确表达这是完整 workspace operator 上下文，而不只是切换 session 路径。历史残留目录不自动删除，避免误删既有文件。

QQ、微信接入时，不在 `contexts/users/{user_id}` 下按渠道再拆权限边界。外部渠道身份通过数据库映射到内部 `user_id`；如果确实需要保存渠道文件，可在 `files/channels/{channel}/` 下组织，但权限边界仍然是内部用户。

### 4.3 Tool 边界

当前 `ToolProvider` 协议是：

```python
definitions() -> list[dict[str, Any]]
execute(name: str, args: dict[str, Any]) -> ToolResult
```

当前 `ToolRegistry.execute()` 只根据 tool name 分发，不知道：

- 哪个用户触发。
- 属于哪个 session / turn。
- 来自 CLI、REST、SSE 还是 WebSocket。
- 是否需要用户确认。
- 审计事件应该写到哪里。

第九部分不能让每个工具自己去 import auth store。工具权限应该收敛到 AgentLoop dispatch 前后的统一策略。

### 4.4 `exec` 边界

当前 `ExecTool` 已有基础安全能力：

- workspace cwd guard。
- timeout。
- 输出截断。
- secret redaction。
- 明确拦截 destructive、network/install、environment dump 和复杂 shell syntax。

第九部分的目标不是移除这些 guard，而是把其中可确认、可审计的高风险操作纳入权限流程：

```text
基础安全不可绕过：
  workspace guard
  timeout
  output truncation
  secret redaction
  unsupported shell syntax deny
  clearly unclassifiable destructive shell deny

可进入权限和确认：
  safe exec
  known network/install command
  known workspace-bounded destructive command
```

环境变量整表 dump 第一版继续拒绝。它风险太高，后续若确有需要，应做专门 secret-aware diagnostic tool，而不是开放 `env` / `set` / `Get-ChildItem Env:`。

---

## 5. 核心模型

### 5.1 实体关系

```text
User
  -> UserRole
  -> Role
  -> RolePermission
  -> Permission

User
  -> AuthSession

User
  -> SessionOwner
  -> JSONL Session
  -> TurnRun
  -> ToolCallRecord
  -> ToolConfirmation
  -> AuditEvent
```

### 5.2 ActorContext

运行时不要在各层传裸 user dict。建议使用一个协议层结构：

```python
@dataclass(frozen=True)
class ActorContext:
    actor_type: str  # user | local_operator | system
    user_id: str | None
    username: str
    display_name: str
    role_keys: frozenset[str]
    permission_keys: frozenset[str]
    channel: str  # cli | web | external_ws | rest | sse
    auth_session_id: str | None = None
```

设计原则：

- app shell 负责从 cookie / token 解析 actor。
- core 层只消费 ActorContext，不查询用户库。
- Web 和外部渠道必须解析到 DB 内部 `user_id` 后才能进入用户目录。
- CLI 使用 `actor_type=local_operator` 的 no-login 表示，Web 使用唯一 `owner` DB 账号的登录表示；两者是同一个 workspace operator，共用全局物理目录。Owner 的 DB id 用于认证、权限、索引和审计，不用于创建 Owner 用户目录。
- CLI 仍要经过 tool policy、危险命令策略、确认、超时、脱敏和审计策略。
- ActorContext 不携带 password hash、token、cookie 或完整请求头。

### 5.3 权限决策

```python
@dataclass(frozen=True)
class PermissionDecision:
    allowed: bool
    code: str
    message: str
    require_confirmation: bool = False
    risk_level: str = "low"  # low | medium | high | critical
```

权限判断只回答“当前 actor 是否允许尝试这个动作”。是否能真正执行，还要继续经过：

```text
input validation
workspace guard
tool-specific safety policy
confirmation
execution timeout
audit
```

所有入口都不能绕过基础安全 guard：workspace guard、危险命令策略、超时、脱敏、确认和审计仍然保留。

### 5.4 ToolExecutionContext

工具 dispatch 需要稳定上下文：

```python
@dataclass(frozen=True)
class ToolExecutionContext:
    actor: ActorContext
    session_id: str
    turn_id: str
    turn_index: int | None
    channel: str
    source: str = "llm"  # llm | command | api
```

第九部分可以通过以下方式引入而不污染 Tool 实现：

```text
AgentLoop.run_turn(..., actor=ActorContext)
  -> parse tool call
  -> build ToolExecutionContext
  -> ToolExecutionPolicy.decide(...)
  -> maybe request confirmation
  -> ToolRegistry.execute(name, args)
  -> audit result
```

`ToolRegistry` 第一版可以继续保持执行职责简单；权限策略在 AgentLoop dispatch 层做。

---

## 6. SQLite schema 草案

第一版使用 SQLite，路径建议：

```text
${ZHICE_AGENT_WORKSPACE}/state/auth.sqlite3
```

原因：

- 从 workspace 派生，符合路径规范。
- `state/` 表示运行状态，不和 `config/` 下的配置模板混在一起。
- 不进入仓库，不提交真实用户数据。
- 单文件便于本地备份和删除重置。
- 第一版是本地轻量 Agent，不需要启动 MySQL / PostgreSQL 这类服务型数据库。

暂不使用 MySQL / PostgreSQL。等出现远程部署、多 gateway 实例、高并发、多 workspace、多个 `admin` 角色用户的复杂统计或大量 audit 查询时，再通过 AuthStore / AuditStore 协议迁移到服务型数据库。

如果后续 audit 量变大，再把 audit events 拆到 `${ZHICE_AGENT_WORKSPACE}/state/audit.sqlite3` 或日志系统。第一版先保持一个库，减少事务和迁移复杂度。

### 6.1 users

```sql
CREATE TABLE users (
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
```

字段约束：

- `status`: `active | disabled`.
- password 使用标准库 `hashlib.pbkdf2_hmac`，每用户独立 salt。
- 不在日志或 API response 中返回 password_hash / password_salt。

### 6.2 auth_sessions

```sql
CREATE TABLE auth_sessions (
  id TEXT PRIMARY KEY,
  user_id TEXT NOT NULL REFERENCES users(id),
  token_hash TEXT NOT NULL UNIQUE,
  created_at TEXT NOT NULL,
  expires_at TEXT NOT NULL,
  revoked_at TEXT,
  user_agent_preview TEXT NOT NULL DEFAULT '',
  remote_addr_preview TEXT NOT NULL DEFAULT ''
);
```

规则：

- cookie 中只保存随机 token，不保存 user_id。
- SQLite 只保存 token hash。
- logout 设置 `revoked_at`。
- 默认过期时间可以先用 7 天；后续再做用户可配置。

### 6.3 external_identities

```sql
CREATE TABLE external_identities (
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

CREATE INDEX idx_external_identities_user ON external_identities(user_id, channel);
```

规则：

- `user_id` 是 ZhiCe-Agent 内部主身份。
- `channel` 当前可以是 `web | cli | qq | weixin`。
- QQ、微信外部 user id 只作为映射，不进入用户目录路径。
- 同一个内部用户可以绑定多个外部渠道身份。

### 6.4 roles / permissions

```sql
CREATE TABLE roles (
  id TEXT PRIMARY KEY,
  key TEXT NOT NULL UNIQUE,
  name TEXT NOT NULL,
  description TEXT NOT NULL DEFAULT '',
  is_builtin INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE permissions (
  key TEXT PRIMARY KEY,
  description TEXT NOT NULL,
  category TEXT NOT NULL
);

CREATE TABLE user_roles (
  user_id TEXT NOT NULL REFERENCES users(id),
  role_id TEXT NOT NULL REFERENCES roles(id),
  PRIMARY KEY (user_id, role_id)
);

CREATE TABLE role_permissions (
  role_id TEXT NOT NULL REFERENCES roles(id),
  permission_key TEXT NOT NULL REFERENCES permissions(key),
  PRIMARY KEY (role_id, permission_key)
);
```

内置 role 可以改权限集合，但不建议删除；删除会增加迁移复杂度。

### 6.5 session_index

```sql
CREATE TABLE session_index (
  session_id TEXT PRIMARY KEY,
  owner_user_id TEXT NOT NULL REFERENCES users(id),
  channel TEXT NOT NULL DEFAULT 'web',
  external_chat_id TEXT NOT NULL DEFAULT '',
  external_thread_id TEXT NOT NULL DEFAULT '',
  title TEXT NOT NULL DEFAULT '',
  preview TEXT NOT NULL DEFAULT '',
  message_count INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  archived_at TEXT
);

CREATE INDEX idx_session_index_owner ON session_index(owner_user_id, updated_at);
CREATE INDEX idx_session_index_channel ON session_index(channel, external_chat_id, updated_at);
```

JSONL 仍是真实消息记录。物理路径由 actor 解析得出：Owner 固定使用全局 `contexts/sessions/{session_id}.jsonl`，普通 Web / 外部渠道用户使用 `contexts/users/{user_id}/sessions/{session_id}.jsonl`。`session_index` 负责列表、标题、更新时间、归档、渠道元数据和审计关联，但不能单独替代物理目录边界。

模型偏好不进入 SQLite，也不在 `session_index` 重复保存。它属于 session metadata：

```json
{
  "title": "...",
  "preferred_endpoint_name": "openai_gpt5",
  "preferred_model_name": "gpt-5-mini"
}
```

CLI 与 Owner Web 写入全局 `contexts/sessions_meta/{session_id}.json`；其他 Web / 外部渠道用户写入 `contexts/users/{user_id}/sessions_meta/{session_id}.json`。偏好字段为空或 metadata 不存在时，使用系统默认 endpoint/model。

### 6.6 turn_runs

```sql
CREATE TABLE turn_runs (
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
  error_code TEXT NOT NULL DEFAULT ''
);

CREATE INDEX idx_turn_runs_session ON turn_runs(session_id, started_at);
CREATE INDEX idx_turn_runs_actor ON turn_runs(actor_user_id, started_at);
```

`turn_runs` 是 Runtime Activity 运行索引，不替代 JSONL message。`turn_id` 已经全局唯一，直接作为主键，不再额外生成重复的 `turn-run-*` id。本地项目不保留旧表兼容结构。

### 6.7 tool_call_records

```sql
CREATE TABLE tool_call_records (
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

CREATE INDEX idx_tool_calls_turn ON tool_call_records(session_id, turn_id);
CREATE INDEX idx_tool_calls_actor ON tool_call_records(actor_user_id, started_at);
```

规则：

- `args_preview` 必须脱敏和截断。
- `args_hash` 用于确认“用户批准的是同一组参数”，不用于还原参数。
- `output_preview` 不写完整工具结果。
- `stdout_tail` / `stderr_tail` 只保留尾部短片段，用于用户可见诊断报告，不替代完整工具输出。
- 对非 `exec` 工具，`command_preview`、`cwd`、`exit_code`、`timeout_seconds` 等字段可以为空。

### 6.8 tool_confirmations

```sql
CREATE TABLE tool_confirmations (
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
```

`status`: `pending | approved | denied | expired | cancelled`。

第一版确认应有短过期时间，例如 5 分钟。过期后必须重新发起工具调用，不复用旧批准。

### 6.9 audit_events

```sql
CREATE TABLE audit_events (
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

CREATE INDEX idx_audit_ts ON audit_events(ts);
CREATE INDEX idx_audit_actor ON audit_events(actor_user_id, ts);
CREATE INDEX idx_audit_session_turn ON audit_events(session_id, turn_id);
```

Audit 与 trace 的分工：

- 每日结构化 JSONL 面向运行排查，主要回答“这一轮发生了什么”。
- `audit_events` 面向安全审计，主要回答“谁在什么时候对什么资源做了什么，决策是什么”。

---

## 7. 基础能力与特权 key

### 7.1 登录用户基础能力

以下功能是每个已认证内部用户的基础能力，不进入 RBAC：

- 查看和修改自己的资料、修改自己的密码。
- 创建、读取、改名、清空和删除自己的 Session。
- 发起聊天、停止自己的活动 turn、读取自己的 turn 信息。
- 查看模型并设置或重置自己 Session 的模型偏好。
- 使用只读工具、低风险 `exec` 和已安装 Skill。
- 查询自己的近期诊断。
- 读取自己的 Memory；在获得对话式用户授权后写入自己的 Memory；汇总自己的 Session。

基础能力不等于取消边界。Session、files、metadata 和 Memory 继续通过 `actor.user_id` 与资源 owner 匹配来隔离；访问其他用户资源默认隐藏，不能通过猜测 id 绕过。

### 7.2 特权权限

当前 `PERMISSIONS` 只包含：

```text
auth.users.read
auth.users.manage
auth.admin.manage
auth.roles.read
auth.roles.manage
session.manage.any
chat.stop.any
turn.read.any
tool.exec.dangerous
skill.sync
audit.read
audit.export
```

- `session.manage.any`、`chat.stop.any`、`turn.read.any` 只用于跨用户范围。
- `tool.exec.dangerous` 只允许请求进入高风险命令确认流程，不能绕过 command policy、workspace guard、永久禁止项或用户确认。
- `skill.sync` 会改变全局 Skill source 状态，因此保留为特权；读取和使用已安装 Skill 是基础能力。
- 用户、角色和审计接口继续检查对应特权。

### 7.3 Ownership 与 permission

```text
资源属于当前 actor
  -> 基础能力直接允许

资源属于其他 actor
  -> 默认拒绝或表现为不存在
  -> 具有对应 any/manage 特权时才允许
```

因此不再保留 `session.read.own`、`session.write.own`、`memory.read.own` 等重复表达 ownership 的 key。`/api/auth/me` 返回的 `permissions` 现在只表示额外特权，不是完整功能清单。

模型偏好仍按 Session 隔离；每个 turn 绑定 call-scoped provider，不修改 gateway 共享 provider 的全局状态。

---

## 8. 默认角色

`owner` 是 CLI workspace operator 在 Web 端的唯一永久最高身份；`admin` 是日常运营角色。CLI 入口不要求登录或持有 DB token，但与 Owner 表示同一个本地运维主体。普通 Admin 默认不能任命管理员；只有 Owner 和被 Owner 直接委派 `auth.admin.manage` 的 Admin 可以提升或降级普通 Admin，且该委派不会传播。

### 8.1 owner

用途：唯一系统拥有者。拥有全部特权，不能被禁用、删除、降级或通过普通角色管理修改。Web 初始化受 `ZHICE_AGENT_SETUP_TOKEN` 保护；CLI 可初始化或恢复同一个 Owner。

### 8.2 admin

用途：用户与运行管理。默认拥有用户读取/管理、角色读取和跨用户运行管理能力，但不包含管理员任命、角色权限修改、危险执行、Skill 同步和审计特权。

权限：

```text
auth.users.read
auth.users.manage
auth.roles.read
session.manage.any
chat.stop.any
turn.read.any
```

Owner 可向指定 Admin 直接委派：

```text
auth.admin.manage
```

该权限存入用户直接权限表，不进入 `admin` 角色，因此新提升的 Admin 不会自动继续任命管理员。

### 8.3 developer

用途：普通本地使用者。当前没有额外特权，日常功能来自登录用户基础能力。保留该角色用于兼容现有账号和后续可能出现的开发类特权，不为维持角色差异硬造权限。

### 8.4 viewer

用途：普通注册用户。额外特权为空，但作为已认证内部用户可以管理自己的会话、聊天、切换当前会话模型、使用安全工具和本人 Memory。跨用户管理、用户/角色管理、审计、Skill 同步和危险执行仍不可用。

### 8.5 auditor

用途：查看审计。

特权：

```text
audit.read
turn.read.any
```

---

## 9. 登录与 token 策略

### 9.1 唯一 Owner 初始化

Owner 必须通过以下两个入口之一初始化，不能由普通注册或角色数组产生：

```bash
zcagent auth init-owner
```

或访问隐藏页面 `/_setup`，由 Owner 初始化表单调用：

```text
POST /api/auth/bootstrap
```

行为：

1. 加载 workspace 和 auth DB。
2. CLI 通过隐藏密码输入创建；Web 必须额外校验部署 Secret `ZHICE_AGENT_SETUP_TOKEN`。
3. 已有普通用户不阻塞初始化；已有 Owner 时永久拒绝再次初始化。
4. 创建的用户只授予唯一 `owner` 角色。
5. 支持 `--username`，密码从安全输入读取，不通过命令行明文传入。

`GET /_setup` 只在 Owner 不存在且部署 Secret 已配置时返回页面，其余情况返回 404；普通登录页、注册页和账号菜单都不展示该入口。Web 用户名由服务端固定为 `owner`，表单只提交一次密码和 setup credential。bootstrap 创建成功后设置 HttpOnly cookie 并跳转首页。

### 9.2 普通用户自助注册

普通用户自助注册由 Owner 独占的持久策略控制，默认关闭。Owner 在管理后台“账号管理”中开启后，匿名页面才展示注册入口，后端才接受：

```text
GET /api/auth/registration-policy
POST /api/auth/register
GET/PATCH /api/admin/auth/registration-policy  # Owner only
```

关闭时前端登录页和 QQ 绑定认证页都不展示注册切换，`POST /api/auth/register` 独立返回 `403 AUTH_REGISTRATION_DISABLED` 并记录拒绝审计，不能通过直接调用接口绕过。管理员手工创建账号不受影响。策略保存在 `auth.sqlite3.auth_settings`，跨 Gateway 和容器重启保留；读取异常 fail closed。

开放后，请求仍只接受 `username`、`password`。服务端令 `display_name = username`，并固定调用 `create_user(..., role_keys=["viewer"])`；客户端额外提交的显示名、角色或权限字段不会改变派生显示名和最终角色。注册成功后创建 HttpOnly cookie 并自动登录，用户可在 Account settings 修改显示名。重复用户名返回 `409 USER_USERNAME_ALREADY_EXISTS`。当前仍未实现验证码、限流、邀请、反滥用和账号验证能力，因此生产公网建议保持关闭，仅在明确时间窗口内开放。

### 9.3 登录 API

```text
POST /api/auth/bootstrap
POST /api/auth/register
POST /api/auth/login
POST /api/auth/logout
GET  /api/auth/me
```

登录成功后设置：

```text
Set-Cookie: zcagent_session=<opaque-token>; HttpOnly; SameSite=Lax; Path=/
```

规则：

- 默认本地 HTTP 不强制 `Secure`，因为 `127.0.0.1` 开发环境通常没有 TLS。
- 如果以后支持 HTTPS 或反代，应自动设置 `Secure`。
- 用户名不存在和密码错误统一返回 `401 AUTH_INVALID_CREDENTIALS`，但审计日志保留 `AUTH_USER_NOT_FOUND` 或 `AUTH_INVALID_PASSWORD` 真实原因；disabled user 明确返回 `403 AUTH_ACCOUNT_DISABLED`。

### 9.4 WebSocket 鉴权

浏览器 WebSocket 依靠同源 cookie。握手后如果没有有效 actor：

```text
channel_status error AUTH_REQUIRED
close code 1008
```

external WS 第一版必须显式认证，建议支持：

```text
Authorization: Bearer <token>
```

如果目标客户端不便设置 header，可以后续增加 `hello` frame auth token，但不优先使用 query token，避免 token 出现在日志、浏览器历史或代理记录里。

---

## 10. 用户上下文和 session 索引策略

### 10.1 新 session

新 session 的内部归属在第一次创建或第一次写入时确定，同时创建对应会话目录和数据库索引：

```text
Web new_session frame
  -> create session id
  -> Owner ensures contexts/sessions exists
  -> other users ensure contexts/users/{actor.user_id}/sessions exists
  -> insert session_index(owner_user_id=current actor, channel=web)

POST /api/chat with new session_id
  -> if session_index missing and target JSONL missing
  -> Owner creates under contexts/sessions
  -> other users create under contexts/users/{actor.user_id}/sessions
  -> insert session_index
```

`session_id` 仍保持全局唯一，避免跨用户同名 session 在 Web/API 上造成歧义。物理路径由内部 `user_id` 和 `session_id` 一起解析，不允许客户端提交路径。

### 10.2 读取和列表

默认规则：

- 所有角色的日常列表只返回 `session_index.owner_user_id = actor.user_id` 的 session。
- `session.manage.any` 只允许显式 read/write/delete 目标 session，不自动扩大聊天侧栏。
- `GET /api/sessions/{session_id}` 对无权限 session 返回 404 或 403 要统一。建议第一版返回 404，避免泄露 session 是否存在。
- 列表优先读 `session_index`，必要时再回读用户 JSONL / `sessions_meta` 校准 preview 和 message_count。

### 10.3 重命名和删除

```text
PATCH /api/sessions/{session_id}
  -> owner match, or require session.manage.any for cross-user access

DELETE /api/sessions/{session_id}
  -> owner match, or require session.manage.any for cross-user access
```

外部渠道 Session 同样允许其内部所有者删除。删除事务先移除所有 `current_session_id` 指向该 Session 的 `channel_conversations` 路由，再删除 index；下一条 QQ/微信消息通过既有 resolve 流程创建新 Session。账号绑定、receipt、审计和平台消息不随 Session 删除。

删除当前 active turn 时，active turn key 必须包含 owner：

```text
(actor.user_id, session_id) -> ActiveTurn
```

避免一个用户用相同 session id 停掉另一个用户的 turn。

### 10.4 CLI 全局 session 保留与 Owner 索引对账

CLI 当前已经存在的全局 JSONL session 不属于普通 DB 用户目录。当前实现口径：

1. `zcagent auth init-owner` 只创建唯一 `owner` DB 用户，不移动或复制既有 CLI JSONL。
2. CLI 与 Owner Web 共同读取和写入 `${ZHICE_AGENT_WORKSPACE}/contexts/sessions` 与 `${ZHICE_AGENT_WORKSPACE}/contexts/sessions_meta`。
3. Owner 列表会自动为未索引的全局 CLI session 补 `session_index(owner_user_id=owner, channel=cli_legacy)`，只补索引，不改写 JSONL/metadata。
4. 普通 Web / 外部渠道用户从 `contexts/users/{user_id}` 和 `session_index` 进入；未索引的 CLI 历史不默认展示给普通用户。
5. 当前阶段不提供普通用户 CLI 历史会话导入；普通用户只从自己的新建会话进入，避免把未归属的 CLI 历史复制到用户目录。

这样既不丢历史，也不让匿名历史在多用户模式下默认暴露。

### 10.5 外部渠道 session

QQ、微信渠道不改变用户目录结构。渠道身份先通过 `external_identities` 解析到内部 `user_id`，再将 session 写入该用户目录。

`session_index` 记录渠道元数据：

```text
channel = qq | weixin | web
external_chat_id = QQ 私聊 / 群聊或微信私聊等外部会话 id
external_thread_id = 外部 thread / topic id，可为空
```

这样同一个内部用户从 Web、QQ、微信进入时仍共用同一套权限和用户上下文边界；渠道 id 变更、解绑、重绑不影响文件路径。

---

## 11. Tool 权限与确认

### 11.1 总体数据流

```text
AgentLoop parsed tool call
  -> build ToolExecutionContext(actor, session_id, turn_id, channel)
  -> ToolExecutionPolicy.decide(tool_name, args, context)
      -> deny: write tool error + audit deny
      -> allow: execute tool + audit result
      -> require_confirmation:
           -> create tool_confirmation
           -> emit confirmation_required event
           -> wait for approve/deny/timeout/cancel
           -> approve: execute tool + audit result
           -> deny/timeout: write tool error + audit deny
```

`AgentLoop` 不直接查询角色表。它只调用协议：

```python
class ToolExecutionPolicy(Protocol):
    def decide(
        self,
        tool_name: str,
        args: dict[str, Any],
        context: ToolExecutionContext,
    ) -> ToolExecutionDecision:
        ...


class ToolConfirmationBroker(Protocol):
    def request(
        self,
        decision: ToolExecutionDecision,
        context: ToolExecutionContext,
    ) -> ToolConfirmationResult:
        ...
```

app shell / auth 模块实现这些协议。

### 11.2 ToolExecutionDecision

```python
@dataclass(frozen=True)
class ToolExecutionDecision:
    action: Literal["allow", "deny", "confirm"]
    code: str
    message: str
    permission_key: str
    risk_level: Literal["low", "medium", "high", "critical"] = "low"
    audit_metadata: dict[str, Any] = field(default_factory=dict)
```

### 11.3 `exec` 风险分类

把当前 `validate_command()` 从只返回 allowed/blocked 扩展为 risk decision：

```text
safe
  -> authenticated user baseline capability
  -> no confirmation

network_or_install
  -> requires tool.exec.dangerous
  -> requires confirmation

destructive_confirmable
  -> requires tool.exec.dangerous
  -> requires confirmation

env_dump
  -> deny

unsupported_shell_syntax
  -> deny

unclassified_destructive
  -> deny
```

第一版只允许可分类的高风险命令进入确认。无法判断路径范围或命令含义的破坏性 shell 仍拒绝。

权限 UI 只暴露真正的特权，例如 `tool.exec.dangerous`；安全 exec 不显示权限开关。诊断、审计和 trace 继续保留更细的 `risk_category`，例如 `network`、`destructive`、`env_dump`、`unsupported_shell`。

### 11.4 确认内容

确认 UI 必须显示：

```text
工具名
风险级别
命令 preview
工作目录
权限 key
当前用户
session_id
turn_id
过期时间
```

确认时记录：

```text
confirmation_id
actor_user_id
decision_actor_user_id
args_hash
approved/denied/expired/cancelled
```

批准只对同一个 `tool_call_record_id` 和 `args_hash` 生效。用户改命令后必须重新发起确认。

### 11.5 CLI 确认

CLI 没有 WebSocket 交互通道，但可以阻塞式询问：

```text
exec wants to run a high-risk command:
  command: ...
  risk: high
Type the confirmation id to approve, or press Enter to deny:
```

CLI 本地操作者如果接入 ToolExecutionPolicy，权限来自本地入口的 `local_operator` 权限配置或内置 profile，不来自 DB 用户角色。没有对应 permission 时不进入确认。

### 11.6 Web 确认

WebRuntime 通过 `on_event` 发出：

```json
{
  "type": "tool_confirmation_required",
  "confirmation_id": "conf_...",
  "tool_name": "exec",
  "risk_level": "high",
  "command_preview": "git clean ...",
  "expires_at": "2026-07-08T20:10:00+08:00"
}
```

前端显示确认卡片或 modal，调用：

```text
POST /api/tool-confirmations/{confirmation_id}/approve
POST /api/tool-confirmations/{confirmation_id}/deny
```

AgentLoop 在 broker 上等待结果。等待期间如果 Web stop 触发 cancellation token，应取消确认并写审计。

---

## 12. Runtime Activity、Audit 与诊断

### 12.1 Security Audit 必须记录的 action

```text
auth.login_success
auth.login_failed
auth.logout
auth.session_revoked

user.created
user.updated
user.disabled
role.updated

session.deleted

tool.call_requested
tool.call_denied
tool.confirmation_requested
tool.confirmation_approved
tool.confirmation_denied
tool.confirmation_expired
tool.call_done
tool.call_error

memory.write
audit.read
audit.export
```

这里的 tool action 只针对危险执行、确认、特权工具、Memory 持久化修改和安全拒绝。普通安全工具的 requested/allowed/done 只写 Runtime Activity 和 trace。

### 12.2 Runtime Activity action

```text
chat.turn_started
chat.turn_done
chat.turn_stopped
chat.turn_error

tool.call_requested
tool.call_allowed
tool.call_denied
tool.confirmation_requested
tool.call_done
tool.call_error
```

模型查看/切换、Provider fallback 和 Session 创建/重命名等普通运行事件写 trace 或业务状态，不写 Security Audit。trace 记录当前 turn 的 preferred/actual 模型、reason code 和耗时。

### 12.3 脱敏规则

Audit 不写：

- 明文密码。
- token / cookie。
- API key。
- 完整 prompt。
- 完整 user input。
- 完整 tool args。
- 完整 tool output。
- 完整环境变量。

可以写：

- 截断 preview。
- args hash。
- output preview。
- error code。
- permission key。
- decision code。
- risk category。
- request id。
- session_id / turn_id / tool_call_id。

### 12.4 和 trace log 的一致性

同一 tool call 至少应能通过以下字段关联：

```text
actor_user_id
auth_session_id
request_id
session_id
turn_id
tool_call_id
tool_name
channel
route
status_code
```

trace log 仍保持轻量，但第九部分实现阶段开始应尽量写入 `actor_user_id`、`auth_session_id`、`request_id`、`session_id`、`turn_id`、`channel`、`route`、`status_code` 等字段。audit events 写 actor、resource、decision、risk 和 reason code，是安全审计账本；trace log 是运行排查流水。

HTTP access / gateway 事件未必都有 `turn_id`，例如登录失败、WebSocket 鉴权失败、session 无权限、模型选择接口失败、请求格式错误。此类事件至少应带 `actor_user_id`（能解析时）、`auth_session_id`、`request_id`、`route`、`status_code` 和安全错误码，方便用户级诊断按用户和时间范围聚合。

### 12.5 用户可见诊断工具

普通用户不能直接读取 raw 每日 JSONL 日志，但可以在当前对话中让 Agent 自助诊断自己的近期问题：

```text
diagnose_my_recent_activity
```

Tool 由 WebRuntime 注入当前诊断上下文：

```text
actor = 当前用户
session_id = 当前 Session
current_turn_id = 正在询问诊断的这一轮
current_request_id = 当前请求
```

模型只表达自然诊断意图：

```text
focus = auto | latency | failure | trend
target = auto | previous_turn | latest_failure | recent_activity
minutes = 默认 30
```

模型和用户都不需要知道 `session_id`、`turn_id` 或 `request_id`。后端自动排除当前诊断 Turn，并从当前 Session 选择上一条已完成 Turn、最近一次失败或近期失败趋势。

证据读取顺序：

```text
turn_runs
  -> 定位目标 Turn、状态、总耗时和 request_id

tool_call_records
  -> 工具决策、耗时、错误码、timeout 和安全输出尾部

相关 trace
  -> 补充 LLM 调用耗时、Provider 和 Session 保存证据
```

`diagnose_my_recent_activity` 始终只查当前用户和当前 Session。即使当前 actor 是 Developer、Admin 或 Owner，在普通聊天中也不会自动扩大到全系统范围。

诊断工具返回安全摘要，不返回 raw trace、cookie、authorization header、完整请求体、完整响应体、完整 prompt、完整 tool args、完整 tool output、其它用户信息、服务器堆栈或敏感路径。

### Owner 工作区工具范围

Owner 是本地工作区的运维主体。Owner Web turn 的文件和 Skill 工具仍以 `${ZHICE_AGENT_WORKSPACE}` 为根，但普通聊天中的 `diagnose_my_recent_activity` 仍只返回 Owner 自己当前 Session 的安全 activity/trace 摘要。全系统诊断放入后续独立监控平台。

### 12.6 用户可见诊断报告规范

用户问“刚才为什么失败了”时，Agent 不能只回答“超时了”或只给错误码。诊断报告必须区分：

```text
直接事实
具体原因或高置信推断
证据
不能确认的部分
下一步建议
```

当前诊断结果结构：

```json
{
  "status": "diagnosed",
  "focus": "failure",
  "target": {
    "session_id": "自动解析",
    "turn_id": "自动解析",
    "request_id": "自动关联"
  },
  "summary": "上一轮失败发生在 exec 工具执行阶段。",
  "failure_stage": "tool.exec",
  "cause_code": "COMMAND_TIMEOUT",
  "confirmed_facts": ["tool=exec", "duration_ms=30000"],
  "probable_cause": "命令超过配置的执行时间限制。",
  "confidence": "high",
  "evidence": [],
  "next_actions": [
    "根据 stdout/stderr 安全尾部缩小复现范围"
  ],
  "limitations": []
}
```

如果日志无法确认具体原因，必须明说：

```text
能确认：命令已启动，未被权限拒绝，运行 30 秒后超时。
不能确认：命令内部卡在哪一步，因为 stdout/stderr 没有输出。
可能原因：等待输入、子进程阻塞、扫描文件过多或外部资源无响应。
下一步：用更小范围或 verbose 参数重跑。
```

### 12.7 Runtime Activity 与 Security Audit

`turn_runs`、`tool_call_records` 由独立 `RuntimeActivitySink` 维护，不再通过 AuditSink 的副作用更新。

```text
Runtime Activity
  -> 普通 turn 生命周期
  -> 普通工具 requested/decision/result
  -> 失败、耗时和诊断索引

Security Audit
  -> 登录和账号安全
  -> 用户、角色和特权管理
  -> 跨用户访问
  -> 危险工具、确认和安全拒绝
  -> Memory 持久化修改安全摘要
  -> Session 删除等安全相关操作
```

普通聊天成功、普通安全工具成功、普通成功 HTTP 请求、模型查看/切换等运行流水不再写入 audit；它们继续存在于 trace、业务状态或 activity records 中。

为支持诊断，`tool_call_records` 和 trace 至少保留安全字段：

```text
tool_name
command_preview
cwd
timeout_seconds
duration_seconds
exit_code
error_code
stdout_tail
stderr_tail
output_truncated
last_event
risk_category
permission_decision
confirmation_status
```

---

## 13. API 与 UI 范围

### 13.1 新 API

Auth：

```text
POST /api/auth/login
POST /api/auth/logout
GET  /api/auth/me
```

Admin：

```text
GET    /api/admin/users
POST   /api/admin/users
PATCH  /api/admin/users/{user_id}
GET    /api/admin/roles
PATCH  /api/admin/roles/{role_id}
```

Audit：

```text
GET /api/audit/events
```

Confirmation：

```text
GET  /api/tool-confirmations
POST /api/tool-confirmations/{confirmation_id}/approve
POST /api/tool-confirmations/{confirmation_id}/deny
```

### 13.2 现有 API 变化

所有现有 `/api/*` 接口都要求 actor：

```text
unauthenticated -> 401 AUTH_REQUIRED
permission denied -> 403 AUTH_PERMISSION_DENIED
resource not visible -> 404 SESSION_NOT_FOUND（session 场景）
```

HTTP 错误 body 统一为 `error.status/code/message/request_id/details`。HTTP status 表示协议结果，`code` 表示稳定业务原因；已知场景不得退化为裸 `FORBIDDEN` 或 `INVALID_REQUEST`。例如普通 Admin 未获得管理员管理委派时返回 `AUTH_ADMIN_MANAGEMENT_NOT_DELEGATED`，所需权限写入 `details.required_permission`。完整规范见 `docs_design/2026-07-11-api-error-code-contract-design.md`。

`/api/health` 可以继续匿名返回基础健康信息，但不能暴露 session 目录、用户信息、endpoint base_url 或密钥来源。

模型 API 改为 actor + session-aware：

```text
GET  /api/models?session_id={session_id}
  -> 校验 actor 对 session 的访问权限
  -> 返回当前 session 的 effective endpoint/model 和可选模型

POST /api/model/preference
  -> 请求体携带 session_id
  -> 校验 actor 对 session 的写权限
  -> 校验 endpoint/model
  -> 写入当前 session metadata
  -> 不修改共享 gateway provider

DELETE /api/model/preference?session_id={session_id}
  -> 清除当前 session 偏好并恢复系统默认
```

POST 请求体至少包含 `session_id` 和 `model`。服务端按当前 session 的有效 endpoint 校验模型，并同时保存该 `endpoint_name`；这里不能读取共享 gateway 的 current endpoint。未来开放 endpoint 选择时再显式增加 endpoint 字段。

### 13.3 静态 UI

第一版继续可以使用 `web/static` 原生 HTML/CSS/JS，不强制前端工程化。

需要新增：

- 登录页或登录视图。
- 普通用户自助注册默认关闭，只有 Owner 可持久开启；开放后注册用户固定为 `viewer`。
- Web 注册不要求 display name；登录、Owner 初始化和普通注册输入框使用图标与灰色 placeholder。
- Owner 可为指定 Admin 开关 `auth.admin.manage`；普通 Admin 默认不能任命管理员，委派不会传播。
- 左下角用户入口显示当前用户和 logout。
- 管理入口，仅拥有 `admin` 角色或对应管理权限的用户可见。
- 用户列表。
- 角色权限配置。
- 审计列表。
- 诊断入口，用于查询当前用户近期失败原因和工具执行摘要。
- tool confirmation modal/card。
- 个人设置：修改显示名、修改密码。
- 现有模型选择器展示当前 session 偏好；用户切换后只更新当前 session，不刷新为 gateway 全局状态。
- 新建 session 后模型选择器立即显示系统默认；切换回已有 session 时重新读取该 session 偏好。

不新增：

- 复杂 dashboard。
- 多租户空间切换。
- 审计统计图。
- 用户头像上传。
- 权限模板市场。
- 注册时自选角色或权限。

---

## 14. 模块设计

### 14.1 `agent/protocols/auth.py`

新增协议层数据结构：

```text
ActorContext
PermissionDecision
AuthProvider
PermissionChecker
AuditSink
UserContextResolver
ExternalIdentityResolver
```

规则：

- 不 import SQLite 实现。
- 不 import FastAPI。
- 不 import AgentLoop。

### 14.2 `agent/protocols/session.py`

增加 session 模型偏好协议：

```text
SessionModelPreference(endpoint_name, model_name)
SessionModelPreferenceStore.get(session_context, session_id)
SessionModelPreferenceStore.set(session_context, session_id, preference)
SessionModelPreferenceStore.reset(session_context, session_id)
```

协议只描述 session metadata，不 import auth、SQLite 或具体 JSON 文件实现。

### 14.3 `agent/protocols/llm.py`

增加 provider-neutral 的调用选择结构，或提供等价协议：

```text
ModelSelection(endpoint_name, model_name, source)
LLMProviderResolver.bind(selection) -> LLMProvider
```

`ModelSelection` 不携带 `user_id`、role、auth session 或 session metadata 路径。app/runtime 先把 session 偏好解析成模型选择，AgentLoop 最终仍只消费 `LLMProvider`。

### 14.4 `agent/protocols/tool.py`

扩展工具执行上下文和策略协议：

```text
ToolExecutionContext
ToolExecutionDecision
ToolExecutionPolicy
ToolConfirmationBroker
ToolConfirmationResult
```

是否修改 `ToolProvider.execute()` 签名要谨慎。优先让 AgentLoop 在调用现有 `execute(name, args)` 前后完成 policy/audit。只有当工具实现自身确实需要 actor 时，再扩展 Tool 协议。

### 14.5 `agent/auth/`

当前实际模块：

```text
agent/auth/__init__.py
agent/auth/store.py
agent/auth/schema.py
agent/auth/passwords.py
agent/auth/tokens.py
agent/auth/audit.py
agent/auth/session_access.py
agent/auth/user_context.py
agent/auth/tool_policy.py
agent/auth/confirmation.py
agent/auth/diagnostics.py
```

职责：

- SQLite schema 初始化和轻量 migration。
- 用户、角色、权限 CRUD。
- password hash / verify。
- token create / verify / revoke。
- permission aggregation。
- 用户上下文目录解析和 path guard。
- session index 访问控制。
- 外部渠道身份映射当前由 `store.py` 提供基础 CRUD/解析，等渠道接入复杂后再独立拆模块。
- audit event 写入。
- tool permission decision。
- confirmation 状态管理。
- 用户可见诊断摘要。

### 14.6 `agent/session/model_preferences.py`

职责：

- 在已解析的 `sessions_meta/{session_id}.json` 中读写模型偏好字段。
- 保留现有 title 等 metadata 字段，不用整文件覆盖其它内容。
- 校验 session id 和 metadata 路径边界。
- metadata 不存在或偏好为空时返回“使用系统默认”。
- reset 只删除模型偏好字段，不清空 session 消息或标题。

### 14.7 `agent/app/auth.py`

FastAPI app shell helper：

```text
get_current_actor(request)
require_permission(actor, key)
set_auth_cookie(response, token)
clear_auth_cookie(response)
```

WS helper：

```text
resolve_ws_actor(websocket)
```

### 14.8 `agent/app/runtime.py`

`WebRuntime` 增加 actor-aware 方法：

```text
list_sessions(actor)
load_session(actor, session_id)
run_chat_events(actor, session_id, message, ...)
rename_session(actor, session_id, title)
delete_session(actor, session_id)
cancel_session(actor, session_id)
get_model_state(actor, session_id)
set_model_preference(actor, session_id, model)
reset_model_preference(actor, session_id)
```

模型方法先校验 actor 对 session 的访问权限，再读写该 session 的 metadata。`run_chat_events()` 在每个 turn 开始前解析当前 session 的有效选择，并创建 turn-local provider：

```text
session_context = session_access.resolve(actor, session_id)
selection = session_model_preference_resolver.resolve(session_context, session_id)
turn_llm = llm_provider_resolver.bind(selection)
agent_loop.run_turn(..., actor=actor, llm_override=turn_llm)
```

Web / REST / SSE / WebSocket 路径禁止调用共享 `self.llm.set_preferred()`。CLI `/model` 也应改为读写当前 CLI session metadata；所有入口最终都按 session 解析模型。

内部 active turn key：

```text
(actor.user_id, session_id)
```

### 14.9 `agent/core/loop.py`

`AgentLoop.run_turn()` 增加可选 actor、policy 和 provider override：

```text
run_turn(session_id, user_text, *, turn_id=None, actor=None, llm_override=None, ...)
```

AgentLoop 仍不做用户业务判断。它只：

1. 使用 `llm_override` 或构造时注入的默认 `LLMProvider` 完成当前 turn，不解析 session 模型偏好。
2. 把 actor 放入 ToolExecutionContext。
3. 调用 ToolExecutionPolicy。
4. 按 decision 写 tool result / 等待确认 / 执行工具。
5. 通过 AuditSink 或 runtime callback 记录事件。
6. 对工具失败保留可诊断的安全摘要，例如 timeout、exit_code、stdout_tail、stderr_tail。

### 14.10 `agent/tools/shell_policy.py`

从“allowed / blocked”扩展为“risk classification”：

```text
CommandPolicyResult(
  allowed: bool,
  code: str,
  message: str,
  category: str,
  risk_level: str,
  risk_category: str,
  required_permission: str,
  requires_confirmation: bool,
)
```

保留当前 secret redaction。

### 14.11 `agent/config.py`

`AppConfig` 增加 auth DB 路径：

```text
state_dir = workspace / "state"
auth_db_path = state_dir / "auth.sqlite3"
```

如果暂不改 dataclass，也可以先由 `agent/auth/store.py` 根据 `config.workspace / "state"` 派生。

### 14.12 `agent/cli.py`

新增 auth 管理子命令：

```text
zcagent auth init-owner
zcagent auth users
zcagent auth reset-password <username>
```

第九部分实现 `init-owner`；Owner 初始化只创建认证记录，Owner 会话首次使用时复用全局 CLI session 目录。

现有聊天命令语法保持不变，但模型状态从进程级改为当前 session：

```text
/model <endpoint>[/<model>] -> 写当前 session metadata
/model reset                -> 清当前 session 模型偏好，恢复系统默认
/new                        -> 新建 session；不继承旧偏好，使用系统默认
```

---

## 15. 数据流

### 15.1 登录

```text
browser
  -> POST /api/auth/login username/password
  -> AuthStore.verify_password
  -> create auth_session token hash
  -> Set-Cookie zcagent_session
  -> audit auth.login_success
```

### 15.2 Session 切换模型

```text
POST /api/model/preference
  -> resolve actor
  -> authorize session_id
  -> validate endpoint/model against current config
  -> update sessions_meta/{session_id}.json
  -> trace model.switched with actor/session/new preference
```

该操作不能调用共享 provider 的 `set_preferred()`，因此不会改变其它 session 正在运行或后续发起的 turn。

```text
/model reset or DELETE /api/model/preference?session_id=...
  -> authorize session_id
  -> remove preferred_endpoint_name / preferred_model_name
  -> current session uses system default

/new or Web new_session
  -> create new session_id and empty session metadata
  -> model preference absent
  -> new session uses system default
```

### 15.3 普通聊天

```text
browser /ws message
  -> resolve actor from cookie
  -> authorize session_id
  -> resolve session model preference
  -> bind turn-local LLMProvider
  -> Owner resolves global workspace/contexts/sessions
  -> ordinary user resolves contexts/users/{actor.user_id}
  -> ensure session_index and authorized JSONL session
  -> WebRuntime.run_chat_events(actor, session_id, message, turn_id)
  -> AgentLoop.run_turn(actor=actor, session_id, turn_id, llm_override=turn_llm)
  -> tool policy checks each tool call
  -> JSONL append messages
  -> audit turn/tool events
```

### 15.4 Session 列表

```text
GET /api/sessions
  -> actor
  -> owner_user_id = actor.user_id
  -> load summaries from JsonlSessionStore
  -> filter/order
```

`session.manage.any` 不改变日常聊天列表，只保留给显式管理动作。管理 UI 使用独立 `/admin` 路由，不在聊天页弹窗中展示。

### 15.5 危险 `exec`

```text
LLM tool_call exec(command="pip install ...")
  -> shell policy classifies network_or_install
  -> requires tool.exec.dangerous
  -> if actor lacks permission: deny + audit
  -> create confirmation
  -> Web event tool_confirmation_required
  -> user approves
  -> execute command
  -> write tool result
  -> audit approval and result
```

---

## 16. 兼容和迁移

1. CLI 保留本地 no-login 管理/开发入口，继续使用全局 session 路径：`${ZHICE_AGENT_WORKSPACE}/contexts/sessions` 和 `${ZHICE_AGENT_WORKSPACE}/contexts/sessions_meta`；Owner 的 Web 会话也复用该路径，但仍通过 `session_index.owner_user_id` 标记归属。
2. 未启用 auth DB 前，当前 CLI / gateway 仍可按本地单用户开发形态运行；第九部分实现 Web 鉴权后，gateway 默认要求 auth。
3. 如果 gateway 尚无 Owner，普通注册保持关闭；配置 `ZHICE_AGENT_SETUP_TOKEN` 后可通过隐藏路径 `/_setup` 或 `zcagent auth init-owner` 初始化，Owner 随后决定是否开放注册。
4. 普通用户注册始终固定获得 `viewer`，不能通过请求字段获得更高权限。
5. `zcagent auth init-owner` 创建唯一永久 Owner，供 Web / 外部渠道登录使用；Owner 与 CLI 共享会话物理目录，Owner 列表会为未索引的全局 CLI 历史补充 Owner session index。
6. CLI 在 trace / audit 中可表示为本地操作者：

```text
actor_type = local_operator
user_id = null
username = local-operator
channel = cli
```

   如果 auth DB 已存在，CLI 高风险工具确认仍应写 audit，但不因此获得 `contexts/users/{user_id}`。
7. 已有 CLI 全局 JSONL session 不在初始化 Owner 时移动或复制；Owner 只补全局 session index，普通用户不接管这些历史。
8. 历史 `trace.log` 在升级时迁移为 `log-YYYY-MM-DD.jsonl`，不需要回填 actor；新 trace 事件继续尽量包含 actor/request/session/turn/channel 字段。
9. 现有 session JSONL message schema 不新增强制字段，避免破坏历史读取。
10. 聊天正文和 session 模型偏好都不迁入 SQLite；SQLite 只保存身份、权限、session 索引、turn/tool 运行记录、confirmation 和 audit。模型偏好保存在对应 `sessions_meta/{session_id}.json`。
11. 外部渠道历史不直接生成用户目录；必须先通过 `external_identities` 映射到内部 `user_id`。
12. 第九部分实现后，所有入口不再使用 gateway / CLI 进程级共享模型偏好。已有 session 没有模型 metadata 时直接使用系统默认，不把旧进程临时偏好迁移给任何 session。
13. `/model reset` 只清当前 session 模型字段；`/new` 创建无模型字段的新 session，因此自然恢复系统默认。

---

## 17. 变更文件

第九部分设计实际新增：

```text
docs_design/zhice-agent-part9-user-auth-permission-design.md
docs_design/2026-07-08-user-auth-permission-boundary-design.md
docs_design/2026-07-10-session-model-preference-scope-design.md
```

第九部分设计实际修改：

```text
README.md
docs_design/README.md
docs_design/zhice-agent-overall-design.md
docs_design/zhice-agent-part6-web-minimum-design.md
docs_design/zhice-agent-part6-web-ui-design.md
docs_design/zhice-agent-part8-gateway-agent-logging-design.md
docs_design/2026-07-06-next-stage-sequencing-design.md
docs_design/2026-06-15-model-command-and-endpoint-failover-design.md
```

第九部分实现实际新增或修改：

```text
agent/protocols/auth.py
agent/protocols/llm.py
agent/protocols/session.py
agent/protocols/tool.py
agent/auth/*
agent/llm/selection.py
agent/session/model_preferences.py
agent/app/auth.py
agent/app/runtime.py
agent/app/api/routes.py
agent/app/api/ws.py
agent/app/api/schemas.py
agent/app/gateway.py
agent/core/loop.py
agent/tools/shell_policy.py
agent/tools/exec.py
agent/tools/scoped.py
agent/tools/diagnostics.py
agent/config.py
agent/cli.py
web/static/index.html
web/static/styles.css
web/static/app.js
tests/unit_test/auth/*
tests/unit_test/app/*
tests/unit_test/agent_loop/*
tests/unit_test/cli/*
tests/unit_test/session/*
tests/unit_test/tools/*
```

实际实现继续保留 `ToolProvider.execute(name, args)` 的简单签名；actor、policy、confirmation 和 audit 在 AgentLoop dispatch 前后通过协议完成，不把用户业务塞进具体 Tool。

---

## 18. 测试方案

### 18.1 Auth store

| 用例 | 输入 | 期望 |
| --- | --- | --- |
| init schema | 空 workspace | 创建 users/roles/permissions/auth_sessions/audit 表 |
| init first admin role user | 无用户，CLI 或 Web bootstrap | 创建第一位用户并授予 `admin` 角色、默认角色和权限 |
| duplicate init-owner | 已有 Owner | 默认拒绝重复初始化 |
| duplicate web bootstrap | 已有 Owner | 409 `AUTH_ALREADY_INITIALIZED`，不创建第二个 Owner |
| invalid setup credential | Secret 缺失或错误 | 503/401，不能创建 Owner |
| public register | Owner 已开启策略且字段合法 | 创建固定 `viewer` 用户、设置 cookie 并自动登录 |
| public register disabled | 默认关闭或 Owner 已关闭 | `403 AUTH_REGISTRATION_DISABLED`，不创建用户 |
| register before setup | 无用户 | 503 `AUTH_SETUP_REQUIRED` |
| duplicate username | 用户名已存在 | 409 `USER_USERNAME_ALREADY_EXISTS` |
| password verify | 正确密码 | 登录成功 |
| user missing | 不存在的用户名 | 401 AUTH_INVALID_CREDENTIALS；audit 为 AUTH_USER_NOT_FOUND |
| password fail | 错误密码 | 401 AUTH_INVALID_CREDENTIALS；audit 为 AUTH_INVALID_PASSWORD |
| disabled user | disabled | 403 AUTH_ACCOUNT_DISABLED |
| logout | valid token | auth session revoked |
| expired token | expires_at 过去 | actor 解析失败 |
| external identity | QQ/微信外部 id | 解析到内部 user_id |

### 18.2 Permission

| 用例 | actor | action | 期望 |
| --- | --- | --- | --- |
| viewer | 查看/修改自己的账号 | allow，额外权限为空 |
| viewer | 自己的 Session 增删读写 | allow，按 ownership |
| viewer | 聊天、模型切换、安全工具、Memory | allow，属于基础能力 |
| viewer | 访问其他用户 Session | deny/404 |
| admin | `session.manage.any` 跨用户访问 | allow |
| viewer/developer | `tool.exec.dangerous` | deny |
| owner 或被授予特权的用户 | dangerous exec | 进入 confirmation |
| viewer/developer | `skill.sync` / audit / user manage | deny |
| auditor | `audit.read`、`turn.read.any` | allow |
| schema reinitialize | 数据库含旧基础权限 | 清理 permission、role permission、user permission 残留 |

### 18.3 User context / session index

| 用例 | 场景 | 期望 |
| --- | --- | --- |
| create user context | 普通新用户首次 chat | 创建 `contexts/users/{user_id}` |
| default cwd | 普通用户 exec 默认 cwd | 指向 `contexts/users/{user_id}/files` |
| protect sessions | 工具写 sessions | 默认拒绝 |
| shared readonly | 普通用户读 shared | 允许只读 |
| create index | 新 session 首次 chat | 写入 session_index |
| list own | 普通用户 | 只返回自己的 session |
| read other | 普通用户读别人 session | 404 或 403，按统一口径 |
| rename own | owner rename | 成功 |
| delete other | 无 `session.manage.any` 删除别人 session | 拒绝 |
| owner daily list | Owner 有 `session.manage.any` | 日常列表仍只返回 Owner 自己的 session |
| explicit manage | 有 `session.manage.any` 且执行显式管理动作 | 可按既有授权解析目标 session |
| owner storage | Owner Web chat | 写入 CLI `contexts/sessions*` 并保留 owner index |
| owner CLI index | Owner 列出未索引全局 CLI JSONL | 同路径只补 session_index，不复制、不移动 |
| channel session | QQ/微信 session | 通过 external identity 写入内部用户目录 |

### 18.4 Session model preference

| 用例 | 场景 | 期望 |
| --- | --- | --- |
| default selection | session metadata 没有模型字段 | 使用系统默认 endpoint/model |
| switch session | 登录用户切换自己的 session A | 只写 session A metadata |
| isolate sessions | 同一用户 session A/B | A 切换不影响 B |
| isolate users | 不同用户使用相同 session 名称 | 按用户目录隔离 metadata |
| external session | QQ/微信外部会话映射到内部 session | 使用该 session 偏好 |
| model reset | 当前 session 执行 `/model reset` | 清偏好字段并恢复系统默认 |
| new session | 当前 session 有偏好后执行 `/new` | 新 session 使用系统默认，不继承旧偏好 |
| reopen session | 切回已有 session | 恢复该 session 保存的偏好 |
| cross-user switch | 用户尝试修改其他人的 session | 404/deny，metadata 不变 |
| stale preference | endpoint/model 已删除或禁用 | 本次回退默认并记录原因，不改写 metadata |
| turn-local provider | 两个 session 并发聊天 | 各自使用独立选择，无共享状态串扰 |
| failover | 首选模型失败后回退 | session 偏好不变，记录 actual endpoint/model |
| owner CLI session | Owner 复用 CLI 全局 session | 读取同路径 `sessions_meta` 模型 metadata |

### 18.5 Web API / WS

| 用例 | 场景 | 期望 |
| --- | --- | --- |
| unauth API | GET /api/sessions | 401 AUTH_REQUIRED |
| unauth WS | connect /ws | error + close 1008 |
| login cookie | POST /api/auth/login | 设置 HttpOnly cookie |
| me | GET /api/auth/me | 返回当前用户和权限摘要 |
| viewer model switch | viewer 切换自己 session 模型 | 成功，不影响其它 session |
| session model state | GET /api/models?session_id=... | 返回当前 session 有效模型 |
| set session model | POST /api/model/preference | 更新请求 session 偏好，不修改 gateway 全局 provider |
| reset session model | DELETE /api/model/preference?session_id=... | 只清请求 session 偏好并恢复系统默认 |
| stop own | owner stop active turn | 成功 |
| stop other | 非 owner stop | 拒绝或不可见 |

### 18.6 AgentLoop / Tool policy

| 用例 | 场景 | 期望 |
| --- | --- | --- |
| readonly tool | actor 有 readonly | 执行并写 audit allow/done |
| readonly deny | actor 无 readonly | tool error + audit deny |
| safe exec | actor 有 exec.safe | 执行 |
| network exec | actor 有 `tool.exec.dangerous` 且 `risk_category=network` | 进入 confirmation |
| destructive no permission | actor 无 `tool.exec.dangerous` 且 `risk_category=destructive` | deny |
| confirmation approve | Web approve | 执行，audit approved + done |
| confirmation deny | Web deny | 不执行，tool error |
| confirmation timeout | 超时 | 不执行，audit expired |
| cancellation | 等待确认时 stop | confirmation cancelled |
| provider error | LLM 错误 | Runtime Activity 标记 turn error，trace 不泄露 secret |
| llm override | runtime 传入 turn-local provider | 当前 turn 使用本 session override，不继承其它 session 状态 |
| diagnostic tails | exec 失败 | 保存 stdout_tail/stderr_tail 等安全摘要 |

### 18.7 Audit

| 用例 | 输入 | 期望 |
| --- | --- | --- |
| login success | 正确登录 | audit auth.login_success |
| login failure | 错误密码 | audit auth.login_failed |
| activity secret | 普通 tool 参数含 token | activity record preview 脱敏，不写 audit |
| dangerous tool | 危险 exec 请求和结果 | audit 含 actor、risk、confirmation 和安全 preview |
| safe tool | 普通只读工具成功 | 只写 activity/trace，不写 audit |
| session delete | 用户删除 Session | audit 含 actor/session |
| model switch/reset | 用户切换或重置模型 | 写 trace 和业务状态，不写 audit |
| model fallback | session 偏好失效或调用 failover | trace 区分 preferred 与 actual 模型 |

### 18.8 Diagnostics

| 用例 | 场景 | 期望 |
| --- | --- | --- |
| previous turn | 用户问“刚刚为什么慢” | 自动选择当前 Session 上一条已完成 Turn |
| latest failure | 用户问“刚才为什么报错” | 上一轮无错误时自动选择当前 Session 最近失败 |
| exclude current | Tool 在诊断 Turn 中运行 | 不把正在执行的诊断 Turn 当成目标 |
| owner recent own | Owner 普通聊天调用 Tool | 仍只返回 Owner 当前 Session 摘要 |
| timeout cause | exec 超时且有输出尾部 | 返回 `COMMAND_TIMEOUT`、证据和下一步 |
| unknown cause | 缺少完整 duration/trace | 返回 `insufficient_evidence`，不猜测 |
| no raw trace | 普通用户诊断 | 不返回 cookie、header、完整 prompt、完整 tool output |

### 18.9 验证命令

实现阶段至少运行：

```bash
python -m ruff check .
python -m pytest --basetemp .tmp/pytest_basetemp
```

文档阶段至少运行：

```bash
git diff --check
```

---

## 19. 实现顺序

第九部分第一版已按以下依赖顺序完成开发：

1. 新增 auth protocols、SQLite schema、AuthStore、password/token helper、默认权限数据和 `state/auth.sqlite3` 路径。
2. 增加 UserContextResolver，建立 `contexts/users/{user_id}`、`files/`、`sessions/`、`sessions_meta/` 和 `contexts/shared/readonly` 的 path policy。
3. 增加 Secret 保护的 Web bootstrap 和 `zcagent auth init-owner`，初始化唯一 Owner。
4. 增加登录、登出、me API、cookie 解析和 external identity 映射基础结构。
5. 给现有 HTTP routes / WebSocket 加 actor 解析、request_id 和 401 / 403。
6. 增加 session_index service，先保护 list/read/rename/delete，并记录 channel metadata。
7. 增加 SessionModelPreferenceStore / Resolver，扩展 `sessions_meta`，把模型 API 和 `/model` 命令改为 session-aware，并为每个 turn 绑定当前 session 的 LLM provider。
8. 实现 Owner 全局 CLI session 索引对账；默认只补 `session_index`，不复制、不移动 JSONL 和 session metadata。
9. 让 WebRuntime 和 active turn 使用 actor-aware key。
10. 增加 audit sink，记录 auth/model/session/turn/request/tool 基础事件。
11. 增加 ToolExecutionPolicy，并在 AgentLoop tool dispatch 前后接入。
12. 扩展 shell_policy 风险分类，覆盖 safe exec、network/install、destructive/env dump；safe exec 是登录用户基础能力，`tool.exec.dangerous` 只用于高风险确认资格。
13. 增加 confirmation broker 和 Web confirmation API/UI。
14. 增加用户可见诊断工具和诊断报告格式。
15. 补齐 Owner bootstrap、普通用户自助注册、个人显示名/密码设置、用户管理、管理员委派、角色权限、审计/诊断基础页面。
16. 更新 README、总体设计、Part 9 活文档状态和测试说明。

实施过程先稳定 auth store、session 模型隔离、权限判断、API/WS 鉴权和 tool policy 测试，再接入静态 UI；没有引入前端工程化重构。

---

## 20. 验收标准

第九部分设计完成时，应满足：

1. 有第九部分活文档，明确用户、登录、权限、用户上下文目录、session index、tool call 和 audit log 关系。
2. 有日期设计记录，说明本次方案承接第七/第八部分、路线排序和 session 级模型偏好修正。
3. 总体设计、README 和设计索引不再把“用户权限设计”描述为未完成下一步，而是指向本文档。
4. 第九部分实现范围清楚，不把 OAuth、组织架构、多租户、生产部署和前端工程化混进第一版。

第九部分第一版当前验收结果：

1. 没有 auth session 时，Web API / WebSocket 默认拒绝访问。
2. 隐藏路径 `/_setup` 的 Secret 保护表单或 `zcagent auth init-owner` 可以初始化唯一 Owner；初始化后该页面返回 404，Web bootstrap 永久关闭。
3. 登录成功后浏览器能使用 Web chat；logout 后不能继续访问受保护 API。
4. 当前用户可以修改显示名和密码；改密成功后撤销同一用户的全部 auth session、清除当前 Cookie，并立即要求重新登录。
5. 新用户有独立 `contexts/users/{user_id}`，默认工具 cwd 为 `files/`，普通工具不能写 `sessions/` 和 `sessions_meta/`。
6. session list/read/rename/delete 按用户目录、session_index 或 `session.manage.any` 控制。
7. `contexts/shared/readonly` 对普通用户只读，不能包含其它用户敏感信息。
8. WebSocket stop 只能停止当前 actor 可操作的 active turn。
9. 模型查看/切换、只读工具、安全 `exec`、已安装 Skill 和本人 Memory 作为登录用户基础能力；`skill.sync` 与危险执行继续按特权 key 判断。
10. 模型偏好保存在当前 session 的 `sessions_meta/{session_id}.json`，不增加用户默认模型或用户偏好表。
11. 同一用户的不同 session 可以使用不同模型；一个 session 切换不会改变另一个 session。
12. Web / 外部渠道每个 turn 使用当前 session 对应的 call-scoped 模型选择，不能调用共享 provider 的进程级 `set_preferred()`。
13. `/model reset` 只清当前 session 偏好并恢复系统默认。
14. `/new` 创建无模型偏好的新 session，不继承旧 session 选择。
15. CLI `/model` 也按当前 CLI session 读写 `contexts/sessions_meta/{session_id}.json`。
16. read-only tools、safe exec 和已安装 Skill 有基础能力测试；高风险 exec 与 Skill 同步有特权测试。
17. 高风险 `exec` 不会只因为 admin 身份自动执行，必须经过确认。
18. `tool.exec.dangerous` 只表示进入危险确认流程，具体风险类型写入 `risk_category`。
19. env dump 和不可分类危险 shell 第一版继续拒绝。
20. trace/audit/request/tool 事件能按 actor_user_id、request_id、session_id、turn_id 和 tool_call 关联，并区分 preferred/actual 模型。
21. 用户可见诊断工具能按当前用户和时间范围返回具体失败原因、证据、置信度和下一步建议。
22. audit 不写明文 token、密码、API key、完整 prompt 或完整 tool output。
23. AgentLoop 不 import FastAPI 或 SQLite 具体实现，也不查询 session 模型 metadata。
24. `JsonlSessionStore` 仍负责 JSONL 消息，不硬编码用户业务；模型偏好由独立 session metadata service 管理。
25. Web / 外部渠道身份通过内部 `user_id` 统一权限和用户目录，不按渠道拆权限边界。
26. CLI 本地操作者继续使用全局 `contexts/sessions` 和 `contexts/sessions_meta`，不被自动迁入 DB 用户目录。
27. 现有 CLI、Web、AgentLoop、ToolRegistry、日志相关测试继续通过。
28. 普通自助注册默认关闭并由 Owner 独占控制；关闭时前端隐藏且后端返回 `403 AUTH_REGISTRATION_DISABLED`。开放后匿名用户可以自定义用户名和密码注册；服务端令 `display_name=username`、固定授予 `viewer`，注册请求不能覆盖显示名或提升角色权限。
29. `viewer` 作为普通用户即使 `permission_keys` 为空，也默认拥有自己的 session 增删读写、聊天、模型切换、只读工具、低风险 exec 和本人 Memory；管理、审计、跨用户和危险执行特权仍被隔离。
30. schema 初始化会清理已废弃的基础 permission key 及其角色/用户关联，但不删除用户、角色或用户角色绑定。

---

## 21. 和其它文档的关系

- `docs_design/zhice-agent-part8-gateway-agent-logging-design.md` 已经提供 `session_id` / `turn_id` 运行日志。第九部分复用这些关联字段，不重新设计日志格式。
- `docs_design/2026-07-06-next-stage-sequencing-design.md` 说明了 turn、日志、用户权限的依赖顺序。本文是该排序中 Milestone 9 的详细设计。
- `docs_design/2026-07-08-user-auth-permission-boundary-design.md` 是本次设计记录，保留当次决策背景；本文是后续实现应优先阅读的活文档。
- `docs_design/2026-07-10-session-model-preference-scope-design.md` 是当前模型偏好范围：所有入口按 session 持久化，不增加用户默认层。
- `docs_design/2026-07-16-authenticated-user-baseline-capabilities-design.md` 将普通功能从 RBAC 中移除：登录用户的本人资源和安全工具属于基础能力，权限只表达少数特权。
- `docs_design/zhice-agent-part10-memory-design.md` 在本文的 workspace operator、普通用户目录、ownership 和 audit 边界上增加长期 Memory：CLI 与 Owner 共用全局 Memory，普通用户使用私有 Memory；当前不提供跨用户 Memory 管理接口。
- 第九部分实现阶段以本文为依据。实现落地后，如果字段、权限 key 或确认流程发生变化，应先新增日期记录，再更新本文当前口径。
