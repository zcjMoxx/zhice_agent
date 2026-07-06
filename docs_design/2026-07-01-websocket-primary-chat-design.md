# ZhiCe-Agent WebSocket 主通道与流式中断设计记录

> 说明：当前 `/ws` 已按 `docs_design/2026-07-06-ws-client-profile-naming-design.md` 收敛为 `hello client=web|external` 和 `command_profile=web|external`；本文正文中的 Web slash command 语义主要对应 Web UI。
> 说明：当前 gateway 默认端口已按 `docs_design/2026-07-06-gateway-default-port-design.md` 改为 `10086`；本文正文中的 `18791` 是当时设计记录，不再代表当前默认端口。

> 承接：`docs_design/2026-07-01-web-stream-command-markdown-design.md`
>
> 结论：Web 主界面下一轮改造直接以 `/ws` 作为双向主通道；SSE 保留为外部或兼容的一次性调用通道，不再作为浏览器主聊天通道。

## 背景

当前 Web 最小版已经具备 FastAPI gateway、静态前端、`POST /api/chat`、`POST /api/chat/stream`、会话 API 和模型选择 API。前端已经能把模型下拉框显示为模型名，后端也已对 `/model` 等 slash command 做短路处理，不再透传给 LLM。

但 `POST /api/chat/stream` 仍有一个关键限制：它在 `_chat_stream_events()` 中先同步等待 `AgentLoop.run_turn()` 完成，再把完整 assistant 文本切成若干 `delta`。这能改善视觉反馈，却不是真正的上游 token 或步骤级流式。用户现在提出的完整体验包括：

- Web 模型选择不暴露 endpoint，只展示当前 endpoint 下的模型名。
- Web 输入 `/model`、`/help`、`/stop` 等命令时不能透传给 LLM。
- assistant 输出前需要稳定反馈，例如 pending 气泡、typing dots 或单点缩放动画。
- assistant 文本需要 Markdown 渲染，普通用户输入仍按纯文本处理。
- 输出要从模型侧真正流式返回，而不是完整结果后再切片。
- `/stop` 需要从前端传到后端，按 session 停止当前 turn、工具和后续流程。
- 18791 仍是本地 Web gateway 端口；预留 WS 能力，但不额外开放一个隐藏端口。
- 当前静态前端的会话列表只支持新建、搜索和打开会话，缺少删除某个聊天、手动重命名聊天等基础管理动作。
- 会话删除和重命名不应只属于 Web；CLI 也要能做同样的本地会话管理，收敛到 `/sessions ...` 子命令下。
- 当前 gateway 启动后终端输出偏静态：启动摘要之外没有明确的 route、ready、请求或错误生命周期信息，不利于本地排查。

参考项目 `C:\Users\84953\Desktop\sthg_nanobot_agent` 的真实链路显示：

- `xagent/app/channel/web.py` 同时注册 `GET /ws` 和 `/api/chat/sse`，其中 `/ws` 是主 Web UI 双向实时通道。
- `web/src/composables/useWebSocket.js` 通过 `{"type":"message","content":"...","session_key":"..."}` 发送普通消息。
- 同文件的 `sendStop()` 也是通过 WebSocket 发送普通消息 `content: "/stop"`。
- `xagent/agent_core/agentloop/loop.py` 用 `_active_tasks: dict[session_key, list[asyncio.Task]]` 跟踪活跃任务，并在 `/stop` 路径调用 `cancel_session()`。
- `xagent/agent_core/agentloop/session_loop_registry.py` 进一步按 session 取消已注册的 active turn，服务多 channel 和子任务场景。
- 参考项目的 SSE 设计文档明确把 `/api/chat/sse` 定位为一次性 HTTP/SSE 调用，输出帧复用 WebSocket frame shape，不替代 `/ws` 的停止、refine、voice 等双向能力。

因此，本仓库不再继续把浏览器主聊天建立在 SSE 上，而是直接对齐“WS 主通道 + SSE 兼容通道”的方向。

## 目标

