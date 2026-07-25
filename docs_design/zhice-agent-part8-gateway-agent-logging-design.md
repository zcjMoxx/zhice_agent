# 智策 Agent 第八部分详细设计文档：Gateway / Agent 运行日志优化

> 关联规范：`AGENTS.md`
>
> 文档类型：阶段活文档。本文档始终按当前代码和当前阶段口径维护。
>
> 承接文档：`docs_design/zhice-agent-part7-turn-context-design.md`
>
> 设计依据：`docs_design/2026-07-02-gateway-runtime-logging-design.md`、`docs_design/2026-07-06-next-stage-sequencing-design.md`、`docs_design/2026-07-25-channel-lifecycle-startup-logging-design.md`
>
> 当前状态：第八部分代码已落地。当前代码已经具备分层 gateway 日志参数、安全 preview / redaction helper、`AgentLoop` / WebRuntime / tool dispatch 运行打点、按 `channels.yml` 顺序输出的 Web/QQ/微信渠道生命周期、Agent 执行的 `[YYYY-MM-DD HH:MM:SS] | LEVEL | component.event | fields` 格式、渠道连接的 Uvicorn `LEVEL: [channel] event` 格式，以及 `${ZHICE_AGENT_WORKSPACE}/logs/YYYY-MM-DD/trace.log` JSONL trace。

---

## 1. 背景

第七部分已经把一次用户请求收敛成稳定 turn：

- `Message` 已有 `turn_id`、`turn_index`、`parent_turn_id`。
- `JsonlSessionStore` 已按顶层字段读写 turn 信息。
- `AgentLoop.run_turn(..., turn_id=...)` 可以复用外部传入的 turn id。
- `WebSocket /ws` 的 accepted、channel_text、done、stopped、error 已经围绕同一个 `turn_id` 传递。
- `ContextBuilder` 已按最近 user turn 候选和本地相关性选择历史。

这为运行日志提供了稳定关联字段。当前日志现状是：

- `agent/app/gateway.py`：`zcagent gateway` 打印启动摘要，接收 `GatewayLogOptions`，并把 HTTP access/server 日志与 Agent lifecycle log 分开配置。
- `agent/app/gateway.py` 的 lifespan 按 `channels.yml` 顺序启动并输出渠道；QQ 有界等待真实 on_ready，微信只显示每用户独立账号的状态聚合；可选渠道失败仍不阻断 Web。
- `agent/app/logging.py`：配置终端 handler、日期目录 JSONL trace handler 和 uvicorn logger level。
- `agent/logging_utils.py`：提供 `preview_text`、`preview_json`、`redact_mapping` 和 `log_event`，供 app/core 复用。
- `agent/app/runtime.py`：使用 `zcagent.agent.web` / `zcagent.agent.session` 打印 WebRuntime chat/session lifecycle。
- `agent/core/loop.py`：使用 `zcagent.agent.turn`、`zcagent.agent.llm`、`zcagent.agent.tool` 和 `zcagent.agent.session` 打印 turn、LLM、tool dispatch、session save 生命周期。
- `agent/tools/registry.py`：仍保持简单 ToolProvider 协议，不接收 `session_id` / `turn_id`；带 turn 的 tool lifecycle 由 AgentLoop dispatch 层负责。
- `agent/config.py`：`${ZHICE_AGENT_WORKSPACE}/logs` 运行目录用于 `logs/YYYY-MM-DD/trace.log`。

因此现在的问题不再是“有没有 turn id”，而是本地开发时仍难以从终端判断：

- 当前请求是否进入 AgentLoop。
- LLM 是直接输出还是选择工具。
- 哪个工具开始执行、是否失败、耗时多久。
- session 是否保存成功。
- stop、error、工具失败是否和同一个 `session_id` / `turn_id` 对齐。

第八部分先补开发者可观测性：终端日志用于实时观察，workspace `logs/YYYY-MM-DD/trace.log` 用于事后回放。它仍然不是完整 audit 平台，也不引入用户权限库表。

---

## 2. 目标

