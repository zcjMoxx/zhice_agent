# Gateway 导入收敛设计记录

> 承接：`docs_design/zhice-agent-part6-web-minimum-design.md`、`docs_design/2026-07-01-web-minimum-implementation-design.md`

## 背景

Part 6 首次落地时，Web gateway 从早期 `agent/gateway.py` scaffold 演进到 `agent/app/gateway.py` FastAPI 实现。为了迁移方便，曾保留顶层 `agent/gateway.py` 作为兼容 re-export。

当前项目已经明确不保留这种中间态文件。入口层应直接依赖 app shell，避免仓库里同时存在真实实现路径和兼容转发路径，降低后续维护时误改旧文件、旧文档误导当前代码的风险。

## 目标

- 删除 `agent/gateway.py` 顶层兼容导出文件。
- `agent/cli.py` 直接从 `agent.app.gateway` 导入 gateway 能力。
- 当前活文档明确 `agent/gateway.py`、`agent/loop.py`、`agent/context.py` 都不再保留。
- 历史日期设计记录只补充过时说明，不重写当时正文。
- 测试层增加回归检查，确保不会重新出现 `agent.gateway` 模块。

## 非目标

- 不改变 `zcagent gateway` 命令参数和用户入口。
- 不改变 FastAPI 路由、WebSocket、SSE 或静态资源服务行为。
- 不处理 gateway 运行时日志参数；日志能力由 `2026-07-02-gateway-runtime-logging-design.md` 单独设计。

## 模块设计

- `agent/cli.py`：入口层直接导入 `format_gateway_check` 和 `run_gateway`。
- `agent/app/gateway.py`：继续作为唯一 gateway 实现模块，负责 `create_app`、`run_gateway`、`gateway_status` 和 `format_gateway_check`。
- `docs_design/zhice-agent-overall-design.md` 与 `docs_design/zhice-agent-part6-web-minimum-design.md`：记录当前代码不保留兼容导出层。
- 历史设计记录：在标题下增加 `> 说明：...`，指向当前活文档和本设计记录。

## 数据流

```text
zcagent gateway
  -> agent.cli._run_gateway
  -> agent.app.gateway.run_gateway
  -> agent.app.gateway.create_app
  -> FastAPI / static files / Web runtime
```

## 变更文件

- 删除 `agent/gateway.py`。
- 修改 `agent/cli.py` 的 gateway 导入路径。
- 更新当前活文档和相关历史设计记录。
- 更新 `tests/unit_test/app/test_core_imports.py` 与 `tests/unit_test/app/test_case.md`。

## 测试方案

- 用 `rg` 检查仓库代码和测试中不再出现 `from agent.gateway` 或 `import agent.gateway`。
- 单元测试确认 `agent.gateway` 已不可导入，且 `agent/gateway.py` 不存在。
- 运行 gateway 和 CLI 相关 focused tests。

## 验收标准

- `agent/gateway.py` 不存在。
- `agent.cli` 直接导入 `agent.app.gateway`。
- `agent.gateway` 不能被 `importlib` 发现。
- `zcagent gateway --check` 行为不变。
