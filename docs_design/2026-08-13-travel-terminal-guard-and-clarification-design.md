# 旅行规划终态守卫与结构化澄清设计

## 背景

真实旅行 Session 中，模型首次错误调用 `load_skills` 后自行修正并成功加载 Skill，但随后仅用自然语言声明“完成第一步”，没有继续查询、优化和调用 `finalize_travel_plan`。通用 AgentLoop 正确地把无 Tool Call 的文本响应视为 Turn 结束，旅行前端却误把 `done` 展示成“规划流程已结束”，造成未生成计划也像正常完成。

## 目标

- 旅行任务只有保存计划、请求用户补充、用户停止或结构化失败四类终态。
- 普通文本响应不能结束旅行任务；Gateway 在同一旅行 Session 内有界自动续跑。
- 用户信息确实不足时必须通过专用 Tool 发出结构化澄清事件，前端回到需求问答。
- Agent 自身漏调工具、过早收尾等问题由自动续跑处理，不要求用户重发。
- 输入框采用 Enter 发送、Shift+Enter 换行，不展示快捷键说明。

## 边界

- 不修改通用 AgentLoop 的“无 Tool Call 即结束 Turn”语义。
- 终态守卫位于 WebSocket 旅行应用编排层，只对已持久化为 `channel=travel` 的 Session 生效。
- 自动续跑仍调用同一 `WebRuntime.run_chat_events`，通过 SessionStore 保存新 Turn，并继续走 LLMProvider、Tool、Skill、MCP 和 finalizer 边界。
- 自动续跑最多两次；耗尽后返回 `TRAVEL_PLAN_NOT_FINALIZED`，避免无限循环和成本失控。
- 澄清 Tool 不直接修改表单，只发布安全的问题数组；前端把问题放回原需求对话，用户回答并再次确认后新建规划 Session。

## 模块设计

### 终态守卫

每个旅行 Turn 结束后检查：

1. 已收到 `travel.plan_ready` 或 Store 中已有该 Session 的计划：成功。
2. 已收到 `travel.clarification_required`：停止规划并回到需求问答。
3. 用户停止：停止。
4. 其它普通完成：自动追加“继续完成当前旅行规划”的内部 Turn。
5. 两次续跑后仍无终态：结构化失败。

### 结构化澄清

新增 `request_travel_clarification` Tool，仅允许 1–6 个短问题。它发出 `travel.clarification_required` RuntimeEvent，问题放在安全 `ui_metadata.detail_data.questions` 中，不包含 Secret、外部正文或模型过程。

### 前端

- 收到澄清事件后停止生成、清除恢复中的 Session id，并把问题加入需求对话。
- 收到无计划的普通 `done` 时显示错误，不再标为完成。
- 刷新恢复若发现 Turn 已结束但无计划，后端投影为 `failed / TRAVEL_PLAN_NOT_FINALIZED`。

## 变更文件

- `agent/app/api/ws.py`
- `agent/app/runtime.py`
- `agent/applications/travel/tools.py`
- `agent/applications/travel/service.py`
- `agent/protocols/runtime_event.py`
- `agent/core/event_emitter.py`
- `prompts/travel_planning.md`
- `prompts/travel_planning_continuation.md`
- `web/frontend/src/components/travel/TravelPlanForm.vue`
- `web/frontend/src/pages/TravelPlannerPage.vue`
- `web/frontend/src/stores/travel.ts`
- 对应测试与 Part 19 活文档

## 测试与验收

- 普通 Web Chat `done` 不续跑。
- travel Session 首次普通收尾后自动续跑；第二 Turn 产生计划后成功结束。
- 连续三次无计划时返回 `TRAVEL_PLAN_NOT_FINALIZED`。
- 澄清事件阻止自动续跑并回到问答区。
- 刷新恢复把无计划 Turn 投影为失败。
- Enter 发送、Shift+Enter 换行，页面不显示快捷键说明。
- 完整运行 Ruff、Pytest、前端 lint、typecheck、test、build。