1. `zcagent gateway` 默认打印简短、可读、脱敏的 Agent 运行痕迹。
2. 分离 Agent lifecycle log、HTTP access log 和 HTTP server log，避免一个 `--log-level` 同时表达多件事。
3. 终端日志每行必须带本地日期时间、等级、`component.event` 和关键字段，避免只看到零散消息。
4. 在 `${ZHICE_AGENT_WORKSPACE}/logs/YYYY-MM-DD/trace.log` 写入结构化 JSONL trace。
5. JSONL trace 使用 `session_id` 和可用时的 `turn_id` 关联运行轨迹；人读终端日志不重复展开完整内部 ID。
6. `AgentLoop` 打印 turn、LLM、tool decision、session save 和 stop/error 关键生命周期。
7. AgentLoop tool dispatch 层打印 tool start/done/error，包含耗时和安全 preview。
8. `WebRuntime` 沿用已有 chat/session lifecycle log，但 logger 命名和字段与 AgentLoop 对齐。
9. `debug` 等级只增加截断 preview，不输出完整 prompt、完整 session、完整工具结果或 secret。
10. 不保留旧 `--log-level`、`--access-log` 兼容入口，只接受语义明确的新参数。
11. 日志实现不改变 `AgentLoop` 的核心职责，不让 core import app/gateway 模块。
12. 为后续用户权限和 audit log 留字段口径，但本阶段不做用户系统、数据库 audit 表或 Web 日志面板。

---

## 3. 范围边界

### 3.1 本阶段包含

- Gateway 日志参数与启动摘要。
- `agent/app/logging.py` 轻量日志配置模块。
- 通用 preview / redaction helper。
- 终端 timestamp formatter。
- workspace date-based trace file handler。
- `AgentLoop` lifecycle logs。
- `ToolRegistry` execution logs。
- `WebRuntime` logger 命名与字段收敛。
- `session_id` / `turn_id` / `tool_name` / `duration_ms` 等稳定字段。
- 单元测试和 `test_case.md` 更新。
- README、总体设计、Part 6 / Part 7 / 索引文档状态同步。

### 3.2 本阶段不包含

- 日志文件查询或 Web 日志面板。
- 每个 token chunk 的终端日志。
- 完整 prompt、完整 session history、完整 provider payload。
- 用户、登录、权限、audit log 数据库。
- 工具调用前的用户确认流。
- CLI 运行中 `/stop`。
- OpenTelemetry、分布式 trace 或外部日志系统。
- 多天日志自动清理、压缩归档和复杂轮转策略。

如果实现时发现某个字段对未来 audit log 有用，可以先作为日志字段保留；但不引入用户身份、权限判断或审计存储。

---

## 4. 日志分层

### 4.1 Agent lifecycle log

面向本地开发者，回答“Agent 这一轮做了什么”。

建议 logger 命名：

```text
zcagent.agent.turn
zcagent.agent.loop
zcagent.agent.llm
zcagent.agent.tool
zcagent.agent.session
```

默认 `info` 输出路标，不输出大对象。终端格式必须带中括号包裹的本地日期时间，不带毫秒；不同部分用 ` | ` 分隔。普通事件第三段使用短 `component.event`，Tool 事件直接突出工具名：

```text
[YYYY-MM-DD HH:MM:SS] | LEVEL | component.event | key=value ...
[YYYY-MM-DD HH:MM:SS] | LEVEL | TOOL tool_name | PHASE | key=value ...
```

示例：

```text
[2026-07-07 21:34:12] | INFO | agent.turn.start | user=user001 turn=5 input="帮我看一下..."
[2026-07-07 21:34:13] | INFO | TOOL read_file | START | user=user001 turn=5
[2026-07-07 21:34:13] | INFO | TOOL read_file | DONE | user=user001 turn=5 duration=18ms
[2026-07-07 21:34:14] | INFO | agent.turn.done | turn=5 duration=2.27s output_preview=结论：先检查真实代码路径。
```

默认 INFO 只保留路标。`llm.call`、`llm.done`、`llm.tool_calls` 保留在 DEBUG；`llm.direct`、成功 `session.save`、`web.chat.accepted` 和 `web.chat.done` 已因与主生命周期重复而删除。需要排查细节时用 `--agent-log-level debug`，或者直接看 workspace `trace.log`。

