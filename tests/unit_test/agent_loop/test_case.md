# AgentLoop 单元测试用例

## 测试目标

验证 AgentLoop 只依赖协议接口完成一轮对话、错误保存和工具调用闭环，不直接依赖具体 LLM SDK 或具体工具实现。

## 用例覆盖

### Case 1: 普通无工具对话

- 输入：Fake LLM 返回普通 assistant 文本。
- 预期：返回 assistant 文本，并按 `user -> assistant` 顺序写入 Session。
- 检查点：LLM 通过 `LLMProvider.chat` 调用；无工具时 `tools=None`。
- 动态 ToolProvider 每次 LLM 调用前重新读取 definitions；`discover_tools` 激活后，下一模型步只新增被选中的业务 schema。

### Case 2: 历史上下文传递

- 输入：Session 中已有历史消息，当前用户输入一条新消息。
- 预期：历史消息交给 ContextBuilder，当前消息不提前写入历史。
- 检查点：ContextBuilder 收到原始 history、当前 user message、workspace 和 session_id。
- Part 15 ContextBuilder 还接收当前已授权 SessionStore、实际 LLMProvider、可见 Tool schemas 和 failover-safe ContextBudget；FakeContextBuilder 兼容旧最小签名。

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
- 默认值：25；一轮 assistant 中的多个 tool call 只计为一次工具决策。
- 预期：停止新工具执行，保存上限错误 marker，并让模型在无工具模式下总结已收集证据。
- 检查点：仍为未执行的 tool_call 生成配对 `tool` 错误消息；最终模型调用不得携带 tools，失败时返回带实际限制值的 fallback。

### Case 8: Session 保存失败

- 输入：SessionStore append 抛出写入错误。
- 预期：保留 LLM 结果文本，同时把保存失败原因追加给用户。

## Part 7 Turn Coverage

- Generate a stable turn id and 1-based turn index for CLI-style calls.
- Reuse externally provided turn ids for Web/runtime calls.
- Stamp user, assistant, tool, error, stopped, and tool-iteration-limit messages with the same turn fields.

## Part 12 RuntimeEvent 与 Hook Coverage

- 无 Tool Turn 发出 turn/context/LLM 的完整 RuntimeEvent 顺序，sequence 在同一 Turn 内单调递增。
- Tool Turn 发出 started/completed/failed/waiting_confirmation，并在 Tool 结果后重新进入 LLM 状态。
- RuntimeEvent sink 异常不改变 Session 或最终回答；RuntimeEvent 不写入 Session Message。
- pre Hook block 只增加限制；modify 后重新通过 Tool schema，policy、确认 broker 和 Tool 收到最终参数。
- admin 的有效权限显式命中单 Hook `exempt_permissions` 时不启动 Runner，但 admin 仍继续经过核心 policy、危险确认和具体 Tool guard。
- post Hook 只增强最终 Tool Event 的 display/ui_metadata，不改变 ToolResult 或成功失败状态。
- Keep Fake LLM tests deterministic while covering normal, error, streaming, cancellation, and tool paths.
- Part 18 覆盖 `load_skills -> run_skill -> skill.* -> ToolResult`；瞬态进度不写 Session，最终 Tool 调用事实保留。

## Part 8 Logging Coverage

- AgentLoop emits concise INFO lifecycle logs for `turn.start` and `turn.done`; `turn.done.output_preview` keeps only the first non-empty answer line up to 80 characters in terminal and trace. `llm.call` and `llm.done` remain at DEBUG, while duplicate `llm.direct` and successful `session.save` events are omitted.
- Tool dispatch emits `tool.start` and `tool.done` with `session_id`, `turn_id`, tool name, success flag, duration, and safe output preview.
- Lifecycle log fields must not leak secret-like values or full long user/tool content.
- 每次初始和 Tool 后 LLM 调用前记录安全的 `context.selection`，包含 phase、mode、数量与 token 估算，不记录完整消息或 embedding。

## Part 9 Tool Policy Coverage

- Tool policy 拒绝时不调用工具，但仍写入结构化 activity；安全拒绝继续写 audit。
- 普通 turn 和安全工具成功只写 Runtime Activity/trace，不进入 Security Audit；危险工具、确认、Memory 写入和 Skill 同步继续审计。
- LLM trace 增加带 Session/Turn/request 关联的 `llm.done` / `llm.error duration_ms`，用于自助性能诊断。
- 确认通过后才执行工具，拒绝、超时或取消均不得执行。
- `llm_override` 只作用于当前 turn，不修改 AgentLoop 默认 provider。
