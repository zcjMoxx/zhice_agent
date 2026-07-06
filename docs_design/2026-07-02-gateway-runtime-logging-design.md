# ZhiCe-Agent Gateway 运行日志设计记录

## 背景

当前 `zcagent gateway` 已经能启动本地 FastAPI gateway，Web 前端也已经有 REST/SSE 兼容接口和 WebSocket 主通道。但终端运行反馈仍然偏静态：启动时打印地址、workspace、routes 和 `logs/access-log` 摘要，运行中主要只能看到 HTTP 请求访问日志。

这导致本地排查时很难直接判断：

- 用户输入是否已经进入 Agent turn。
- LLM 是否已经开始调用。
- 模型是直接回答，还是选择了工具。
- 工具是否开始、成功、失败、耗时多久。
- 最终 assistant 内容是否已经组装并保存。

参考项目 `sthg_nanobot_agent` 的做法是把终端日志、结构化 trace 和 WebSocket 进度事件分层处理。本项目第一版不直接引入完整 trace 系统，而是先补一条面向开发者的终端简短运行痕迹。

## 目标

1. `zcagent gateway` 默认持续打印简短 Agent 运行痕迹，而不是只打印启动摘要。
2. Agent 日志和 HTTP 日志分开控制，避免一个模糊的 `--log-level` 同时影响所有东西。
3. 默认日志适合日常使用：能看见发生了什么，但不刷完整上下文、完整工具参数或完整输出。
4. `debug` 只打开 Agent 细节 preview，不联动打开 uvicorn/FastAPI 的 debug 噪声。
5. HTTP server log level 支持完整常用等级：`debug`、`info`、`warning`、`error`、`critical`。
6. HTTP access log 保持独立开关，用来判断前端是否请求到后端。
7. 日志必须截断、脱敏，不能输出 API key、token、password、authorization 等敏感字段。

## 非目标

- 不引入完整 `trace.jsonl`、日志轮转、日志文件查询或可视化面板。
- 不把每个 SSE/WS token chunk 都打印到终端。
- 不把完整 prompt、完整 session history、完整 provider payload 打到终端。
- 不实现持久化 turn 模型；本设计中的 `turn_id` 可以先作为运行期关联字段。
- 不改变 HTTP API、WebSocket 协议或 SessionStore 数据格式。

## 参数设计

旧参数问题：

- `--log-level` 语义太宽，用户无法判断它控制 Agent、HTTP access 还是 uvicorn server。
- `--access-log` 比较接近真实含义，但没有说明它只控制 HTTP 请求访问记录。

新参数建议：

```bash
zcagent gateway \
  --agent-log on \
  --agent-log-level info \
  --http-access-log on \
  --http-server-log on \
  --http-server-log-level warning
```

默认等价于：

```bash
zcagent gateway \
  --agent-log on \
  --agent-log-level info \
  --http-access-log on \
  --http-server-log on \
  --http-server-log-level warning
```

### Agent 日志参数

`--agent-log on|off`

- `on`：打印 Agent 运行痕迹。
- `off`：关闭 Agent 运行痕迹；HTTP access/server 日志不受影响。

`--agent-log-level debug|info|warning|error|critical`

- `debug`：打印额外 preview，例如用户输入、工具参数、工具输出、assistant 输出摘要。
- `info`：默认。打印 turn、LLM、工具和最终输出的简短生命周期。
- `warning`：只打印异常但可恢复的 Agent 状态，例如工具参数非法、保存失败后仍返回。
- `error`：只打印明确失败，例如 LLM 调用失败、工具执行失败、turn error。
- `critical`：只打印严重到进程或核心能力不可用的问题。

### HTTP access 日志参数

`--http-access-log on|off`

- `on`：打印每次 HTTP 请求访问记录，例如 `POST /api/chat/stream 200`。
- `off`：关闭 HTTP 请求访问记录。

HTTP access log 本质是“请求记录”，不是一套完整等级体系。它可以在底层显示为 `INFO` 行，但不受 `--agent-log-level` 控制。

### HTTP server 日志参数

`--http-server-log on|off`

- `on`：允许 uvicorn/FastAPI server logger 输出。
- `off`：尽量关闭 server logger，只保留明确异常传播。

`--http-server-log-level debug|info|warning|error|critical`

