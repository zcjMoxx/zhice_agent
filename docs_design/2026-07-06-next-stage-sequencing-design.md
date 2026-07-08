# ZhiCe-Agent 后续阶段排序设计记录

> 说明：后续 Part 7 实现已按本地开发口径移除旧 JSONL、metadata fallback 和 legacy grouping 兼容；上下文注入也改为先做本地相关性选择。第八部分 Gateway / Agent 运行日志也已落地。当前上下文方案参考 `docs_design/2026-07-06-context-relevance-selection-design.md` 和 `docs_design/zhice-agent-part7-turn-context-design.md`，第八部分当前实现口径参考 `docs_design/zhice-agent-part8-gateway-agent-logging-design.md`。

> 日期：2026-07-06
> 状态：路线调整记录；本记录只调整后续阶段顺序和文档口径，不代表代码已经实现。
> 说明：第七部分当前施工图见 `docs_design/zhice-agent-part7-turn-context-design.md`；本记录只说明后续阶段排序和依赖关系。

## 1. 背景

第六部分 Web 最小版已经落地，当前代码具备 FastAPI gateway、WebSocket 主聊天通道、Web stop、会话 API、模型选择和静态 Web UI。后续讨论中出现了三个候选方向：

1. `2026-07-04-turn-runtime-and-context-design.md` 中的 turn 运行单元和上下文治理。
2. `2026-07-02-gateway-runtime-logging-design.md` 中的 Gateway / Agent 运行日志优化。
3. 用户、登录、多用户权限和危险工具执行授权。

这三个方向都重要，但依赖关系不同。尤其是用户权限系统不是单纯的“平台化 UI”，它将用于解决 `exec` 等高风险能力当前一刀切拦截的问题；但权限判断和审计需要稳定的 `session -> turn -> tool` 运行边界。

因此本记录用于调整总体设计中的阶段顺序，避免继续把 Memory/MCP/Hooks/Subagent 作为第六部分之后的默认下一阶段。

## 2. 结论

后续主线顺序调整为：

```text
Milestone 7：Turn 运行单元与上下文治理
  -> Milestone 8：Gateway / Agent 运行日志优化
  -> Milestone 9：用户、登录与权限执行边界设计
  -> Milestone 10：用户权限系统第一版实现
  -> 后续：Memory / MCP / Hooks / Subagent
```

一句话原则：

```text
先 turn，后日志，再用户权限设计，最后实现用户权限 UI 和执行管控。
```

## 3. 设计理由

### 3.1 Turn 是日志和权限的前置地基

当前 Web runtime 已有内存态 `ActiveTurn`，`AgentLoop.run_turn()` 也天然表示“一次用户请求”，但 `Message` 和 `SessionStore` 还没有持久 `turn_id`。这会影响后续能力：

- 运行日志无法稳定关联同一轮 user / assistant / tool 消息。
- Web accepted / done / stopped 和历史消息无法完全对齐。
- 审计日志难以回答“哪一次用户请求触发了哪个工具调用”。
- 多用户并发时，权限判断和 active turn 归属不够清楚。

所以第七部分先落地 turn 边界。

### 3.2 运行日志应复用统一 turn_id

Gateway 运行日志优化需要打印：

- turn start / done / stopped / error。
- LLM call / tool decision。
- tool start / done / error。
- session save failure。

如果在 `turn_id` 统一前先大改日志，后续还要再补一遍关联字段。因此日志优化排在 turn 之后最稳。

### 3.3 用户权限系统需要审计和执行边界

用户系统的目标不是只做登录壳，而是为危险工具执行提供身份、权限、确认和审计基础。第一版可以使用 SQLite 保存用户、角色、权限、登录态、session owner 和 audit logs，但应该建立在清楚的运行边界上：

```text
User -> Session -> Turn -> ToolCall / AuditLog
```

这样后续 `exec`、Skill、模型切换、session 管理等能力都可以按用户权限收口，而不是散落在 Web route、Tool 和 AgentLoop 里。

## 4. 本次文档调整范围

本次只更新设计文档，不写业务代码：

- `docs_design/zhice-agent-overall-design.md`
  - 更新未来扩展顺序。
  - 更新后续能力章节。
  - 把 Milestone 7 从 Memory 改为 Turn。
  - 把日志和用户权限排入后续主线。
- `docs_design/zhice-agent-part7-turn-context-design.md`
  - 将 2026-07-04 turn 未来设计收敛为第七部分可开发施工图。
  - 明确 turn 闭环自然包含什么，以及不主动引入哪些独立系统。
- `docs_design/README.md`
  - 登记本记录。
  - 补充当前下一阶段阅读顺序。
- `docs_design/zhice-agent-part6-web-minimum-design.md`
  - 调整第六部分后续演进顺序。
- `docs_design/zhice-agent-part6-web-ui-design.md`
  - 调整 UI 后续演进顺序。
- `README.md`
  - 对齐当前 WebSocket 已落地事实和后续路线。

## 5. 非目标

- 不在本次实现 turn 持久化。
- 不在本次实现日志参数或日志模块。
- 不在本次设计完整用户系统库表。
- 不在本次实现登录界面、用户管理页或权限检查。
- 不回头改写 `2026-07-04-turn-runtime-and-context-design.md` 或 `2026-07-02-gateway-runtime-logging-design.md` 的正文。

## 6. 后续执行建议

### 6.1 Milestone 7

按 `docs_design/zhice-agent-part7-turn-context-design.md` 开发第七部分。该活文档已经把 `2026-07-04-turn-runtime-and-context-design.md` 收敛为可落地任务：

- `Message` 增加可选 turn 字段。
- `JsonlSessionStore` 读写 turn 字段并兼容旧 JSONL。
- `AgentLoop.run_turn()` 支持外部传入 `turn_id`。
- WebSocket accepted / done / stopped 使用同一个 `turn_id`。
- `ContextBuilder` 支持按最近 user turn 裁剪。

### 6.2 Milestone 8

在 turn 统一后落地 `2026-07-02-gateway-runtime-logging-design.md`：

- 分层日志参数。
- `agent/app/logging.py`。
- AgentLoop / ToolRegistry / WebRuntime 生命周期日志。
- 截断和脱敏。

### 6.3 Milestone 9

用户权限先做设计，不直接开代码：

- 账号和登录态。
- 角色和权限。
- session owner。
- tool permission。
- dangerous exec 权限 + 用户确认。
- audit logs。
- SQLite schema。
- 登录页和管理界面范围。

### 6.4 Milestone 10

按 Milestone 9 设计实现第一版：

- 初始化管理员。
- 登录 / 登出。
- 用户、角色、权限管理。
- Web API 鉴权。
- session 按用户隔离。
- 工具执行权限检查和审计。

## 7. 验收标准

本次文档调整完成后应满足：

1. 总体设计不再把 Memory 作为第六部分后的默认 Milestone 7。
2. 总体设计明确下一步是 turn，然后是运行日志，再是用户权限设计和实现。
3. Part 6 后续演进与总体设计顺序一致。
4. README 不再声称当前 Web 只有 SSE 或没有 WebSocket。
5. 日期设计索引包含本记录，Part 7 活文档成为第七部分开发依据，旧 turn/logging 设计记录保留为背景和后续参考。