1. 浏览器主聊天使用同一个 `18791` FastAPI gateway 上的 `GET /ws`。
2. 前端发送普通文本、slash command、`/stop` 都走同一个 WS 通道。
3. 后端为每个 session 维护 active turn registry，支持 `cancel_session(session_id)`。
4. WS 输出统一事件模型：`connected`、`session_created`、`channel_status`、`channel_text`。
5. 前端在发送后立即展示 pending assistant 气泡和 typing 指示器，首个 `channel_text` 到达后替换为流式 Markdown。
6. 真正流式输出需要扩展 `LLMProvider` 或 `AgentLoop` 的流式回调能力；当前“完整结果后切片”不能作为最终验收。
7. SSE 保留为兼容接口，用于脚本、外部服务或不方便持有 WS 的一次性调用方。
8. 取消能力沉到 Web runtime 和 AgentLoop 可复用层，供 WebSocket 和后续外部通道复用。
9. 前端会话列表支持每条会话的更多菜单，至少包含重命名和删除；后端提供对应 API，并通过 SessionStore 维护一致的会话元数据。
10. CLI 会话管理保留 `/sessions` 作为展示入口，并新增 `/sessions rename`、`/sessions delete (<id>)`；Web 和 CLI 复用同一套 SessionStore 语义。
11. Gateway 启动时提供清楚的终端启动摘要，并默认持续打印运行日志，覆盖 HTTP 请求、WS 连接和 chat 生命周期，避免本地服务只在启动时有反馈。

## 非目标

- 不引入多用户、鉴权、远程部署、HTTPS、反向代理或多实例 sticky session 方案。
- 不增加第二个本地 WS 端口；`/ws` 挂在当前 `18791` 服务上。
- 不照搬参考项目的 aiohttp channel、webauth、审批、market、voice、refine 等平台能力。
- 不把 Web slash command 做成完整 CLI 终端；只支持 Web 当前需要的命令集合。
- 第一轮不实现断线续传、跨进程 pub/sub、Redis 广播和消息回放。
- 不在第一轮改造 CLI 输入循环为并发 reader；CLI 运行中打断仍保留 `Ctrl+C`。
- 不做自动标题生成、归档、多选批量管理或会话文件夹；本次只补手动重命名和单条删除。
- 不打印 api_key、完整用户长文本、完整 assistant 输出或原始 provider payload；即使 `debug` 也只输出摘要。

## WS 与 SSE 取舍

| 维度 | WebSocket | SSE |
| --- | --- | --- |
| 浏览器主聊天 | 适合，双向、同连接收发 | 不适合单独承载，客户端上行还要另配 REST |
| `/stop` | 可直接发同一个 WS message | 不能在同一个 SSE 连接上行，需要额外 REST cancel |
| 外部一次性调用 | 客户端实现略重 | 适合 curl、脚本、HTTP client |
| 代理兼容性 | 需要支持 WS upgrade | 普通 HTTP 流，部署更容易 |
| 当前本地 18791 | 挂 `/ws` 即可 | 保留 `/api/chat/stream` 或后续 `/api/chat/sse` |

本阶段选择：Web UI 以 WS 为主；SSE 作为兼容传输，不承载主交互。

## 协议设计

### 客户端到服务端

```json
{"type":"message","session_id":"chat-20260701","content":"帮我总结一下","model":"gpt-5","metadata":{}}
```

```json
{"type":"new_session"}
```

```json
{"type":"stop","session_id":"chat-20260701","reason":"user_stop"}
```

```json
{"type":"heartbeat"}
```

兼容规则：

- 用户在输入框输入 `/stop` 时，前端可以继续发送 `type="message"`、`content="/stop"`；后端必须把它映射到同一个 stop 流程。
- 其他以 `/` 开头的输入都进入后端命令处理，不进入 LLM。
- 普通文本如果确实需要以 `/` 开头，后续可设计转义规则；第一阶段保留 `/` 作为命令命名空间。
- 每个非 heartbeat frame 都必须带 `session_id`，`new_session` 由服务端生成后返回。

### 服务端到客户端

```json
{"event":"connected","data":{"connection_id":"ws-...","session_id":"chat-20260701"}}
```

```json
{"event":"session_created","data":{"session_id":"chat-20260701"},"session_id":"chat-20260701"}
```

```json
{"event":"channel_status","data":{"type":"accepted","turn_id":"turn-..."},"session_id":"chat-20260701"}
```

```json
{"event":"channel_text","data":"正在分析","session_id":"chat-20260701"}
```

```json
{"event":"channel_status","data":{"type":"done","turn_id":"turn-..."},"session_id":"chat-20260701"}
```

```json
{"event":"channel_status","data":{"type":"stopped","turn_id":"turn-...","cancelled":1},"session_id":"chat-20260701"}
```

```json
{"event":"channel_status","data":{"type":"error","payload":{"code":"LLM_ERROR","message":"provider failed"}},"session_id":"chat-20260701"}
```

