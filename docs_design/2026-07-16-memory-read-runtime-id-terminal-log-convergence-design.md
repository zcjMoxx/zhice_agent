# Memory 读取、运行 ID 与终端日志收敛设计

> 说明：本文的 `memory_read(mode=session_summary)` 已随未闭环 Session Summary 能力删除；当前 `memory_read` 只保留 `list/search`，参考 `docs_design/2026-07-16-remove-unclosed-session-summary-design.md` 和 Part 10 活文档。

## 背景

Part 10 Memory 已能把普通用户的长期信息写入其私有 `MEMORY.md`，但真实运行中暴露出两个问题：

- 用户询问“我的记忆有什么”时，模型可能不调用 `memory_read`，或者使用泛化自然语言查询，导致已经写入的记忆返回 0 条。
- Agent 终端日志直接展开结构化 trace 字段，`session_id`、`turn_id`、`request_id`、`tool_call_id` 和 `actor_user_id` 同时出现，工具名和执行结果反而不突出。

同时，运行索引表 `turn_runs` 既保存全局唯一的 `turn_id`，又额外生成 `turn-run-*` 主键。当前没有其他表引用 `turn_runs.id`，该字段属于重复身份。

## 目标

1. 为 `memory_read` 提供明确的 `list` / `search` 模式。
2. Memory 列表按固定类别整理返回，并包含数量与是否还有后续结果。
3. 将系统核心运行身份收敛为 `user_id -> session_id -> turn_id`。
4. 删除 `turn_runs.id`，直接以 `turn_id` 作为主键。
5. WebSocket 对话不再把由 `turn_id` 派生的 `ws-{turn_id}` 继续传播为 Agent `request_id`。
6. 终端突出显示 Tool 名称、阶段、用户、轮次和结果；完整关联字段继续写入 JSONL trace。

## 范围边界

本次包含：

- `memory_read` Tool schema、执行结果和 Memory Prompt。
- Markdown Memory 的分页读取与数量统计。
- `turn_runs` 新表结构及当前代码写入逻辑。
- Agent Tool 终端日志的人读格式与字段白名单。
- WebSocket 对话到 AgentRuntime 的重复 `request_id` 清理。
- 单元测试、Part 8 / Part 10 活文档和总体设计同步。

本次不包含：

- 删除 `user_id`、`session_id`、`turn_id`。
- 删除 LLM 消息协议要求的 `tool_call_id`。
- 删除鉴权使用的 `auth_session_id`、危险操作使用的 `confirmation_id` 或数据库内部 Tool/Audit 记录主键。
- 旧 `auth.sqlite3` 表结构兼容、自动迁移或双写。
- 自动删除仓库外现有 workspace 运行数据库。

## 模块设计

### 1. Memory 读取模式

`memory_read` 增加必填 `mode`：

- `list`：不依赖查询词，按类别列出当前用户的长期 Memory。
- `search`：要求非空 `query`，只返回与具体事实相关的 Memory。

`mode=session_summary` 读取指定 Session 摘要；长期 Memory 的列表和搜索仍受 `limit`、字符上限和可选 `offset` 约束。

长期 Memory 返回：

```json
{
  "mode": "list",
  "entries": [],
  "categories": {
    "profile": [],
    "preferences": [],
    "projects": [],
    "constraints": [],
    "decisions": []
  },
  "total": 0,
  "returned": 0,
  "offset": 0,
  "has_more": false
}
```

用户询问全部记忆时，Prompt 必须要求模型调用 `mode=list`，禁止直接根据对话上下文回答 0 条。

### 2. 核心 ID 模型

运行主链只保留：

```text
user_id
  -> session_id
      -> turn_id
```

`turn_index` 是同一 Session 内的人读序号，不是稳定身份。

局部 ID 继续由所属模块管理：

