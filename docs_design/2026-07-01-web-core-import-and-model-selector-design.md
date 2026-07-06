# Web Core 导入收敛与模型选择设计记录

> 承接：`docs_design/2026-07-01-web-minimum-implementation-design.md`

## 背景

Part 6 首次落地时为了降低迁移风险，保留了 `agent/loop.py` 和 `agent/context.py` 作为 `agent.core.*` 的 re-export。当前用户明确要求删除这层中间入口，让所有调用方直接导入 `agent.core.loop` 和 `agent.core.context`。

同时，第一版 Web UI 只显示禁用的 `auto` 模型占位，不能反映当前 endpoint，也不能选择 endpoint 支持的模型。需要把 `/model endpoint/model` 已有语义暴露给 Web 最小界面，但仍不扩展成完整模型管理页。

## 目标

- 删除 `agent/loop.py` 和 `agent/context.py`。
- 仓库内所有导入统一改为 `agent.core.loop` / `agent.core.context`。
- Web API 新增当前模型信息和模型偏好设置接口。
- Web UI 下拉框显示当前 endpoint 下的默认模型和 `supported_models`，可切换模型。
- 聊天区滚动条贴近主页面右侧，消息内容仍保持居中阅读宽度。
- 初始欢迎语改为英文。

## 非目标

- 不做跨 endpoint 的完整模型管理页。
- 不在 Web UI 暴露 API key、base_url 或配置文件路径。
- 不改变 CLI `/model` 的命令语义。

## 模块设计

- `agent/app/runtime.py`：从 LLMProvider 读取当前 endpoint，格式化可用模型，设置当前 endpoint 的模型偏好。
- `agent/app/api/routes.py`：新增 `GET /api/models` 和 `POST /api/model/preference`。
- `web/static/app.js`：页面启动时读取模型列表；下拉变化时提交偏好。
- `web/static/styles.css`：让 `.chat-wrap` 占满主区宽度，只让内部消息列保持最大宽度。

## 测试方案

- API 测试覆盖模型列表和模型切换成功。
- API 测试覆盖模型切换不可用或非法模型时返回 `INVALID_REQUEST`。
- 导入检查用 `rg` 确认仓库内不再引用旧 `agent.loop` / `agent.context`。
- 浏览器验收桌面和窄屏布局，确认无水平溢出且滚动条位置合理。

## 验收标准

- `agent/loop.py`、`agent/context.py` 不存在。
- `python -m ruff check .` 通过。
- `python -m pytest --basetemp .tmp/pytest_basetemp_full_part6_final2 -o cache_dir=.tmp/pytest_cache` 通过。
- Web 首页显示英文欢迎语。
- 模型下拉框显示当前 endpoint 的模型列表，并可提交切换。