事件约束：

- 所有 session scoped frame 都带顶层 `session_id`。
- `channel_text` 是 assistant 可见文本增量；前端按顺序拼接。
- `channel_status.accepted` 是后端收到并开始处理的确认，不等同于首个模型 token。
- `channel_status.stopped` 是用户中断后的最终状态，前端据此清 typing、恢复输入、保留用户消息。
- 后续工具步骤可以复用参考项目方向增加 `tool_step_start`、`tool_step_progress`、`tool_step_done`，但不在第一轮 UI 验收中强求。

## 模块设计

### `agent/app/api/ws.py`

新增 FastAPI WebSocket router：

- `@router.websocket("/ws")` 接受连接，发送 `connected`。
- 循环读取 JSON frame，校验 `type`、`session_id`、`content`、`model`。
- `type="message"` 时调用 `WebRuntime.start_turn(...)`。
- `type="stop"` 或 `content="/stop"` 时调用 `WebRuntime.cancel_session(session_id)`。
- `type="new_session"` 时生成新 session id，返回 `session_created`。
- `type="heartbeat"` 只更新连接活跃状态，返回可选 pong 或静默处理。

### `agent/app/gateway.py`

- 注册 HTTP API router 的同时注册 WS router。
- 仍由 `zcagent gateway --port 18791` 启动同一个服务。
- health 输出可以继续显示 endpoint/model 给开发排查，但前端模型选择控件不展示 endpoint。
- 启动时打印一次清楚的启动摘要：URL、workspace、config、sessions、static_dir、WS route、主要 API route。
- 增加 CLI 参数或配置开关，例如 `--log-level <level>` 和 `--access-log on|off`，传给 `uvicorn.run(..., log_level=..., access_log=...)`。
- 默认日志级别使用 `info`，并打开 HTTP access log；startup summary 只负责说明服务已就绪，运行中仍要继续打印请求和生命周期日志。
- 发生 gateway 构建失败、端口占用、静态资源目录缺失、runtime 配置错误时，终端输出要给出稳定错误码或短原因，不只留下 traceback。

### `agent/app/runtime.py`

在 `WebRuntime` 中增加 Web turn 管理能力：

- `start_turn(session_id, message, on_event, model=None) -> turn_id`
- `cancel_session(session_id) -> CancelResult`
- `rename_session(session_id, title) -> SessionSummary`
- `delete_session(session_id) -> None`
- `_active_turns: dict[str, ActiveTurn]`

`ActiveTurn` 至少包含：

- `session_id`
- `turn_id`
- `task`
- `cancel_event`
- `started_at`
- `last_event_at`

第一阶段可以先把现有同步 `AgentLoop.run_turn()` 放入 executor 或 `asyncio.to_thread()`，并把返回文本转换为 `channel_text`。但这只解决 WS 形态和 UI feedback，不算最终流式验收。

真正流式阶段需要：

- `AgentLoop.run_turn_stream(session_id, message, on_event, cancellation)`，或给 `run_turn()` 增加可选回调参数。
- 在每次 LLM chunk、工具开始、工具完成、工具异常时发事件。
- 在循环、工具调用前后检查 cancellation。
- tool subprocess 需要保存进程句柄，取消时终止进程并清理资源。

### `agent/cli.py`

会话管理在 CLI 层也要补齐，并统一收进 `/sessions` 命令族：

- `/sessions`：列出会话，保持当前已有入口，并在底部追加一行简短 Tip。
- `/sessions rename <session_id> <title>`：手动设置会话展示名；`title` 使用剩余参数，允许空格，做长度限制。
- `/sessions delete (<session_id>)`：删除指定会话；省略 `session_id` 时处理当前会话。

Tip 文案参考 `/model` 的渐进披露方式，例如：

```text
Tip: use '/sessions rename <id> <title>' to rename, '/sessions delete (<id>)' to delete.
```

`/help` 顶层仍只展示 `/sessions` 一行；具体子命令由 `/sessions` 输出底部的 Tip 渐进披露，不需要新增单独的 sessions help 主入口。

删除语义要区分 CLI 和 Web：

- CLI 中 `/sessions delete` 或 `/sessions delete <current_session_id>` 等价于 `/reset`：清空当前会话消息，保留当前 `session_id`，避免当前输入循环悬空。
- CLI 中 `/sessions delete <other_session_id>` 删除指定非当前会话的消息和元数据。
- Web 中删除当前会话后进入默认空界面：无选中会话、无消息列表，等待用户输入后再创建或绑定新会话。

