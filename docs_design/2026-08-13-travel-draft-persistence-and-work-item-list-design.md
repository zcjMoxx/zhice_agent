# 旅行草稿持久化与统一工作项列表设计

## 背景

旅行页面在正式生成计划前，需求问答和结构化旅行条件只保存在浏览器组件内。用户刷新页面后会丢失；左栏又只读取已完成计划，无法恢复需求收集、生成中、等待候选选择或失败的旅行任务。

## 目标

- 第一次有效旅行需求输入即创建用户隔离的 `channel=travel` Session 并保存草稿。
- 后续问答更新同一草稿，刷新后恢复对话和结构化条件。
- 左栏统一展示需求收集中、规划进行中、等待选择、规划失败和已完成工作项。
- 未完成工作项可继续或删除；正式生成复用草稿 Session。
- 旅行 Session 继续从普通聊天列表排除。

## 范围边界

- SessionStore 仍是需求问答真值，不新增重复的消息数据库。
- 结构化 `TravelRequirementDraft` 写入 Session sidecar metadata，避免刷新时再次调用 LLM 推断。
- 只有尚未启动 Agent Turn 的 collecting Session 可以替换需求阶段消息；running、awaiting_candidate、failed（已有 Turn）和 completed 均冻结历史。
- 已完成计划正文仍由 TravelPlanStore 管理；统一列表只做只读投影。
- 不把旅行页面扩展成通用聊天 Agent，无关问题仍走携问返回主聊天。

## 模块设计

### Runtime 与 Session

`persist_travel_conversation` 扩展为保存完整问答和可选结构化草稿。保存前校验 actor ownership、`channel=travel`、消息边界和严格 `TravelRequirementDraft`。若不存在 Turn，可受控 `clear + append` 替换 collecting 阶段消息，并写入 sidecar；若已有 Turn，仅允许完全相同的幂等请求。

新增旅行草稿读取、未完成 Session 删除和工作项列表方法。删除前拒绝 completed Session，清理 candidate review、source ledger 和 Session 索引。

### 工作项投影

Runtime 一次读取当前用户全部 travel Session、最近 Turn、候选审核和计划摘要，按优先级合并：

1. 有计划：`completed`；
2. 当前活动 Turn：`running`；
3. 候选待确认：`awaiting_candidate`；
4. 有终态 Turn但无计划：`failed`；
5. 无 Turn：`collecting`。

每项只返回安全字段：Session/Plan id、状态、标题、预览、更新时间和错误码。

### 前端

- Travel store 在首轮有效输入前创建 travel Session。
- 表单每轮提取结束后发出完整问答与结构化草稿，由 store 自动保存。
- 页面初始化读取统一工作项；URL 支持 `session` 或 `plan`，无显式目标时恢复最近未完成草稿。
- 点击 collecting/failed 恢复需求表单；running/awaiting_candidate 使用现有生成恢复；completed 打开计划。
- 左栏按状态显示徽标，并为每个工作项提供统一删除入口。

## 数据流

```text
用户首轮输入
  -> WebSocket 创建 channel=travel Session（不启动 Turn）
  -> LLMProvider 提取严格 TravelRequirementDraft
  -> REST 保存问答 + draft 到 SessionStore
  -> 左栏出现 collecting 工作项
  -> 用户确认
  -> 同一 Session sendMessage 启动 Agent Turn
  -> running / awaiting_candidate / failed / completed 投影更新
```

## 变更文件

- `agent/app/runtime.py`
- `agent/app/api/travel_routes.py`
- `agent/app/api/schemas.py`
- `agent/applications/travel/service.py`
- `web/frontend/src/api/client.ts`
- `web/frontend/src/api/types.ts`
- `web/frontend/src/stores/travel.ts`
- `web/frontend/src/components/travel/TravelPlanForm.vue`
- `web/frontend/src/pages/TravelPlannerPage.vue`
- 相关后端、前端测试与 Part 19 活文档

## 测试方案

- collecting 首次保存、更新、幂等、刷新读取和严格字段校验。
- 已启动 Turn 后拒绝覆盖。
- actor 隔离、非 travel Session 拒绝、普通聊天列表继续过滤。
- 工作项五类状态投影、completed 去重和排序。
- 未完成删除与 completed 删除边界。
- 前端首轮创建、自动保存、同 Session 生成、恢复与删除。
- 运行 Ruff、Pytest、前端单测、lint、typecheck 和 build。

## 验收标准

- 需求问答完成前刷新页面不丢失。
- 左栏可看到并打开所有旅行工作状态。
- 正式规划不创建第二个 Session。
- 失败或未完成草稿可恢复和删除。
- 旅行记录不进入普通聊天列表，跨用户不可见。
