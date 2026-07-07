# 智策 Agent 第六部分详细设计文档：Web 最小版

> 关联规范：`AGENTS.md`
>
> 文档类型：阶段活文档。本文档始终按当前代码和当前阶段口径维护。
>
> 承接文档：`docs_design/zhice-agent-part5-skill-loader-design.md`
>
> 当前状态：已实现 Web 最小版。当前代码提供 FastAPI gateway、REST/SSE 兼容聊天 API、WebSocket 主聊天通道、会话 API、模型选择 API、Web stop 和 `web/static` 静态前端；仍不包含鉴权、多用户或远程部署安全边界。

---

## 1. 背景

前五部分已经完成一个可运行的本地 CLI Agent 内核：

- CLI 能加载 workspace 配置、Prompt、Session、LLM endpoint 和 Skill source。
- `AgentLoop` 已经能完成多轮 tool calling、受控 `exec`、Skill 说明加载和 Skill 脚本执行。
- `zcagent gateway` 已经作为稳定 Web 入口存在，当前提供 `/`、`/health`、`/api/*`、`/ws` 和静态前端。

第六部分的目标不是把项目改造成完整平台，而是给现有内核加一个最小 Web 使用面：

```text
browser
  -> Web API
  -> AgentLoop
  -> LLMProvider / ToolProvider / SkillProvider / SessionStore
```

这个阶段也正好是目录边界演进的触发点：一旦出现真实 `POST /api/chat` 和会话查询 API，就不能继续把 HTTP 路由、前端资源和启动逻辑都塞在 `agent/gateway.py` 里。Web/API 属于 app shell，AgentLoop 仍然属于 core。

---

## 2. 目标

1. 支持在浏览器里完成一次普通聊天。
2. 支持 Web 端查看会话列表和单个会话消息。
3. 提供最小 HTTP API：`POST /api/chat`、`POST /api/chat/stream`、`GET /api/sessions`、`GET /api/sessions/{id}`、`PATCH /api/sessions/{id}`、`DELETE /api/sessions/{id}`、`GET /api/models`、`POST /api/model/preference`。
4. 保留现有 `zcagent gateway` 命令入口和 `--check` 行为。
5. 引入轻量 `app -> core -> protocols` 分层，避免 Web/API 逻辑反向进入 AgentLoop。
6. Web 聊天复用现有配置、Prompt、SessionStore、LLMProvider、ToolRegistry、SkillLoader 和 `AgentLoop`。
7. 浏览器主聊天使用同端口 `WebSocket /ws`；REST 与 SSE 保留给兼容或外部一次性调用。
8. 前端只做本地单用户最小界面：会话列表、重命名/删除、聊天窗口、输入框、基础加载、停止和错误状态。
9. 默认不改变 CLI 行为。

---

## 3. 范围边界

本阶段包含：

- `agent/app/gateway.py`：Web 服务启动、静态资源挂载、健康检查。
- `agent/app/api/routes.py`：HTTP 路由与请求处理。
- `agent/app/api/ws.py`：WebSocket 主聊天通道。
- `agent/app/api/schemas.py`：请求、响应和错误结构。
- `agent/app/runtime.py`：Web 运行时依赖装配、模型状态、会话 mutation 和 active turn cancellation。
- `web/static/`：最小 HTML/CSS/JS 前端。
- `agent/core/`：承载 `AgentLoop` 和 `ContextBuilder` 等 core 模块。
- `pyproject.toml`：新增 Web 运行依赖。
- `tests/unit_test/app/`：API、WebSocket、schema、gateway 行为和 core 直接导入测试。
- `docs_design/README.md`：登记第六部分活文档。

本阶段不包含：

- 登录、用户系统和多租户隔离。
- 远程公网部署、安全网关和 HTTPS。
- 多模型管理页面。
- Skill 市场、Skill 编辑器或后台同步面板。
- Memory、MCP、Hooks、Subagent。
- 数据库会话存储。继续使用现有 JSONL SessionStore。
- 完整前端工程化。第一版不引入 Vite、React、Vue 或复杂构建链。
- 持久化 turn 模型。当前 Web stop 只依赖内存态 active turn 与 cancellation token。