- `debug`：服务器/框架调试信息，排查 ASGI、uvicorn、连接生命周期时才开。
- `info`：启动、关闭、普通服务状态。
- `warning`：默认推荐。显示警告、错误和严重错误，不显示普通框架状态。
- `error`：只显示明确错误。
- `critical`：只显示严重到服务可能不可用的问题。

第一版不暴露 `trace`。如果以后确实需要 ASGI 帧级别排查，再单独设计。

### 兼容策略

仓库仍处于本地开发阶段，可以优先使用清晰新参数。为了减少脚本瞬间失效，第一版实现可以保留旧参数作为兼容别名：

- `--access-log on|off`：兼容映射到 `--http-access-log on|off`。
- `--log-level LEVEL`：兼容映射到 `--http-server-log-level LEVEL`，并在帮助或启动摘要中提示推荐使用新参数。

旧参数不应该再控制 Agent 日志，避免误解。

## 日志输出分层

### 默认 info 输出

默认只输出路标：

```text
INFO [agent.turn] start session=web-xxx turn=turn-abc user="帮我分析这个日志..."
INFO [agent.llm] call model=gpt-5 messages=12 tools=5
INFO [agent.llm] tool_calls tools=read_file, grep
INFO [agent.tool] start name=read_file
INFO [agent.tool] done name=read_file ok duration=32ms output="## 总体设计..."
INFO [agent.llm] direct output="这个问题主要在..."
INFO [agent.turn] done session=web-xxx turn=turn-abc duration=4.2s output="这个问题主要在..."
```

### debug 输出

`debug` 追加截断、脱敏后的细节：

```text
DEBUG [agent.turn] user_preview="帮我分析这个日志为什么只有 HTTP 请求..."
DEBUG [agent.llm] messages count=12 system=2 history=9 user=1 tools=5
DEBUG [agent.tool] args name=read_file args={"path":"docs_design/..."}
DEBUG [agent.tool] output name=read_file output="## 背景..."
DEBUG [agent.turn] assistant_preview="这个问题不是前端，而是后端可观测..."
```

### warning/error/critical 输出

高等级只看异常路径：

```text
WARNING [agent.session] save_failed session=web-xxx reason="..."
ERROR [agent.llm] failed model=gpt-5 error_type=LLMProviderError message="..."
CRITICAL [agent.runtime] unavailable reason="prompt loader missing identity.md"
```

## 脱敏与截断规则

统一 preview 规则：

- 用户输入默认截断到 24 到 40 个可见字符。
- assistant 输出默认截断到 80 个可见字符。
- 工具参数默认 `info` 不打印；`debug` 打印截断 JSON。
- 工具结果默认 `info` 只打印前 80 个字符；`debug` 可提高到 200 到 300 个字符。
- 多行文本压成单行，连续空白折叠为一个空格。

敏感字段统一脱敏：

```text
api_key
apikey
key
token
access_token
refresh_token
authorization
password
passwd
secret
credential
cookie
set-cookie
```

脱敏示例：

```json
{"api_key":"***","path":"docs_design/README.md"}
```

即使 `debug` 也不能打印完整 secret。

## 模块设计

### `agent/cli.py`

- 替换 gateway 参数文案，主推新参数。
- 解析 `--agent-log`、`--agent-log-level`、`--http-access-log`、`--http-server-log`、`--http-server-log-level`。
- 兼容旧 `--log-level`、`--access-log` 时，映射到新字段。
- `--check` 只验证参数和配置，不启动 server，也不强制初始化运行日志。

### `agent/app/gateway.py`

- `run_gateway()` 接收结构化日志选项，而不是单一 `log_level`。
- 启动摘要打印清楚四个维度：

```text
agent-log: on level=info
http-access-log: on
http-server-log: on level=warning
```

- 调用日志配置函数后再启动 uvicorn。
- uvicorn 的 `access_log` 只由 `http_access_log` 决定。
- uvicorn 的 `log_level` 只由 `http_server_log_level` 决定。

### `agent/app/logging.py`

建议新增一个轻量日志配置模块：

- 定义 `GatewayLogOptions`。
- 定义 `configure_gateway_logging(options)`。
- 只配置 `zcagent.*` logger 和 uvicorn 相关 logger，不污染 root logger。
- `zcagent.*` logger 根据 `agent_log` 和 `agent_log_level` 决定是否输出。
- uvicorn logger 根据 `http_server_log` 和 `http_server_log_level` 控制。
- access log 开关仍交给 uvicorn `access_log`。

### `agent/core/loop.py`

