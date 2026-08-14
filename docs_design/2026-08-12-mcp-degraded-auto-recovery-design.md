# MCP degraded 自动恢复设计

## 背景

远程 MCP Server 在 Gateway 启动时可能因网络抖动、TLS、代理、远端断流或初始化超时进入 `degraded`。现有 worker 在失败后只等待手动 reconnect 或排队调用；但失败 Server 的 Tool 已从 Catalog 移除，模型无法通过调用触发恢复，导致瞬时故障实际持续到重启或人工操作。

## 目标

- 初始化或 transport 失败后自动重连，不要求重启 Gateway。
- 使用有界指数退避，避免远端长期不可用时形成紧密重试循环。
- 手动 reconnect、shutdown 和排队请求可立即打断退避。
- 未知远端结果的当前 Tool 调用仍不自动重放。
- 日志只记录稳定错误码、顶层异常和叶子异常类型，不记录 URL、Header、参数或响应正文。

## 范围边界

- 不改变 AgentLoop、ToolProvider 或 MCP Tool schema。
- 不自动重放已经发送但结果未知的 Tool 调用。
- 不为永久配置错误绕过认证或修改 Secret。
- 一个 Server 的恢复不影响其他 Server 的 Catalog。

## 模块设计

`_server_worker` 为每个连接维护连续失败次数。失败后进入 interruptible backoff：1、2、4、8、16、最高 30 秒；等待期间 shutdown、手动 reconnect 或新排队请求可立即唤醒。新排队请求不会被重放，而是收到 `MCP_SERVER_UNAVAILABLE`，worker 随即开始新连接。连接成功并完成 Catalog 后失败计数归零，并记录 `mcp.server_ready`。

`ExceptionGroup` 日志展开到第一个非 group 的叶子异常类型，例如 `ReadTimeout` 或 `ConnectError`，同时保留 `MCP_TRANSPORT_ERROR` 稳定码。

## 数据流

```text
connect/initialize/list_tools failed
  -> degraded + remove only this Server catalog
  -> safe error log
  -> interruptible exponential backoff
  -> reconnect
  -> discover catalog
  -> ready + publish snapshot
```

## 变更文件

- `agent/mcp/runtime.py`
- `tests/unit_test/mcp/test_runtime.py`
- `tests/unit_test/mcp/test_case.md`
- `docs_design/zhice-agent-part11-mcp-design.md`

## 测试方案

- 正常：首次连接失败、下一次成功，自动恢复为 ready。
- 异常：持续失败时退避受上限约束，不紧密循环。
- 边界：手动 reconnect/关闭可中断等待；当前失败调用不重放；ExceptionGroup 只暴露叶子类型名。

## 验收标准

- 瞬时初始化失败无需重启即可恢复 Catalog。
- 日志中能看见 degraded、重试计划和最终 ready。
- 长期失败不会高频打远端。
- 全量 Ruff/Pytest 与前端既有检查通过。
