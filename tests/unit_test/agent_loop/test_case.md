# AgentLoop 单元测试用例

## 测试目标

验证 AgentLoop 只依赖协议接口完成一轮对话、错误保存和工具调用闭环，不直接依赖具体 LLM SDK 或具体工具实现。

## 用例覆盖

### Case 1: 普通无工具对话

- 输入：Fake LLM 返回普通 assistant 文本。
- 预期：返回 assistant 文本，并按 `user -> assistant` 顺序写入 Session。
- 检查点：LLM 通过 `LLMProvider.chat` 调用；无工具时 `tools=None`。

### Case 2: 历史上下文传递

- 输入：Session 中已有历史消息，当前用户输入一条新消息。
- 预期：历史消息交给 ContextBuilder，当前消息不提前写入历史。
- 检查点：ContextBuilder 收到原始 history、当前 user message、workspace 和 session_id。

### Case 3: LLM 调用失败

- 输入：LLMProvider 抛出配置错误、请求错误或未知异常。
- 预期：保存 `user -> assistant(error marker)`，返回可读错误信息。
- 检查点：不泄露 secret；缺少 API key 和缺少环境变量时给出明确修复提示。

### Case 4: 单工具调用

- 输入：Fake LLM 第一次返回一个 `tool_call`，第二次返回最终 assistant 文本。
- 预期：AgentLoop 执行工具，把工具结果作为 `tool` 消息回填，再调用 LLM 生成最终回答。
- 检查点：Session 顺序为 `user -> assistant(tool_calls) -> tool -> assistant(final)`。

### Case 5: 多工具调用

- 输入：同一条 assistant 消息请求多个工具。
- 预期：按模型返回顺序串行执行并全部回填。
- 检查点：每条 `tool` 消息保留对应 `tool_call_id`。

### Case 6: 工具错误与坏参数

- 输入：工具返回 `ToolResult(is_error=True)`，或模型返回非法 JSON 参数。
- 预期：错误被包装成结构化 `tool` 消息交回 LLM，不让 AgentLoop 崩溃。
- 检查点：错误码进入 tool payload 和 message metadata。

### Case 7: 工具轮数上限

- 输入：LLM 连续请求工具超过 `max_tool_iterations`。
- 预期：停止循环，保存上限错误 marker。
- 检查点：仍为未执行的 tool_call 生成配对 `tool` 错误消息，避免历史不完整。

### Case 8: Session 保存失败

- 输入：SessionStore append 抛出写入错误。
- 预期：保留 LLM 结果文本，同时把保存失败原因追加给用户。

## Part 7 Turn Coverage

- Generate a stable turn id and 1-based turn index for CLI-style calls.
- Reuse externally provided turn ids for Web/runtime calls.
- Stamp user, assistant, tool, error, stopped, and tool-iteration-limit messages with the same turn fields.
- Keep Fake LLM tests deterministic while covering normal, error, streaming, cancellation, and tool paths.