---

## 4. 分层设计

### 4.1 目标依赖方向

```text
agent/app/gateway.py
agent/app/api/routes.py
agent/app/api/ws.py
web/static/*
        |
        v
agent/core/loop.py
agent/core/context.py
        |
        v
agent/protocols/*
```

规则：

- `app` 可以依赖 `core`、`protocols`、`config`、`session`、`tools`、`skills` 和 `llm` 的工厂函数。
- `core` 不能 import `app`。
- `AgentLoop` 仍然只处理通用循环，不知道 HTTP、浏览器、JSON schema 或前端状态。
- HTTP 层负责参数校验、错误映射、响应结构和前端资源。

### 4.2 Core 直接导入

当前代码里 `AgentLoop` 和 `ContextBuilder` 已经收敛到 `agent/core/`：

```text
agent/core/loop.py       # AgentLoop 真实实现
agent/core/context.py    # ContextBuilder 真实实现
```

仓库内调用方直接导入 `agent.core.loop` 和 `agent.core.context`。不再保留 `agent/loop.py` 或 `agent/context.py` 中间导出层。

### 4.3 Gateway 启动职责

`zcagent gateway` 继续是用户入口：

```bash
zcagent gateway --host 127.0.0.1 --port 10086
zcagent gateway --check
```

第六部分落地后：

- `--check` 仍只做配置检查，不启动服务。
- `/health` 返回服务状态和 workspace 信息。
- `/` 返回最小 Web 页面。
- `/api/*` 由 API router 处理。
- `/ws` 是浏览器主聊天通道。

---

## 5. API 设计

### 5.1 `GET /api/sessions`

用途：返回当前 workspace 下已有会话列表。

响应：

```json
{
  "sessions": [
    {
      "session_id": "chat-20260701",
      "title": "讨论 WebSocket",
      "message_count": 12,
      "updated_at": "2026-07-01T11:30:00+08:00",
      "preview": "最近一条消息摘要"
    }
  ]
}
```

说明：

- 数据来自现有 `JsonlSessionStore.list_sessions()`。
- `title` 来自 session sidecar metadata；没有标题时前端使用 preview 或 session id。
- `updated_at` 输出 ISO 8601 字符串；内部可以继续用文件 mtime。
- 列表默认按最近更新时间倒序。

### 5.2 `GET /api/sessions/{session_id}`

用途：读取指定会话消息。

响应：

```json
{
  "session_id": "chat-20260701",
  "messages": [
    {
      "role": "user",
      "content": "你好"
    },
    {
      "role": "assistant",
      "content": "你好，我在。"
    }
  ]
}
```

约束：

- `session_id` 必须使用现有 SessionStore 能安全处理的 id。
- 不返回超大历史。第一版可复用当前最近消息裁剪策略，或在 API 层加 `limit` 默认值。
- 工具调用消息可以先以结构化 JSON 原样返回，前端第一版只渲染用户和助手文本；工具日志面板作为可选展示。

### 5.3 `PATCH /api/sessions/{session_id}`

用途：设置会话展示标题，不改变 JSONL 文件 id。

请求：

```json
{
  "title": "新的会话标题"
}
```

响应：

```json
{
  "session_id": "chat-20260701",
  "status": "renamed",
  "title": "新的会话标题"
}
```

### 5.4 `DELETE /api/sessions/{session_id}`

用途：删除指定会话消息和 sidecar metadata。Web 删除当前会话时，前端进入空界面；后端会先尝试取消该 session 的 active turn。

响应：

```json
{
  "session_id": "chat-20260701",
  "status": "deleted"
}
```

### 5.5 `POST /api/chat`

用途：向指定会话发送一条用户消息，并返回助手最终文本。

请求：

```json
{
  "session_id": "chat-20260701",
  "message": "请看看当前目录有什么文件"
}
```