`debug` 增加 LLM/session 细节和 preview：

```text
[2026-07-07 21:34:12] | DEBUG | agent.llm.call | messages=8 tools=6
[2026-07-07 21:34:12] | DEBUG | agent.llm.tool_calls | count=2 tools=read_file,grep
[2026-07-07 21:34:14] | DEBUG | agent.llm.done | endpoint=cpa_one model=gpt-5.4 duration_ms=2100
```

字段区继续用空格分隔 `key=value`，因为这些字段数量可变；固定区一律使用 ` | ` 分隔。终端 formatter 使用事件专属白名单：`actor_user_id`、`request_id`、`tool_call_id`、完整 `session_id` / `turn_id` 留在 trace，不在普通 Tool 行铺开；终端使用 `username` 和 `turn_index`。

终端中的实际 `duration_ms` 自适应显示为 `duration`：小于一秒使用 ms，小于一分钟使用最多两位小数的 s，分钟级以上四舍五入为整数秒并组合 m/s，小时级组合 h/m/s。Trace 继续保留原始数值 `duration_ms`。

交互式终端允许使用 ANSI 颜色增强可读性：时间段使用和 uvicorn `INFO:` 一致的绿色，`component.event` 段按 component 着色。重定向输出、测试输出或设置 `NO_COLOR` 时保持纯文本。

component 由内部 logger 映射而来，内部 logger 名不变：

```text
zcagent.agent.turn/session/llm/tool -> agent
zcagent.agent.web                   -> web
zcagent.gateway                     -> gateway
zcagent.ws                          -> ws
其它 zcagent.*                       -> zcagent
```

### 4.2 HTTP access log

面向 Web/API 连通性，回答“前端是否请求到后端”。

第一版仍交给 uvicorn `access_log` 控制，不自造 HTTP access 格式。

### 4.3 HTTP server log

面向 ASGI / uvicorn 服务自身状态，回答“服务器框架是否有异常”。

它不应被 Agent `debug` 联动打开。日常默认可以低噪声，排查 gateway 框架问题时再手动提高。

### 4.4 Workspace trace log

面向事后排查和后续日志面板，回答“某个 turn 的完整关键生命周期是什么”。

路径固定从 `AppConfig.logs_dir` 派生：

```text
${ZHICE_AGENT_WORKSPACE}/logs/YYYY-MM-DD/trace.log
```

例如：

```text
C:\Users\84953\ZhiCe_Agent_Workspace\logs\2026-07-07\trace.log
```

`trace.log` 使用 JSONL：一行一个事件。文件名保持 `trace.log`，日期放在目录名里，方便同一天后续增加 `gateway.log`、`error.log`、`audit.log`。

字段建议：

```json
{"ts":"2026-07-07T21:34:12.041+08:00","level":"INFO","component":"agent","event":"turn.start","session_id":"chat-20260707","turn_id":"turn-abc","turn_index":3,"input_preview":"帮我看一下..."}
{"ts":"2026-07-07T21:34:12.088+08:00","level":"DEBUG","component":"agent","event":"llm.call","session_id":"chat-20260707","turn_id":"turn-abc","messages":8,"tools":6}
{"ts":"2026-07-07T21:34:13.922+08:00","level":"INFO","component":"agent","event":"tool.done","session_id":"chat-20260707","turn_id":"turn-abc","tool":"read_file","ok":true,"duration_ms":18,"output_preview":"..."}
```

规则：

- `ts` 使用带时区的 ISO 8601，本地时区即可。
- `component` 使用和终端一致的短模块名，不额外写完整内部 logger，避免字段重复。
- `event` 使用稳定点号命名，例如 `turn.start`、`llm.call`、`tool.done`、`session.save_failed`。
- `session_id`、`turn_id`、`turn_index` 能拿到就写。
- preview 字段仍必须截断和脱敏。
- 不写完整 prompt、完整 history、完整 tool output 或 secret。
- 第一版不做跨天滚动守护；进程启动或每次取当前日期时确保当天目录存在即可。

---

