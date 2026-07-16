# ZhiCe-Agent 用户登录与权限执行边界设计记录

> 说明：当前代码已于 2026-07-16 将登录用户日常功能收敛为基础能力，RBAC 只保留跨用户、管理、审计、危险执行和全局同步特权；本文早期的 `*.own`、safe tool 等权限清单不再适用。请参考 `docs_design/2026-07-16-authenticated-user-baseline-capabilities-design.md` 和当前 Part 9 活文档。

> 说明：2026-07-10 对模型偏好范围做了补充修正，并在后续讨论中最终确定为 session 级持久化：不增加用户默认层，`/model reset` 清当前 session 偏好，`/new` 创建使用系统默认的新 session。详见 `docs_design/2026-07-10-session-model-preference-scope-design.md` 和当前 Part 9 活文档。本文其余正文保留当时方案原貌。
>
> 说明：后续实现已将最高特权账号收敛为唯一 Owner；Owner Web 只复用全局 `contexts/sessions*`，列出会话时为未索引 CLI 历史补 Owner index，不复制、不移动、不回退到 Owner 用户目录。普通用户 CLI 历史导入暂未纳入当前实现。当前口径以 Part 9 活文档和 `docs_design/2026-07-14-owner-cli-session-index-reconciliation-design.md` 为准。

> 日期：2026-07-08
>
> 状态：设计已完成；代码尚未实现。
>
> 说明：本文是第九部分方案的日期设计记录。后续实现优先阅读当前活文档 `docs_design/zhice-agent-part9-user-auth-permission-design.md`。

## 1. 背景

第七部分已经落地 turn 运行单元，第八部分已经落地 Gateway / Agent 运行日志。当前代码具备：

- `turn_id` / `turn_index` 持久化。
- WebSocket accepted / done / stopped 和 AgentLoop 使用同一个 turn id。
- Agent lifecycle log 和 workspace `trace.log` 能通过 `session_id` / `turn_id` 关联运行轨迹。

但当前代码仍没有用户身份和权限边界：

- Web API / WebSocket 没有鉴权。
- session 没有 owner。
- `ToolProvider.execute(name, args)` 没有 actor、session、turn 上下文。
- `exec` 对危险命令仍是一刀切静态拦截。
- `trace.log` 是运行日志，不是带 actor / permission decision 的安全审计日志。

因此本次设计承接 `docs_design/2026-07-06-next-stage-sequencing-design.md`，完成第九部分用户、登录与权限执行边界设计；审核确认后直接进入第九部分实现阶段。

## 2. 问题

如果直接做登录页，会留下三个核心问题：

1. 登录态只保护页面，不保护工具执行。
2. session 列表和 active turn 仍可能按裸 `session_id` 串扰。
3. 危险工具没有“谁触发、按什么权限允许、是否确认、结果如何”的审计链路。

所以第九部分必须先把执行链路设计清楚：

```text
User -> Session -> Turn -> ToolCall / AuditLog
```

## 3. 目标和非目标

目标：

- 定义本地第一版用户、角色、权限、登录态、用户上下文目录和 session index 模型。
- 定义 tool execution context、tool permission policy 和 confirmation broker 边界。
- 定义 `exec` 风险分类、权限检查、确认和审计策略。
- 定义 SQLite schema 草案。
- 定义 Web API / WebSocket 鉴权变化和 UI 范围。
- 定义第九部分实现顺序、测试方案和验收标准。

非目标：

- 不设计 OAuth / SSO。
- 不设计组织架构和多租户。
- 不把 gateway 变成公网生产服务。
- 不把 JSONL 会话消息全部迁移到数据库。
- 不做复杂审批流。
- 不做完整前端工程化。

## 4. 结论

本次方案沉淀为当前活文档：

```text
docs_design/zhice-agent-part9-user-auth-permission-design.md
```

核心结论：