响应：

```json
{
  "session_id": "chat-20260701",
  "assistant": {
    "role": "assistant",
    "content": "当前目录包含 ..."
  }
}
```

错误响应：

```json
{
  "error": {
    "code": "INVALID_REQUEST",
    "message": "message is required"
  }
}
```

第一版错误码：

- `INVALID_REQUEST`：请求体缺字段、类型错误、空消息或 session id 不合法。
- `CONFIG_ERROR`：workspace、LLM endpoint 或 Skill source 配置无法加载。
- `LLM_ERROR`：Provider 调用失败。
- `TOOL_ERROR`：工具链返回失败但无法生成最终回复。
- `INTERNAL_ERROR`：未预期异常。

### 5.6 `POST /api/chat/stream`

用途：保留 SSE 兼容调用。浏览器主聊天不再依赖它，但外部一次性调用可以继续使用。

事件：

```text
status  {"phase":"accepted"}
delta   {"content":"..."}
done    {"session_id":"...","assistant":{"role":"assistant","content":"..."}}
stopped {"session_id":"...","assistant":{"role":"assistant","content":"[stopped]"}}
error   {"error":{"code":"LLM_ERROR","message":"..."}}
```

### 5.7 `WebSocket /ws`

用途：浏览器主聊天通道。前端发送 message frame，后端返回 `channel_status` 与 `channel_text`。

主要 frame：

```json
{"type":"hello","client":"web"}
{"type":"message","session_id":"chat-20260701","content":"你好","model":"gpt-5"}
{"type":"stop","session_id":"chat-20260701"}
{"type":"heartbeat","session_id":"chat-20260701"}
```

主要事件：

```json
{"event":"connected","data":{"connection_id":"ws-..."}}
{"event":"hello","data":{"client":"web","command_profile":"web","capabilities":{"history_command":false,"exit_command":false}}}
{"event":"channel_status","session_id":"chat-20260701","data":{"type":"accepted","turn_id":"turn-..."}}
{"event":"channel_text","session_id":"chat-20260701","data":"增量文本"}
{"event":"channel_status","session_id":"chat-20260701","data":{"type":"done","turn_id":"turn-...","assistant":{"role":"assistant","content":"..."}}}
{"event":"channel_status","session_id":"chat-20260701","data":{"type":"stopped","turn_id":"turn-...","assistant":{"role":"assistant","content":"[stopped]"}}}
```

说明：

- `hello` 可声明 `client="web"` 或 `client="external"`；未声明时默认按 `web` 处理。
- 浏览器通道不支持 `/history` 和 `/exit`，但仍在后端短路为英文“不支持当前通道”文本，不透传给 LLM。
- external command profile 声明 `client="external"` 后支持 `/history` 和 `/exit`；其中 `/exit` 只关闭当前 WS 连接，不退出 gateway。
- `/sessions` 支持与 CLI 对齐的子命令：`/sessions` 列表，`/sessions rename <id> <title>` 重命名，`/sessions delete (<id>)` 删除指定会话；不带 id 删除时清空当前会话。
- `content="/stop"` 或 `type="stop"` 都在 WebSocket 路由层拦截，不透传给 LLM。
- 当前 WebSocket accepted 的 `turn_id` 和 runtime active turn 仍未完全统一，第七部分施工图见 `docs_design/zhice-agent-part7-turn-context-design.md`。

### 5.8 `GET /api/models`

用途：返回当前 Web gateway 进程使用的 endpoint、当前模型和该 endpoint 可选模型。

响应：

```json
{
  "endpoint": "openai_gpt5",
  "current_model": "gpt-5",
  "models": ["gpt-5", "gpt-5-mini"]
}
```

约束：

- `models` 来源于当前 `LLMEndpoint.model` 和 `supported_models`。
- 不返回 `api_key`、`base_url` 或配置文件路径。
- 第一版只针对当前 endpoint 切换模型，不做完整 endpoint 管理页。

### 5.9 `POST /api/model/preference`

