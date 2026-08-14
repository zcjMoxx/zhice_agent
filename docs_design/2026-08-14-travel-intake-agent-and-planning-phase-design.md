# 旅行接待 Agent 与正式规划阶段设计

> 说明：当前代码进一步支持用户通过自然语言“确认 / 开始执行”直接提交规划，并保证主聊天交接卡持续可用；增量方案见 `2026-08-14-travel-intake-action-commit-design.md`。

## 背景

当前旅行工作台在确认规划前使用 `/api/travel/requirements/extract` 将整段对话交给结构化分类器，再由 `TravelPlanForm.vue` 根据 `intent` 拼接固定话术。模型只负责分类和字段抽取，不负责用户可见回复，因此问候、身份询问、能力询问、旅行讨论和无关问题容易表现为机械分类；一旦分类偏差，前端还会重复展示固定的缺失字段列表。

同时，`channel=travel` 的 WebSocket Turn 当前直接装配完整旅行规划 Prompt、内部规划 Tool 和 MCP Tool，缺少“确认前只接待、确认后才检索规划”的能力边界。旅行规划 Prompt 还被作为全局额外系统 Prompt 装入通用 ContextBuilder，边界不够清晰。

## 目标

- 确认前复用普通 AgentLoop 和当前会话选择的默认 LLM，形成能自然交流的旅行接待 Agent。
- 接待 Agent 能理解问候、身份与能力询问、旅行知识讨论、需求补充与修正，不依赖关键词钉子和前端固定回答。
- 无关问题不回答实质内容，由 Agent 自然说明旅行工作台边界，并提供“携带问题返回主聊天”。
- 只有用户明确确认后，Session 才从 `intake` 原子切换到 `planning`，开放现有地图、天气、12306、Tavily、小红书、候选方案和 finalizer 流程。
- 对话、草稿、阶段和交接事件可刷新恢复；正式规划继续复用已有候选确认、来源账本和结构化计划持久化。

## 范围边界

本次包含：

- 旅行 Session 两阶段元数据与确认接口。
- 旅行接待系统 Prompt。
- 接待阶段的草稿更新 Tool 与主聊天交接 Tool。
- WebSocket 旅行阶段装配与前端对话事件消费。
- 旅行表单从 REST 分类器主链路切换到 Agent WebSocket 主链路。
- 后端和前端的阶段、正常路径、异常路径与边界测试。

本次不包含：

- 将旅行工作台扩展为通用聊天 Agent。
- 在确认前调用地图、搜索、铁路、天气或其它外部数据源。
- 重写正式旅行规划、optimizer、候选卡、finalizer 或计划展示结构。
- 删除结构化输出与调用级 generation options；旧提取接口暂时保留为兼容能力，不再作为工作台正常入口。

## 模块设计

### Session 阶段

旅行 Session metadata 新增：

- `travel_phase`: `intake | planning`，新建旅行 Session 默认 `intake`。
- `travel_draft`: 当前服务端校验后的完整旅行草稿。
- `travel_intake_turn_ids`: 最近接待对话 Turn id，用于只投影用户可见的接待消息。
- `travel_draft_version`: 草稿结构版本，继续使用 `1`。
- `travel_planning_confirmed_at`: 用户确认进入正式规划的时间。

旧旅行 Session 没有 `travel_phase` 时按以下方式兼容：存在正式 Turn、候选或计划状态的会话视为 `planning`；其余旅行草稿视为 `intake`。新请求会补写显式阶段。

### 接待 Agent Prompt

新增 `prompts/travel_intake.md`。Prompt 约束：

- 身份是“智策旅行助手”，不主动解释模型、工作模式或内部架构。
- 旅行领域问题可以自然回答，并渐进收集条件；每次优先追问一到两个最有价值的信息，不机械罗列全部缺失项。
- 已有条件可被后续表达修正，不能每轮从头询问。
- 对无关问题不作实质回答，调用交接 Tool，再自然提示继续旅行或携问返回主聊天。
- 每个接待 Turn 必须调用且只调用一个接待 Tool，以便服务端得到结构化草稿或交接事件。
- 不得声称已查询实时数据，不得启动正式规划。

### 受限接待 Tool

`update_travel_draft`

- 接收增量字段和可选 `clear_fields`，由服务端与现有草稿合并。
- 服务端执行字段类型、数量、日期范围和枚举校验。
- 保存完整 `travel_draft`，计算 `missing_fields` 和 `ready`。
- 发出 `travel.intake_draft_updated`，只向前端投影安全的草稿、缺失字段和 ready 状态。
- 空 patch 合法，用于问候、身份说明或旅行知识讨论，同时登记本 Turn 为接待对话。

`offer_main_chat_handoff`

- 接收用户原问题和简短主题，不接收或执行通用任务。
- 登记本 Turn，发出 `travel.main_chat_handoff`。
- 前端显示携问返回主聊天操作，问题只预填到主聊天，不自动发送。

两个 Tool 都通过普通 `ToolProvider`/`ToolExecutionContext` 执行，不在 AgentLoop 内增加旅行判断。

### 阶段化能力装配

`WebRuntime.run_chat_events` 在完成 actor-owned Session 解析后读取 `travel_phase`：

