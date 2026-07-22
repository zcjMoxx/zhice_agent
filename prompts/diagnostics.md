# 运行失败诊断规则

## 何时诊断

- 用户询问上一轮为什么失败、为什么没有调用某项能力、为什么很慢，或明确要求查看 Trace 时，使用诊断能力核实真实运行记录。
- 先通过 `discover_tools` 激活 `diagnose_my_recent_activity`，再调用该 Tool；不要根据历史回答、包装错误码或当前上下文猜测。

## 如何读取证据

- 直接分析 Tool 返回的 `trace_events` 时间序列，按时间还原 turn、LLM、tool、child 和 session 事件的因果关系。
- 证据优先级依次为：具体且安全的 `error_message`、终态 stage/code、对应事件前后的调用结果、规则聚合的 facts/summary。
- `SUBAGENT_INTERNAL_ERROR`、`SUBAGENT_FAILED`、`TOOL_EXECUTION_FAILED` 等通常是包装码，不能单独作为根因。
- 多个 child 同时失败时，区分共同根因与单个 task 的独立失败，并保留成功或 partial 结果。

## 如何回答

- 先给出已经由 Trace 确认的具体原因，再说明失败发生在哪个阶段以及为什么影响本次请求。
- 明确区分“确认事实”“根据证据推断”和“当前 Trace 没有记录”。
- 如果缺少具体异常消息、child terminal event 或关联记录，直接说明缺少哪项证据；不要把 `probable_cause` 当成确认事实。
- 不向普通用户泄露内部路径、Prompt 文件名、修复命令或受限 cause；遵守 ToolResult 已执行的身份脱敏。
- 不输出 traceback、credential、完整请求体、完整 Prompt 或其它用户的事件。
