# ContextBuilder 单元测试用例

## 测试目标

验证 ContextBuilder 能把 prompt、运行环境、完整 Session Turn、派生 evidence 和当前用户消息组装成 OpenAI-compatible messages，按 endpoint 输入预算治理，并保留完整工具调用块。

## 用例覆盖

### Case 1: 构造 system prompt

- 输入：workspace、session_id 和当前 user message。
- 预期：第一条消息为 system，包含 identity、tool_use_policy、skills_intro 和运行环境；存在 `diagnostics.md` / `exec.md` 时分别追加独立 `Diagnostics Policy` / `Exec Policy`，缺失时不阻断主流程。
- 检查点：workspace 与 session_id 进入 system prompt；当前 user message 追加到最后。

### Case 2: 预算内完整历史

- 输入：多条历史消息以及已废弃的固定 Turn/消息数参数。
- 预期：预算允许时全部保留并保持原顺序，固定数量参数不再主动删除 Session 状态。
- 检查点：当前用户消息不参与历史裁剪，始终追加在末尾。

### Case 3: 历史内容截断

- 输入：超过 `max_message_chars` 的历史消息。
- 预期：内容被截断并带 `[truncated]` 标记。
- 检查点：assistant、tool 等历史内容都受同一长度限制。

### Case 4: 完整 tool 调用块进入上下文

- 输入：历史中存在 `assistant(tool_calls) -> tool` 消息块。
- 预期：完整工具调用块进入 LLM messages。
- 检查点：保留 `assistant.tool_calls`、`tool_call_id` 和可选 `name`。

### Case 5: assistant tool_calls 保留

- 输入：历史中存在带 `tool_calls` 的 assistant 消息。
- 预期：assistant 消息原样保留 `tool_calls`。
- 检查点：后续 provider 能重放完整工具调用轨迹。

### Case 6: 孤立或不完整工具消息过滤

- 输入：历史裁剪后只剩 `tool` 消息，或只剩未配对 tool 结果的 `assistant(tool_calls)`。
- 预期：不合法的工具历史块不会进入 LLM messages。
- 检查点：OpenAI-compatible provider 不会收到孤立 `tool` 消息。

### Case 7: prompt 缺失

- 输入：缺少必需 prompt 文件。
- 预期：构造上下文时抛出可定位的错误。
- 检查点：错误信息包含缺失 prompt 名称。

## Part 15 Coverage

- 预算允许时完整 Session Turn 全量进入上下文，不受旧 `max_history_turns` / `max_history_messages` 固定数量策略影响。
- 历史过长时由 ContextPlan 装配 compaction、deterministic history evidence、retrieved old Turn 和连续 recent raw Turn。
- 无 `turn_id` 的旧 Session 消息按 user 边界懒推导稳定 Turn，不丢弃历史真值。
- 每次 Tool result 后重新执行 failover-safe budget fit；删除以完整 Turn/tool block 为原子。
- 保留 OpenAI-compatible tool-call block 过滤、tool result 截断和固定区超预算错误。