### `agent/protocols/llm.py`

保持 `LLMProvider.chat(...)` 兼容，新增可选流式能力。推荐协议形态：

```python
class LLMStreamEvent(TypedDict):
    type: str
    content: str
    metadata: dict[str, object]

class StreamingLLMProvider(Protocol):
    def stream_chat(self, messages, *, tools=None) -> Iterator[LLMStreamEvent]:
        ...
```

`AgentLoop` 只能依赖协议，不直接依赖 OpenAI、LiteLLM 或 SDK。Provider 不支持 streaming 时，允许 fallback 到现有 `chat()`，但 UI 上应标记为兼容模式。

### `web/static/app.js`

- 新增 socket manager，负责 connect、reconnect、send、stop、heartbeat。
- 发送消息后立即插入用户消息和 pending assistant 气泡。
- pending 气泡展示单点缩放或 typing dots，不写解释性文案。
- 收到 `channel_status.accepted` 后维持等待态。
- 收到首个 `channel_text` 后切换为 streaming Markdown。
- 收到 `done`、`stopped`、`error` 后清理 streaming 状态并恢复输入。
- 模型下拉框只渲染 `models` 和 `current_model`，不展示 `endpoint`。

### `web/static/styles.css`

- 增加 pending dot 动画、stop 状态、Markdown 正文样式。
- 保证按钮、下拉框和输入栏在移动端不挤压或重排跳动。

### `agent/app/api/routes.py`

- 保留 `POST /api/chat` 作为同步兼容 API。
- `POST /api/chat/stream` 保留为 SSE 兼容 API，后续可将事件格式调整为与 WS frame 一致。
- 如果外部 SSE 客户端需要 stop，新增独立 REST：`POST /api/chat/{session_id}/cancel`，不要假设 SSE 同连接能接收客户端命令。
- 新增 `PATCH /api/sessions/{session_id}`，请求体 `{"title": "..."}`，用于手动重命名会话展示名。
- 新增 `DELETE /api/sessions/{session_id}`，删除会话前先取消该 session 的活跃 turn，再删除消息文件和元数据。

### `agent/protocols/session.py` 与 `agent/session/jsonl_store.py`

当前 `SessionSummary` 只有 `session_id`、`preview`、`updated_at`、`message_count`，没有 title；`JsonlSessionStore` 也只有 `load`、`append`、`clear`、`list_sessions`。

会话管理需要把“聊天展示名”和“session 文件 id”分开：

- `SessionSummary` 增加可选 `title` 字段。
- `SessionState.metadata` 可返回 title 等会话级元数据。
- `JsonlSessionStore` 增加 `rename(session_id, title)`，只更新展示标题，不改 session_id 和 JSONL 文件名。
- `JsonlSessionStore` 增加 `delete(session_id)`，删除 JSONL 文件和对应元数据；内部可复用当前 `clear()` 的文件删除能力，但语义上面向会话删除。
- 元数据可先用轻量 sidecar JSON 保存，例如 `contexts/sessions_meta/{session_id}.json`；后续如果迁移数据库，再由 SessionStore 实现内部替换。

删除与重命名都必须继续走 session id 校验，不能允许路径穿越；Web 删除当前活跃 session 后，前端应进入默认空界面，不自动切到最近会话。

### 前端会话管理

当前 `web/static/app.js` 的 `renderSessions()` 为每条会话生成一个整体 button，只能点击打开。后续改为：

- 会话行主体点击打开会话。
- 右侧增加 icon-only 更多菜单按钮，菜单项包括“重命名”和“删除”。
- 重命名打开轻量弹窗或 inline input，提交 `PATCH /api/sessions/{session_id}`。
- 删除需要二次确认，提交 `DELETE /api/sessions/{session_id}`。
- 删除成功后刷新会话列表；若删除的是当前会话，则进入默认空界面，不自动打开其他会话。
- 会话标题显示优先级：`title` -> `preview` -> `session_id`。
- 搜索同时匹配 `title`、`preview` 和 `session_id`。
- 发送中或当前 session 正在 streaming 时，删除当前会话按钮禁用；非当前会话可以删除。

## Gateway 终端反馈

`zcagent gateway` 面向本地开发，启动后不能只打印一次启动摘要。默认终端日志要持续给出运行反馈：请求进来、WS 建连/断开、chat 开始/完成/停止/失败、session rename/delete 等关键生命周期都应能看到。

