# 旅行自然语言工作台与可见进度设计

## 背景

现有旅行页把完整结构化表单固定放在左栏，首次使用更像后台配置页，不符合用户从自然语言开始规划的习惯；运行时虽然已有 Tool、Skill 和 finalizer RuntimeEvent，页面只显示单行状态，缺少可感知的中间过程。空状态英雄标题也占据过多视觉空间。

## 目标

- 主区域以聊天式自然语言输入作为唯一首要入口。
- 本地确定性解析常见旅行字段，解析结果放入可展开、可修改的结构化表单。
- 左栏只保留“我的计划 / 最近生成”。
- 真实展示本次生成收到的 RuntimeEvent 时间线，不编造未发生步骤。
- 缩小空状态英雄标题和占用高度。
- LLM endpoint 缺省请求超时和 Failover 总 deadline 都从原来的 60/120 秒调整为 180 秒；旅行私有配置继续显式使用 240/300 秒。

## 范围边界

- 自然语言解析只处理常见中文表达，不引入新的 LLM 调用或外部服务。
- 解析结果必须允许用户展开校正；日期、目的地等关键字段仍由表单约束校验。
- RuntimeEvent 只显示安全的 display title/detail、阶段和 Tool/Skill 名，不展示参数或外部响应正文。
- 不改变 AgentLoop、Session、Tool、Skill、LLMProvider 和 Secret 边界。

## 模块设计

- `TravelPlanForm.vue`：聊天式 composer、常见字段解析、结构化摘要与折叠编辑区。
- `TravelPlannerPage.vue`：左侧仅计划列表，主区依次为 composer、真实进度、空状态或计划结果。
- `travel.ts`：维护有界 progress event 列表，从 RuntimeEvent 映射阶段和安全文案。
- `TravelProgress.vue`：保留六阶段总览，并增加当前任务的实时事件时间线。
- `travel.css`：重排桌面/移动布局并缩小英雄标题。
- LLM 协议与配置加载的 request timeout、total deadline 缺省值统一为 180 秒，显式 endpoint 值优先。

## 数据流

```text
自然语言输入
  -> 本地确定性解析
  -> 可展开字段校正
  -> 可见普通聊天消息
  -> AgentLoop / Tool / Skill
  -> RuntimeEvent
  -> 页面实时步骤列表
  -> finalize_travel_plan
  -> 最近生成与计划详情
```

## 变更文件

- `agent/protocols/llm.py`
- `agent/config.py`
- `web/frontend/src/components/travel/TravelPlanForm.vue`
- `web/frontend/src/components/travel/TravelProgress.vue`
- `web/frontend/src/pages/TravelPlannerPage.vue`
- `web/frontend/src/stores/travel.ts`
- `web/frontend/src/styles/travel.css`
- 相关单元测试与活文档

## 测试方案

- 默认 LLM endpoint timeout 为 180 秒，显式 240 秒仍保持 240 秒。
- 自然语言解析正常、缺字段和非法日期边界。
- 表单可展开修改并输出正常聊天消息。
- RuntimeEvent 追加有界、安全的进度项，完成/错误正确收口。
- 前端 lint、typecheck、test、build 与后端 Ruff/Pytest。

## 验收标准

- 左栏不再出现新计划表单，只显示最近生成计划。
- 主区域首屏提供类似聊天输入框的自然语言入口。
- 输入常见中文行程后，展开区自动填充并允许修改。
- 规划期间至少展示收到的真实 Tool/Skill/finalizer 中间事件。
- 空状态标题在常见桌面分辨率不超过两行且显著小于原版。
- 未配置 endpoint timeout/deadline 时都使用 180 秒；旅行部署显式 240/300 秒不被覆盖。
