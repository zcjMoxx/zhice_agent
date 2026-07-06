# ZhiCe-Agent Web 最小版落地设计记录

> 说明：这是 Part 6 首次落地记录。当前代码已按 `docs_design/2026-07-01-web-core-import-and-model-selector-design.md` 和 `docs_design/2026-07-02-gateway-import-convergence-design.md` 删除 `agent/gateway.py`、`agent/loop.py` 与 `agent/context.py` 兼容导出层；CLI 直接导入 `agent.app.gateway`，core 调用方直接导入 `agent.core.*`。旧正文中“保持旧导入路径可用”的描述不再适用，当前口径以 Part 6 活文档为准。
>
> 承接：`docs_design/zhice-agent-part6-web-minimum-design.md`、`docs_design/zhice-agent-part6-web-ui-design.md`

## 背景

Part 6 活文档已经定义了 Web 最小版目标：在不改变 CLI 聊天行为的前提下，新增本地 Web API、会话读取能力和静态聊天界面。当前代码只有 `agent/gateway.py` 中的 `http.server` scaffold，无法承载真实 `POST /api/chat`、会话 API 和前端静态资源边界。

本次落地触及 3 个以上文件，并引入 `app -> core -> protocols` 分层，因此补充日期设计记录。

## 目标

- 用 FastAPI 承载 `/`、`/health` 和 `/api/*`。
- 提供 `GET /api/sessions`、`GET /api/sessions/{session_id}`、`POST /api/chat`。
- 静态资源放在 `web/static`，由 gateway 以可替换 `static_dir` 方式挂载。
- 将 `AgentLoop` 和 `ContextBuilder` 迁入 `agent/core/`，保留 `agent/loop.py` 和 `agent/context.py` 兼容导入。
- `zcagent gateway --check` 保持只检查并退出，不启动 HTTP 服务。

## 非目标

- 不做登录、鉴权、远程部署、HTTPS、WebSocket、SSE。
- 不新增数据库，继续使用 JSONL SessionStore。
- 不新增模型切换 Web API，第一版 UI 只显示只读模型占位。
- 不把 Web/API 逻辑写入 AgentLoop。

## 模块设计

- `agent/app/gateway.py`：创建 FastAPI app、挂载静态资源、启动 uvicorn、提供健康信息。
- `agent/app/api/routes.py`：声明 API 路由，调用运行时依赖对象。
- `agent/app/api/schemas.py`：定义请求、响应和错误结构。
- `agent/app/runtime.py`：集中构建 Web 运行时依赖，包括 SessionStore、PromptLoader、LLMProvider、ToolRegistry、SkillLoader 和 AgentLoop。
- `agent/core/loop.py`、`agent/core/context.py`：承载原有 core 实现。
- `agent/gateway.py`、`agent/loop.py`、`agent/context.py`：保持旧导入路径可用。
- `web/static/*`：静态聊天页面、样式和 API 调用脚本。

## 数据流

```text
browser
  -> FastAPI route
  -> WebRuntime
  -> AgentLoop.run_turn
  -> LLMProvider / ToolProvider / SkillProvider / SessionStore
```

## 变更文件

- 新增 `agent/app/`、`agent/core/`、`web/static/` 和 `tests/unit_test/app/`。
- 修改 `agent/cli.py`、`agent/gateway.py`、`agent/loop.py`、`agent/context.py`、`pyproject.toml`、`README.md`、`docs_design/README.md`。

## 测试方案

- API 单元测试用 fake runtime 覆盖 session list、session read、chat 成功和错误响应。
- gateway 测试覆盖静态首页、`/health`、`--check` 非启动行为。
- 兼容测试覆盖旧导入路径和新 `agent.core.*` 导入路径。
- 全量运行 `python -m ruff check .` 和 `python -m pytest --basetemp .tmp/pytest_basetemp`。

## 验收标准

- 浏览器访问 `/` 能打开静态聊天页面。
- `/health` 返回 workspace/config/sessions 基础信息。
- 三条 `/api/*` 返回稳定 JSON 结构。
- Web 聊天能复用现有 AgentLoop 并写入 JSONL 会话。
- CLI 聊天和 `zcagent gateway --check` 行为不变。
