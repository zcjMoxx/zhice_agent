# ContextBuilder 单元测试用例

## 测试目标

验证 ContextBuilder 能把 prompt、运行环境、混合 Turn 历史和当前用户消息组装成 OpenAI-compatible messages，按 endpoint 输入预算裁剪，并保留完整工具调用块。

## 用例覆盖

### Case 1: 构造 system prompt

- 输入：workspace、session_id 和当前 user message。
- 预期：第一条消息为 system，包含 identity、tool_use_policy、skills_intro 和运行环境；存在 `diagnostics.md` / `exec.md` 时分别追加独立 `Diagnostics Policy` / `Exec Policy`，缺失时不阻断主流程。
- 检查点：workspace 与 session_id 进入 system prompt；当前 user message 追加到最后。

### Case 2: 历史消息顺序

- 输入：多条历史消息和 `max_history_messages` 限制。
- 预期：只保留最近的历史消息，并保持原顺序。
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

## Part 7 Turn Coverage

- Treat `max_history_turns` as the recent user-turn candidate count.
- Default to 50 recent user-turn candidates and keep at most 5 relevant turns.
- Select only locally relevant candidate turns before injecting history.
- Omit unrelated prior turns, including greeting-only current inputs.
- Keep direct follow-ups when the current input references terms from a previous full turn.
- Keep short confirmations only when the immediately previous assistant message is asking for confirmation.
- Keep the immediately previous Turn for short Chinese contextual follow-ups such as “为什么没调用，什么原因”, even when bigram overlap alone is below the relevance threshold.
- Keep the immediately previous Turn for explicit meta-references such as “我刚刚问了什么”“上一轮我问的是什么” and “What did I just ask?”, without making latest-Turn inclusion unconditional.
- Preserve the old message-count behavior when `max_history_turns=None`.
- Drop old turns as whole units when the message hard cap is exceeded.
- Ignore untagged history messages in recent user turn selection.
- Keep OpenAI-compatible tool-call block filtering after turn selection.