```text
ZhiCe-Agent gateway
url: http://127.0.0.1:18791
workspace: ...
config: ...
sessions: ...
static: web/static
routes: /, /health, /api/*, /ws
logs: info, access-log: on, lifecycle-log: on
```

可选参数：

- `--log-level <level>`：设置日志详细程度；`<level>` 只能取 `info`、`warning` 或 `debug`，例如 `zcagent gateway --log-level warning`。默认 `info`。
- `--access-log on|off`：设置是否打印 HTTP 请求访问日志，例如 `GET /health 200`。默认 `on`；脚本或嵌入场景需要安静时可设为 `off`。

实现时在 `agent/cli.py` 中把 `--access-log` 解析为 `on|off`，再由 `agent/app/gateway.py` 调用：

```python
uvicorn.run(app, host=host, port=port, log_level=log_level, access_log=access_log)
```

因此默认启动 `zcagent gateway` 时，终端应直接看到接口调用情况，例如 `GET /health`、`GET /api/sessions`、`POST /api/chat` 的状态码；这类 HTTP access log 和应用自己的 WS/chat 生命周期日志一起构成本地排查反馈。

日志边界：

- `info` 默认打印启动摘要、HTTP access log、WS connect/disconnect、chat accepted/done/stopped/error、session rename/delete。
- `warning` 作为显式降噪模式，只打印配置风险、端口/启动失败、runtime warning/error。
- `debug` 增加事件帧摘要和取消检查点，但不得打印 api_key、完整用户长文本或原始 provider payload。

## 数据流

```text
browser submit
  -> optimistic user message + pending assistant bubble
  -> WS /ws message
  -> WebRuntime.start_turn
     -> slash command: command reply, no LLM call
     -> /stop: cancel_session, no LLM call
     -> normal: AgentLoop streaming path
  -> channel_status.accepted
  -> channel_text*
  -> channel_status.done|stopped|error
  -> browser finalizes Markdown bubble
```

## 中断流

```text
browser /stop or stop button
  -> local UI marks stopRequested and clears typing
  -> WS stop frame or message content "/stop"
  -> WebRuntime.cancel_session(session_id)
     -> cancel active turn task
     -> set cancellation token
     -> cancel tool subprocess handles when available
     -> suppress late channel_text by turn_id
  -> channel_status.stopped
```

边界：

- 没有活跃任务时，后端仍返回 `stopped`，`cancelled=0`。
- 如果同步 provider 无法立即打断阻塞调用，先停止 UI 和后续事件投递；下一阶段通过 provider streaming 和 cancellation token 实现真正中断。
- 工具执行需要独立取消钩子，尤其是 `exec` 子进程，否则只能停止 AgentLoop 后续流程，不能保证 OS 进程立即退出。

## 变更文件

- `agent/app/api/ws.py`：新增 WebSocket route 和 frame 分发。
- `agent/app/gateway.py`：注册 WS router，仍使用 18791；补启动摘要、日志级别和 access log 开关。
- `agent/app/runtime.py`：新增 active turn registry、start/cancel、rename/delete session 能力。
- `agent/cli.py`：保留 `/sessions` 列表入口，并扩展 rename、delete 子命令；gateway 子命令增加日志参数透传。
- `agent/protocols/llm.py`：新增可选 streaming provider 协议。
- `agent/protocols/session.py`：扩展 session summary 元数据和会话管理协议。
- `agent/session/jsonl_store.py`：新增 title sidecar、rename 和 delete。
- `agent/core/loop.py`：增加流式事件回调和 cancellation 检查点。
- `agent/tools/*`：为长任务工具补取消钩子，第一轮至少覆盖 `exec`。
- `agent/app/api/routes.py`：保留同步和 SSE 兼容，新增 REST cancel、rename 和 delete。
- `web/static/app.js`：从主聊天 fetch/SSE 切到 WS，新增 stop、pending、会话更多菜单、重命名和删除状态。
- `web/static/styles.css`：新增 pending、streaming、Markdown、stop、会话菜单和确认弹窗样式。
- `web/static/index.html`：补 stop 控件、会话菜单容器和必要的可访问属性。
- `tests/unit_test/app/test_ws_routes.py`：新增 WS route 单测。
- `tests/unit_test/session_store/test_session_store.py`：覆盖会话 title、rename 和 delete。
- `tests/unit_test/cli/test_cli_init.py`：覆盖 `/sessions rename`、`/sessions delete`、gateway 启动参数传递和启动摘要。
- `tests/unit_test/app/test_case.md`：补充 WS、stop、streaming、rename 和 delete 覆盖说明。
- `tests/unit_test/cli/test_case.md`：补充 sessions 子命令和 gateway 日志参数覆盖说明。
- `docs_design/README.md`：登记本设计记录。

