# ZhiCe-Agent Endpoint 预算配置简化设计

> 日期：2026-07-22
>
> 状态：已实现并完成当前本地工作区迁移
>
> 承接：`2026-07-22-endpoint-context-budget-and-hybrid-turn-selection-design.md`

## 1. 背景

讨论中曾考虑把总窗口、可选输入上限和输出上限全部暴露为 endpoint 配置，虽然表达完整，但第一阶段实际只需要总窗口和输出上限。额外输入上限没有发送给模型接口，当前 endpoint 也没有独立于总窗口的输入限制，继续保留只会增加理解成本。

`max_output_tokens` 虽然语义清晰，但与参考项目、LiteLLM 和当前 OpenAI-compatible 请求使用的 `max_tokens` 不一致，造成了额外映射解释。当前阶段恢复单一 `max_tokens` 即可，并在文档中明确它是单次最大输出。

## 2. 最终配置

```json
{
  "context_window": 131072,
  "max_tokens": 16384
}
```

- `context_window`：可选，总上下文窗口；缺失时默认 `131072`。
- `max_tokens`：可选，单次最大输出 token；默认 `4096`，并直接用于 Provider 请求。
- 不再提供 `max_input_tokens`。

本地有效输入预算固定为：

```text
input_token_limit = context_window - max_tokens
```

`ContextBudget` 内部字段使用 `input_token_limit`，避免把派生值误认为 endpoint 配置字段。

## 3. 工作区迁移

- 把当前工作区的 `max_output_tokens` 改回 `max_tokens`。
- 删除可能存在的 `max_input_tokens`。
- 保留已经核实的显式窗口：`deepseek=131072`，三个 CPA endpoint 为 `200000`。
- 其他 endpoint 未声明 `context_window` 时由加载器使用默认 `131072`。
- 迁移前创建同目录时间戳备份，不修改密钥、模型、路由和优先级。

## 4. 变更范围

- LLM 协议、配置加载、CLI init、Provider adapter 和 Failover budget。
- ContextBuilder、AgentLoop、Subagent、Memory extraction 的内部预算字段。
- 模板、README、总体设计、Part 2/7、测试与实际工作区配置。

## 5. 验收

- 仓库和当前工作区 endpoint 配置只出现 `context_window` 与 `max_tokens` 两个预算字段。
- 缺失 `context_window` 时加载为 `131072`。
- ContextBudget 使用 `input_token_limit=context_window-max_tokens`。
- 全量 Ruff、pytest、JS 检查和 `git diff --check` 通过。

## 6. 实施结果

- `LLMEndpoint` 恢复 `max_tokens`，`context_window` 默认 `131072`。
- endpoint 配置、CLI init 和模板不再提供独立输入上限字段。
- `ContextBudget` 使用内部派生名 `input_token_limit`，值固定为 failover 候选中最小的 `context_window - max_tokens`。
- 当前工作区已迁移并通过真实加载器验证：`deepseek` 输入预算为 `114688`，三个 CPA endpoint 输入预算为 `183616`。
- 迁移前文件已在实际 workspace config 目录创建时间戳备份。
