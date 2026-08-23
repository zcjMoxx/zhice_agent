# 智策 Agent 拖拽工作流、定时调度与用户连接设计

> 说明：该方案已进入当前代码基线；实现事实与运行边界以 `zhice-agent-part20-visual-workflow-scheduler-design.md` 为准，2026-08-19 的成熟编辑器交互移植见 `2026-08-19-visual-workflow-editor-interaction-design.md`，本文正文保留当时方案与验收设计。

> 日期：2026-08-10
>
> 状态：方案已确认，尚未实现
>
> 归属：Milestone 20，智策 Agent 第二项特色应用
>
> 前置能力：Part 9 用户与权限、Part 11 MCP、Part 12 RuntimeEvent/Hook、Part 16 Vue Web、Part 17 单进程可靠性
>
> 调研说明：本文的社区活跃度、版本和服务行为是 2026-08-10 的公开网页快照；实施前必须重新核对上游版本、许可证、OAuth政策和邮件Provider要求。

## 1. 背景

智策 Agent 已有聊天、Tool、MCP、LLMProvider、用户权限、Activity/Audit 和 Vue Web，但没有用户可持久化、可定时触发、可观察运行历史的后台自动化能力。当前 Subagent 只负责同一父 Turn 内的有界并行，不是跨 Turn 后台 Job，也不应被改造成定时器。

第二项特色应用确定为轻量拖拽工作流：所有正常登录用户都能创建、立即运行和定时运行本人的工作流，通过可视化节点组合 MCP 查询、受限 LLM 转换、条件判断、官方通知和个人账号动作。

典型演示：

```text
每天 08:00
  -> 查询重庆天气
  -> AI 生成大学生穿衣与出行建议
  -> 降雨概率 >= 60% ?
       yes -> 增加带伞提醒
       no  -> 普通天气摘要
  -> 智策 Agent 官方邮箱通知本人
```

个人授权演示：

```text
每周一 09:00
  -> 查询一周天气
  -> AI 生成摘要
  -> 使用当前用户已授权的 Outlook 邮箱发送给指定收件人
```

## 2. 在线调研结论

### 2.1 画布候选

| 候选 | 2026-08-10 实况 | 特点 | 结论 |
| --- | --- | --- | --- |
| `bcakmakoglu/vue-flow` | 6775 stars，MIT，2026-07-14 仍有提交；`@vue-flow/core` 最新 `1.48.2` | 原生 Vue 3/TypeScript，自带节点、边、拖拽、缩放、MiniMap、Controls、状态 composable | 采用；与现有 Vue 3/Vite/Pinia 栈最贴合 |
| `didi/LogicFlow` | 11632 stars，Apache-2.0，2026-07-30 仍有提交 | 业务流程图能力丰富、中文社区较好、框架无关 | 不采用；当前Vue产品栈下会增加适配层且没有抵消成本的必要能力 |

Vue Flow 官方指南确认支持自定义节点和边、元素拖拽、缩放和平移、MiniMap、Controls、NodeToolbar、NodeResizer 和图状态辅助函数，能够覆盖完整编辑器需求。

### 2.2 调度与工作流引擎候选

| 候选 | 实况 | 结论 |
| --- | --- | --- |
| APScheduler | GitHub 7599 stars，MIT；PyPI 稳定版 `3.11.3`，进程内 Cron-like scheduler | 采用，只负责何时触发 |
| n8n | 约 20 万 stars，fair-code，400+ 集成 | 不采用；嵌入后展示的是第三方平台而非智策 Agent 自身设计，并引入独立Node运行面和许可证边界 |
| Prefect | 23594 stars，Apache-2.0 | 不采用；更适合韧性数据管道，需要额外服务与概念 |
| Celery | 28779 stars，分布式任务队列 | 不采用；需要 Broker，仍不解决本项目的画布和领域节点协议 |
| Temporal | 22208 stars，MIT | 不采用；服务端和持久执行语义明显超过个人演示项目需求 |

APScheduler 官方文档给出以下关键事实：

- `BackgroundScheduler` 适合在应用内后台运行；
- 默认 MemoryJobStore 适用于应用启动时自行重建任务；
- 一个应用通常只运行一个 scheduler；
- JobStore 不能在多个 scheduler 之间共享；
- `max_instances`、`misfire_grace_time` 和 `coalesce` 可控制重入和错过执行；
- 持久 JobStore 会序列化 callable，并带来重复注册和迁移复杂度。

因此本项目不使用 APScheduler 保存业务真值：SQLite 保存工作流、版本、计划时间和运行历史；APScheduler 使用 MemoryJobStore，Gateway 启动时从 SQLite 重建 schedule。

### 2.3 OAuth、凭据和邮件候选

