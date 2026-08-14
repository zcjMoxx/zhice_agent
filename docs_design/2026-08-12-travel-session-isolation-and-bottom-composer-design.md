# 旅行应用 Session 隔离与底部输入布局设计

> 说明：实现后的界面试用表明底部固定输入区与旅行结果工作台的视觉重心不协调。当前输入区已恢复为主内容顶部，Session 隔离、默认新建、过程折叠和结果展示口径保持不变；以 Part 19 活文档为准。

## 背景

旅行规划页曾复用普通聊天的当前 Session。若当前会话来自 CLI，旅行请求会继续写入该 CLI Session；模型的结构化最终回复也会被普通聊天界面按消息正文展示。同时，页面会在没有显式选择时自动打开最近计划，输入框位于内容顶部，长时间规划的过程反馈与最终结果层级不清晰。

## 目标

- 旅行规划仍完整经过 Session、WebSocket、AgentLoop、LLMProvider、Tool 与 Skill 边界。
- 旅行运行 Session 不进入普通聊天 Session 列表，结构化内部回复不在聊天界面展示。
- 访问 `/travel` 时保持新建状态；只有 URL 指定或用户点击时才打开已保存计划。
- 主内容上方按时间展示可折叠规划过程与最终计划，输入区固定在主区域底部。
- 生成开始时展开过程，完整 TravelPlanV1 就绪后自动收起，且允许用户再次展开。

## 范围边界

- 不删除、改写或迁移既有 Session；此前已写入 CLI Session 的历史真值保留。
- 不在 AgentLoop 中识别旅行请求，也不改变 ToolResult 的结构化约定。
- 不隐藏普通聊天自身的 assistant 文本；通过应用 Session 隔离解决旅行 JSON 污染。
- 不改变 TravelPlanV1、旅行存储和外部服务契约。

## 模块设计

### 应用 Session

WebSocket `new_session` 帧允许受控的 `application=travel`。服务端将其持久化为 `channel=travel`；默认仍为 `web`。SessionAccessService 在“普通聊天列表”投影中排除 `travel`，但按 session id 的授权读取、AgentLoop 写入和审计能力保持不变。

旅行 Pinia store 独立持有本次 `sessionId`，每次新生成创建新的旅行 Session，直接通过共享 WebSocket 发送消息并只消费匹配该 Session 的事件。它不再修改普通 `sessions.activeId/messages`，也不再依赖 chat store 的 pending assistant 气泡。

### 新建与选择

`startNew()` 清空当前计划、选中 id、过程、错误和 URL 查询参数。页面挂载时只刷新计划列表；仅当 `?plan=` 存在时打开指定计划。左栏提供显式“新建计划”入口。

### 页面布局

主区域使用两行网格：`minmax(0, 1fr)` 的滚动内容区和底部 composer dock。空状态、过程面板、错误与最终计划均位于滚动区；表单位于 dock，不随结果滚动离开视口。移动端使用同样的视口内两行结构。

### 过程折叠

TravelProgress 维护本地展开状态：`active` 从 false 变 true 时展开；`stage` 进入 `complete` 且不再 active 时收起。标题栏始终可见并显示当前状态，用户可手动切换。

## 数据流

```text
自然语言输入
  -> WebSocket new_session(application=travel)
  -> SessionAccess(channel=travel)
  -> WebSocket message
  -> AgentLoop -> Tool / Skill / MCP -> LLMProvider
  -> RuntimeEvent -> 旅行过程面板
  -> finalize_travel_plan -> TravelPlanStore
  -> travel.plan_ready -> 打开完整计划并自动收起过程

/api/sessions -> 排除 channel=travel -> 普通聊天侧栏与消息区不展示旅行内部 JSON
```

## 变更文件

- `agent/app/api/ws.py`
- `agent/auth/session_access.py`
- `web/frontend/src/websocket/client.ts`
- `web/frontend/src/stores/travel.ts`
- `web/frontend/src/pages/TravelPlannerPage.vue`
- `web/frontend/src/components/travel/TravelProgress.vue`
- `web/frontend/src/styles/travel.css`
- 对应后端、前端单元测试与活文档

## 测试方案

- 正常：travel Session 正确创建、执行、接收匹配事件并完成计划。
- 异常：创建或发送失败时结束生成态并显示安全错误。
- 边界：普通 Session 列表排除 travel，但 web/CLI/外部渠道仍保持既有可见性；不匹配的 WebSocket 事件不影响当前旅行。
- 交互：无 `plan` 查询时不自动打开最近计划；新建清空选择；过程生成时展开、完成后收起且可手动展开。
- 全量运行后端 Ruff/Pytest 与前端 lint/typecheck/test/build。

## 验收标准

- 新旅行规划不再产生普通聊天侧栏条目，不在聊天窗口展示结构化 JSON。
- `/travel` 默认为空白新建态，点击历史计划后才展示。
- 输入框固定在主区域底部，过程和结果在上方滚动。
- 过程面板可折叠，生成时展开，完整计划出现后自动收起。
- MCP、Tool、Skill、Session 与 LLMProvider 调用链未被绕过。