## 5. 参数设计

旧 `--log-level` / `--access-log` 控制对象不清楚，容易让用户误以为它们同时控制 Agent lifecycle log、HTTP access log 和 uvicorn server log。当前项目仍是本地开发项目，不保留这两个旧入口，直接使用语义清楚的新参数：

```bash
zcagent gateway \
  --agent-log on \
  --agent-log-level info \
  --http-access-log on \
  --http-server-log on \
  --http-server-log-level info \
  --trace-log on
```

默认等价于：

```bash
zcagent gateway \
  --agent-log on \
  --agent-log-level info \
  --http-access-log on \
  --http-server-log on \
  --http-server-log-level info \
  --trace-log on
```

参数含义：

- `--agent-log on|off`：是否打印 Agent lifecycle log。
- `--agent-log-level debug|info|warning|error|critical`：Agent lifecycle log 等级。
- `--trace-log on|off`：是否写入 workspace date-based `trace.log`。默认 `on`。
- `--http-access-log on|off`：是否打印 HTTP 请求访问记录。
- `--http-server-log on|off`：是否允许 uvicorn/FastAPI server logger 输出。
- `--http-server-log-level debug|info|warning|error|critical`：HTTP server logger 等级。

旧参数处理：

- `--access-log on|off` 不再注册，使用时由 argparse 报 `unrecognized arguments`。
- `--log-level LEVEL` 不再注册，使用时由 argparse 报 `unrecognized arguments`。
- 降噪统一使用 `--http-access-log off`、`--http-server-log off` 或 `--http-server-log-level ...`。

`--check` 只验证配置和参数，不启动 server，也不需要触发真实日志输出。

---

## 6. 脱敏与截断

统一 preview helper 建议放在 `agent/app/logging.py` 或单独轻量模块中。若 core 和 tools 也要复用，helper 不能依赖 FastAPI、uvicorn 或 app runtime。

### 6.1 敏感字段

以下 key 及其大小写变体必须脱敏：

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

输出统一替换为：

```text
***
```

### 6.2 preview 规则

- 多行文本压成单行，连续空白折叠为一个空格。
- user input 默认保留 40 字符以内。
- assistant output 默认保留 80 字符以内。
- tool args 不单独生成事件；安全 JSON preview 合并到 `tool.start` 的 trace 字段，普通终端 Tool 行不展开参数。
- tool output 在 `info` 只打印短 preview；`debug` 最多 200 到 300 字符。
- provider error message 继续沿用现有安全错误文本，不输出请求体或 secret。

---

## 7. 模块设计

### 7.1 `agent/app/logging.py`

新增轻量模块：

```python
@dataclass(frozen=True)
class GatewayLogOptions:
    agent_log: bool = True
    agent_log_level: str = "info"
    trace_log: bool = True
    http_access_log: bool = True
    http_server_log: bool = True
    http_server_log_level: str = "info"
```

职责：

- `configure_gateway_logging(options)` 配置 `zcagent.*` 和 uvicorn logger。
- `configure_gateway_logging(options, logs_dir=config.logs_dir)` 配置终端 handler 和 `${logs_dir}/YYYY-MM-DD/trace.log` file handler。
- `preview_text(text, limit=...)` 做单行截断。
- `redact_value(value)` / `redact_mapping(data)` 做敏感字段脱敏。
- `format_terminal_record(record)` 输出带本地日期时间的终端文本。
- `format_trace_record(record)` 输出 JSONL trace。
- 不配置 root logger，避免污染测试或调用方日志。
- 多次调用保持幂等，避免重复 handler。

如果实现时发现 preview helper 被 core/tools 复用但 app 模块依赖方向不合适，可以将 helper 放到 `agent/logging_utils.py`；配置函数仍保留在 `agent/app/logging.py`。

### 7.2 `agent/cli.py`

修改 gateway 参数：

- 新增 `--agent-log`、`--agent-log-level`。
- 新增 `--trace-log`。
- 新增 `--http-access-log`、`--http-server-log`、`--http-server-log-level`。
- 不注册 `--log-level`、`--access-log` 旧入口。
- 解析后生成 `GatewayLogOptions` 或等价结构，传给 `run_gateway()`。
- `--check` 输出可以包含最终日志选项摘要，但不强制配置 handler。