| 候选 | 2026-08-10 实况 | 结论 |
| --- | --- | --- |
| Authlib | GitHub 5396 stars，BSD-3-Clause；PyPI `1.7.2` | 采用，提供中性的 OAuth/OIDC client 能力 |
| `cryptography` | PyPI `50.0.0`，Apache-2.0 OR BSD-3-Clause | 采用 AES-GCM 加密用户 refresh token |
| Microsoft Graph `sendMail` | delegated `Mail.Send` 支持个人和组织账号；`POST /me/sendMail` 返回 `202 Accepted` | 个人邮箱OAuth Provider之一 |
| Gmail API | `messages.send` / `drafts.send`，MIME + base64url，OAuth scope | 个人邮箱OAuth Provider之一 |
| `aiosmtplib` | PyPI `5.1.2`，MIT，asyncio SMTP client | 不采用；Workflow worker是同步线程，官方邮箱和个人SMTP连接使用标准库 `smtplib` |

Microsoft 官方文档要求 OAuth authorization code flow 使用 redirect URI、state，并推荐 PKCE；需要长期后台发送时请求 `offline_access`，刷新后必须保存新 refresh token。Graph `202 Accepted` 只表示请求被接受，不证明最终投递成功，工作流结果必须显示 `accepted` 而不是 `delivered`。

## 3. 目标

1. 所有正常登录用户都能创建、编辑、立即运行、定时运行和查看本人的工作流。
2. 通过 RBAC 区分基础使用、查询、对外发送、个人连接、社交发布和全局管理。
3. 使用 Vue Flow 提供可拖拽、可连线、可配置、可实时显示状态的工作流画布。
4. 使用 APScheduler 处理 date / interval / cron 触发，业务真值保存在 SQLite。
5. 工作流执行由独立 `WorkflowRuntime` 和有界 DAG Executor 完成，不进入 AgentLoop。
6. MCP Tool 节点继续经过 actor、schema、RBAC、Hook、Activity 和 Audit 执行边界。
7. LLM 节点只做上游数据转换，不自主调用 Tool，不创建聊天 Session。
8. 官方邮箱只通知当前用户已验证地址；个人邮箱通过Microsoft OAuth、Gmail OAuth或通用SMTP授权码向外发送。
9. 凭据按用户隔离、加密落库，不进入工作流 JSON、Session、日志或前端。
10. 支持实时节点状态、运行历史、Dry Run、暂停和错误定位。

## 4. 非目标

- 不实现 Airflow、n8n、Prefect 或 Temporal 的完整替代品。
- 不支持任意 Python、JavaScript、Shell 或 `exec` 节点。
- 不支持循环、递归子工作流、动态图生成、长时间人工审批或无限等待。
- 不提供能自主选择 Tool 的完整 Agent 节点；本应用只接受发布时确定的DAG和Tool。
- 不使用聊天 Session 作为工作流定义或运行历史真值。
- 不让 APScheduler JobStore 成为工作流真值。
- 不支持多个 Gateway/多个 scheduler 共同执行同一 workspace。
- 不允许管理员查看用户 OAuth token 或擅自使用用户外部账号。
- 不默认自动重试邮件、发帖等可能已经产生副作用的动作。
- 不实现具体社交平台的私有账号Cookie托管；社交发布统一通过Owner审核并允许的写操作MCP Tool。
- 不提供公共 webhook trigger、事件总线或分布式队列。

## 5. 核心决策

### 5.1 工作流是独立 Runtime，不侵入 AgentLoop

```text
AgentLoop
  -> 处理用户 Turn、LLM Tool 循环、Session

WorkflowRuntime
  -> 加载定义、调度、执行节点、保存运行历史
```

两者只复用稳定 Provider：

```text
Workflow Tool Node -> actor-scoped ToolProvider
Workflow LLM Node  -> LLMProvider
Workflow Events    -> RuntimeEvent-compatible presentation
```

AgentLoop 不识别 cron、DAG、工作流状态或 OAuth connection。

### 5.2 所有用户可用，按动作分级授权

新增权限：

```text
workflow.use
workflow.schedule
workflow.notify.self
workflow.email.send
workflow.external.action
workflow.social.publish
workflow.manage.any
workflow.settings.manage
```

固定默认授权：

| 角色/用户 | 默认能力 |
| --- | --- |
| 正常 `viewer/developer/admin/owner` | `workflow.use`、`workflow.schedule`、`workflow.notify.self` |
| 获得 `workflow.email.send` 的用户 | 绑定本人邮箱并向外发送 |
| 获得 `workflow.external.action` 的用户 | 使用Owner审核进入写操作allowlist的MCP Action；社交发布还必须同时具有 `workflow.social.publish` |
| Admin/Owner | `workflow.manage.any`，可暂停异常工作流和查看脱敏运行元数据 |
| Owner | `workflow.settings.manage`，配置官方邮箱、OAuth应用和全局额度 |
| Auditor | 继续保持只读审计边界，不默认创建或执行工作流 |

当前 RBAC 支持角色权限和用户单独授予。敏感动作在编辑器 Catalog、发布工作流和每次实际运行时都重新检查；创建时权限不形成永久授权快照。

### 5.3 工作流始终属于创建用户

每条定义保存：

```text
owner_user_id
created_by_user_id
required_permissions[]
connection_ids[]
```

定时触发时不依赖已过期的浏览器 auth session，而是：

```text
workflow.owner_user_id
  -> 读取当前用户状态和当前权限
  -> 构造 ActorContext(channel="workflow")
  -> 校验定义、Tool和Connection
  -> 执行
```