用途：为当前 Web gateway 进程设置当前 endpoint 的模型偏好。

请求：

```json
{
  "model": "gpt-5-mini"
}
```

响应同 `GET /api/models`。

约束：

- 模型必须在当前 endpoint 的默认模型或 `supported_models` 中。
- 非法模型返回 `INVALID_REQUEST`。
- 该偏好只影响当前 gateway 进程，不写回配置文件。

---

## 6. 前端设计

第一版前端使用静态文件，不引入构建工具：

```text
web/
  static/
    index.html
    styles.css
    app.js
```

界面包含：

- 左侧侧边栏：显示 logo、折叠按钮、`New chat`、`Search chats`、`Recents` 和用户入口占位。
- 中间聊天区：按时间顺序显示 user/assistant 消息。
- 底部输入框：提交后调用 `WebSocket /ws`，同步 `POST /api/chat` 与 `POST /api/chat/stream` 保留给兼容调用方。
- 输入栏右下角模型选择下拉栏，显示当前 endpoint 下的可用模型并允许切换。
- 状态提示：pending typing、streaming cursor、发送中、停止中、失败、空会话。
- 会话操作：当前静态前端提供重命名和删除按钮；删除当前会话后进入空界面。
- assistant 输出：使用最小安全 Markdown 渲染标题、列表、代码块、加粗、行内 code 和安全链接。

前端约束：

- 不做登录页。
- 不做营销首页。
- 不在界面里写大段说明文字。
- 不要求移动端复杂适配，但窄屏下会话列表应能折到顶部或隐藏。
- 错误只显示简短可读消息，详细堆栈不暴露给浏览器。
- 不显示推荐问题、知识库、技能市场、定时任务或其它当前未实现入口。

当前 UI 的详细布局、状态和视觉规则见 `docs_design/zhice-agent-part6-web-ui-design.md`。

### 6.1 为什么第一版先用静态资源

静态 HTML/CSS/JS 不是长期限定，而是第六部分的最小可用入口。它的作用是先验证 Web API、会话读取、聊天提交和 gateway 服务边界，避免在 API 还没稳定前同时引入前端工程化、构建产物、Node 依赖和开发代理。

为了以后好升级，第一版静态前端要遵守这些约束：

- API 路径固定在 `/api/*`，前端页面路径固定由 gateway 服务，避免后续 Vue/Vite 改造时改 API。
- `app.js` 内部把 API 调用集中成小函数，例如 `fetchSessions()`、`fetchSession(sessionId)`、`sendMessage(payload)`。
- 不把大量业务状态写进 HTML inline script，避免以后迁移组件时难拆。
- CSS 使用少量语义类名和基础变量，避免写成不可维护的临时样式堆。
- gateway 的静态资源服务逻辑保留 `static_dir` 概念，第一版服务 `web/static`，后续可以服务前端构建后的 `web/dist`。

### 6.2 什么时候升级到 Vue/Vite

出现以下情况时，再单独设计并引入工程化前端：

- 页面超过聊天主界面，开始包含 `/model` 控制面、Skill source 状态页、工具调用日志、设置页等多个视图。
- 前端状态明显复杂，需要组件化、路由、状态管理或系统化表单。
- 需要工具步骤折叠、长会话虚拟滚动、复杂消息操作、多页面设置等交互。
- 需要前端单元测试、类型检查、热更新和独立设计系统。
- 静态 `app.js` 已经变得难以阅读，继续维护成本高于引入构建链。

如果升级，建议使用根目录独立前端工程：

```text
web/
  package.json
  vite.config.ts
  src/
    main.ts
    api/
    components/
    views/
  dist/
```

运行方式：

- 开发态：`web` 使用 Vite dev server，代理 `/api` 到 `zcagent gateway`。
- 生产态或本地单命令态：`npm run build` 生成 `web/dist`，gateway 服务 `web/dist`。
- API 合约仍由 `agent/app/api/schemas.py` 和 `/api/*` 路由定义，前端工程不能反向影响 Agent core。

