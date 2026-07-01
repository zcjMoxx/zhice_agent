# CLI 会话命令设计

## 背景

2026-06-12 的临时改动曾让 `zcagent` 在每次启动时默认新建会话，但这并不符合智策 Agent 预期的本地聊天工作流：

- 普通启动应该回到稳定的默认会话
- 用户应显式决定何时新开会话
- 用户应能在 CLI 内查看已保存的会话

这次改动会影响 CLI 行为、Session 持久化协议、JSONL SessionStore 以及用户文档，因此需要独立设计记录。

## 目标

- 将默认聊天会话调整为按天生成的 `chat-YYYYMMDD`，避免长期 `default` 历史污染普通问候和短对话。
- 新增 `/new`，用于创建并切换到新会话。
- 新增 `/reset`，用于清空当前会话历史。
- 新增 `/sessions`，用于列出已保存会话，并显示基于首条 user 消息的简短预览。

## 范围边界

本次包含：

- CLI slash 命令
- SessionStore 协议扩展
- JSONL 会话列举与清空能力
- 测试与 README 更新

本次不包含：

- 在 CLI 内切换到任意历史会话
- 会话重命名、删除、置顶、归档
- GUI 会话列表展示

## 模块设计

### `agent/protocols/session.py`

新增：

- `SessionSummary`
- `SessionStore.clear(session_id)`
- `SessionStore.list_sessions()`

这样 CLI 仍然依赖抽象协议，而不是直接依赖 JSONL 存储细节。

### `agent/session/jsonl_store.py`

新增：

- `clear(session_id)`：如果存在则删除对应 session JSONL 文件
- `list_sessions()`：扫描 `contexts/sessions/*.jsonl`，提取：
  - `session_id`
  - 首条 `user` 消息作为预览
  - 最后一条消息时间戳作为 `updated_at`
  - `message_count`

会话列表按 `updated_at` 倒序排列。

### `agent/cli.py`

启动逻辑：

- `--session` 默认值为自动生成的当天 session：`chat-YYYYMMDD`

slash 命令：

- `/help`：打印可用命令
- `/new`：生成新 session id 并切换当前会话
- `/reset`：清空当前会话文件
- `/sessions`：打印已保存会话及简短预览
- 保留既有 `/history`、`/prompts`、`/exit`

对 `/sessions`，如果当前会话尚未持久化，也应把它作为一个内存中的空项显示出来，避免用户不知道自己当前处于哪个会话。

## 数据流

1. 用户执行 `zcagent`
2. CLI 默认进入 `chat-YYYYMMDD`，除非显式传入 `--session`
3. `/new` 更新当前活动 `session_id`
4. 正常聊天轮次写入当前 session JSONL
5. `/reset` 删除当前 session JSONL 文件
6. `/sessions` 读取 Store 摘要并打印

## 影响文件

- `agent/protocols/session.py`
- `agent/session/jsonl_store.py`
- `agent/cli.py`
- `tests/unit_test/session_store/test_session_store.py`
- `tests/unit_test/cli/test_cli_init.py`
- `README.md`

## 测试方案

- 单元测试：默认启动写入当天 session，且普通聊天不打印 workspace/session 横幅
- 单元测试：`/new` 能切换到新会话
- 单元测试：`/reset` 能清空已持久化会话
- 单元测试：`/sessions` 能打印预览信息
- 单元测试：SessionStore 的 `clear()` 能删除文件
- 单元测试：SessionStore 的 `list_sessions()` 能正确排序并生成预览

## 验收标准

- `zcagent` 不再默认每次启动都新建会话
- `/new` 能创建并切换新会话
- `/reset` 能清空当前会话
- `/sessions` 能列出已保存会话及简短预览