- 第一版使用 `${ZHICE_AGENT_WORKSPACE}/state/auth.sqlite3` 保存 users、roles、permissions、auth_sessions、external_identities、session_index、turn_runs、tool_call_records、tool_confirmations 和 audit_events。
- JSONL 继续作为聊天消息真值；auth DB 只负责身份、权限、session 索引、渠道映射、运行记录、确认和审计。
- 用户上下文目录为 `${ZHICE_AGENT_WORKSPACE}/contexts/users/{user_id}`；`files/` 是默认可写工作目录，`sessions/` 和 `sessions_meta/` 是系统维护目录。
- 公共只读上下文放在 `${ZHICE_AGENT_WORKSPACE}/contexts/shared/readonly`。
- `admin` 是 ZhiCe-Agent 用户系统中的最高权限角色，不是 Web 专用身份，也不是 CLI 默认账号；任何 DB 用户都可以被授予该角色。
- CLI 继续作为本地 no-login 操作者入口，保留当前 `contexts/sessions` 和 `contexts/sessions_meta` 全局会话路径，不因启用 auth DB 自动迁入用户目录。
- Web API / WebSocket 默认要求 actor；未登录返回 401，权限不足返回 403，不可见资源优先按统一口径隐藏。
- session 权限以内部 `user_id` 的物理目录为第一边界，`session_index` 只做列表、标题、渠道元数据、归档和审计关联。
- 外部渠道账号通过 `external_identities` 映射到内部 `user_id`，不在用户目录下按渠道再拆权限边界。
- AgentLoop 不 import FastAPI、SQLite 或具体用户业务；它通过 `ActorContext`、`ToolExecutionContext`、`ToolExecutionPolicy` 和 `ToolConfirmationBroker` 协议接收必要上下文。
- `exec` 不因为 admin 身份自动放开。高风险命令必须同时满足基础安全策略、`tool.exec.dangerous` 权限和用户确认；具体风险写入 `risk_category`。
- 普通用户不直接读取 raw trace；通过用户级诊断工具按 actor/time/session/turn/request 过滤，返回具体原因、证据、置信度和下一步建议。
- env dump 和不可分类危险 shell 第一版继续拒绝。

## 5. 模块设计

第九部分实现预计新增：

```text
agent/protocols/auth.py
agent/auth/store.py
agent/auth/schema.py
agent/auth/passwords.py
agent/auth/tokens.py
agent/auth/policy.py
agent/auth/audit.py
agent/auth/session_access.py
agent/auth/user_context.py
agent/auth/external_identity.py
agent/auth/tool_policy.py
agent/auth/confirmation.py
agent/auth/diagnostics.py
agent/app/auth.py
```

预计修改：

```text
agent/protocols/tool.py
agent/app/runtime.py
agent/app/api/routes.py
agent/app/api/ws.py
agent/app/api/schemas.py
agent/core/loop.py
agent/tools/shell_policy.py
agent/tools/exec.py
agent/config.py
agent/cli.py
web/static/index.html
web/static/styles.css
web/static/app.js
```

## 6. 数据流

普通 Web 聊天：

```text
browser cookie
  -> resolve ActorContext
  -> require chat.run
  -> resolve contexts/users/{actor.user_id}
  -> ensure session_index and user JSONL session
  -> WebRuntime.run_chat_events(actor, session_id, message)
  -> AgentLoop.run_turn(actor=actor, session_id, turn_id)
  -> tool policy checks each tool call
  -> JSONL append messages
  -> audit turn/tool events
```

高风险 `exec`：

```text
LLM tool_call exec
  -> shell policy classifies risk
  -> permission check
  -> create confirmation
  -> Web confirmation event
  -> user approve/deny/timeout
  -> execute or return tool error
  -> audit decision and result
```

## 7. 测试方案

第九部分实现时至少覆盖：

- AuthStore schema 初始化、首个 `admin` 角色用户初始化、password verify、token revoke / expiry。
- permission aggregation 和默认角色。
- user context path guard、session_index create/list/read/rename/delete。
- external identity -> internal user_id mapping。
- API 401 / 403 / 404 口径。
- WebSocket cookie 鉴权和 external client token 口径。
- AgentLoop tool policy allow / deny / confirmation。
- `exec` safe / dangerous / env_dump 分类和 `risk_category` 记录。
- audit event 脱敏、截断和 actor/session/turn/tool_call 关联。
- 用户可见诊断报告的具体原因、证据、置信度和下一步建议。
- CLI 全局 JSONL session 显式导入到指定 DB 用户。

默认验证命令：

```bash
python -m ruff check .
python -m pytest --basetemp .tmp/pytest_basetemp
```

## 8. 验收标准

设计阶段验收：

1. 有第九部分活文档，明确 User -> Session -> Turn -> ToolCall / AuditLog。
2. SQLite schema、权限 key、默认角色、登录态、用户上下文目录、session_index、tool policy、confirmation、诊断和 audit 方案完整。
3. README、总体设计和设计索引都指向第九部分方案。
4. 第九部分实现范围清楚，不把 OAuth、多租户、生产部署和前端工程化混入第一版。

实现阶段验收：

1. 未登录不能访问受保护 Web API / WebSocket。
2. 普通用户只能操作自己 `contexts/users/{user_id}` 下的 session 和 files。
3. tool call 在执行前经过权限策略。
4. 高风险 `exec` 需要用户确认。
5. audit_events 能说明谁在什么时候对哪个 session/turn/tool 做了什么决策。
6. 用户可见诊断能说明失败的具体原因、证据和下一步，而不是只返回错误码。
7. AgentLoop 不依赖 FastAPI 或 SQLite 具体实现。
8. CLI 本地操作者路径保持不变，历史全局 JSONL 只有显式导入后才进入某个 DB 用户目录。
