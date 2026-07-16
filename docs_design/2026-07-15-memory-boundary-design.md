# ZhiCe-Agent Memory 作用域、主动候选与确认写入设计记录

> 说明：本文记录的 Memory tool confirmation、候选状态机和 Web 编辑确认方案已被 `docs_design/2026-07-16-conversational-memory-consent-design.md` 替代；显式 Session Summary 也已因没有上下文消费闭环而删除，参考 `docs_design/2026-07-16-remove-unclosed-session-summary-design.md`。当前仍适用的是 Memory 作用域、Markdown 存储和安全过滤边界。

> 日期：2026-07-15
>
> 状态：已完成设计并落地代码。
>
> 当前实现口径：`docs_design/zhice-agent-part10-memory-design.md`
>
> 落地说明：Part 10 已实现 Markdown Memory、CLI/Owner workspace 作用域、普通用户私有作用域、受控读写、主动候选策略、确认写入、显式 session 摘要及安全审计边界。Web 确认界面支持修改候选内容；修改时取消旧 confirmation，以新参数和新 `args_hash` 创建替代 confirmation，并要求重新批准。

## 1. 背景

Part 7 已经把 Session 内历史收敛为可持久化 turn，并通过 `ContextBuilder` 选择最近相关 turn。Part 9 又增加了内部用户、用户上下文目录、session owner、权限、工具确认和 audit。

当前缺少的是跨 session 长期信息：用户在一个 session 中明确给出的稳定偏好、项目背景、长期约束或决策，新的 session 默认无法读取。

早期总体设计曾用单个：

```text
contexts/memory/MEMORY.md
```

表示未来 Memory。但这个单用户假设已经不符合 Part 9 的代码事实。如果所有 Web 用户共享一个全局文件，会破坏用户隔离。

本记录用于确定 Part 10 的能力边界：Memory 放在哪里、谁能读写、何时写入、是否自动摘要，以及 Memory 与 Session/ContextBuilder 的关系。

## 2. 目标

1. 让每个 actor 只能访问自己的长期 Memory。
2. 保持轻量、可人工检查，不引入向量数据库。
3. 让 Memory 复用 Tool、RBAC、trace 和 audit，而不是绕开当前运行链。
4. 允许模型基于高可信长期风格或约束主动提出候选，但长期 Memory 只能在当前用户确认具体内容后修改。
5. 提供显式 session summary，但不在每个 turn 后自动增加 LLM 调用。
6. 保持 Session JSONL 为真实聊天记录，不让摘要或 Memory 替代历史。

## 3. 非目标

- 不做静默 memory extraction/write，也不增加每轮结束后的隐藏 review 调用。
- 不做 embedding/vector search。
- 不做 graph memory。
- 不做跨用户管理员读取。
- 不做后台摘要任务或定时 compaction。
- 不做 Memory 管理 Web 页面。
- 不把 Memory 具体实现写进 AgentLoop。

## 4. 当前实现依据

### 4.1 Session 与 turn

当前：

```text
Session JSONL
  -> Message(turn_id, turn_index)
  -> ContextBuilder recent relevant turns
```

因此 session summary 可以按完整 turn 读取和增量更新，不需要重新发明消息分组。

### 4.2 用户目录

当前普通登录用户目录：

```text
contexts/users/{user_id}/
  files/
  sessions/
  sessions_meta/
```

CLI local operator 与 Owner Web 是同一个 workspace operator 的两个入口，继续共用全局 sessions、metadata 和 workspace。Memory 必须沿用同一语义；普通用户才进入 `contexts/users/{user_id}`。

### 4.3 工具权限

当前 AgentLoop 已在 Tool dispatch 前构造 ToolExecutionContext，并通过 ToolExecutionPolicy 做 permission 判断。Memory Tool 应直接复用这条链。

## 5. 关键决策

### 5.1 Memory 按 workspace operator / ordinary user 隔离

选择：

```text
CLI local operator + Owner Web
  -> contexts/memory/

ordinary database user
  -> contexts/users/{user_id}/memory/
```

原因：

- Owner 是 CLI 本地操作者在 Web 端的认证身份，本质上是同一个 workspace operator。
- Owner 与 CLI 已经共用 workspace、sessions 和 metadata，Memory 也必须共用全局路径。
- Owner 的 DB user id 只服务登录、权限、session index 和 audit，不能产生独立 Owner 用户目录。
- 普通用户未来绑定多个渠道时仍共享同一个内部用户 Memory。
- 不会因 username 改名或渠道迁移改变路径。

禁止方案：为 Owner 创建 `contexts/users/{owner_id}/memory`。这会把一个 workspace operator 错拆成两个存储身份，并与现有全局 session/workspace 语义冲突。

`FilesystemUserContextResolver` 已在 Part 10 前置修正中改为通过 `use_workspace_context=True` 返回完整 workspace operator 上下文，不创建 Owner root/files 目录，并直接使用 workspace/global sessions。不得自动删除已有目录；已有目录只视为历史残留，后续通过显式检查和清理处理。

### 5.2 MEMORY.md 是长期记忆真值

