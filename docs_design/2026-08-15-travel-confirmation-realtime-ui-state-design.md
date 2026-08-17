# 旅行确认实时状态与并发交互设计

## 背景

旅行接待 Tool 已尝试发出草稿更新、主聊天交接和规划确认事件，但三个事件未注册到统一
Runtime Event 协议，Emitter 校验失败后按 best-effort 语义丢弃。自然语言“确认”实际已在
服务端切换到 planning，前端却只能刷新后发现；同时确认条只判断正式规划 busy，没有判断
接待 Agent 的 intake busy，用户补充信息后模型思考期间仍可点击确认。
页面初始化还会异步恢复上次生成状态；如果用户在恢复请求返回前点击“新建旅行计划”，旧状态会
晚到并覆盖刚创建的空白工作区。

## 目标

- 文字确认和按钮确认成功后都立即进入同一前端规划态。
- 草稿更新、交接和确认事件通过真实 Runtime Event + WebSocket 链路送达。
- 用户补充信息或模型思考期间不显示可点击确认条，详情面板也不能并发确认。
- 不终止文字确认后的同请求自动续跑，确认事件只更新 UI 状态。
- 新建或切换工作区后，初始化恢复和轮询的旧响应不得回灌当前页面。

## 范围边界

- 不改变确认 Tool 的服务端事务、旅行草稿内容或正式规划循环。
- `travel.planning_confirmed` 不是终态，WebSocket 仍继续等待候选、澄清或最终计划。
- 接待业务详情保持受 Runtime Event 安全字段、大小与敏感内容校验约束。

## 模块设计与数据流

1. Runtime Event 协议注册 `travel.intake_draft_updated`、`travel.main_chat_handoff` 和
   `travel.planning_confirmed`，并注册对应 UI detail type 与安全默认标题。
2. Tool Emitter 产出的结构化事件通过 WebSocket 立即转发。
3. Store 收到 planning confirmed 后原子切换 `phase=planning`、`intakeBusy=false`、
   `generating=true` 并展示正式规划进度。
4. Form 在 `intakeBusy` 或 `busy` 时隐藏确认条、禁止打开补充面板并禁用面板确认按钮。
5. Travel Store 维护单调递增的 `workspaceVersion`；新建、打开计划和打开未完成任务均推进
   版本。恢复请求和轮询在发起时捕获版本，返回及关键 await 后只在版本仍一致时应用状态。

## 变更文件

- `agent/protocols/runtime_event.py`
- `agent/core/event_emitter.py`
- `web/frontend/src/components/travel/TravelPlanForm.vue`
- `web/frontend/src/stores/travel.ts`
- 对应 Runtime Event、WebSocket、组件测试和测试说明

## 测试方案

- 使用真实 RuntimeEventEmitter 验证三个接待事件均成功创建并送入 sink。
- 验证 WebSocket 在 Turn 完成前转发 planning confirmed。
- 验证 intake busy 时确认条消失、详情确认按钮禁用且不触发 submit。
- 保留 Store 收到 planning confirmed 后立即进入生成态的现有测试。
- 使用 deferred Promise 验证旧生成状态在 `startNew()` 后返回时，会话、进度和错误仍为空。

## 验收标准

- 用户输入“确认”后无需点击按钮或刷新，页面立即显示正式规划状态。
- 用户补充信息后的模型思考期间不存在可并发点击的确认入口。
- 点击按钮确认与文字确认最终落入同一 planning 状态和进度链路。
- 页面加载期间立即新建或切换计划，不会再次出现上一次失败或运行中的进度。
