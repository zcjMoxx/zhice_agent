# 旅行规划过程后端恢复设计

## 背景

现有旅行规划过程按用户和 `session_id` 缓存在浏览器 `sessionStorage`。它能解决同一标签页刷新和不同计划之间的状态串用，但关闭标签页、换浏览器、历史计划生成时尚未写入缓存等场景仍只能显示最后的完成节点。旅行 Session 的消息和 Tool 调用结果已经通过 SessionStore 持久化，因此可以从后端安全重建用户可见过程。

## 目标

- 历史计划按 `source_session_id` 从后端恢复完整、受限的用户可见规划过程。
- 复用现有旅行 Tool 展示投影，避免把内部工具名、候选机器 ID或原始敏感输出返回前端。
- 浏览器缓存继续用于实时事件和快速恢复，后端历史与本地缓存按事件 ID 合并。
- 兼容已有计划，不要求迁移数据库或重新运行规划。

## 范围边界

- 不把 RuntimeEvent 写入 AgentLoop 或新增 AgentLoop 业务判断。
- 不新增运行时依赖和数据库表；历史来源仍是 SessionStore。
- 第一阶段重建 Tool 完成、失败、需求确认和计划完成节点，不伪造无法从持久化消息证明的实时 started 状态。
- 返回最多 100 条安全展示项，原始 Tool 输出不直接进入 API 响应。

## 模块设计

### 后端历史投影

新增旅行应用层历史投影模块，顺序扫描 Session 消息：

1. 从 assistant `tool_calls` 建立 `tool_call_id -> name/arguments` 映射。
2. 将对应 tool 消息转换为 `PostToolHookRequest`。
3. 复用 `travel_tool_presentation` 生成经过安全裁剪的标题、详情和搜索摘要。
4. 按工具类型映射到基础数据、攻略、求解、校验阶段。
5. 对成功保存计划补充完成节点。

### API

新增 actor-owned 只读接口：

```text
GET /api/travel/sessions/{session_id}/progress
```

Runtime 先通过现有 SessionAccess 校验所有权和 travel channel，再读取 SessionStore 并调用应用层投影。

### 前端恢复

- 打开计划或恢复旅行任务时先读取本地 session 缓存，再请求后端历史。
- 按事件 ID 合并，实时缓存覆盖同 ID 的历史投影。
- 合并结果继续写回本地缓存，加快后续切换。

## 数据流

```text
SessionStore messages
  -> travel history projector
  -> authenticated progress API
  -> travel store merge by event id
  -> TravelProgress component
```

## 变更文件

- `agent/applications/travel/history.py`
- `agent/app/runtime.py`
- `agent/app/api/schemas.py`
- `agent/app/api/travel_routes.py`
- `web/frontend/src/api/client.ts`
- `web/frontend/src/api/types.ts`
- `web/frontend/src/stores/travel.ts`
- `tests/unit_test/travel/test_progress_history.py`
- `web/frontend/src/stores/travel.test.ts`
- `tests/unit_test/travel/test_case.md`

## 测试方案

- 从持久化 Tool 消息重建地图、网页、optimizer、finalizer 等过程。
- 内部工具不进入历史响应；失败结果保持错误状态且不泄漏原始异常。
- API 拒绝越权和非 travel Session。
- 前端合并后保留服务端历史与本地实时事件，并按 session 隔离。
- 运行旅行单元测试、前端 travel store 测试、Ruff 和完整 pytest。

## 验收标准

- 关闭并重新打开页面后，已有计划仍显示可从 Session 证明的完整规划过程。
- 没有浏览器缓存的旧计划不再只显示最后一条完成记录。
- 不展示内部 Tool 名、候选机器 ID、Secret 或原始异常正文。
- 两个不同旅行 Session 的过程不互相混合。