选择：人可读 Markdown 文件，固定分类和稳定 entry id。

原因：

- 本地项目可直接查看和备份。
- 不需要数据库迁移或外部服务。
- 用户可以知道 Agent 实际记住了什么。
- 条目数量有严格上限，不需要复杂索引。

未选择：

- SQLite：Part 9 SQLite 面向 auth/audit 状态，不应顺手变成私人内容库。
- JSONL：适合 append-only 历史，但长期 Memory 需要明确修改和删除当前条目。
- Vector DB：当前数据规模和检索需求不足以证明依赖成本。

### 5.3 Memory 允许主动候选，但只确认后写入

选择：用户明确要求“记住/修改/忘记”时，模型提出 `memory_write`；模型也可以在正常回答使用的同一次 LLM 调用中，根据当前 session 的高可信长期沟通偏好、工作风格或项目约束主动提出候选。所有 LLM 发起的 Memory 修改统一进入 confirmation broker，由当前用户批准具体内容后才执行，绝不静默写入。

原因：

- 完全依赖用户主动说“记住”会使风格和反复纠正难以沉淀。
- 主动候选可以降低用户维护成本，同时确认仍能阻止模型推测被直接固化。
- 普通聊天里的事实可能只是临时上下文。
- 用户需要知道哪些信息会跨 session 保存。
- 降低敏感数据和错误信息长期残留风险。
- 当前 ToolExecutionPolicy 不读取完整 user message，统一确认比关键词判断更可靠。

主动候选约束：

- 仅允许 add/replace `preferences` 或 `constraints`；主动 delete 和其它分类禁止。
- 必须引用当前用户可访问 session 中的真实 user turn：一个明确长期表达，或至少两个一致表达/纠正。
- 同一 session 同一时间最多存在一个未处理 Memory 候选；已有 pending confirmation 时不能创建第二个候选。
- 候选处理完成后，主动候选至少等待 20 个 user turns，并且必须出现 candidate hash 不同、至少一个依据 turn 晚于上次处理的新高可信长期信息，才恢复提示资格；达到 20 turns 不代表必须提示。
- session metadata 只保存 pending confirmation id、候选 hash、处理 turn、decision 和时间，不保存候选明文或用户原话。
- 用户明确要求不受 20-user-turn 冷却限制，但不能绕过同 session 单 pending 和确认要求。
- 主动候选不能中断当前任务；通道无法同时交付回答和确认时应推迟候选。

未选择：每个 turn 结束后额外自动 review/write。该方案会增加 LLM 调用、隐藏成本和生命周期 hook；Part 10 只允许正常回答中的 LLM 顺带提出候选。

### 5.4 通过 Tool 按需读取

选择：ContextBuilder 只加载 Memory 使用规则，不自动注入完整 Memory；模型通过 `memory_read` 查询相关条目。

原因：

- 不让每轮上下文固定膨胀。
- 不把所有私人 Memory 无条件发送给模型 provider。
- ToolResult 可以限制数量和字符数。
- 继续保持 ContextBuilder 的短期 turn 职责清晰。

自动 recall 可以在 Part 16 Agent 运行可靠性与上下文优化阶段基于真实使用数据评估。

### 5.5 Session summary 只显式触发

选择：

```text
/memory summarize [session_id]
```

原因：

- 用户知道何时会发生额外 LLM 调用。
- 可以先检查 session 权限。
- provider 失败不会影响正常聊天 turn。
- 摘要可以删除和重建，JSONL 继续是真值。

未选择：每轮自动更新摘要。该方案会增加隐藏成本，并提前引入后台任务/turn lifecycle hook。

### 5.6 本地检索

选择：分类过滤、规范化子串、token/CJK bigram overlap、更新时间排序。

原因：

- 与当前 Part 7 本地相关性方向一致。
- 确定性强，单元测试稳定。
- 不访问网络，不要求 embedding 模型。
- 对最多 200 条 Memory 足够。

## 6. 数据布局

```text
contexts/
  memory/                         # CLI local operator + Owner Web
    MEMORY.md
    session_summaries/
      {session_id}.md
  users/
    {user_id}/                    # ordinary users only
      files/
      sessions/
      sessions_meta/
      memory/                     # database user
        MEMORY.md
        session_summaries/
          {session_id}.md
```

`contexts/shared/readonly` 不属于 Memory，不允许 Memory Tool 写入。

## 7. 协议与模块

新增：

```text
agent/protocols/memory.py
  MemoryContext
  MemoryEntry
  SessionMemorySummary
  MemoryStore

agent/memory/markdown_store.py
  MarkdownMemoryStore

agent/memory/proposal.py
  MemoryProposalPolicy
  JsonMemoryProposalStateStore

agent/memory/safety.py
  MemorySafetyPolicy

agent/memory/summary.py
  SessionMemorySummaryService

agent/tools/memory.py
  MemoryReadTool
  MemoryWriteTool
```

依赖：

```text
cli/app -> memory service -> protocols
tools   -> memory protocols
core    -> ToolProvider
```

AgentLoop 不 import MemoryStore 具体实现。

