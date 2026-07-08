# App / Web gateway tests

## 测试目标

覆盖 Part 6 Web 最小版的 HTTP app、API schema、静态资源服务和 core 直接导入。

## 用例覆盖

- `/health` 返回 workspace、config、sessions 和当前模型基础信息。
- `/` 从可替换 `static_dir` 返回静态首页。
- `GET /api/sessions` 返回会话摘要并把更新时间格式化为 ISO 8601。
- `GET /api/sessions/{session_id}` 返回指定会话消息。
- `POST /api/chat` 调用 runtime 并返回 assistant 消息。
- `POST /api/chat` 遇到 slash command 时由 Web runtime 短路处理，不透传给 LLM。
- `POST /api/chat/stream` 返回 SSE `status`、`delta`、`done` 或 `error` 事件。
- `GET /api/models` 返回当前 endpoint 和可选模型。
- `POST /api/model/preference` 设置当前 endpoint 的模型偏好。
- 空消息、缺失字段和非法 session id 返回 `INVALID_REQUEST`。
- runtime 抛出配置、LLM 和未知错误时返回稳定错误结构，不暴露堆栈。
- core 测试只使用 `agent.core.loop` 和 `agent.core.context` 新路径。
- gateway 测试确认不再保留 `agent/gateway.py` 顶层兼容导出模块。
- WebSocket `hello client=web` 返回 web command profile 能力，默认不支持 `/history` 和 `/exit`。
- WebSocket `hello client=external` 打开 external command profile 能力，`/history` 进入 external profile，`/exit` 关闭当前 WS 连接。
- `/sessions rename <id> <title>`、`/sessions delete <id>` 和 `/sessions delete` 在 runtime slash command 层有覆盖。

## 关键检查点

- API 测试只使用 fake runtime，不访问真实 LLM 或网络。
- gateway `--check` 仍只做配置检查，不启动 HTTP 服务。
- Web/API 层不反向进入 AgentLoop 之外的业务分支。

## Part 7 Turn Coverage

- WebRuntime accepts an optional `turn_id` and passes it to AgentLoop.
- WebSocket accepted, text, done, stopped, and error events carry the aligned turn id.
- Session history API exposes optional message turn fields.
- SSE status, delta, done, stopped, and error payloads carry one consistent turn id.

## Part 8 Logging Coverage

- Gateway logging options split Agent lifecycle log, HTTP access log, HTTP server log, and workspace trace log.
- Terminal Agent log lines use `[YYYY-MM-DD HH:MM:SS] | LEVEL | component.event | fields` without milliseconds, and can color the timestamp and component/event segment on TTY.
- Workspace trace writes JSONL to `logs/YYYY-MM-DD/trace.log` with `component` and no full internal logger name.
- Logging configuration is idempotent and can disable terminal Agent logs while keeping trace on.
- Preview helpers redact sensitive fields, collapse multiline text, and truncate long values.
- WebRuntime keeps correlated `chat.accepted` and `chat.done` events at DEBUG with `session_id` and `turn_id`, while stop/error events remain visible at higher levels.