- `intake`: 使用 `travel_intake` Prompt，只注册两个接待 Tool；不创建 UserScopedToolProvider，不装配 MCP、Skill 执行、exec、memory 写入或 subagent Tool，也不包裹 Tool discovery。
- `planning`: 使用 `travel_planning` Prompt，保持现有旅行内部 Tool、MCP、首选来源预激活、来源账本、候选确认和终态守卫。
- 非旅行 channel: 使用通用系统 Prompt，不注入旅行规划 Prompt。

LLM 仍由现有 session model preference 和 `ConfiguredLLMProviderResolver` 决定，不新增内置小模型。

### 确认切换

新增 actor-owned REST 确认接口。服务端：

1. 校验 Session 所有权与 `channel=travel`。
2. 用现有 `TravelRequirementDraft` 白名单校验用户最终表单草稿。
3. 校验出发地、目的地、开始日期、结束日期、人数和有效日期范围。
4. 一次 metadata 更新写入完整草稿、`travel_phase=planning` 和确认时间。
5. 返回公开阶段状态，不向前端暴露内部 Prompt。

前端确认成功后只发送简短的“已确认，请开始规划”消息。正式规划 Prompt 由后端按 Session 阶段注入，并附服务端草稿上下文，不再由前端拼接长内部指令。

### 前端状态

`travel` store 新增 `intakeBusy` 和 `handoffQuestion`：

- 用户输入先进入 store 对话，再通过已有 WebSocket 发送。
- `travel.intake_draft_updated` 更新 `activeDraft`；`travel.main_chat_handoff` 更新交接卡。
- `channel_status.done` 使用 Agent 最终自然回复追加 assistant 消息。
- 刷新时 `/draft` 根据 `travel_intake_turn_ids` 恢复接待对话和草稿。
- `TravelPlanForm` 不再调用 `/requirements/extract`，不再维护 greeting/identity/capability/help/unrelated 固定话术。
- 固定文本仅保留网络失败兜底、按钮标签和表单校验提示。

## 数据流

```text
用户接待消息
  -> WebSocket message
  -> WebRuntime 读取 travel_phase=intake
  -> AgentLoop + travel_intake Prompt + 受限 ToolProvider
  -> update_travel_draft 或 offer_main_chat_handoff
  -> Session metadata + Runtime Event
  -> Agent 自然回复
  -> travel store 更新对话/草稿/交接卡

用户确认
  -> POST travel planning confirmation
  -> 校验并写 travel_phase=planning
  -> WebSocket 简短确认消息
  -> AgentLoop + travel_planning Prompt + 正式旅行/MCP ToolProvider
  -> 来源查询 -> optimizer -> 候选卡 -> 用户选择 -> finalizer
```

## 变更文件

- `docs_design/2026-08-14-travel-intake-agent-and-planning-phase-design.md`
- `prompts/travel_intake.md`
- `agent/applications/travel/tools.py`
- `agent/applications/travel/service.py`
- `agent/app/runtime.py`
- `agent/app/api/schemas.py`
- `agent/app/api/travel_routes.py`
- `web/frontend/src/api/client.ts`
- `web/frontend/src/api/types.ts`
- `web/frontend/src/stores/travel.ts`
- `web/frontend/src/components/travel/TravelPlanForm.vue`
- `web/frontend/src/pages/TravelPlannerPage.vue`
- 对应旅行后端、WebSocket、store、表单与页面测试
- `tests/unit_test/travel/test_case.md`
- `docs_design/zhice-agent-part19-intelligent-travel-planner-design.md`

## 测试方案

- Tool 单测：增量合并、清空字段、空 patch、非法日期/数量/枚举、ready/missing、交接事件、非旅行 channel 拒绝。
- Runtime 单测：新旅行 Session 默认 intake；intake 只暴露两个 Tool；同一默认 LLM 生效；非旅行不注入旅行 Prompt；确认后切 planning 并恢复完整 Tool；intake 不触发规划 continuation。
- API 单测：确认成功、字段缺失、日期错误、非本人 Session、非旅行 Session、重复确认。
- WebSocket 单测：接待自然回复完成、draft event、handoff event、确认后正式规划终态守卫仍工作。
- 前端 store 单测：乐观用户消息、Agent 最终回复、draft/handoff 事件、错误恢复、确认切换。
- 表单与页面单测：不再调用 extractor、展示模型自然回复、恢复草稿、交接按钮、确认后启动规划。
- 回归：候选卡选择、计划完成、进度投影、刷新恢复、删除任务和主聊天携问不退化。
- 全量执行 Ruff、Pytest、前端 test、ESLint、TypeScript 和生产 build。

## 验收标准

- “你好”“你是谁”“你能做什么”由 LLM 结合旅行 Prompt 自然回答，前端不存在对应固定长话术分支。
- “我想去大理”“大理几月合适”“两个人，国庆出发”“改成三个人”能连续讨论并正确增量更新同一草稿。
- “你能编代码吗”等无关问题不被回答为旅行需求，也不执行通用能力；显示可携带原问题返回主聊天。
- 用户确认前没有任何地图、天气、12306、Tavily、小红书、Skill、exec 或 subagent 调用。
- 用户确认后现有正式旅行规划能力、候选方案卡和 finalizer 继续生效。
- 刷新后接待对话、草稿和阶段可恢复；内部 Prompt、Tool 名称和业务 JSON 不出现在用户界面。