新增标准库 logger，例如：

```python
logger = logging.getLogger("zcagent.agent.loop")
```

需要打点：

- turn start：session、turn、user preview。
- LLM call start：model 可用时打印 model，messages 数、tools 数。
- LLM response：直接输出、选择工具、错误。
- tool iteration limit。
- turn done/stopped/error。
- session save 失败。

`core` 只能依赖标准库 logging 和本项目通用 preview helper，不能 import `agent.app.*`。

### `agent/tools/registry.py`

新增 logger，例如：

```python
logger = logging.getLogger("zcagent.agent.tool")
```

需要打点：

- tool start：工具名。
- tool done：工具名、成功/失败、耗时、输出 preview。
- tool dispatch error：未知工具、参数非 dict、自定义工具异常。

### `agent/app/runtime.py`

保留 WebRuntime 的 active turn lifecycle 日志，但命名改到更明确的 logger：

```python
logging.getLogger("zcagent.agent.turn")
```

如果 `AgentLoop.run_turn()` 后续接收 `turn_id`，WebRuntime 负责传入同一个 turn id；第一版也可以只在 WebRuntime 层打印 turn id，在 AgentLoop 层用 session id 关联。

## 数据流

```text
zcagent gateway
  -> parse log flags
  -> configure_gateway_logging()
  -> uvicorn.run(access_log=http_access_log, log_level=http_server_log_level)

browser / CLI
  -> WebRuntime or CLI runner
  -> AgentLoop.run_turn()
      -> log user preview
      -> log LLM call
      -> log model tool decision
      -> ToolRegistry.execute()
          -> log tool start/done/error
      -> log final assistant preview
      -> save session
```

## 变更文件

预计新增：

```text
agent/app/logging.py
tests/unit_test/app/test_logging.py
```

预计修改：

```text
agent/cli.py
agent/app/gateway.py
agent/app/runtime.py
agent/core/loop.py
agent/tools/registry.py
tests/unit_test/cli/test_cli_init.py
tests/unit_test/agent_loop/test_agent_loop.py
tests/unit_test/tools/test_tool_registry.py
tests/unit_test/app/test_gateway.py
tests/unit_test/app/test_case.md
tests/unit_test/cli/test_case.md
```

如实现时发现不需要新增 `agent/app/logging.py`，也可以把配置函数放在 `agent/app/gateway.py` 附近，但不能把 preview/脱敏逻辑写进 CLI。

## 测试方案

### CLI 参数测试

- `zcagent gateway` 使用默认日志选项。
- `zcagent gateway --agent-log off` 传入关闭 Agent 日志。
- `zcagent gateway --agent-log-level debug` 只影响 Agent logger。
- `zcagent gateway --http-access-log off` 关闭 uvicorn access log。
- `zcagent gateway --http-server-log-level error` 和 `critical` 均可解析。
- 旧参数 `--log-level warning --access-log off` 仍能兼容映射。

### 日志配置测试

- `zcagent.*` logger 在 `agent_log=on` 时有终端 handler。
- `agent_log=off` 时不输出 Agent 痕迹。
- `agent_log_level=debug` 不会把 uvicorn logger 设置成 debug。
- `http_server_log_level=critical` 会传给 uvicorn server log 配置。

### AgentLoop 日志测试

- 普通直接回答：出现 turn start、LLM call、direct、turn done。
- 工具调用：出现 tool_calls、tool start、tool done。
- 工具失败：出现 tool error，且错误摘要截断。
- LLM 失败：出现 error，且不泄漏 workspace 外敏感配置。
- cancellation：出现 stopped。

### 脱敏测试

- 参数或结果里出现 `api_key`、`token`、`authorization`、`password` 时，日志只出现 `***`。
- 长文本被截断，日志中不出现完整长输入或完整长输出。

## 验收标准

1. 默认 `zcagent gateway` 终端能看到 HTTP access log 和 Agent 简短运行痕迹。
2. `--agent-log off` 后不再显示 Agent 运行痕迹。
3. `--agent-log-level debug` 会显示截断后的参数/结果 preview，但 HTTP server 不变成 debug。
4. `--http-access-log off` 后不显示接口访问记录。
5. `--http-server-log-level error` 和 `--http-server-log-level critical` 可用。
6. 日志不输出完整 prompt、完整 session、完整工具结果或 secret。
7. 现有 Web、CLI、AgentLoop、ToolRegistry 测试继续通过。