用户被禁用、权限被撤销或连接失效后，工作流停止执行并转为 `paused_attention`，不能使用旧权限快照继续运行。

### 5.4 官方邮箱与个人邮箱是两条凭据链

官方通知：

```text
ZhiCe Agent official account
  -> 当前 workflow owner 已验证的 notification email
```

- 用户不能填写任意收件人；
- 适合天气提醒、计划完成、失败告警和系统摘要；
- SMTP/API credential 由 Owner 通过环境变量配置；
- 普通用户只能看到发送状态和脱敏地址。

个人邮箱：

```text
current user OAuth connection
  -> Microsoft Graph /me/sendMail
  -> user-selected recipients
```

- Token 属于用户，不属于 workspace 共享 MCP；
- 第一个 Provider 使用 Microsoft Graph delegated `Mail.Send`；
- 工作流只保存 `connection_id`；
- 后台运行时确认 `connection.owner_user_id == workflow.owner_user_id`。

### 5.5 用户级外部连接不塞进当前 MCP Runtime

当前 MCP credential、Catalog 和连接是 workspace 共享的，`McpCredentialManager` 以 `server_id` 管理 Token，而不是 `(user_id, server_id)`。这适合高德、Tavily等服务账号，不适合个人邮箱。

本方案新增中性的 `ExternalConnectionProvider` / `EmailProvider`，并将其确定为用户级凭据能力的唯一入口。当前workspace共享MCP继续承载系统服务账号；个人凭据不进入MCP配置，也不设计第二条隐式授权链。

### 5.6 APScheduler 只保存触发器，不保存业务定义

采用：

```text
APScheduler 3.11.x
BackgroundScheduler
MemoryJobStore
ThreadPoolExecutor
job_id = workflow:{workflow_id}
replace_existing = true
coalesce = true
max_instances = 1
```

Gateway 启动时从 `WorkflowStore` 加载 active 定义并注册。SQLite 保存 `last_scheduled_at` 和 `next_run_at`；重启时若发现逾期且仍在 `misfire_grace_seconds` 内，只补执行一次，否则记录 skipped misfire。

继续服从 Part 17 的 `gateway workers = 1` 和单 workspace 单进程边界。配置检测到同一workspace存在第二个scheduler时直接拒绝启动，本应用不支持多副本。

## 6. 总体架构

```text
Vue Workflow Page
  -> REST CRUD / publish / run-now
  -> run event stream
          |
          v
    WorkflowService
      +-- WorkflowStore(SQLite truth)
      +-- WorkflowAuthorizationPolicy
      +-- WorkflowCatalog
      +-- WorkflowScheduler(APScheduler MemoryJobStore)
      +-- WorkflowExecutor(stable topological order)
             +-- MCP Query/Action Node -> UserScopedToolProvider
             +-- LLM Transform -> LLMProvider
             +-- Template / Condition
             +-- Official Notification -> OfficialEmailProvider
             +-- Personal Email -> UserConnectionStore -> GraphEmailProvider
          |
          v
 workflow.* events / Activity / Audit / node run records
```

依赖方向：

```text
web/app -> workflow runtime -> protocols/providers
workflow node handlers -> protocols and business services
email providers -> external SDK/HTTP only
AgentLoop <- no workflow imports
```

## 7. 工作流定义协议

### 7.1 `WorkflowDefinitionV1`

```text
schema_version
workflow_id
owner_user_id
name
description
version
status
timezone
nodes[]
edges[]
required_permissions[]
connection_ids[]
created_at
updated_at
published_at
```

状态：

```text
draft
active
paused
paused_attention
archived
```

发布生成不可变版本；继续编辑会创建新 draft，不能原地改变正在执行的版本。

### 7.2 Node

```text
id
type
position{x,y}
title
config
input_bindings
timeout_seconds
retry_policy
```

交付节点：

```text
schedule_trigger
mcp_query
mcp_action
llm_transform
template
condition
official_notification
personal_email
```

“立即运行”是定义级动作，不需要额外 manual node。

### 7.3 Edge

```text
id
source_node_id
source_port
target_node_id
target_port
condition_branch
```

定义约束：

- 恰好一个 `schedule_trigger`；
- 最多 30 个节点、60 条边；
- 必须是 DAG；
- condition 只能有 `true/false` 两个分支；
- 不允许孤立动作节点；
- 不允许连接不存在的 output port；
- 发布前所有必填参数必须解析。

### 7.4 数据绑定

仅支持受限引用：

```text
${nodes.weather.output.daily[0].precipitation_probability}
${nodes.summary.output.text}
```

前端通过上游字段选择器生成引用。后端使用自己的 parser 解析节点ID、output和字段路径，不使用 `eval`、JavaScript、Jinja任意表达式或 Python format 执行。

模板节点只支持文本替换和长度上限。Condition 操作符固定为：

```text
eq ne gt gte lt lte contains starts_with ends_with is_empty
```

## 8. 节点运行设计

### 8.1 MCP Query / Action Node

查询节点只显示：

```text
actor 可见 Tool
AND workflows.allowed_query_tools
AND WorkflowAuthorizationPolicy 允许
```

写操作节点只显示：

