# 旅行完成未读提醒与 MCP 管理监控设计

## 背景

旅行任务已支持跨路由持续生成，但用户停留在聊天页时缺少完成提醒。管理后台的 Skills 页目前只展示 Skill source，无法直接判断 MCP Server 是否 ready、是否正在自动重连以及近期调用是否失败。

## 目标

- 后台旅行规划完成后，在聊天侧栏旅行入口显示数字未读徽标。
- 用户进入旅行页后清除未读；刷新页面后未读提醒仍可恢复。
- Skills 页增加只读 MCP 运行监控，展示 Server、Catalog、调用和自动重连的安全真值。

## 范围边界

- 徽标只表示当前用户有一份未查看的完成结果，当前取值为 `0/1`，不作为计划总数。
- 浏览器只按用户持久化未读布尔状态，不保存计划正文、消息或 Secret。
- MCP 监控只读，不新增 reconnect/reload 权限，不暴露命令、参数、URL、Header、Token、Cookie、宿主机路径或原始异常。
- MCP Tool 调用和自动恢复仍由既有 McpRuntime 负责；管理页不绕过 Runtime。

## 模块设计

### 旅行未读状态

`travel.plan_ready` 或刷新恢复到 completed 时：若当前路由不是 `/travel`，Store 写入当前用户的未读标记。`TravelPlannerPage` 挂载后清除。`SessionSidebar` 根据 Store 状态渲染 `1` 数字徽标。

### MCP 安全监控投影

新增 `/api/admin/mcp/status`，沿用 `skill.sources.read` 读取权限。接口从 `McpRuntime.snapshot()` 和 `stats_snapshot()` 显式聚合：

- Catalog version、活跃调用数、刷新次数、list_changed 次数、自动重连次数；
- 每个 Server 的 state、tool_count、error_code；
- 每个 Server 聚合调用/成功/失败/取消数与最近 Tool 错误码；
- 最近一次连接状态、时间和 reason_code；
- OAuth 只展示 disabled/configured/refreshing/ready/error 状态。

## 数据流

```text
travel.plan_ready -> Travel Store -> 当前不在 /travel -> 未读 1 -> SessionSidebar
进入 /travel -----------------------------------------------------> 清除未读

Admin Skills -> GET MCP status -> McpRuntime snapshots -> 安全聚合 -> Server 卡片
```

## 变更文件

- `agent/app/api/schemas.py`
- `agent/app/api/routes.py`
- `web/frontend/src/api/client.ts`
- `web/frontend/src/api/types.ts`
- `web/frontend/src/stores/admin.ts`
- `web/frontend/src/stores/travel.ts`
- `web/frontend/src/components/SessionSidebar.vue`
- `web/frontend/src/layouts/AdminLayout.vue`
- `web/frontend/src/styles/app.css`
- 对应测试和当前活文档。

## 测试方案

- 旅行完成时在聊天页写入未读，进入旅行页清除，当前已在旅行页时不产生未读。
- 用户身份切换时未读按用户隔离。
- MCP 正常、degraded、disabled、无调用、最近错误和权限拒绝均返回稳定安全字段。
- 管理页能展示 Server 状态、调用计数、错误码和重连次数，并能只读刷新。

## 验收标准

- 后台完成后聊天侧栏旅行入口显示红色 `1`。
- 进入旅行页后徽标消失，刷新聊天页前仍保持未读。
- Skills 页可看到所有已配置 MCP Server 的当前状态和安全运行统计。
- API 与页面不泄露 MCP 配置或 Secret，不授予额外变更权限。
