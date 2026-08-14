# 旅行规划生成态跨页面连续性设计

## 背景

旅行规划页原先在组件卸载时取消 RuntimeEvent 订阅，并在无 `plan` 查询参数时无条件执行 `startNew()`。因此用户在生成中返回聊天再进入旅行页时，前端会清空正在运行的 Session、进度和生成态；整页刷新还会丢失 Pinia 内存状态。

## 目标

- 旅行任务在聊天页与旅行页之间切换时保持订阅和 UI 状态。
- 重新进入旅行页时优先恢复正在生成的工作区，不自动新建或打开最近历史计划。
- 整页刷新后可恢复当前用户仍在运行的旅行 Session，或打开该 Session 已生成的计划。
- 任务状态查询必须验证当前用户与 `channel=travel` 的 Session 所有权。

## 范围边界

- AgentLoop、Tool、Skill、LLMProvider 和 SessionStore 的职责不变。
- 浏览器只持久化按用户隔离的非敏感 `session_id`；不保存计划正文、消息、Secret 或外部服务响应。
- RuntimeEvent 仍是实时进度来源；刷新后的已运行任务通过受控 REST 状态投影恢复，不重放 Tool 调用。
- Gateway 重启导致的中断仍按既有 Turn recovery 语义结束，不伪装为可继续运行。

## 模块设计

### 前端应用级旅行 Store

`App.vue` 在认证身份确定后初始化旅行 Store。旅行页卸载不再取消订阅。身份退出或切换时才清理订阅、轮询和内存状态。

旅行页进入规则：

1. URL 明确携带 `?plan=` 时打开指定历史计划。
2. 无指定计划但 Store 存在生成中 Session、过程或结果时保留当前工作区。
3. 仅在没有任何当前工作区时展示空白新建态。

### 刷新恢复投影

新增当前旅行生成状态查询。后端从当前 actor、Session 索引、WebRuntime 活动 Turn 和 TravelPlan Store 派生：

- `running`：该用户的旅行 Session 存在进程内活动 Turn。
- `completed`：该 Session 已产出当前用户拥有的 TravelPlanV1。
- `finished`、`failed`、`stopped`：Turn 已结束但没有计划。
- `idle`：没有可恢复任务。

前端恢复 `running` 后进行有界轮询，直到进入终态。实时 WebSocket 事件到达时仍优先更新过程和结果。

## 数据流

```text
确认需求 -> 创建 channel=travel Session -> 保存 session_id -> AgentLoop 运行
                                                    |-> RuntimeEvent 实时更新 Store
路由切换 --------------------------------------------|-> Store 与订阅常驻
整页刷新 -> 当前用户状态 API -> active Turn / TravelPlan -> 恢复生成态或结果
```

## 变更文件

- `agent/app/runtime.py`
- `agent/app/api/travel_routes.py`
- `agent/app/api/schemas.py`
- `web/frontend/src/App.vue`
- `web/frontend/src/api/client.ts`
- `web/frontend/src/api/types.ts`
- `web/frontend/src/pages/TravelPlannerPage.vue`
- `web/frontend/src/stores/travel.ts`
- 对应后端、前端测试和旅行测试说明。

## 测试方案

- 正常：路由离开后订阅仍接收 `travel.plan_ready`；再次进入不清空运行态。
- 异常：恢复 API 失败、连接中断和 Turn 失败均显示安全终态，不把未知状态当完成。
- 边界：显式 `?plan=` 优先；其它用户或非旅行 Session 不可查询；无任务时保持空白新建态；非当前 Session 事件不影响状态。

## 验收标准

- 生成中返回聊天再进入旅行页，Session、进度和生成态仍在。
- 停留聊天页时计划完成，旅行 Store 能接收完成事件；回到旅行页可见完整计划。
- 整页刷新可恢复运行态，并在完成后加载 TravelPlanV1。
- 点击“新建旅行计划”不会在仍生成时静默丢弃当前任务。
- 前后端 lint、typecheck、单元测试和构建通过。