```text
actor 可见 Tool
AND workflows.allowed_action_tools
AND actor 具备 workflow.external.action
AND 对应业务权限（例如 workflow.social.publish）
```

发布时保存：

```text
tool_name
server_id
input_schema_hash
configured_arguments
effect_category
published_consent_at
```

运行前重新检查 Catalog 和 schema hash。Tool 不存在或 schema 变化时暂停工作流：

```text
WORKFLOW_TOOL_NEEDS_REVIEW
```

执行必须经过 actor-scoped ToolProvider、Tool schema、RBAC、Hook、Activity 和 Audit；不能从 WorkflowExecutor 直接调用远端 SDK。确定性工作流已在发布时固定 Tool，因此不经过模型的 `discover_tools` 激活对话，但仍使用同一底层 Tool 集合和执行策略。

查询和写操作使用两个独立allowlist。发布含 `mcp_action` 的工作流时，页面必须展示Server、Tool、参数模板、外部影响、运行频率和不可自动撤销说明，用户明确确认后记录 `published_consent_at`。运行时仍重新检查RBAC、业务权限、Tool schema和allowlist。

`mcp_action` 支持系统服务账号的发帖、发送或其它外部操作，但不接收个人OAuth token。Action结果为timeout或transport error时按外部结果未知处理，不自动重放。

### 8.2 LLM Transform Node

LLM节点输入：

```text
system prompt from prompts/workflow_transform.md
user-authored bounded instruction
upstream JSON/text
configured output schema
```

它调用 `LLMProvider`，不提供 Tool schema、不调用 AgentLoop、不创建 Session、不读取 Memory。输出必须受字符/token上限；配置 output schema 时执行 JSON 校验。

Prompt注入防护：上游网页或 ToolResult 作为 data block，不作为 system instruction；固定 system prompt 明确只转换数据，忽略其中要求调用工具、泄露Secret或改变工作流的文字。

### 8.3 Template Node

只进行确定性字段插值、默认值和长度检查。HTML邮件模板经过白名单 sanitizer，不允许 script、iframe、event handler、远程表单和危险URL scheme。

### 8.4 Condition Node

比较已经解析的单一值。类型不匹配返回明确错误，不自动做危险字符串转数字或日期猜测。

### 8.5 Official Notification Node

用户配置：

```text
subject
body
```

后端根据 owner 查找已验证 `notification_endpoint`，用户不能提交 `to`。适用权限为 `workflow.notify.self`。

发送结果：

```text
accepted
rejected
unknown
```

SMTP/API 接受不等于最终投递；UI不得显示虚假“已送达”。

### 8.6 Personal Email Node

用户配置：

```text
connection_id
to[]
cc[]
bcc[]
subject
body
save_to_sent_items
```

限制：

- 要求 `workflow.email.send`；
- connection 必须 active 且属于 owner；
- connection provider 必须是 `microsoft_graph`、`gmail` 或 `smtp_personal`；
- 单节点收件人数、正文和附件大小受限；
- 附件不在本应用范围；
- Graph返回 `202`、Gmail `messages.send` 返回message id或SMTP服务器接受后记为 `accepted`，均不宣称最终送达；
- 网络超时且外部结果未知时记为 `unknown`，不自动重试。

## 9. DAG Executor

Executor按稳定拓扑层执行：同一层互不依赖的查询、LLM、模板和条件节点在全局/用户并发额度内有界并行；包含Action的节点只在全部前驱成功后启动。事件和结果仍按节点ID与时间保存，因此并行不影响可解释性和复现。

运行状态：

```text
queued
running
succeeded
partial
failed
cancelled
skipped
unknown_effect
```

执行流程：

```text
load immutable version
  -> reload owner and current permissions
  -> resolve required connections
  -> validate DAG and schema hashes
  -> create WorkflowRun
  -> execute nodes in stable topological order
  -> persist every node transition
  -> emit workflow.* events
  -> finalize run and calculate next_run_at
```

失败规则：

- query节点可按明确 retryable 错误有限重试；
- LLM重试服从现有 Provider deadline/failover；
- Action节点默认 `max_attempts=1`；
- Action返回 `unknown` 后整条Run标记 `unknown_effect`，下游动作停止；
- condition 未选中的分支记录 `skipped`；
- 用户取消只停止尚未开始的节点，不能宣称已经提交的外部动作被撤回。

## 10. 调度设计

### 10.1 Trigger

支持：

```text
date
interval
cron
```

UI优先提供“每天、每周、固定间隔”的表单，Cron表达式放在高级模式。所有定义显式保存 IANA timezone，默认 `Asia/Shanghai`，不依赖服务器本地时区。

### 10.2 重启恢复

SQLite保存：

```text
last_scheduled_at
last_started_at
last_finished_at
next_run_at
misfire_grace_seconds
coalesce
```

启动流程：

1. 获取单实例 scheduler lock；
2. 加载 active workflow latest published version；
3. 检查 owner、权限和配置；
4. 对逾期 schedule 应用 misfire policy；
5. 使用固定 `workflow:{id}` 注册 APScheduler Job；
6. 发布 scheduler ready health。

停止时拒绝新Run、取消未开始任务、有界等待正在执行节点并关闭 scheduler。

### 10.3 并发与额度

