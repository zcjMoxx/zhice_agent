# MCP 显式代理与 Gateway 启动清理设计

## 背景

Tavily Streamable HTTP MCP 在 Gateway 启动时进入 `degraded`，调用栈经过
`httpcore._async.http_proxy`，但同机绕过代理直连 `https://mcp.tavily.com/mcp`
可以完成 TLS 握手并取得 HTTP 响应。当前 Runtime 创建 `httpx.AsyncClient` 时未声明
代理策略，因此默认继承启动终端的 `HTTP_PROXY`、`HTTPS_PROXY`、`ALL_PROXY` 等环境。
同一份运行配置会因启动方式不同而产生不同连接结果。

同时，Gateway 先构造完整 Runtime，再进入 Uvicorn/FastAPI lifespan。若 Uvicorn 启动、
端口绑定或 lifespan 进入前失败，正常 lifespan shutdown 不一定执行，已创建的 MCP、渠道
和本地 sidecar 需要由 `run_gateway` 提供最后一道幂等清理保证。

## 目标

- HTTP MCP 默认直连，不隐式继承启动终端代理。
- 确有代理需求时，由每个 MCP Server 显式选择环境代理模式。
- Streamable HTTP、SSE 和 OAuth token refresh 使用同一代理策略。
- Gateway 无论正常退出、启动失败还是端口绑定失败，均关闭已经构造的 Runtime。
- 配置错误、代理行为和启动失败清理均有稳定测试。

## 范围边界

- 本次新增 `proxy_mode: direct | environment`，不保存独立代理 URL 或代理凭据。
- `direct` 为默认值；`environment` 才读取 httpx 支持的代理、CA 和 netrc 环境。
- stdio MCP 不接受 `proxy_mode`，避免无效配置造成误解。
- 不屏蔽第三方 MCP SDK 的所有错误日志；先消除错误代理这一根因，并保留真正的远端故障证据。
- 本地 Ops 监督器在仅终止 10086 子进程后继续存活属于现有监督语义，本次不把它误判为
  Gateway 僵尸进程，也不改变 Ops 的启停协议。

## 模块设计

### 协议与配置

`McpServerSpec` 增加 `proxy_mode`。Loader 严格接受：

- `direct`：HTTP 客户端使用 `trust_env=False`。
- `environment`：HTTP 客户端使用 `trust_env=True`。

缺省值为 `direct`。未知值和 stdio 上的 `proxy_mode` 返回 `MCP_CONFIG_INVALID`。

### HTTP 客户端

Runtime 提供一个小型客户端工厂，将同一策略传给：

- Streamable HTTP 的显式 `httpx.AsyncClient`；
- SSE 的 `httpx_client_factory`；
- OAuth refresh 的 `httpx.AsyncClient`。

除代理策略外保留 MCP SDK 的 redirect 和 timeout 语义。

### Gateway 清理

`run_gateway` 在构造 Runtime 后使用外层 `finally` 调用幂等 `runtime.shutdown()`。
FastAPI lifespan 仍负责正常服务周期内的 shutdown；外层调用负责覆盖 lifespan 未进入、
Uvicorn 启动失败和二次 Ctrl+C 等路径。

## 数据流

```text
config.yml mcp.servers.<id>.proxy_mode
  -> McpServerSpec.proxy_mode
  -> streamable HTTP / SSE / OAuth HTTP client
  -> httpx trust_env
     direct      -> false
     environment -> true

run_gateway
  -> build Runtime
  -> Uvicorn run
  -> lifespan shutdown（正常路径）
  -> outer finally shutdown（所有路径兜底，幂等）
```

## 变更文件

- `agent/protocols/mcp.py`
- `agent/mcp/config.py`
- `agent/mcp/runtime.py`
- `agent/mcp/auth.py`
- `agent/app/gateway.py`
- `config/config.example.yml`
- `docs_design/zhice-agent-part11-mcp-design.md`
- `tests/unit_test/mcp/test_mcp_config.py`
- `tests/unit_test/mcp/test_http_runtime.py`
- `tests/unit_test/mcp/test_auth.py`
- `tests/unit_test/mcp/test_case.md`
- `tests/unit_test/app/test_gateway.py`
- `tests/unit_test/app/test_case.md`

## 测试方案

- Loader 验证默认 direct、显式 environment、非法值和 stdio 非法配置。
- 在环境中注入不可达代理，本地 Streamable HTTP/SSE fake Server 仍可连接和调用。
- OAuth refresh 在不可达代理环境下仍按 direct 访问本地 token endpoint。
- 显式 environment 模式通过构造参数测试确认 `trust_env=True`。
- 模拟 Uvicorn 启动异常，确认 Runtime shutdown 仍执行且原异常继续抛出。
- 运行 MCP/App 相关单测、完整 Ruff 和后端测试。
- 用带无效代理环境启动正式 Gateway，确认 Tavily Catalog 恢复为 ready。

## 验收标准

- 默认配置下，终端代理变量不会改变远程 MCP 的连接路径。
- Tavily 在当前正式配置和无效代理环境下仍能成功初始化。
- 需要环境代理的部署可通过 `proxy_mode: environment` 明确恢复原行为。
- Gateway 启动异常不会遗留已经构造但未关闭的 Runtime。
- 不在日志、状态接口或测试输出中暴露代理凭据、MCP Authorization 或 Cookie。