### 7.3 `agent/app/gateway.py`

`run_gateway()` 从散装参数收敛为结构化日志选项：

```python
def run_gateway(
    config: AppConfig,
    *,
    host: str = "127.0.0.1",
    port: int = 10086,
    log_options: GatewayLogOptions | None = None,
) -> None:
    ...
```

职责：

- 调用 `configure_gateway_logging(log_options)`。
- 日志配置必须接收 `config.logs_dir`，并在 `trace_log=on` 时创建当天目录。
- 启动摘要打印清楚：

```text
agent-log: on level=info
http-access-log: on
http-server-log: on level=warning
trace-log: on path=${workspace}/logs/YYYY-MM-DD/trace.log
```

- `uvicorn.run(..., access_log=log_options.http_access_log, log_level=...)`。
- `http_server_log=off` 时尽量把 uvicorn logger 降到 `critical` 或禁用 handler，但不能吞掉明确异常。

### 7.4 `agent/app/runtime.py`

当前代码使用 WebRuntime 和 session 两类 logger：

```python
web_logger = logging.getLogger("zcagent.agent.web")
session_logger = logging.getLogger("zcagent.agent.session")
```

打点：

- chat stopped/error：`session_id`、`turn_id`；普通 accepted/done 由 `turn.start/done` 覆盖。
- cancel requested：`session_id`、`turn_id`。
- session renamed/deleted：`session_id`。
- model switched/reset：仅在 Session 有效模型真实变化时记录；不再逐 Turn 记录 `model.turn_selected`。

WebRuntime 负责 transport/app shell 层生命周期，不重复打印 LLM 和 tool 细节。

### 7.5 `agent/core/loop.py`

使用标准库 logger，不能 import `agent.app.*`：

```python
turn_logger = logging.getLogger("zcagent.agent.turn")
llm_logger = logging.getLogger("zcagent.agent.llm")
tool_logger = logging.getLogger("zcagent.agent.tool")
session_logger = logging.getLogger("zcagent.agent.session")
```

打点：

- turn start：`session_id`、`turn_id`、`turn_index`、user preview。
- LLM call：`session_id`、`turn_id`、messages count、tools count。
- LLM tool decision：工具名列表。
- tool start/done/error：工具名、安全参数摘要、`ok|error`、duration、output preview；不再拆分 `tool.args` 事件。
- tool iteration limit。
- cancellation stopped。
- LLM/provider error：error type、安全摘要。
- session save failed。
- turn done：duration + 最终回答第一条非空行的 `output_preview`，最多 80 字符，终端和 trace 都保留；error/stopped 继续记录状态，不伪造正常回答。

注意：

- AgentLoop 不判断日志是否输出，只按 logger 等级发事件。
- AgentLoop 不关心 HTTP、WebSocket、uvicorn 或 CLI 参数。
- `on_event` 继续只承载 runtime event，不用它替代 logging。

### 7.6 `agent/tools/registry.py`

`ToolRegistry` 当前不增加日志上下文参数，继续保持简单 ToolProvider 协议：

```python
execute(name: str, args: dict[str, Any]) -> ToolResult
```

原因是 `ToolRegistry.execute(name, args)` 当前没有 `session_id` / `turn_id`，如果为了日志扩展协议，会影响所有 ToolProvider 实现。第八部分选择在 AgentLoop tool dispatch 层打印带 turn 的 tool lifecycle。等用户权限系统需要在 Tool 层拿到 actor / session / turn 时，再通过独立设计扩展协议。

---

## 8. 数据流