- 同一工作流 `max_instances=1`；
- 同一用户默认最多一个 active Run；
- 全局工作流 worker 数默认 4；
- 每用户工作流数量、active schedule数量、每日运行数和每日邮件数可配置；
- 队列满时拒绝新Run并记录 `WORKFLOW_QUEUE_FULL`，不能静默丢失。

## 11. 持久化

新增 `${ZHICE_AGENT_WORKSPACE}/state/workflows.sqlite3`：

```text
workflow_definitions
workflow_versions
workflow_schedules
workflow_runs
workflow_node_runs
workflow_events
outbound_deliveries
```

主要字段：

```text
workflow_definitions
  id, owner_user_id, name, status, latest_draft_version,
  active_version, created_at, updated_at

workflow_versions
  workflow_id, version, definition_json, schema_version,
  required_permissions_json, tool_schema_hashes_json, published_at

workflow_runs
  id, workflow_id, version, owner_user_id, trigger_type,
  scheduled_for, status, started_at, finished_at, error_code

workflow_node_runs
  run_id, node_id, node_type, status, attempt,
  safe_input_summary, safe_output_summary, started_at, finished_at, error_code
```

Node输入输出有大小上限并再次脱敏。完整OAuth token、邮件正文中的Secret和MCP credential永不进入运行记录。

用户删除必须先停止并删除其 active schedules、工作流、运行数据和外部连接；与当前用户删除的渠道解绑前置检查保持一致。

## 12. 用户通知与外部连接

### 12.1 Notification Endpoint

建议在 auth schema 增加：

```text
user_notification_endpoints
  id
  user_id
  type
  address
  verified_at
  status
  is_default
  created_at
  updated_at
```

邮箱验证使用短期一次性token和发送频率限制。未验证地址不能成为官方通知目标。

### 12.2 External Connection

新增 `${ZHICE_AGENT_WORKSPACE}/state/connections.sqlite3`：

```text
external_connections
oauth_authorization_states
connection_audit_events
```

连接记录：

```text
id
owner_user_id
provider
account_display
credential_ciphertext
credential_nonce
key_version
scopes_json
expires_at
status
created_at
updated_at
```

Token使用 AES-GCM：

```text
key = env ZHICE_AGENT_CREDENTIAL_ENCRYPTION_KEY
aad = owner_user_id | connection_id | provider
```

环境密钥缺失或非法时，个人连接能力 `unavailable`，不阻断普通聊天、MCP查询、工作流编辑和官方通知。密钥不写入 config.yml、数据库、镜像公开层或Git。

### 12.3 Microsoft OAuth

授权流程：

```text
GET /api/connections/email/microsoft/start
  -> create state + PKCE verifier
  -> redirect Microsoft authorize

GET /api/connections/email/microsoft/callback
  -> verify state / owner / expiry
  -> exchange code server-side
  -> encrypt refresh token
  -> save account display + scopes
```

最小 delegated scopes：

```text
openid
profile
email
offline_access
Mail.Send
```

不请求 Mail.Read。Refresh响应出现新 refresh token 时原子替换旧 token。`invalid_grant`、consent_required或权限不足时把连接标记为 `reauthorization_required`，通知用户重新授权。

### 12.4 Gmail OAuth

授权流程与Microsoft共用 `ExternalConnectionProvider`、state、PKCE、owner校验和加密Token Store：

```text
GET /api/connections/email/google/start
GET /api/connections/email/google/callback
```

最小scope：

```text
openid
email
https://www.googleapis.com/auth/gmail.send
```

不请求读取、修改或删除邮件的scope。发送时构造RFC 2822 MIME消息，执行base64url编码并调用 `users.messages.send`。API返回message id只映射为 `accepted`；OAuth撤销、`invalid_grant`和scope不足统一进入 `reauthorization_required`。

### 12.5 通用个人SMTP连接

QQ邮箱、163邮箱和其它支持SMTP授权码的Provider使用：

```text
provider = smtp_personal
host
port
security = starttls | tls
username
app_password
from_address
```

`app_password`与OAuth refresh token使用相同AES-GCM Store，绝不进入工作流定义。连接创建时执行一次受限连通性验证；不接受关闭TLS校验、任意CA文件路径或明文SMTP。发送使用标准库 `smtplib`，服务器接受只记为 `accepted`。

## 13. Web 产品设计

新增 `/workflows` 页面：

```text
left: node palette and workflow list
center: Vue Flow canvas
right: selected node inspector
bottom: run timeline and node input/output summary
top: save draft / publish / pause / run now / next run
```

节点状态：

```text
gray   idle
blue   running
green  succeeded
red    failed
yellow skipped / attention
purple scheduled
orange unknown external effect
```

使用 Vue Flow：

- custom nodes / edges；
- Background；
- MiniMap；
- Controls；
- NodeToolbar；
- drag、zoom、pan、selection；
- Pinia保存编辑态，后端published version才是运行真值。

新增“设置 → 外部连接”：

- 已验证通知邮箱；
- Microsoft、Gmail和个人SMTP邮箱连接、状态、scope/安全方式、过期时间；
- 重新授权、撤销；
- 不展示 access token、refresh token、client secret。

