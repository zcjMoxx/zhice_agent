# 旅行规划过程隔离与候选方案展示设计

## 背景

旅行页的规划过程事件目前只保存在 Pinia 内存状态。页面刷新或从“等待选择候选方案”状态恢复时，历史事件不会恢复；切换不同旅行任务时，单份全局进度状态还可能被复用。候选选择后的进度文案直接显示机器 `candidate_id`，导致内部英文标识泄漏到用户界面。

## 目标

- 以旅行 `session_id` 为边界保存和恢复规划过程事件。
- 切换计划或任务时只显示当前会话的过程，不复用其它会话状态。
- 候选选择过程展示中文可读名称，不展示机器候选 ID。
- 保持现有 WebSocket 事件和后端生成流程不变。

## 范围边界

- 本次使用浏览器 `sessionStorage` 保存有界的前端展示事件，不新增后端接口。
- 不改变候选 ID 在 API、校验和 Agent 续跑消息中的内部用途。
- 不尝试从完整 Session 工具日志重新推导历史 UI 事件。

## 模块设计

- `travel` store 增加按用户与 `session_id` 派生的进度缓存键。
- 记录、完成、恢复和切换会话时同步缓存；无缓存时从空过程开始。
- 打开已保存计划时使用其 `source_session_id` 作为当前会话，并加载该会话缓存。
- 候选展示名称优先使用候选首日城市与前两个地点，无法解析时使用“已选方案”。

## 数据流

```text
WebSocket runtime event
  -> travel store recordProgress
  -> sessionStorage(user, session_id)
  -> refresh / reopen
  -> restore progress for same session only
```

## 变更文件

- `web/frontend/src/stores/travel.ts`
- `web/frontend/src/stores/travel.test.ts`
- `docs_design/2026-08-14-travel-progress-isolation-design.md`

## 测试方案

- 候选选择文案不包含机器 ID，并显示中文方案名称。
- 同一用户的两个 session 使用独立进度缓存。
- 恢复候选状态时加载对应 session 的历史过程。
- 现有旅行进度事件和计划打开测试保持通过。

## 验收标准

- 刷新或恢复候选选择后，当前 session 之前的过程仍可见。
- 在“你好”和“重庆南山”之间切换时，过程列表不互串。
- 页面可见文案不出现 `classic-riverside-loop` 等候选机器 ID。
