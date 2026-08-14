# 旅行助手问候意图与低随机性分类设计

> 说明：当前代码已采用真正的 `travel_phase=intake` 旅行接待 Agent，问候、身份、能力和旅行讨论由默认 LLM 在专用 Prompt 下自然回复，不再由前端根据 `assistant_greeting` 等 intent 套固定话术。旧提取接口仍保留兼容；当前主线请参考 `2026-08-14-travel-intake-agent-and-planning-phase-design.md` 与 Part 19 活文档。

## 背景

严格 JSON Schema 已解决旅行意图提取的输出格式漂移，但 Schema 只能保证结构正确，不能替代语义分类。当前意图集合没有“问候”，模型面对“你好”必须在旅行需求、身份、能力、帮助和无关中选择；同时旅行提取沿用 endpoint 默认 temperature，Prompt 的字段示例又以 `travel_requirement` 开头，最终可能把普通问候误判成旅行需求。前端随后使用缺失字段模板，显示“好呀，再告诉我……”，与旅行专用助手的产品边界不一致。

## 目标

- 所有自然语言意图仍由当前默认 LLM 统一判断，不使用关键词钉子替代语义理解。
- 增加受限 `assistant_greeting` 意图，覆盖纯问候和确认助手是否在线。
- 问候只返回审核过的欢迎话术并引导用户描述旅行，不进入需求字段追问。
- 旅行分类调用固定使用 temperature 0，减少同一输入的随机漂移。
- 普通 AgentLoop 与其它 LLM 调用继续使用 endpoint 原配置。

## 范围边界

- 不开放自由闲聊；`assistant_greeting` 只负责欢迎和回到旅行规划正轨。
- 编程、写作、知识问答等仍为 `unrelated`，继续展示携问返回主聊天。
- “我想出去玩”等虽字段为空但语义明确的旅行请求仍为 `travel_requirement`。
- LLM Schema、本地白名单校验和一次有限纠正继续生效。
- temperature 是调用级可选参数，不修改 endpoint 配置或用户的主聊天模型偏好。

## 模块设计

### 调用级生成参数

在 `LLMProvider` 协议增加不可变 `LLMGenerationOptions`。第一阶段仅支持可选 temperature；OpenAI-compatible、LiteLLM 与 Failover 全链透传。未提供时继续使用 `LLMEndpoint.temperature`。

### 意图协议

`TravelRequirementDraft.intent` 新增 `assistant_greeting`。Prompt 明确区分：

- 纯问候、在吗：`assistant_greeting`；
- 询问身份：`assistant_identity`；
- 明确旅行意愿，即使字段不足：`travel_requirement`；
- 通用任务与闲聊：`unrelated`。

### 前端话术

`assistant_greeting` 使用固定文案：说明自己是智策旅行助手、当前页面专注旅行规划，并邀请用户直接描述目的地、出发地或时间。不出现“再告诉我”，因为此前尚未收到旅行需求；不展示携问按钮，因为纯问候没有值得转交的问题。

## 数据流

```text
“你好”
  -> TravelRequirementExtractor
  -> LLMProvider.chat(JSON Schema, temperature=0)
  -> intent=assistant_greeting
  -> 固定欢迎话术
  -> 等待用户描述旅行
```

## 变更文件

- `agent/protocols/llm.py`
- `agent/llm/openai_provider.py`
- `agent/llm/litellm_provider.py`
- `agent/llm/failover_provider.py`
- `agent/applications/travel/requirements.py`
- `prompts/travel_requirement_extraction.md`
- `web/frontend/src/api/types.ts`
- `web/frontend/src/components/travel/TravelPlanForm.vue`
- Provider、旅行提取与前端相关测试和活文档

## 测试方案

- OpenAI/LiteLLM 请求使用调用级 temperature 0，普通调用仍使用 endpoint temperature。
- Failover 在主备 endpoint 间保持生成参数。
- “你好/在吗”由 LLM 返回 greeting；“你是谁”、无关问题和字段为空的旅行意愿不混淆。
- greeting 使用固定欢迎话术，不出现五项缺失字段、不展示主聊天携问卡。
- 非法 greeting topic、未知 intent 和 Schema 不兼容回退继续受控。

## 验收标准

- 输入“你好”只显示旅行助手欢迎和旅行描述引导。
- 输入“我想出去玩”继续进入旅行条件收集。
- 输入“帮我写代码”继续提供携问返回主聊天。
- 输入“你是谁”继续返回固定身份介绍。
- 全量 Ruff、Pytest、前端 test/lint/typecheck/build 通过。
