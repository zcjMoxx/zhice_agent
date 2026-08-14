# LLM Provider 单元测试用例

## 测试目标

- `LLMProvider` 的调用级严格 JSON Schema 能被 OpenAI-compatible、LiteLLM 和 failover 链完整透传；未指定 response format 的普通 AgentLoop 调用保持兼容。
- 调用级 `LLMGenerationOptions.temperature` 能覆盖 OpenAI/LiteLLM endpoint 默认值并穿过 failover，未指定时继续使用 endpoint 配置且不改变普通 AgentLoop 行为。

验证 LLMProvider 边界稳定：AgentLoop 只依赖协议，OpenAIProvider 与 LiteLLMProvider 负责各自 OpenAI-compatible HTTP 请求、响应归一化和错误脱敏。

## 用例覆盖

### Case 1: 发送 chat completions 请求

- 输入：system、assistant、user messages。
- 预期：向 `/chat/completions` 发送模型、消息、max_tokens、temperature。
- 检查点：过滤未知 message 字段；空 assistant tool-call content 转为 `None`；空普通 content 转为 `(empty)`。

### Case 2: API key 处理

- 输入：endpoint 中包含或缺失 `api_key`。
- 预期：有 key 时写入 Bearer Authorization；缺失 key 时请求前失败。
- 检查点：错误提示指向`models.json`。

### Case 3: tools 请求体

- 输入：`tools=None`、空列表、非空工具 schema。
- 预期：只有非空 tools 会写入请求体。
- 检查点：不会把空 tools 发送给 provider。

### Case 4: 响应归一化

- 输入：OpenAI-compatible 原始响应。
- 预期：返回 `LLMResponse`。
- 检查点：保留 content、reasoning_content、usage、finish_reason 和多个 tool_calls 的顺序。

### Case 5: HTTP 错误脱敏

- 输入：HTTPError 响应体中包含 API key。
- 预期：抛出安全的 provider 错误。
- 检查点：错误文本不泄露 secret。

### Case 6: LiteLLMProvider

- 输入：`protocol="litellm"` 的 endpoint。
- 预期：`create_llm_provider` 返回 `LiteLLMProvider`。
- 检查点：调用 `litellm.completion(...)`，模型名、api_key、可选 api_base、tools、tool_calls 与 usage 正常透传。

### Case 7: Provider stable error semantics

- Cover OpenAI HTTP 401/403/404/429/5xx, network failures, and invalid JSON.
- Cover LiteLLM SDK status codes, `Retry-After`, and secret redaction.
- Check `code/http_status/retryable/safe_message/endpoint/model` without response bodies or API keys.

### Case 8: Bounded retry, total deadline, and cooldown

- Retry retryable failures on the same endpoint; fail over non-retryable failures immediately.
- Prefer `Retry-After` over exponential backoff and stop new calls after the total deadline.
- Skip an exhausted endpoint during process-local cooldown and record structured evidence.
- Check successful responses and final errors both carry bounded `provider_attempts` evidence.

### Case 9: EndpointFailoverProvider

- 输入：多个 endpoint，包含 preferred endpoint、不同 priority、disabled endpoint。
- 预期：优先尝试 preferred endpoint；失败后按 priority 尝试其它 enabled endpoint。
- 检查点：成功响应 metadata 记录 endpoint 和 attempted_endpoints；`endpoint/model` 覆盖只允许默认模型或命中 `supported_models` 的模型，且只作用于首选 endpoint；全部失败时错误信息包含 endpoint 摘要且不泄露 secret。

### Case 10: Endpoint request timeout

- 输入：endpoint 配置长响应超时，并分别不传、显式传入 Provider timeout 上限。
- 预期：默认采用 endpoint 的 `request_timeout_seconds`；显式上限取两者较小值。
- 检查点：OpenAI 与 LiteLLM Provider 均不会被隐藏的 60 秒默认值截断。
