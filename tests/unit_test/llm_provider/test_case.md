# LLM Provider 单元测试用例

## 测试目标

- `LLMProvider` 的调用级严格 JSON Schema 能被 OpenAI-compatible、LiteLLM 和 failover 链完整透传；未指定 response format 的普通 AgentLoop 调用保持兼容。
- 调用级 `LLMGenerationOptions.temperature` 能覆盖 OpenAI/LiteLLM endpoint 默认值并穿过 failover，未指定时继续使用 endpoint 配置且不改变普通 AgentLoop 行为。

验证 LLMProvider 边界稳定：AgentLoop 只依赖协议，OpenAIProvider 与 LiteLLMProvider 负责各自 OpenAI-compatible HTTP 请求、响应归一化和错误脱敏。

## 用例覆盖

### 用例 1: 发送 chat completions 请求

- 输入：system、assistant、user messages。
- 预期：向 `/chat/completions` 发送模型、消息、max_tokens、temperature。
- 检查点：过滤未知 message 字段；空 assistant tool-call content 转为 `None`；空普通 content 转为 `(empty)`。

### 用例 2: API key 处理

- 输入：endpoint 中包含或缺失 `api_key`。
- 预期：有 key 时写入 Bearer Authorization；缺失 key 时请求前失败。
- 检查点：错误提示指向`models.json`。

### 用例 3: tools 请求体

- 输入：`tools=None`、空列表、非空工具 schema。
- 预期：只有非空 tools 会写入请求体。
- 检查点：不会把空 tools 发送给 provider。

### 用例 4: 响应归一化

- 输入：OpenAI-compatible 原始响应。
- 预期：返回 `LLMResponse`。
- 检查点：保留 content、reasoning_content、usage、finish_reason 和多个 tool_calls 的顺序。

### 用例 5: HTTP 错误脱敏

- 输入：HTTPError 响应体中包含 API key。
- 预期：抛出安全的 provider 错误。
- 检查点：错误文本不泄露 secret。

### 用例 6: LiteLLMProvider

- 输入：`protocol="litellm"` 的 endpoint。
- 预期：`create_llm_provider` 返回 `LiteLLMProvider`。
- 检查点：调用 `litellm.completion(...)`，模型名、api_key、可选 api_base、tools、tool_calls 与 usage 正常透传。

### 用例 7：Provider 稳定错误语义

- 覆盖 OpenAI HTTP 401/403/404/429/5xx、网络失败和非法 JSON。
- 覆盖 LiteLLM SDK 状态码、`Retry-After` 和 Secret 脱敏。
- 检查 `code/http_status/retryable/safe_message/endpoint/model`，不得包含响应正文或 API Key。

### 用例 8：有界重试、总截止时间与冷却

- 可重试失败在同一 endpoint 上重试；不可重试失败立即切换 endpoint。
- `Retry-After` 优先于指数退避，到达总截止时间后停止发起新调用。
- 在进程内冷却期间跳过已耗尽的 endpoint，并记录结构化证据。
- 检查成功响应和最终错误都携带有界的 `provider_attempts` 证据。

### 用例 9: EndpointFailoverProvider

- 输入：多个 endpoint，包含 preferred endpoint、不同 priority、disabled endpoint。
- 预期：优先尝试 preferred endpoint；失败后按 priority 尝试其它 enabled endpoint。
- 检查点：成功响应 metadata 记录 endpoint 和 attempted_endpoints；`endpoint/model` 覆盖只允许默认模型或命中 `supported_models` 的模型，且只作用于首选 endpoint；全部失败时错误信息包含 endpoint 摘要且不泄露 secret。

### 用例 10：Endpoint 请求超时

- 输入：endpoint 配置长响应超时，并分别不传、显式传入 Provider timeout 上限。
- 预期：默认采用 endpoint 的 `request_timeout_seconds`；显式上限取两者较小值。
- 检查点：OpenAI 与 LiteLLM Provider 均不会被隐藏的 60 秒默认值截断。