## 测试方案

| 用例 | 输入或设置 | 期望 | 风险覆盖 |
| --- | --- | --- | --- |
| WS 建连 | `TestClient.websocket_connect("/ws")` | 收到 `connected` | route 注册和 frame 格式 |
| 普通消息 | fake runtime streaming 返回两段文本 | `accepted`、两条 `channel_text`、`done` | 基础流式 |
| slash command | 发送 `/model` | 返回 command 文本，fake LLM 未调用 | 命令不透传 |
| `/stop` message | 发送 `content="/stop"` | 调用 `cancel_session`，返回 `stopped` | 输入框 stop |
| stop frame | 发送 `{"type":"stop"}` | 同一取消路径 | 外部控制 |
| 无活跃任务 stop | 空 registry | `stopped.cancelled=0` | 幂等 |
| provider error | fake LLM 抛错 | `channel_status.error`，不暴露 traceback | 错误映射 |
| SSE 兼容 | `POST /api/chat/stream` | 仍返回可解析 SSE | 旧调用方兼容 |
| 模型选择 | `GET /api/models` + 前端渲染 | dropdown 只显示模型名 | endpoint 不暴露 |
| CLI sessions | `/sessions` | 列出会话，并在底部打印简短 Tip 子命令提示 | 兼容旧行为 |
| CLI sessions rename | `/sessions rename alpha 新标题` | alpha title 更新，session_id 不变 | CLI/Web 一致 |
| CLI sessions delete by id | `/sessions delete alpha`，alpha 非当前 | alpha 消息和元数据删除 | CLI 删除能力 |
| CLI sessions delete current | `/sessions delete` 或 `/sessions delete <current_id>` | 等价 `/reset`，清空当前会话并保留当前 session_id | 当前态一致 |
| 会话重命名 | `PATCH /api/sessions/{id}` | list/detail 返回新 title，session_id 不变 | 展示名和文件 id 分离 |
| 会话删除 | `DELETE /api/sessions/{id}` | 删除消息和元数据，列表不再出现 | 基础会话管理 |
| Web 删除当前会话 | 当前 session 被删除 | 先 cancel，再 delete，前端进入默认空界面 | 状态一致性 |
| Gateway 默认启动 | `zcagent gateway` | 打印 URL、workspace、config、sessions、routes、logs 摘要，并持续输出 HTTP/WS/chat 生命周期日志 | 启动和运行可见性 |
| Gateway 日志参数 | `zcagent gateway --log-level warning --access-log off` | 参数传给 uvicorn，显式进入降噪模式，启动摘要显示日志状态 | 嵌入或自动化场景 |

落地时至少运行：

```bash
python -m ruff check .
python -m pytest --basetemp .tmp/pytest_basetemp
```

## 验收标准

- 本地 Web 仍通过 `http://127.0.0.1:18791` 访问，WS 路由为同端口 `/ws`。
- 浏览器主聊天不再依赖 `POST /api/chat/stream`。
- 发送后立即出现 pending assistant 反馈，首个文本增量到达后变为 Markdown 流式气泡。
- `/model`、`/help`、未知 slash command 不进入 LLM。
- `/stop` 可从输入框和 stop 控件触发，后端按 session 执行取消并返回 `stopped`。
- CLI 具备 `/sessions`、`/sessions rename`、`/sessions delete (<id>)`；删除和重命名走 SessionStore，不绕过路径校验；`/sessions` 输出底部包含简短 Tip，参考 `/model` 的提示风格。
- 模型下拉框只显示模型名，不显示 endpoint。
- 会话列表每条会话有重命名和删除入口；重命名不改变 `session_id`，Web 删除当前会话后进入默认空界面。
- `zcagent gateway` 启动后终端能看到 URL、workspace、主要 route 和日志状态；运行中默认持续打印 HTTP/WS/chat 生命周期日志，需要降噪时可用 `--log-level warning` 或 `--access-log off`。
- 真正流式验收以 fake streaming provider 能在 turn 完成前多次发 `channel_text` 为准。
- SSE 兼容 API 仍可用于外部一次性调用；如果需要中断，使用独立 REST cancel，而不是假设 SSE 同连接上行。