运行历史展示 workflow、version、trigger、scheduled_for、每个节点状态、耗时和错误。管理员全局页只显示脱敏元数据、用户、工作流状态和错误码，不显示邮件正文和连接凭据。

## 14. API 与事件

### 14.1 Workflow API

```text
GET    /api/workflows
POST   /api/workflows
GET    /api/workflows/{id}
PUT    /api/workflows/{id}/draft
POST   /api/workflows/{id}/publish
POST   /api/workflows/{id}/pause
POST   /api/workflows/{id}/resume
POST   /api/workflows/{id}/run
GET    /api/workflows/{id}/runs
GET    /api/workflow-runs/{run_id}
POST   /api/workflow-runs/{run_id}/cancel
DELETE /api/workflows/{id}
```

所有API使用当前 Web auth、ownership和CSRF/同源边界。

### 14.2 Connection API

```text
GET    /api/connections
GET    /api/connections/email/microsoft/start
GET    /api/connections/email/microsoft/callback
GET    /api/connections/email/google/start
GET    /api/connections/email/google/callback
POST   /api/connections/email/smtp
POST   /api/connections/{id}/reauthorize
DELETE /api/connections/{id}
```

### 14.3 Event

```text
workflow.run.queued
workflow.run.started
workflow.node.started
workflow.node.progress
workflow.node.completed
workflow.node.failed
workflow.node.skipped
workflow.run.completed
workflow.run.failed
workflow.run.cancelled
workflow.schedule.misfired
workflow.paused_attention
connection.authorization.completed
connection.authorization.revoked
```

运行事件持久化后通过受认证的 run event stream 投影给页面；断线重连先读取数据库游标，再继续接收新事件。瞬态进度可不写完整payload，但状态转换必须持久化。

## 15. 配置

`config/config.example.yml` 新增非Secret配置：

```yaml
workflows:
  enabled: false
  max_workflows_per_user: 20
  max_active_schedules_per_user: 10
  max_nodes_per_workflow: 30
  max_edges_per_workflow: 60
  max_global_workers: 4
  max_daily_runs_per_user: 100
  max_daily_official_emails_per_user: 10
  max_daily_personal_emails_per_user: 20
  default_timezone: Asia/Shanghai
  default_misfire_grace_seconds: 900
  allowed_query_tools: []
  allowed_action_tools: []

connections:
  microsoft_email:
    enabled: false
    client_id: "${MICROSOFT_OAUTH_CLIENT_ID}"
    client_secret: "${MICROSOFT_OAUTH_CLIENT_SECRET}"
    redirect_uri: "${MICROSOFT_OAUTH_REDIRECT_URI}"
  google_email:
    enabled: false
    client_id: "${GOOGLE_OAUTH_CLIENT_ID}"
    client_secret: "${GOOGLE_OAUTH_CLIENT_SECRET}"
    redirect_uri: "${GOOGLE_OAUTH_REDIRECT_URI}"
  personal_smtp:
    enabled: true
    allowed_ports: [465, 587]

official_email:
  enabled: false
  provider: smtp
  host: "${ZHICE_SMTP_HOST}"
  port: 587
  username: "${ZHICE_SMTP_USERNAME}"
  password: "${ZHICE_SMTP_PASSWORD}"
  from_address: "${ZHICE_SMTP_FROM}"
```

凭据和AES-GCM master key只进入 `${ZHICE_AGENT_WORKSPACE}/config/.env` 或部署Secret。配置显式启用但Secret缺失时只禁用对应Provider并报告结构化 capability warning；工作流核心可继续运行无邮件节点的定义。

## 16. 安全与可靠性

- 所有用户只能 CRUD 本人工作流和连接；
- 管理权限不能转化为使用他人个人连接的权限；
- OAuth state单次使用、短期过期、绑定当前用户和PKCE verifier；
- client secret和refresh token只在后端使用；
- Graph只请求 delegated `Mail.Send`，不请求读邮件；
- Gmail只请求 `gmail.send`，不请求读、改、删邮件；
- 个人SMTP只允许TLS/STARTTLS和固定端口集合，授权码加密保存；
- 邮件地址数量、长度、正文和调用频率受限；
- 官方通知节点收件人由后端解析，用户不能覆盖；
- 个人邮件节点支持Dry Run，发布时明确展示外部影响；
- MCP Action节点必须命中写操作allowlist、业务权限和发布确认；
- Action节点外部结果不确定时不自动重放；
- Tool descriptions、上游网页和节点输出视为不可信数据；
- 工作流不提供任意代码、Shell、文件路径或环境变量展开；
- LLM transform没有Tool，不读取Secret，输出再次脱敏；
- Tool schema变化、权限撤销、连接失效、用户禁用都会暂停工作流；
- Scheduler单实例、固定job id、replace_existing和最大实例限制防止重复注册；
- 工作流和connection数据库位于持久volume，升级镜像不丢失；
- 运行日志只保存安全摘要，敏感正文按产品设置选择是否保存并有明确上限；
- 安全审计记录发布、启停、授权、撤销、发送接受/拒绝和管理员动作。

## 17. 错误码

