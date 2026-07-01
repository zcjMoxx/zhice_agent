# Endpoint 配置收敛设计

## 背景

当前 endpoint 加载器支持了多种兼容字段名，包括把 `provider` 当作协议别名，以及 `openai_protocol`、`openai_base_url`、`litellm_protocol`、`llmlite_*` 等字段。这让第一阶段的配置入口比实际需要更复杂。

## 目标

- 保持 endpoint 配置小而明确。
- `protocol` 只表示本地适配器：`openai` 或 `litellm`。
- `provider` 字段统一保留；`openai` endpoint 写空字符串，`litellm` endpoint 写 LiteLLM provider 前缀，例如 `anthropic`。
- 本地配置里的 `model` 和 `supported_models` 都只写普通模型名。
- 对于键值对象写法，使用外层对象 key 作为 endpoint 名称。
- 只有在可选的顶层 `endpoints` 列表写法中，才要求每一项提供 `name`。

## 范围

包含：

- `agent.config` 中的 endpoint 解析。
- `LLMEndpoint` 共享数据结构。
- LiteLLM 请求时的模型名格式化。
- 配置示例和聚焦单元测试。

不包含：

- Session 持久化变更。
- AgentLoop 行为变更。
- 新的 provider 协议。

## 模块设计

`agent.config` 只读取以下 endpoint 字段：

- `protocol`
- `provider`
- `base_url`
- `api_key`
- `model`
- `supported_models`
- `max_tokens`
- `temperature`
- `priority`
- `enabled`
- `role`

对于键值对象写法，JSON key 就是 endpoint 名称。对于列表写法，每个条目必须提供 `name`。

对于 OpenAI-compatible endpoint，`provider` 保持为空字符串，表示不需要 provider 前缀。对于 LiteLLM endpoint，`provider` 是必填字段，配置里的 `model` 仍然保持不带前缀。`LiteLLMProvider` 在真正调用 SDK 时，再把模型名格式化成 `provider/model`。

当前仓库模板 `config/llm_endpoints.example.json` 保持两个示例 endpoint：

```json
{
  "default": "openai_gpt5",
  "openai_gpt5": {
    "protocol": "openai",
    "provider": "",
    "base_url": "https://api.openai.com/v1",
    "api_key": "${ZHICE_LLM_OPENAI_API_KEY}",
    "model": "gpt-5.5",
    "supported_models": ["gpt-5.5", "gpt-5.4", "gpt-5.4-mini"],
    "max_tokens": 16384,
    "temperature": 0.7,
    "priority": 1,
    "enabled": true,
    "role": "default"
  },
  "litellm_claude": {
    "protocol": "litellm",
    "provider": "anthropic",
    "api_key": "${ANTHROPIC_API_KEY}",
    "model": "claude-opus-4.8",
    "supported_models": ["claude-opus-4.8", "claude-opus-4.6"],
    "max_tokens": 16384,
    "temperature": 0.7,
    "priority": 1,
    "enabled": true,
    "role": "default"
  }
}
```

## 数据流

```text
${ZHICE_AGENT_WORKSPACE}/config/llm_endpoints.json
  -> load_llm_endpoints()
  -> _iter_endpoint_mappings()
  -> _endpoint_from_mapping()
  -> LLMEndpoint(provider=..., model=...)
  -> LiteLLMProvider
  -> completion(model=f"{provider}/{model}")
```

## 变更文件

- `agent/config.py`
- `agent/protocols/llm.py`
- `agent/llm/litellm_provider.py`
- `config/llm_endpoints.example.json`
- `tests/unit_test/config` 和 `tests/unit_test/llm_provider` 下的单元测试

## 测试方案

- 先运行 config 和 LLM provider 相关的聚焦单元测试。
- 聚焦测试通过后，再运行完整单元测试套件。

## 验收标准

- 不再接受旧兼容别名作为 protocol/base URL 的快捷写法。
- OpenAI 配置显式保留 `provider: ""`；LiteLLM 配置使用具体 `provider` 加普通 `model`。
- 键值对象写法中的 `endpoint.name` 来自外层 key。