这意味着第六部分静态版不是死路，而是可替换的第一层 UI 壳。真正需要 Vue/Vite 时，迁移重点是替换页面资源，不是重写 AgentLoop 或 API。

---

## 7. 数据流

### 7.1 启动

```text
zcagent gateway
  -> load_app_config
  -> config.ensure_dirs()
  -> build runtime dependencies
       -> JsonlSessionStore
       -> PromptLoader
       -> LLMProvider chain
       -> ToolRegistry
       -> SkillSourceSync / SkillLoader
       -> AgentLoop
  -> start FastAPI app
```

### 7.2 聊天

```text
browser submit message
  -> WebSocket /ws message frame
  -> validate frame
  -> optional model preference update
  -> WebRuntime.run_chat_events(session_id, message)
  -> AgentLoop.run_turn(session_id, message, on_event, cancellation_token)
  -> ContextBuilder loads prompt/history/skill summaries/current user message
  -> LLMProvider.stream_chat(...) when available, otherwise chat(...)
  -> ToolRegistry executes tool calls when needed
  -> SessionStore appends user/tool/assistant messages
  -> WebSocket emits channel_text and final channel_status
```

REST/SSE 兼容路径仍保留：

```text
external caller
  -> POST /api/chat or POST /api/chat/stream
  -> same WebRuntime / AgentLoop path
```

### 7.3 会话读取

```text
browser opens session
  -> GET /api/sessions/{session_id}
  -> JsonlSessionStore.load(session_id)
  -> API formats messages
  -> browser renders chat history
```

### 7.4 停止 active turn

```text
browser stop button or input /stop
  -> WebSocket /ws stop frame
  -> WebRuntime.cancel_session(session_id)
  -> CancellationToken.cancel()
  -> AgentLoop reaches cancellation checkpoint
  -> SessionStore appends assistant stopped marker
  -> WebSocket emits stopped status
```

当前 stop 仍是内存态 active turn 能力，不代表 turn 已持久化。第七部分 turn 施工图见 `docs_design/zhice-agent-part7-turn-context-design.md`，背景记录见 `docs_design/2026-07-04-turn-runtime-and-context-design.md`。

---

## 8. 运行依赖

第六部分需要新增 Web 运行依赖：

```toml
dependencies = [
  "fastapi>=0.110.0",
  "uvicorn>=0.29.0",
]
```

取舍：

- FastAPI 只用于本地 HTTP API 和静态资源服务。
- Uvicorn 只作为本地开发和单进程运行 server。
- 暂不引入数据库、前端构建工具、后台任务框架或进程管理器。

---

## 9. 安全与边界

第一版只面向本地开发，默认监听 `127.0.0.1`。

必须保持：

- `exec` 仍受 workspace guard、超时、危险命令拦截和输出截断约束。
- HTTP 请求不能绕过 AgentLoop 直接执行工具。
- API 不接收任意文件路径读取请求；读文件仍必须经 LLM tool 调用和已有工具策略。
- 错误响应不暴露 API key、环境变量、完整堆栈或本地敏感路径。
- 如果用户显式设置 `--host 0.0.0.0`，CLI 输出应提示这是本地开发服务，不自带鉴权。

---

## 10. 变更文件

预计新增：

```text
agent/app/__init__.py
agent/app/gateway.py
agent/app/runtime.py
agent/app/api/__init__.py
agent/app/api/routes.py
agent/app/api/ws.py
agent/app/api/schemas.py
agent/core/__init__.py
agent/core/loop.py
agent/core/context.py
web/static/index.html
web/static/styles.css
web/static/app.js
tests/unit_test/app/test_gateway.py
tests/unit_test/app/test_api_routes.py
tests/unit_test/app/test_case.md
```

预计修改：

```text
agent/cli.py
pyproject.toml
docs_design/README.md
README.md
```

说明：

