# 旅行页双侧可折叠栏设计

## 背景

自然语言输入卡中的结构化字段展开后会显著拉长主内容，左侧计划列表也长期占用宽度。旅行工作台需要让主内容保持聚焦，同时保留随时核对条件和历史计划的能力。

## 目标

- 自然语言输入和生成结果继续位于中央主内容。
- 结构化旅行条件改为右侧检查栏，可从输入卡打开并随时关闭。
- 左侧“我的计划”可折叠为窄栏，并可恢复。
- 两侧状态互不影响，表单值、计划选择和生成链路保持不变。

## 范围边界

- 不改变 TravelRequestV1、Prompt、AgentLoop、Session 或 Tool 调用协议。
- 不把表单状态移动到全局 Session；仍由 TravelPlanForm 在当前页面生命周期内维护。
- 小屏右侧检查栏覆盖主内容，桌面端为主内容预留空间。

## 模块设计

TravelPlanForm 继续拥有自然语言与结构化字段的同一份 reactive 状态。输入卡中的摘要按钮控制右侧 inspector；inspector 仍位于同一 form DOM 中，保证提交语义不变，并通过 `details-change` 告知页面调整内容空间。

TravelPlannerPage 维护 `leftCollapsed` 和 `formInspectorOpen` 两个纯 UI 状态。左栏收起后只保留恢复按钮；右栏由输入卡摘要打开、内部关闭按钮收起。

## 变更文件

- `web/frontend/src/components/travel/TravelPlanForm.vue`
- `web/frontend/src/pages/TravelPlannerPage.vue`
- `web/frontend/src/styles/travel.css`
- `web/frontend/src/components/travel/TravelPlanForm.test.ts`
- `docs_design/zhice-agent-part19-intelligent-travel-planner-design.md`

## 测试与验收

- 正常：摘要按钮打开右栏，字段仍参与生成；关闭按钮收起右栏。
- 边界：左右栏独立切换；移动端右栏不造成横向溢出。
- 异常：busy 状态不影响栏位关闭和已有字段值。
- 前端 lint、typecheck、test、build 全部通过。
