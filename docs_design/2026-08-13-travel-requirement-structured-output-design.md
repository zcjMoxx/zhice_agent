# 旅行需求结构化输出根治设计

## 背景

旅行需求提取虽然在 Prompt 中要求完整 JSON，但 `LLMProvider` 只能发起普通文本生成，具体 Provider 没有把 JSON Schema 传给上游模型。模型可能正确理解“你是谁”为身份问题，却因省略空字段或输出对象外解释而被后端判为无效。针对个别句式做关键词短路只能掩盖症状，不能覆盖其它身份表达、无关问题或旅行字段提取。

## 目标

- 所有旅行页输入仍由当前默认 LLM 做统一语义判断。
- 通过 Provider 协议显式请求严格 JSON Schema 输出，而非只靠 Prompt 约定。
- OpenAI-compatible、LiteLLM 和 Failover 链路一致透传结构化输出契约。
- 后端继续做独立白名单校验；模型格式异常最多纠正一次。
- 不因结构化输出改动影响普通 AgentLoop、Tool calling 或其它 LLM 调用。

## 范围边界

- 不新增旅行专用小模型。
- 不用关键词规则承担身份、能力、帮助或无关问题的正确性。
- `response_format` 为调用级可选参数；普通调用不传时行为完全不变。
- Schema 不接受未知字段；所有字段都 required，业务可空值用空字符串、空数组或 null 表达。
- 服务端校验仍是真正信任边界，不能只相信模型或上游 Provider。

## 模块设计

### Provider 协议

新增不可变 `LLMResponseFormat`，包含 schema 名称、JSON Schema 和 strict 标记。`LLMProvider.chat` 增加可选 `response_format` 参数。

### Provider 实现

- OpenAI-compatible：转换为 `response_format.type=json_schema` 请求体。
- LiteLLM：使用相同 OpenAI-compatible 结构传给 SDK。
- EndpointFailoverProvider：每次 endpoint 尝试原样透传，保留现有重试、熔断和审计证据。
- 若所有端点明确以 HTTP 400/unsupported format 拒绝 JSON Schema，旅行提取器仅针对该请求自动回退普通 JSON Prompt；认证、限流、超时和服务故障不得伪装成“不支持 Schema”。回退结果仍经过同一白名单校验与一次有限纠正。

### 旅行需求提取

旅行提取器为全部输入传入固定严格 Schema。模型仍同时判断 `travel_requirement`、身份、能力、帮助和无关意图。返回后执行本地 JSON 解析与 `TravelRequirementDraft` 白名单校验；若格式仍不合法，在同一 Schema 约束下追加修正请求一次。

## 数据流

```text
旅行页输入
  -> TravelRequirementExtractor
  -> LLMProvider.chat(response_format=strict travel schema)
  -> OpenAI/LiteLLM/Failover 透传 JSON Schema
  -> 本地 JSON 解析 + TravelRequirementDraft 校验
  -> 固定产品话术 / 携问主聊天 / 旅行需求收集
```

## 变更文件

- `agent/protocols/llm.py`
- `agent/llm/openai_provider.py`
- `agent/llm/litellm_provider.py`
- `agent/llm/failover_provider.py`
- `agent/applications/travel/requirements.py`
- Provider 与旅行提取相关测试、设计活文档

## 测试方案

- OpenAI 请求体包含严格 JSON Schema。
- LiteLLM SDK 参数包含相同结构。
- Failover 在主 endpoint、重试与备用 endpoint 中保留 response format。
- 不支持 JSON Schema 的兼容网关安全回退；认证、超时等错误不回退。
- “你是谁”、能力、帮助、无关问题和旅行需求均调用 LLM，并携带同一 Schema。
- 缺字段、未知字段、非法枚举、非 JSON 与对象外文本覆盖纠正和最终失败路径。
- 普通无 response format 的 AgentLoop 与 Provider 测试不回归。

## 验收标准

- 不依赖特定中文句式即可由 LLM 判断页面输入意图。
- 支持结构化输出的上游模型在生成阶段即受严格 Schema 约束。
- 任意字段或意图的格式漂移都由统一校验和有限纠正处理。
- 无关问题继续进入携问返回主聊天流程，旅行需求继续进入确认流程。
