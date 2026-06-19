# ZhiCe-Agent LiteLLMProvider 设计

## 背景

当前 `LLMProvider` 边界已经稳定，`OpenAIProvider` 负责直连 OpenAI-compatible Chat Completions 接口。此前 `protocol="litellm"` 已经可以通过配置层解析，但 provider 工厂会明确报“暂未实现”。

用户现在需要通过 LiteLLM 接入 Anthropic、Gemini、DeepSeek 等非 OpenAI 模型商。

## 目标

- 新增 `LiteLLMProvider`。
- 不改 `LLMProvider` 协议。
- 不让 `AgentLoop` 感知具体模型商。
- 新增 `litellm` Python 运行时依赖。
- 第一版支持进程内 LiteLLM SDK 调用，不要求用户额外启动 LiteLLM Proxy。

## 方案

第一版 `LiteLLMProvider` 走进程内 LiteLLM SDK：

```text
ZhiCe-Agent
  -> LiteLLMProvider
  -> litellm.completion(...)
  -> Anthropic / Gemini / DeepSeek / other provider
```

`LiteLLMProvider` 复用 OpenAI-compatible 的消息清洗规则和 tools schema，随后把请求参数交给 `litellm.completion(...)`。模型名由用户配置为 LiteLLM 识别的格式，例如：

```json
{
  "claude": {
    "protocol": "litellm",
    "provider": "anthropic",
    "api_key": "${ANTHROPIC_API_KEY}",
    "model": "claude-sonnet-4"
  }
}
```

## OpenAIProvider 与 LiteLLMProvider 的区别

两者都实现 `LLMProvider` 协议，但调用方式不同：

```text
OpenAIProvider
  -> urllib HTTP POST {base_url}/chat/completions

LiteLLMProvider
  -> litellm.completion(...)
```

`protocol="litellm"` 不再要求外部 LiteLLM Proxy。`base_url` 在 LiteLLM SDK 模式下变成可选字段：

- 不填 `base_url`：LiteLLM SDK 按模型前缀和 `api_key` 调真实模型商。
- 填 `base_url`：作为 `api_base` 传给 LiteLLM SDK，用于公司内部网关、自建 OpenAI-compatible 网关或其它自定义服务。

因此：

- 公司内部网关、OpenRouter、DeepSeek 这类已经提供 OpenAI-compatible `/v1/chat/completions` 的地址，优先配置为 `protocol="openai"`。
- Anthropic、Gemini、DashScope、Groq、Ollama 等希望交给 LiteLLM 统一适配的模型商，配置为 `protocol="litellm"`。
- 厂商原生 API 地址通常不需要填到 `base_url`；除非 LiteLLM 对该模型商明确要求自定义 `api_base`。

`provider` 只用于 LiteLLM 模型供应商前缀，`model` 保持未加前缀的模型名。例如：

```json
{
  "claude": {
    "protocol": "litellm",
    "provider": "anthropic",
    "api_key": "${ANTHROPIC_API_KEY}",
    "model": "claude-sonnet-4"
  }
}
```

加载后 `LLMEndpoint.model` 仍是 `claude-sonnet-4`，实际调用 LiteLLM SDK 时才拼成 `anthropic/claude-sonnet-4`。

## 边界

本次只通过 LiteLLM SDK 聚合模型商，不直接引入 Anthropic/Gemini/DeepSeek 等各家 SDK。

暂不实现 LiteLLM Proxy 管理能力。也就是说，ZhiCe-Agent 不负责启动、停止或配置 `litellm --proxy` 服务；如果用户自己已经有 LiteLLM Proxy，也可以用 `base_url` 把它当作自定义网关传给 SDK。

## 验收

- `create_llm_provider()` 能为 `protocol="litellm"` 创建 `LiteLLMProvider`。
- `LiteLLMProvider.chat()` 调用 `litellm.completion(...)`。
- `api_key`、可选 `api_base`、tools、max_tokens、temperature 会传给 LiteLLM SDK。
- tool_calls、usage、finish_reason 会归一化为 `LLMResponse`。
- Provider 错误不泄露 api key。