Memory Tool 需要可信的 source session/turn，但当前 ToolProvider 协议不把 ToolExecutionContext 传给 Tool。Part 10 由 app/CLI 在 turn 开始前构造绑定 `MemoryContext + session_id + turn_id` 的工具集合，并通过 `tools_override` 交给 AgentLoop；不把最终 entry 的 source ids 暴露成模型参数，也不为了 Memory 修改通用 ToolProvider 协议。主动候选的 `evidence_turn_ids` 可以由模型提供，但必须在绑定的当前 session 中验证为真实 user turns，只用于确认依据，不作为最终 entry source。

## 8. 权限决策

新增：

```text
memory.read.own
memory.write.own
memory.summarize.own
```

默认：

- viewer/developer/admin：自己的 user Memory read/write/summarize。
- owner 与 CLI local operator：全局 workspace Memory read/write/summarize。
- auditor：自己的 read。
- 不增加 any/manage 权限。
- `session.manage.any` 不推导出 Memory 权限。

## 9. 安全决策

- Memory Tool 不接受路径参数。
- 明显 password/token/API key/private key 拒绝写入。
- 完整日志、完整工具输出和大段源码拒绝作为单条 Memory。
- trace/audit 只记录 entry id、category、proposal origin、evidence count、hash、长度和结果，不记录候选明文、proposal reason 或用户原话。
- Memory 内容不写入 auth.sqlite3。
- 格式损坏时拒绝写入，不自动覆盖用户文件。
- 写入采用同目录临时文件和原子 replace。

## 10. 会话摘要决策

摘要读取 actor 已授权的 SessionStore，按完整 turn 处理。

固定输出：

```text
Goal
Decisions
Constraints
Open Items
Relevant Files
```

摘要文件保存：

```text
memory/session_summaries/{session_id}.md
```

约束：

- 使用当前 session 的 call-scoped provider。
- 只处理未被 summarized_through_turn_index 覆盖的 turn。
- stopped/error turn 不总结为完成结果。
- LLM 调用或格式校验失败时保留旧摘要。
- 摘要不是 SessionStore metadata，也不进入 JSONL。

## 11. 变更文件

实际涉及：

```text
agent/protocols/memory.py
agent/memory/__init__.py
agent/memory/markdown_store.py
agent/memory/proposal.py
agent/memory/safety.py
agent/memory/summary.py
agent/tools/memory.py
agent/config.py
agent/protocols/auth.py
agent/app/auth.py
agent/auth/user_context.py
agent/auth/session_access.py
agent/auth/schema.py
agent/auth/confirmation.py
agent/auth/tool_policy.py
agent/protocols/tool.py
agent/tools/__init__.py
agent/tools/scoped.py
agent/core/context.py
agent/core/loop.py
agent/cli.py
agent/app/runtime.py
agent/app/api/schemas.py
agent/app/api/routes.py
web/static/index.html
web/static/app.js
web/static/styles.css
prompts/memory_policy.md
prompts/memory_summary.md
tests/unit_test/memory/
tests/unit_test/tools/
tests/unit_test/auth/
tests/unit_test/app/
tests/unit_test/cli/
```

## 12. 测试方案

核心矩阵：

| 主题 | 关键检查 |
| --- | --- |
| path | CLI/Owner 共用全局、普通用户隔离、非法 id 拒绝 |
| store | initialize/add/replace/delete/duplicate/malformed/atomic/limit |
| retrieval | category、中文相关性、空结果、输出上限 |
| proposal | explicit/inferred、证据校验、类别限制、同 session 单 pending、处理后 20-turn 冷却、新信息判断、拒绝后 7 天抑制、编辑重新确认 |
| safety | secret、超长内容、完整日志拒绝且不泄露 |
| permission | read/write/summarize own，auditor 只读，无 cross-user |
| tools | 参数校验和结构化 ToolResult |
| summary | 当前/指定 session、增量、无权限、provider 失败、格式失败 |
| regression | Session JSONL、ContextBuilder、Tool、Skill、auth/audit 不回归 |

默认测试使用 Fake LLM，不访问真实模型或网络。

## 13. 验收标准

1. Part 10 活文档明确 Memory 与 Session、ContextBuilder、Tool 和用户权限的边界。
2. CLI 与 Owner 共用全局 Memory，普通用户使用各自目录；Owner 不创建 `contexts/users/{owner_id}`。
3. 长期 Memory 使用可读 Markdown 和稳定 entry id。
4. 用户明确请求或 LLM 高可信主动候选都可以发起修改，但只有当前用户确认后才写入；没有静默写入或自动 turn-end review。
5. 读取有界且不依赖 vector database。
6. session summary 显式触发，失败不影响聊天或覆盖旧摘要。
7. 没有跨用户 Memory 管理权限。
8. trace/audit 不保存完整 Memory 内容。
9. AgentLoop 不依赖 Markdown、用户库或业务记忆规则。
10. 主动候选有类别、证据、同 session 单 pending、处理后 20-turn 冷却、新信息、去重和拒绝抑制边界，且候选明文不进入 session metadata、trace 或 audit。
11. 设计文件、变更文件、测试矩阵和实现顺序足以直接进入代码开发。
