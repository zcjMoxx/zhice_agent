# LLM provider unit cases

These tests pin the second-stage HTTP provider boundary without real network
access.

- OpenAI-compatible provider sends `/chat/completions` requests with model,
  sanitized messages, and Bearer auth from the configured environment variable.
- OpenAI-compatible provider normalizes content, reasoning content, usage,
  finish reason, and tool calls into `LLMResponse`.
- Anthropic, Gemini, DeepSeek, and other non-OpenAI providers are intentionally
  reserved for a future `LiteLLMProvider` implementation.
- Missing API keys and HTTP failures raise clear errors without leaking secrets.