```text
zcagent gateway
  -> parse gateway log flags
  -> build GatewayLogOptions
  -> configure_gateway_logging(options, logs_dir=config.logs_dir)
      -> terminal handler with timestamp format
      -> trace handler to logs/YYYY-MM-DD/trace.log
  -> uvicorn.run(access_log=http_access_log, log_level=http_server_log_level)

browser / external WS / REST
  -> WebRuntime.run_chat_events(session_id, message, turn_id)
      -> AgentLoop.run_turn(session_id, user_text, turn_id)
          -> log turn start
          -> ContextBuilder builds prompt/history/current user
          -> log LLM call
          -> LLMProvider.chat or stream_chat
          -> log LLM done or tool decision
          -> ToolRegistry.execute(...)
              -> log tool execution result
          -> SessionStore.append(...)
          -> log turn done/stopped/error
      -> only log Web stop/error or real model state changes
```

---

## 9. 变更文件

实际新增：

```text
agent/app/logging.py
agent/logging_utils.py
tests/unit_test/app/test_logging.py
docs_design/zhice-agent-part8-gateway-agent-logging-design.md
```

实际修改：

```text
agent/cli.py
agent/app/gateway.py
agent/app/runtime.py
agent/core/loop.py
tests/unit_test/cli/test_cli_init.py
tests/unit_test/app/test_gateway.py
tests/unit_test/app/test_case.md
tests/unit_test/app/test_runtime_commands.py
tests/unit_test/agent_loop/test_agent_loop.py
tests/unit_test/agent_loop/test_agent_loop_tools.py
README.md
docs_design/README.md
docs_design/zhice-agent-overall-design.md
docs_design/zhice-agent-part6-web-minimum-design.md
docs_design/zhice-agent-part6-web-ui-design.md
docs_design/zhice-agent-part7-turn-context-design.md
```

preview/redaction helper 已放到独立 `agent/logging_utils.py`，对应测试位于 `tests/unit_test/app/test_logging.py`。

---

## 10. 测试方案

### 10.1 CLI 参数

| 用例 | 输入 | 期望 |
| --- | --- | --- |
| defaults | `zcagent gateway --check` | 使用默认日志选项 |
| agent off | `--agent-log off` | Agent logger 关闭 |
| agent debug | `--agent-log-level debug` | 只影响 Agent logger |
| trace off | `--trace-log off` | 不写 workspace trace file |
| access off | `--http-access-log off` | uvicorn access log 关闭 |
| server level | `--http-server-log-level error` | server log level 为 error |
| removed access | `--access-log off` | argparse 拒绝旧参数 |
| removed level | `--log-level warning` | argparse 拒绝旧参数 |

### 10.2 日志配置

| 用例 | 输入 | 期望 |
| --- | --- | --- |
| handler idempotent | 连续 configure 两次 | 不重复输出 |
| agent off | `agent_log=False` | `zcagent.agent.*` 不打印 info |
| terminal timestamp | 默认终端 handler | 每行包含 `[YYYY-MM-DD HH:MM:SS]`，不带毫秒 |
| terminal separator | 默认终端 handler | 固定区使用 ` | ` 分隔 |
| terminal action | `zcagent.agent.turn` 的 `turn.start` 事件 | 第三段显示 `agent.turn.start`，不显示完整 logger |
| trace path | `trace_log=True` | 写入 `logs/YYYY-MM-DD/trace.log` |
| trace jsonl | 写入一个事件 | 文件最后一行是合法 JSON object |
| debug isolation | `agent_log_level=debug` | uvicorn 不变成 debug |
| server off | `http_server_log=False` | server 普通日志不输出 |

### 10.3 脱敏与截断

| 用例 | 输入 | 期望 |
| --- | --- | --- |
| secret key | `{"api_key":"abc"}` | 输出 `***` |
| nested token | 嵌套 dict/list 中含 token | 全部脱敏 |
| multiline text | 多行文本 | 单行 preview |
| long output | 超长工具结果 | 被截断 |
| trace secret | trace event 中含 secret 字段 | `trace.log` 中只出现 `***` |

### 10.4 AgentLoop

| 用例 | 场景 | 期望 |
| --- | --- | --- |
| direct answer | LLM 直接回答 | turn start、LLM call、direct、turn done |
| tool call | LLM 调工具后回答 | tool decision、tool start/done、turn done |
| tool error | 工具返回 error | tool error 日志，turn 仍完成 |
| LLM error | provider 抛错 | error 日志，不泄漏 secret |
| save error | SessionStore.append 抛 OSError | session save failed 日志 |
| cancellation | cancellation token 取消 | stopped 日志，same turn id |
| iteration limit | 超出 tool 轮数 | iteration limit warning |

