# WebSocket 命令通道能力设计记录

> 说明：当前 WS hello 命名已按 `docs_design/2026-07-06-ws-client-profile-naming-design.md` 收敛为 `client=web|external` 和 `command_profile=web|external`；本文正文保留 2026-07-02 当时方案。

> 承接：`docs_design/2026-07-01-websocket-primary-chat-design.md`、`docs_design/zhice-agent-part6-web-minimum-design.md`

## 背景

ZhiCe-Agent 的 slash command 应该是一套语义，而不是 CLI、浏览器前端和外部 WebSocket 客户端各自维护一套名字相同但行为不同的命令。当前 Web runtime 已经能拦截 `/model`、`/stop`、`/reset`、`/sessions`、`/history`、`/exit` 等输入，避免透传给 LLM。

问题在于浏览器前端和外部项目都会连接同一个 `WebSocket /ws`。仅凭连接方式无法区分“浏览器 UI 通道”和“外部 WS 服务通道”，而两者对命令的期望不同：

- 浏览器前端已有页面历史、会话侧边栏和关闭标签页等 UI 能力，不需要 `/history` 和 `/exit`。
- 外部 WS 客户端可能没有前端页面，需要通过 `/history` 查看当前会话历史，通过 `/exit` 正常关闭当前 WS 连接。

## 目标

- WebSocket 连接支持 `hello` 握手声明客户端类型。
- 默认客户端类型为 `browser`，保持当前浏览器前端的保守行为。
- 浏览器通道不支持 `/history` 和 `/exit`，但不会透传给 LLM，而是返回英文“不支持当前通道”的提示。
- 外部 WS 通道支持 `/history` 和 `/exit`。
- `/exit` 在外部 WS 通道中只关闭当前 WebSocket 连接，不退出 gateway 进程。
- `/quit` 不作为命令支持，统一走未知命令提示。

## 非目标

- 不在本次实现 `/new` 的连接级默认 session 切换；当前 WS message 仍要求携带 `session_id`。
- 不改变 CLI `/history` 和 `/exit` 的行为。
- 不引入鉴权。`client=external` 当前只是本地协议声明，后续开放外部服务时再加 token 或其它认证。
- 不把浏览器前端做成完整终端。

## 模块设计

### WebSocket `hello`

浏览器前端连接后发送：

```json
{"type":"hello","client":"browser"}
```

外部 WS 客户端连接后发送：

```json
{"type":"hello","client":"external"}
```

后端返回：

```json
{
  "event": "hello",
  "data": {
    "client": "browser",
    "command_channel": "browser",
    "capabilities": {
      "history_command": false,
      "exit_command": false
    }
  }
}
```

如果客户端不发送 `hello`，后端按 `browser` 处理。

### 命令能力

| 命令 | CLI | Browser WS | External WS |
|---|---|---|---|
| `/help` | 支持 | 支持 | 支持 |
| `/model...` | 支持 | 支持 | 支持 |
| `/stop` | 未来支持 | 支持 | 支持 |
| `/reset` | 支持 | 支持 | 支持 |
| `/sessions` | 支持列表、重命名、删除 | 支持列表、重命名、删除 | 支持列表、重命名、删除 |
| `/history` | 支持 | 不支持当前通道 | 支持 |
| `/exit` | 退出 CLI | 不支持当前通道 | 关闭当前 WS 连接 |
| `/quit` | 不支持 | 不支持 | 不支持 |

浏览器通道收到 `/history` 或 `/exit` 时返回普通 assistant 文本：

```text
Command not supported in this channel: `/history`.

Use `/help` to see available commands.
```

未知命令返回：

```text
Unsupported command: `/foo`.

Use `/help` to see available commands.
```

`/sessions` 子命令保持与 CLI 口径一致：

```text
/sessions
/sessions rename <id> <title>
/sessions delete (<id>)
```

其中 `/sessions delete` 不带 id 时作用于当前 session。CLI 中等同于清空当前会话；Web/WS runtime 中也保持为清空当前会话，而不是删除当前 session 文件，避免浏览器或外部客户端当前 session 指针突然失效。删除其它 id 时调用 `SessionStore.delete(id)`。

## 数据流

```text
browser
  -> WS /ws
  -> {"type":"hello","client":"browser"}
  -> command_channel = browser
  -> /history or /exit
  -> command text: not supported in this channel

external client
  -> WS /ws
  -> {"type":"hello","client":"external"}
  -> command_channel = external_ws
  -> /history
  -> command text: recent history

external client
  -> WS /ws
  -> {"type":"hello","client":"external"}
  -> /exit
  -> channel_status closing
  -> close current socket
```

## 变更文件

- `agent/app/runtime.py`：按 command channel 判断 `/history`、`/exit` 和 help 输出，并处理 `/sessions` 子命令。
- `agent/app/api/ws.py`：支持 `hello` 握手，按客户端类型传递 command channel，外部 WS `/exit` 关闭当前连接。
- `web/static/app.js`：浏览器 WS 建连后发送 `hello`。
- `tests/unit_test/app/test_ws_routes.py`：覆盖 hello、浏览器默认通道、外部 WS `/history` 和 `/exit`。
- `tests/unit_test/app/test_case.md`：补充命令通道能力测试说明。
- `docs_design/zhice-agent-part6-web-minimum-design.md`、`docs_design/zhice-agent-part6-web-ui-design.md`：同步当前口径。

## 测试方案

- 浏览器前端 hello 后返回 `history_command=false`、`exit_command=false`。
- 未发送 hello 的旧客户端默认按 browser 处理，保持兼容。
- 浏览器通道发送 `/history` 不进入 LLM，返回“不支持当前通道”。
- 外部 WS 通道发送 `/history` 时 runtime 收到 `external_ws` command channel。
- 外部 WS 通道发送 `/exit` 时返回 closing 状态并关闭当前 WS 连接。
- `/sessions rename <id> <title>` 调用 SessionStore.rename。
- `/sessions delete <id>` 调用 SessionStore.delete。
- `/sessions delete` 不带 id 时清空当前 session。
- `/quit` 不作为 alias 支持。

## 验收标准

- 浏览器前端仍能通过 `/ws` 正常聊天和 `/stop`。
- 浏览器输入 `/history`、`/exit` 不透传给 LLM，返回英文不支持提示。
- 外部 WS 客户端显式声明 `client=external` 后可以使用 `/history`，并能通过 `/exit` 关闭当前连接。
- `POST /api/chat` 和 `POST /api/chat/stream` 继续按 browser 通道处理 slash command。
- `/sessions` 子命令在 CLI、浏览器 WS 和外部 WS 上保持同一语义。
