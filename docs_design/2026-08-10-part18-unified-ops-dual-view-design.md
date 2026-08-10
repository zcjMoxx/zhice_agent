# ZhiCe-Agent Part 18 统一 Ops 双视图设计

> 说明：本文的三端同构双视图与服务器同源 Caddy/dashboard/ttyd 拓扑继续有效；服务器浏览器认证已由 `2026-08-10-part18-persistent-ops-login-design.md` 从 Basic Auth 临时缓存改为长期签名 Cookie，正文保留当时方案。

> 日期：2026-08-10
>
> 状态：已实现并进入本地进程、本地 Docker 与服务器生产基线
>
> 前置记录：`2026-08-09-part18-multi-runtime-ops-correction-design.md`、`2026-08-10-part18-server-side-ops-auth-design.md`

## 1. 背景

Part 18 已分别实现本地进程监控页、本地 Docker sidecar 监控页和服务器 restricted ttyd。三种运行形态的安全目标一致，但产品表现不一致：本地页面主要展示状态与日志，服务器终端提供完整固定命令；页面结构、诊断入口和操作反馈也分别维护。用户确认将本地监控体验与服务器 restricted 运维体验统一为同一套双视图 Ops。

## 2. 目标

- 本地进程、本地 Docker 和云服务器均提供“监控面板 / 运维终端”切换。
- 统一状态、日志跟随、诊断、重启、帮助、视觉、滚动和按钮反馈。
- 本地运维终端只实现安全的通用固定命令；服务器继续额外提供配置事务命令。
- 服务器独立 Ops 地址在 Agent 容器失效时仍可打开监控面板和 ttyd。
- 切换视图不销毁 ttyd iframe，避免无意义断线重连。

## 3. 范围边界

继续禁止通用 Bash、任意 Docker、任意容器名、任意路径、`sudo -i` 和宿主机完整 Shell。不增加多服务器、多 profile、CLI Session 管理或配置 Secret 新存储。本次不把服务器 Ops 改为 Docker 容器，也不让 Gateway 代理宿主机 Docker/日志/重启动作。

本地通用命令限定为：

```text
status
logs [1..500]
logs-follow
diagnose
restart
help
exit
```

服务器继续保留已确认的完整 `config` 子命令。

## 4. 模块设计

### 4.1 共享页面

共享自包含 Ops 页面提供：

- 运行形态、固定目标、状态与健康摘要；
- 人类可读的状态卡，不直接把 JSON 当主界面；
- 格式化 Gateway 日志、自动跟随、滚动暂停与恢复；
- 结构化诊断区和受确认保护的重启操作；
- “监控面板 / 运维终端”双视图，终端 DOM 保持挂载；
- 本地受限命令控制台或服务器 ttyd iframe。

本地进程和 Docker backend 继续只绑定 loopback，并新增 `/api/meta`、`/api/diagnose` 与受限 `/api/command`。页面不接受 executable、容器名、路径或任意参数。

### 4.2 服务器入口

服务器复用已安装的 Caddy，由独立 systemd 服务在 loopback `7681` 提供统一入口：

```text
private OpsUrl
  -> existing Cloudflare Tunnel
  -> 127.0.0.1:7681 Caddy Basic Auth
       +-- /              shared dashboard
       +-- /api/*         loopback dashboard adapter :7683
       +-- /terminal/*    restricted ttyd :7682
```

Caddy 和 ttyd 使用同一 `owner` credential。Caddy bcrypt hash 由安装器通过 stdin 生成，配置文件不保存明文；ttyd 继续执行第二层 Basic Auth。dashboard adapter 与 ttyd 只监听 loopback，均以 `zhice-operator` 运行；只有固定 Python root wrapper 经过既有 sudoers 执行固定动作。

`zhice-ops.service` 成为统一入口服务，新增 `zhice-ops-dashboard.service` 和 `zhice-ops-terminal.service`。Cloudflare Tunnel 仍只转发 `127.0.0.1:7681`，安全组不开放任何 Ops 端口。

### 4.3 服务器监控 API

dashboard adapter 只提供：

```text
GET  /api/meta
GET  /api/status
GET  /api/logs?lines=1..500
GET  /api/diagnose
POST /api/restart    body={"confirm":"restart"}
```

所有 subprocess 调用使用固定 argv、最小环境、timeout 和输出上限；输出继续经过 root wrapper 的 Secret 脱敏。adapter 不提供配置正文 API，配置编辑仍只在 restricted ttyd 内完成。

## 5. 数据流

```text
monitor tab -> fixed HTTP API -> mode adapter -> fixed target
terminal tab(local) -> restricted command parser -> local supervisor/sidecar
terminal tab(server) -> same-origin iframe -> ttyd -> zhice-ops-shell -> fixed root wrapper
```

服务器切换监控与终端时只改变可见状态，不卸载 iframe。首次 Basic Auth 后页面、API 和 ttyd WebSocket 共用同源认证缓存。

## 6. 变更文件

- 共享 Ops 页面静态资源与本地页面加载逻辑；
- `agent/operations/local_supervisor.py`；
- `deploy/ops/local_sidecar.py`、`deploy/ops/Dockerfile.local`、Compose build context；
- `deploy/ops/libexec/zhice_ops_dashboard.py`；
- `deploy/ops/install.sh`、Caddy 配置模板和三个 systemd unit；
- `deploy/scripts/remote_ops.py` 与部署静态验收；
- Part 18 活文档、总体设计、部署 README 与交叉引用；
- `tests/unit_test/operations/`、`tests/unit_test/deploy/` 及同目录 `test_case.md`。

## 7. 测试方案

- 本地进程真实 smoke：两个 tab、status/logs/diagnose/restart、受限命令、follow/退出。
- Docker sidecar 真实 smoke：固定容器、相同页面和通用命令，拒绝额外参数及任意目标。
- Server adapter 单元测试：固定路由、参数边界、输出上限、timeout、restart confirmation。
- 静态测试：Caddy Basic Auth、loopback、服务依赖、ttyd base path、credential 不进入输出。
- 前端/静态 DOM 测试：tab 切换保持 iframe、状态不展示原始 JSON、按钮反馈和滚动行为。
- 运行 Ruff、全量 pytest、前端 lint/typecheck/test/build、Shell syntax、Caddy validate。

## 8. 验收标准

1. 三种运行形态均能在同一 Ops 页面切换监控面板和运维终端。
2. 本地终端只接受通用固定命令；服务器终端继续接受已确认完整命令集。
3. 服务器匿名访问页面、API、静态资源和终端均返回 `401`。
4. 正确认证后页面、API、ttyd WebSocket 正常，切换视图不主动断开终端。
5. Agent 容器退出后服务器监控页、诊断和 restricted ttyd 仍可用并能恢复固定容器。
6. Ops adapter、ttyd 和 Caddy 均只绑定 loopback；Secret 不进入 Git、日志或响应。
7. 新 Digest 发布后 credential、宿主机配置和双视图入口继续保留。
