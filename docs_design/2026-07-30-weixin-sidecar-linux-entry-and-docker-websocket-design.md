# 微信 Sidecar Linux 入口与 Docker WebSocket Runtime 设计记录

> 日期：2026-07-30
>
> 状态：已落地
>
> 归属：Part 17 私有镜像真实运行链补强

## 1. 背景

微信 Sidecar 源码当前通过手工拼接 `file:///` 判断模块是否为进程直接入口。Windows 路径在现有本地测试中可以碰巧命中，但 Linux 绝对路径会形成多余斜杠，容器内执行 `node .../dist/main.js` 时可能只加载模块而不启动 NDJSON Sidecar。

同时，Gateway 使用 FastAPI/Uvicorn 提供浏览器 WebSocket，但 Python 基础依赖只声明普通 `uvicorn`，Docker 仅安装 `.[qq]`。不能依赖 QQ SDK 或其它传递依赖偶然带入 WebSocket 协议实现。

## 2. 目标

1. 使用 Node 标准 `pathToFileURL()` 完成 Windows/Linux 一致的直接入口判断。
2. 用真实 Node 子进程覆盖 `hello`、`health.get`、`binding.start` 二维码和连接事件链，测试不访问公网。
3. 在 Python 项目元数据中显式声明 Gateway WebSocket runtime extra，并让 Docker 镜像安装该 extra。
4. 用部署静态测试锁定 Sidecar Linux 入口和 Docker WebSocket 依赖，不修改现有 Python 3.11 或本地 smoke 修复。

## 3. 范围边界

- 不改变 Sidecar NDJSON 协议、微信凭据结构或业务状态机。
- 不把测试 fetch fixture 带入生产入口；fixture 仅由 Node 测试子进程预加载。
- 不更换 Uvicorn、FastAPI 或前端 WebSocket 协议。
- 不修改 `deploy/scripts/run-local.ps1` 和 Subagent Python 3.11 兼容代码。
- 不执行真实微信登录或外网请求。

## 4. 模块设计

### 4.1 Sidecar 直接入口

`integrations/weixin_sidecar/src/main.js` 导入 `node:url` 的 `pathToFileURL`，使用：

```text
process.argv[1] -> pathToFileURL(process.argv[1]).href -> compare import.meta.url
```

该转换由 Node 处理盘符、绝对路径和 URL 转义，不再自行拼接 URL。

### 4.2 真实子进程协议测试

Node 测试启动真实 `src/main.js` 进程，通过 stdin/stdout NDJSON 完成：

```text
hello -> hello.ok
health.get -> health.status(available)
binding.start -> binding.qr -> binding.connected
shutdown -> shutdown.ok -> exit 0
```

测试借助 `NODE_OPTIONS=--import ...` 预加载本地 fetch fixture，fixture 只返回二维码申请与确认的确定性响应，禁止公网调用。

### 4.3 Docker WebSocket runtime

`pyproject.toml` 新增 `gateway` optional dependency，显式包含受限版本的 `websockets`。Docker Runtime 安装 `.[gateway,qq]`，分别表达浏览器 Gateway WebSocket 和 QQ 渠道能力，不依赖传递依赖。

## 5. 数据流

```text
Docker node runtime
  -> node /app/integrations/weixin_sidecar/dist/main.js
  -> pathToFileURL direct-entry match
  -> createSidecar(officialDriver)
  -> stdin/stdout NDJSON

Browser
  -> ws://gateway/ws
  -> Uvicorn websockets protocol implementation
  -> FastAPI WebSocket route
```

## 6. 变更文件

```text
integrations/weixin_sidecar/src/main.js
integrations/weixin_sidecar/test/sidecar.test.js
integrations/weixin_sidecar/test/process-fetch-fixture.js
pyproject.toml
deploy/Dockerfile
tests/unit_test/deploy/test_deploy_assets.py
tests/unit_test/deploy/test_case.md
docs_design/zhice-agent-part17-reliability-diagnostics-deployment-design.md
```

## 7. 测试方案

- `npm test`：既有 Sidecar 单元测试与新增真实子进程协议链。
- `npm run build`：确认 `dist/main.js` 可由源文件生成。
- Deploy Python 测试：锁定 `pathToFileURL`、`gateway` extra、`websockets` 和 Docker `.[gateway,qq]`。
- Ruff 与 deploy 定向 Pytest。

## 8. 验收标准

1. Linux/Windows 直接执行 Sidecar 主文件都会启动 NDJSON 循环。
2. 真实子进程测试收到 `hello.ok`、可用 health、二维码 data URL 和连接终态，并正常 shutdown。
3. 测试二维码链不访问公网、不输出真实凭据。
4. Docker 镜像显式安装 Gateway WebSocket runtime 和 QQ extra。
5. 不覆盖当前共享工作区中的 Python 3.11 与 `run-local` 修复。

## 9. 实施验证

- 微信 Sidecar Node 测试：`14 passed`，包含真实子进程 hello、health、二维码和 shutdown 链。
- 微信 Sidecar build：通过，生成的 `dist/main.js` 使用 `pathToFileURL` 入口判断。
- Deploy 定向测试：通过，锁定 `gateway` WebSocket extra 与 Docker `.[gateway,qq]` 安装命令。
- Ruff：通过。Python 首轮全量并行测试为 `798 passed, 1 skipped, 1 failed`；唯一失败是 Memory Windows retry 用例的并行偶发失败，单独复跑通过。
- 前端：`37 passed`，lint、typecheck、production build 均通过。
- 本地生产镜像：使用临时阿里软件源成功构建，正式 Dockerfile 未改镜像源；Compose 容器 `deploy-zcagent-1` 为 healthy，镜像内 `websockets=15.0.1`，日志包含 `[weixin] channel ready`，Gateway routes 包含 `/ws`。
- 私有 registry push 与真实云端部署尚未执行，继续作为生产环境验收项保留。