- `auth_session_id`：鉴权登录态。
- `request_id`：HTTP 请求与错误响应。
- `connection_id`：WebSocket 连接。
- `tool_call_id`：LLM Tool 消息配对。
- `tool_call_record_id` / `audit_id`：运行索引和安全审计存储内部主键。
- `confirmation_id`：危险操作确认。

WebSocket 一轮对话已经有 `turn_id`，不再向 AgentRuntime 传播内容相同的 `ws-{turn_id}`。

### 3. `turn_runs` 新结构

`turn_runs` 直接使用：

```sql
turn_id TEXT PRIMARY KEY
```

`session_id` 继续用于会话内查询，不再生成 `turn-run-*`。写入冲突目标改为 `turn_id`。

本地项目不提供旧表兼容。已有 workspace `state/auth.sqlite3` 需要由开发者明确重建；代码不会自动删除运行数据。

### 4. 终端日志

JSONL trace 继续保存完整结构化字段。终端 formatter 使用事件专属展示，不再展开所有 `record.fields`。

Tool 示例：

```text
[2026-07-16 13:35:23] | INFO | TOOL memory_read | START | user=user001 turn=5
[2026-07-16 13:35:23] | INFO | TOOL memory_read | DONE | user=user001 turn=5 duration=11ms matches=1
```

规则：

- Tool 名进入动作段并使用 Tool 颜色。
- `tool_call_id`、`request_id`、完整 `session_id` / `turn_id` 不在普通 Tool 终端行展示。
- `actor_user_id` 在终端替换为 `username`。
- Memory 终端日志只展示数量、分类、操作和错误码，不展示 Memory 内容。
- 失败事件展示 `FAILED`、错误码和必要的人读上下文。
- 非 Tool 事件默认隐藏底层记录 ID；Session 生命周期可以保留其目标 `session_id`。

## 数据流

```text
用户问题
  -> LLM 判断 list / search
  -> memory_read
  -> MarkdownMemoryStore 分页读取与计数
  -> ToolResult 结构化分类
  -> LLM 整理为自然语言回答

Web / CLI Turn
  -> session_id + turn_id
  -> AgentLoop Tool dispatch
  -> terminal: 人读摘要
  -> trace.log: 完整结构化关联字段
  -> Runtime Activity: turn_id 主键的 turn_runs
```

## 变更文件

- `agent/protocols/memory.py`
- `agent/memory/markdown_store.py`
- `agent/tools/memory.py`
- `prompts/memory_policy.md`
- `agent/auth/schema.py`
- `agent/auth/store.py`
- `agent/app/api/ws.py`
- `agent/app/logging.py`
- `agent/core/loop.py`
- 对应单元测试与活文档

## 测试方案

1. `memory_read(mode=list)` 能返回按五类整理的现有记忆。
2. `memory_read(mode=search)` 必须有具体 query，能命中中文 Memory。
3. 列表返回 `total/returned/offset/has_more`。
4. 新建 SQLite store 后 `turn_runs` 不包含 `id` 列，`turn_id` 为主键。
5. 同一 `turn_id` 的 start/done 正确更新，不生成 `turn-run-*`。
6. Tool 终端日志突出 `TOOL memory_read`，不出现完整关联 ID。
7. JSONL trace 仍保存完整 `session_id`、`turn_id` 和 `tool_call_id`。
8. WebSocket Chat 调用不向 AgentRuntime 传播派生的 `ws-{turn_id}` request id。
9. 运行 `python -m ruff check .` 和 `python -m pytest`。

## 验收标准

- 已写入“喜欢吃西瓜”后，询问“我的记忆有什么”能通过 `mode=list` 返回该条记忆。
- 终端 Tool 日志不再出现一整排完整 ID，工具名和状态一眼可见。
- 系统当前设计文档只把 `user_id/session_id/turn_id` 作为 Agent 主链身份。
- `turn_runs` 不再存在重复的 `id` 字段。
- 不增加旧 SQLite 表结构兼容代码。