- `agent/gateway.py`、`agent/loop.py` 和 `agent/context.py` 已删除，不再作为兼容导出层。
- CLI 入口直接导入 `agent.app.gateway`；core 调用方直接使用 `agent.core.*`。
- `README.md` 只补充最小启动方式，不写成长篇 Web 使用手册。

---

## 11. 测试方案

### 11.1 单元测试

新增 `tests/unit_test/app/test_case.md`，至少覆盖：

1. `zcagent gateway --check` 行为保持不启动服务。
2. `/health` 返回 workspace、config、sessions 基础信息。
3. `GET /api/sessions` 能返回 SessionStore 摘要。
4. `GET /api/sessions/{session_id}` 能返回消息列表。
5. `POST /api/chat` 能调用 fake AgentLoop 并返回助手消息，`POST /api/chat/stream` 能返回 SSE 事件。
6. `PATCH /api/sessions/{session_id}` 能重命名会话标题。
7. `DELETE /api/sessions/{session_id}` 能删除会话消息和 sidecar metadata。
8. `WebSocket /ws` 能建连、发送 message、收到 accepted/text/done。
9. `WebSocket /ws` 的 `type="stop"` 或 `content="/stop"` 能触发 runtime cancellation 并返回 stopped。
10. 空消息、缺失 `session_id`、非法 JSON 返回 `INVALID_REQUEST`。
11. AgentLoop 抛出 LLM/config 错误时，API 返回稳定错误结构。

### 11.2 Core 导入测试

- 现有 CLI 测试继续通过。
- 现有 AgentLoop、Tool、Skill、Session 测试继续通过。
- 仓库内不再使用 `from agent.loop import AgentLoop` 或 `from agent.context import ContextBuilder`。

### 11.3 手工验收

```bash
python -m ruff check .
python -m pytest --basetemp .tmp/pytest_basetemp
zcagent gateway --check
zcagent gateway
```

启动后访问：

```text
http://127.0.0.1:10086/
http://127.0.0.1:10086/health
http://127.0.0.1:10086/api/sessions
```

---

## 12. 验收标准

第六部分完成时应满足：

1. `zcagent gateway --check` 仍能快速验证配置并退出。
2. `zcagent gateway` 能启动本地 Web 服务。
3. 浏览器访问 `/` 能看到最小聊天界面。
4. Web 端能通过 `/ws` 发送消息并展示 pending、streaming 和最终助手回复。
5. Web 端能查看已有 session 列表。
6. Web 端能打开一个 session 并展示历史消息。
7. Web 端能重命名和删除 session；删除当前 session 后进入空界面。
8. Web 端模型下拉框只显示当前 endpoint 下的模型名。
9. Web `/stop` 不透传给 LLM，后端按 session 取消 active turn。
10. HTTP API 使用稳定 JSON 请求和响应结构，SSE 兼容接口仍可解析。
11. Web/API 逻辑不进入 `AgentLoop`，`core` 不 import `app`。
12. CLI 聊天入口行为不变。
13. 默认测试不访问真实 LLM 或网络。
14. `python -m ruff check .` 通过。
15. `python -m pytest --basetemp .tmp/pytest_basetemp` 通过；如果存在无关历史失败，需要在交付说明中写明。

---

## 13. 后续演进

第六部分完成后再考虑：

- 持久化 turn_id，让 Web accepted/done/stopped 与 Session 历史完全统一。
- Gateway / Agent 运行日志优化，复用统一后的 `turn_id` 打印 turn、LLM、tool 和 session 保存轨迹。
- 用户、登录与权限执行边界设计；这属于后续安全执行主线，不并入第六部分 Web 最小版。
- CLI `/stop`，等待 turn 持久化、active turn registry 和并发输入通道稳定后再做。
- 会话自动标题、归档和全文搜索。
- 工具调用日志面板。
- `/model` Web 控制面。
- Skill source 状态页。
- 登录页、用户/角色/权限管理页和审计视图；这些等权限设计确定后再做。
- Dockerfile 和本地容器运行方式。

Memory、MCP、Hooks 和 Subagent 继续按后续里程碑单独设计，不并入 Web 最小版。
