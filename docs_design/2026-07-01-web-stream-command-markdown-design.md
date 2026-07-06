# ZhiCe-Agent Web 流式交互与 Markdown 渲染设计记录

> 说明：这是一份历史过渡方案，曾选择 SSE 作为 Web 主界面的流式入口。当前新方案改为 WebSocket 主通道，SSE 只保留为兼容或外部一次性调用；后续实现请参考 `docs_design/2026-07-01-websocket-primary-chat-design.md`。

> 承接：`docs_design/2026-07-01-web-core-import-and-model-selector-design.md`

## 背景

当前 Web 最小版已经能通过 `POST /api/chat` 完成一次同步聊天，并通过模型下拉框切换当前 endpoint 下的模型。但实际使用时有四个体验缺口：

- 模型下拉框显示 `endpoint/model`，在当前 UI 中过长且暴露了不必要的 endpoint 细节。
- 用户在 Web 输入 `/model` 等 slash 命令时会被当作普通消息透传给 LLM。
- 同步请求等待期间没有稳定的“正在思考”反馈。
- 助手返回 Markdown 风格文本时只按纯文本展示，标题、列表、代码块和加粗不易读。

用户同时希望 Web 侧具备流式输出体验。本阶段仍保持轻量边界：不改 `LLMProvider` 协议，不改 `AgentLoop` 工具调用循环，不引入前端构建链。Web API 先提供 SSE 流式通道，前端用 typing 状态覆盖模型等待期，并在收到最终结果后以 chunk 方式增量渲染。

## 目标

- 模型下拉框只显示模型名，内部仍使用当前 endpoint 的 `model` 值提交偏好。
- Web API 对 slash 命令做后端短路处理，不把未知或已知 slash 命令送入 LLM。
- 新增 `POST /api/chat/stream`，以 SSE 返回 `status`、`delta`、`done` 或 `error` 事件。
- 前端优先使用流式接口；等待模型期间展示 assistant typing 指示器。
- assistant 消息使用安全的最小 Markdown 渲染；用户消息继续纯文本展示。

## 非目标

- 不实现 token 级上游 LLMProvider streaming。
- 不解析流式 tool-call delta。
- 不引入 React/Vue/Vite、第三方 Markdown 包或前端依赖管理。
- 不把 Web slash 命令扩展成完整 CLI 终端。

## 模块设计

- `agent/app/runtime.py`
  - 增加 `run_chat_or_command(session_id, message)`，普通消息走 `AgentLoop.run_turn`。
  - 增加 `handle_command(session_id, message)`，所有 `/...` 输入都短路，已知命令返回 Web 友好的文本，未知命令返回提示。
  - 复用当前 LLM failover provider 的 endpoint/model 能力处理 `/model`。
- `agent/app/api/routes.py`
  - `POST /api/chat` 改为调用 `run_chat_or_command`，兼容旧同步响应。
  - 新增 `POST /api/chat/stream`，先发送 status，再发送 delta，最后发送 done；异常映射为 SSE error。
- `web/static/app.js`
  - 提交消息时调用 `sendMessageStream`。
  - 在等待期间插入 `isPending` assistant 消息。
  - 收到 delta 后更新同一个 assistant 消息内容。
  - assistant 内容进入最小 Markdown 渲染器，先 escape HTML，再识别常见 Markdown 结构。
- `web/static/styles.css`
  - 增加 typing dots、Markdown 正文、代码块、列表和标题样式。

## 数据流

```text
browser submit
  -> POST /api/chat/stream
  -> WebRuntime.run_chat_or_command
     -> slash command: return command text without AgentLoop
     -> normal message: AgentLoop.run_turn
  -> SSE status/delta/done
  -> browser updates pending assistant bubble
```

## 变更文件

- `agent/app/runtime.py`
- `agent/app/api/routes.py`
- `web/static/app.js`
- `web/static/styles.css`
- `web/static/index.html`
- `tests/unit_test/app/test_api_routes.py`
- `tests/unit_test/app/test_case.md`
- `docs_design/README.md`
- `README.md`

## 测试方案

- `POST /api/chat` 遇到 slash 命令时返回 command reply，fake runtime 的 `run_chat` 不被调用。
- `POST /api/chat/stream` 返回可解析 SSE，包含 `status`、`delta`、`done`。
- `POST /api/chat/stream` 遇到 slash 命令时同样不调用 `run_chat`。
- provider/config/runtime 错误映射到 SSE `error` 事件，不暴露 traceback。
- 运行 `python -m ruff check .`。
- 运行 `python -m pytest --basetemp .tmp/pytest_basetemp`。

## 验收标准

- 模型下拉框只显示模型名。
- `/model`、未知 slash 命令等 Web 输入不会进入 LLM。
- 发送后立即出现 assistant typing 反馈。
- assistant Markdown 文本能以标题、列表、代码块、加粗等形式展示。
- 普通同步 API 仍可用，新增流式 API 不破坏旧调用方。