```text
WORKFLOW_DISABLED
WORKFLOW_NOT_FOUND
WORKFLOW_ACCESS_DENIED
WORKFLOW_PERMISSION_REVOKED
WORKFLOW_OWNER_DISABLED
WORKFLOW_SCHEMA_INVALID
WORKFLOW_GRAPH_CYCLE
WORKFLOW_GRAPH_TOO_LARGE
WORKFLOW_NODE_CONFIG_INVALID
WORKFLOW_TOOL_NOT_ALLOWED
WORKFLOW_TOOL_NEEDS_REVIEW
WORKFLOW_QUEUE_FULL
WORKFLOW_RUN_CONFLICT
WORKFLOW_NODE_TIMEOUT
WORKFLOW_NODE_FAILED
WORKFLOW_ACTION_OUTCOME_UNKNOWN
WORKFLOW_SCHEDULE_INVALID
WORKFLOW_SCHEDULE_MISFIRED
CONNECTION_NOT_FOUND
CONNECTION_ACCESS_DENIED
CONNECTION_AUTH_EXPIRED
CONNECTION_REAUTHORIZATION_REQUIRED
CONNECTION_CREDENTIAL_KEY_MISSING
CONNECTION_PROVIDER_UNSUPPORTED
CONNECTION_SMTP_INSECURE
OFFICIAL_EMAIL_NOT_CONFIGURED
NOTIFICATION_EMAIL_NOT_VERIFIED
EMAIL_RATE_LIMITED
EMAIL_REJECTED
EMAIL_OUTCOME_UNKNOWN
```

## 18. 变更文件规划

```text
agent/workflows/__init__.py
agent/workflows/schemas.py
agent/workflows/store.py
agent/workflows/catalog.py
agent/workflows/authorization.py
agent/workflows/scheduler.py
agent/workflows/executor.py
agent/workflows/nodes.py
agent/workflows/runtime.py
agent/connections/__init__.py
agent/connections/protocols.py
agent/connections/store.py
agent/connections/crypto.py
agent/connections/oauth.py
agent/connections/runtime.py
agent/integrations/email/protocols.py
agent/integrations/email/official_smtp.py
agent/integrations/email/microsoft_graph.py
agent/integrations/email/google_gmail.py
agent/integrations/email/personal_smtp.py
agent/app/api/workflow_routes.py
agent/app/api/connection_routes.py
agent/app/api/schemas.py
agent/app/runtime.py
agent/auth/schema.py
agent/auth/store.py
agent/auth/tool_policy.py
agent/protocols/runtime_event.py
prompts/workflow_transform.md
config/config.example.yml
pyproject.toml
web/frontend/package.json
web/frontend/src/pages/WorkflowPage.vue
web/frontend/src/components/workflow/*
web/frontend/src/stores/workflows.ts
web/frontend/src/stores/connections.ts
web/frontend/src/api/client.ts
web/frontend/src/api/types.ts
web/frontend/src/router/index.ts
tests/unit_test/workflows/test_case.md
tests/unit_test/workflows/*
tests/unit_test/connections/test_case.md
tests/unit_test/connections/*
tests/integration_test/workflows/*
docs_design/README.md
docs_design/zhice-agent-overall-design.md
README.md
```

依赖建议：

```text
Python: APScheduler >=3.11,<4
Python: Authlib >=1.7,<2
Python: cryptography >=50,<51
Web:    @vue-flow/core ^1.48.2
Web:    @vue-flow/background / controls / minimap 与同一版本族
```

实现提交必须锁定实际解析版本并重新运行许可证检查。

## 19. 测试方案

### 19.1 定义与Store

- 正常DAG、cycle、孤立节点、错误port、超限节点和边；
- draft/published不可变版本和并发编辑冲突；
- per-user ownership、Admin脱敏管理、Auditor只读；
- 删除用户前暂停schedule和清理connection；
- 工作流、运行、节点、event游标和恢复。

### 19.2 Scheduler

- date/interval/cron和IANA timezone；
- 固定job id与replace_existing；
- 重启从SQLite重建；
- grace内补一次、grace外skipped、coalesce；
- max_instances=1和单实例lock；
- shutdown拒绝新Run并有界等待。

### 19.3 Executor与节点

- 稳定拓扑顺序、condition跳过、取消和partial；
- 受限引用parser拒绝表达式注入；
- MCP Query/Action双allowlist、actor、schema hash、发布确认、Hook、Activity和Audit；
- LLM transform不收到Tool、不创建Session、结构化输出校验；
- Query retry与Action不重放；
- timeout后的unknown external effect；
- 输入输出截断和Secret脱敏。

### 19.4 OAuth与邮件

- state、PKCE、owner、过期、重放和callback错误；
- AES-GCM正常、错误key、AAD不匹配和密文篡改；
- refresh token原子轮换、reauthorization_required和撤销；
- 官方通知只能发给已验证本人地址；
- 个人connection ownership、Mail.Send权限和每日额度；
- Graph `202`映射accepted，不映射delivered；
- Gmail `messages.send`、scope最小化和message id映射accepted；
- 个人SMTP TLS/STARTTLS、固定端口、授权码加密和服务器接受语义；
- HTTP timeout映射unknown且不自动重试；
- Token、client secret和邮件敏感正文不进入日志和API。