### 10.5 WebRuntime

| 用例 | 场景 | 期望 |
| --- | --- | --- |
| accepted/done | 普通 Web chat | accepted 和 done 带同一 turn |
| stopped | stop frame | cancel requested 和 stopped 带同一 turn |
| error | AgentLoop 抛异常 | chat error 带 session/turn |
| command | `/help` 等短路命令 | 不伪造 AgentLoop turn 日志 |

---

## 11. 实现顺序

1. 增加 `GatewayLogOptions`、日志配置、terminal formatter、trace JSONL formatter 和 preview/redaction helper。
2. 改 gateway CLI 参数和 `run_gateway()` 签名，不保留旧参数兼容。
3. 接入 `${config.logs_dir}/YYYY-MM-DD/trace.log` file handler，确认目录自动创建。
4. 收敛 WebRuntime logger 命名和现有 lifecycle 日志字段。
5. 给 AgentLoop 增加 turn/LLM/session lifecycle 打点。
6. 给 ToolRegistry 或 AgentLoop tool dispatch 增加 tool start/done/error 打点。
7. 增加日志配置、脱敏、CLI 参数、trace 文件和生命周期日志测试。
8. 更新测试说明和相关设计文档状态。
9. 运行：

```bash
python -m ruff check .
python -m pytest --basetemp .tmp/pytest_basetemp
```

---

## 12. 验收标准

第八部分完成时，应满足：

1. 默认 `zcagent gateway` 能看到 HTTP access log 和 Agent lifecycle log。
2. `--agent-log off` 后不再输出 Agent lifecycle log。
3. 终端 Agent lifecycle log 每行包含 `[YYYY-MM-DD HH:MM:SS]` 格式的本地日期时间，精度到秒，不带毫秒。
4. 终端 Agent lifecycle log 固定区使用 ` | ` 分隔，例如 `[time] | level | component.event | fields`；交互式终端可对时间段和 `component.event` 段着色。
5. 默认 `zcagent gateway` 写入 `${ZHICE_AGENT_WORKSPACE}/logs/YYYY-MM-DD/trace.log`。
6. `trace.log` 是 JSONL，每行包含 `ts`、`level`、`component`、`event`，以及可用的 `session_id`、`turn_id`；不额外写完整内部 logger。
7. `--trace-log off` 后不写 workspace trace file。
8. `--agent-log-level debug` 会显示截断 preview，但不会把 uvicorn server log 提升到 debug。
9. `--http-access-log off` 后不显示 HTTP access log。
10. `--http-server-log-level error|critical` 可解析并传给 server log 配置。
11. AgentLoop 日志能用 `session_id` 和 `turn_id` 串起 LLM、tool、session save 和 final status。
12. Tool execution 日志能在 AgentLoop dispatch 层看出工具名、成功/失败、耗时和安全 output preview。
13. 终端日志和 `trace.log` 都不输出完整 prompt、完整 session、完整工具结果或 secret。
14. core 层不 import app/gateway/logging 配置模块；只依赖标准库 logging 和安全的通用 helper。
15. 现有 Web、CLI、AgentLoop、ToolRegistry 测试继续通过。

---

## 13. 和其它文档的关系

- `docs_design/2026-07-02-gateway-runtime-logging-design.md` 是本阶段的历史设计记录，保留早期参数和分层思路；本文是第八部分当前实现口径。
- `docs_design/zhice-agent-part7-turn-context-design.md` 已经提供统一 `turn_id`，第八部分必须复用它，不重新设计 turn 模型。
- 用户、登录与权限执行边界已在第九部分完成设计和第一版实现，详见 `docs_design/zhice-agent-part9-user-auth-permission-design.md`；第九部分基于 `User -> Session -> Turn -> ToolCall / AuditLog` 建模，并复用第八部分的 `session_id` / `turn_id` 关联字段。
- 第八部分已经成为当前实现能力；未来只保留日志查询面板、audit log、清理归档等尚未实现增强项。
