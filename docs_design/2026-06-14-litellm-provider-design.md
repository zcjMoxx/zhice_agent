# ZhiCe-Agent LiteLLMProvider 设计

## 背景

当前 `LLMProvider` 边界已经稳定，`OpenAIProvider` 负责直连 OpenAI-compatible Chat Completions 接口。此前 `protocol="litellm"` 已经可以通过配置层解析，但 provider 工厂会明确报“暂未实现”。

用户现在需要通过 LiteLLM 接入 Anthropic、Gemini、DeepSeek 等非 OpenAI 模型商。

## 目标

- 新增 `LiteLLMProvider`。
- 不改 `LLMProvider` 协议。
- 不让 `AgentLoop` 感知具体模型商。
- 不新增 Python 运行时依赖。
- 第一版支持 LiteLLM Proxy 的 OpenAI-compatible `/chat/completions` 接口。

## 方案

第一版 `LiteLLMProvider` 走 LiteLLM Proxy：

```text
ZhiCe-Agent
  -> LiteLLMProvider
  -> LiteLLM Proxy /chat/completions
  -> Anthropic / Gemini / DeepSeek / other provider
```

`LiteLLMProvider` 复用现有 OpenAI-compatible 请求、消息清洗、tool schema 传递、响应解析和错误脱敏逻辑。模型名由用户配置为 LiteLLM 识别的格式，例如：

```json
{
  "claude": {
    "protocol": "litellm",
    "base_url": "http://127.0.0.1:4000/v1",
    "api_key": "${LITELLM_MASTER_KEY}",
    "model": "anthropic/claude-sonnet-4"
  }
}
```

## 边界

本次不直接引入 `litellm` Python 包，不在 ZhiCe-Agent 进程内做 provider SDK 聚合。这样可以避免把 Anthropic/Gemini/DeepSeek 等多家 SDK 依赖拉进本地 Agent 内核。

如果后续确实需要进程内 LiteLLM SDK，可另写设计文档，评估依赖、环境变量、异常格式和工具调用兼容性。

## 验收

- `create_llm_provider()` 能为 `protocol="litellm"` 创建 `LiteLLMProvider`。
- `LiteLLMProvider.chat()` 向 `{base_url}/chat/completions` 发送请求。
- tools、tool_calls、usage、finish_reason 行为与 OpenAI-compatible provider 保持一致。
- HTTP 错误不泄露 api key。