### 19.5 Web与E2E

- Vue Flow拖拽、连线、删除、属性编辑、字段选择器；
- 发布校验、运行状态颜色、MiniMap、Controls和移动端；
- Run Now完整链和定时触发完整链；
- 断线后从event游标恢复运行轨迹；
- 两个用户工作流和connection完全隔离；
- E2E走真实FastAPI、正式WorkflowRuntime、Fake MCP、Fake LLM和Fake Email Provider；
- 真实Microsoft OAuth、Google OAuth、个人SMTP和官方邮件分别由显式环境变量/测试账号开启并完成smoke。

提交前运行：

```bash
python -m ruff check .
python -m pytest
cd web/frontend
npm run lint
npm run typecheck
npm run test
npm run build
```

## 20. 实施依赖顺序（不分期发布）

以下顺序只表达代码依赖；第1～12项必须一次全部完成，中间状态不发布、不验收。

1. 固化 WorkflowDefinitionV1、Node、Edge、Run和错误码。
2. 实现SQLite Store、版本发布、DAG校验和Run Now有界并行Executor。
3. 实现Template、Condition和Fake Action节点。
4. 接入actor-scoped MCP Query/Action节点、双allowlist、schema hash和发布确认门控。
5. 接入无Tool的LLM Transform Node和专用Prompt。
6. 实现APScheduler MemoryJobStore、重建、misfire和单实例生命周期。
7. 实现Vue Flow画布、属性面板、发布、运行历史和事件动画。
8. 实现已验证Notification Endpoint与官方SMTP通知本人。
9. 实现ExternalConnection Store、AES-GCM、Authlib、Microsoft OAuth和Google OAuth。
10. 实现Microsoft Graph、Gmail API、个人SMTP三种Personal Email Provider及权限/额度/unknown outcome。
11. 完成全部节点、全量测试、本地重启恢复、真实OAuth/SMTP、写操作MCP和浏览器演示验收。
12. 全部验收关闭后创建工作流特色应用活文档，并同步当前实现基线。

## 21. 验收标准

1. 正常登录用户可以创建、编辑、发布、立即运行、定时、暂停和删除本人的工作流。
2. 不同用户不能读取、修改、执行或引用彼此工作流和连接。
3. 画布支持拖拽、连线、缩放、MiniMap、属性面板和节点状态动画。
4. 八种节点全部可用，并能组合完成“天气 -> AI建议 -> 条件 -> 官方通知”和“搜索 -> AI摘要 -> 受控MCP发布”。
5. 工作流重启后从SQLite恢复，逾期触发按grace和coalesce执行，不重复注册。
6. MCP Query/Action节点经过当前actor、双allowlist、schema hash、RBAC、发布确认、Hook、Activity和Audit。
7. LLM节点不调用Tool、不写Session；节点配置output schema时必须返回并通过结构化校验。
8. 官方邮件只能发送给workflow owner已验证的本人地址。
9. 获得权限的用户可以绑定本人Microsoft、Gmail或个人SMTP邮箱，并向指定地址发送。
10. 用户Token加密落库，不进入工作流JSON、Session、日志、API或管理员页面。
11. Graph `202`、Gmail message id和SMTP服务器接受均显示accepted而不是delivered；超时结果未知时不自动重发。
12. 用户禁用、权限撤销、Tool schema变化、connection失效都会暂停工作流并给出可操作原因。
13. 任意代码、Shell、exec、循环和完整Agent节点均不可用，这是固定产品边界而非待补能力。
14. 当前单Gateway单scheduler边界在配置、health、日志、测试和部署文档中保持一致。

## 22. 调研来源

以下链接均于 2026-08-10 实际访问：

- Vue Flow GitHub：<https://github.com/bcakmakoglu/vue-flow>
- Vue Flow 官方指南：<https://vueflow.dev/guide/>
- LogicFlow GitHub：<https://github.com/didi/LogicFlow>
- APScheduler GitHub：<https://github.com/agronholm/apscheduler>
- APScheduler PyPI：<https://pypi.org/project/APScheduler/>
- APScheduler 3.x User Guide：<https://apscheduler.readthedocs.io/en/3.x/userguide.html>
- n8n GitHub：<https://github.com/n8n-io/n8n>
- Prefect GitHub：<https://github.com/PrefectHQ/prefect>
- Celery GitHub：<https://github.com/celery/celery>
- Temporal GitHub：<https://github.com/temporalio/temporal>
- Authlib GitHub：<https://github.com/authlib/authlib>
- Authlib PyPI：<https://pypi.org/project/Authlib/>
- cryptography PyPI：<https://pypi.org/project/cryptography/>
- Microsoft OAuth Authorization Code Flow：<https://learn.microsoft.com/en-us/entra/identity-platform/v2-oauth2-auth-code-flow>
- Microsoft Graph `sendMail`：<https://learn.microsoft.com/en-us/graph/api/user-sendmail?view=graph-rest-1.0>
- Gmail发送指南：<https://developers.google.com/workspace/gmail/api/guides/sending>
- aiosmtplib PyPI：<https://pypi.org/project/aiosmtplib/>
