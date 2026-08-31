# 定时天气工作流可靠性修复设计

## 背景

服务器 Owner 的每日天气工作流能够手动完成天气查询和 QQ 投递，但在每日定时点连续失败。线上 SQLite 运行记录证明调度节点按时启动，失败集中在 Open-Meteo 查询节点，错误码为 `WORKFLOW_SOURCE_UNAVAILABLE`；稍后手动运行使用同一工作流、用户和 QQ 绑定成功。

Open-Meteo MCP 进程长期复用 HTTP 客户端。闲置连接被上游关闭时，每天第一次请求可能收到传输错误；当前适配器只尝试一次，工作流又把所有 MCP 错误压缩成同一不可用错误。同时，调度回调通过普通手动运行入口执行，运行历史错误记录为 `manual`。

## 目标

- 对 Open-Meteo 的瞬时传输失败、超时、429 和 5xx 执行有界重试。
- 新建天气模板为只读天气节点声明有限重试策略，并实际执行退避等待。
- 保留 MCP 超时、限流、配置漂移和普通不可用的稳定工作流错误分类。
- 调度运行保存真实触发类型与计划执行时间。
- 与工作区已有的条件状态重试能力共同验证，继续禁止自动重试外部写操作和通知。

## 范围边界

- 不重试普通 4xx、参数错误或业务校验失败。
- QQ、微信、邮件和 MCP Action 仍不自动重试，避免重复外部副作用。
- 不改变 AgentLoop、聊天 Session 或 APScheduler 的真值边界。
- 不修改已发布工作流定义；已有工作流通过 Open-Meteo 适配器的源头重试立即受益，新建天气工作流额外获得节点级保护。

## 模块设计

### Open-Meteo 适配器

`_get_json` 默认最多尝试 3 次，按尝试次数线性退避。只重试 `TransportError`、`TimeoutException`、HTTP 429 和 5xx；所有上限均通过受限环境变量配置。最终失败结果记录安全的异常类型与实际尝试次数，不包含 URL 参数或凭据。

### 工作流执行与错误分类

只读 `mcp_query` 继续使用定义中的 `RetryPolicy`，失败后按 `backoff_seconds * attempt` 等待。MCP ToolResult 元数据映射为稳定错误：timeout/cancelled、rate limit、schema/not-found 和普通 transport/unavailable。外部 Action 始终使用 outcome unknown 语义且不重放。

### 调度审计

调度回调读取 SQLite schedule 的真实类型，将其与 `scheduled_for` 一并传给执行器。Web 手动执行和草稿试跑仍保持 `manual`。

## 数据流

```text
APScheduler cron
  -> scheduled actor
  -> WorkflowRuntime(trigger_type=cron, scheduled_for=...)
  -> weather mcp_query
  -> geocode/forecast Open-Meteo HTTP bounded retry
  -> LLM transform
  -> QQ notification (single attempt)
```

## 变更文件

- `integrations/open_meteo_mcp/server.py`
- `agent/workflows/nodes.py`
- `agent/workflows/executor.py`
- `agent/workflows/runtime.py`
- `agent/app/runtime.py`
- `web/frontend/src/api/types.ts`
- `web/frontend/src/utils/workflow-templates.ts`
- 对应后端、前端测试与 Part 20 活文档

## 测试方案

- 模拟闲置连接断开后第二次成功，验证只读天气请求被安全重试。
- 模拟不可重试 400，验证只尝试一次。
- 验证 MCP 错误分类映射和外部 Action outcome unknown。
- 验证调度运行历史保存 `cron` 与 `scheduled_for`。
- 回归已有条件状态重试、QQ timeout 不重试、天气地理编码与前端天气模板。
- 执行 Ruff、完整 Pytest、前端 lint/typecheck/test/build。

## 验收标准

- 单次瞬时 Open-Meteo 传输故障不会导致每日天气工作流失败。
- 连续故障最终仍有界失败，并展示准确的稳定错误类型。
- QQ 投递保持单次执行。
- 定时运行历史不再显示为手动触发。
- 服务器新镜像健康，真实 Owner 工作流完成天气查询、建议生成和 QQ 投递。
