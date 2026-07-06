# Gateway 默认端口调整设计记录

> 承接：`docs_design/zhice-agent-part6-web-minimum-design.md`

## 背景

当前 `zcagent gateway` 默认绑定 `127.0.0.1:18791`。用户希望本地默认 gateway 端口改为 `10086`，同时保留 `--port` 显式覆盖能力。

## 目标

- `zcagent gateway` 不传 `--port` 时默认监听 `10086`。
- `agent.app.gateway.run_gateway()` 直接调用时默认端口也统一为 `10086`。
- `zcagent gateway --check` 输出默认 URL 时使用 `10086`。
- 当前活文档和 README 中的默认访问地址同步为 `http://127.0.0.1:10086/`。

## 非目标

- 不改变 `--host`、`--port` 参数语义。
- 不新增端口自动探测、端口冲突重试或多端口监听。
- 不修改历史日期设计记录正文；旧记录只补充当前端口已变更的说明。

## 变更文件

- `agent/cli.py`
- `agent/app/gateway.py`
- `tests/unit_test/cli/test_cli_init.py`
- `README.md`
- `docs_design/zhice-agent-part6-web-minimum-design.md`
- `docs_design/README.md`
- `docs_design/2026-07-01-websocket-primary-chat-design.md`

## 测试方案

- CLI 单元测试确认不传 `--port` 时 `run_gateway` 收到 `port=10086`。
- 既有 `--check --port 19000` 测试继续验证显式端口覆盖。
- 运行 gateway/CLI focused tests 和 lint。

## 验收标准

- 默认启动地址为 `http://127.0.0.1:10086/`。
- 显式 `--port` 不受影响。
- 文档不再把 `18791` 描述为当前默认端口。
