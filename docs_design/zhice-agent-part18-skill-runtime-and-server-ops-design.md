# ZhiCe-Agent Part 18：正式 Skill Runtime、Skill 管理与服务器 Ops

> 文档类型：当前活文档
>
> 当前状态：实现、本机验证与真实服务器部署已进入当前基线。长期签名 Cookie、旧 Basic 自动迁移、dashboard/ttyd index、三个 Ops 服务重启保留登录、主动退出失效、错误 Basic 拒绝、loopback ttyd `401`、固定容器重建、宿主机权威配置只读挂载及 `models.json` 跨 Digest 更新均已验收。真实浏览器 PTY/iframe、15 分钟 idle 后交互和容器故障救援继续作为环境交互验收单列，不属于未实现代码。
>
> 日期设计记录：`docs_design/2026-08-09-part18-skill-runtime-and-server-ops-design.md`
>
> 当前服务器认证修正：`docs_design/2026-08-10-part18-persistent-ops-login-design.md` 使用既有 Cloudflare Tunnel、服务器独立 credential 与长期签名 Cookie；Caddy 认证后只向 loopback ttyd 注入 Basic Auth，不再创建 Access/IdP/MFA，也不依赖浏览器临时 Basic Auth 缓存。
>
> 当前统一 Ops 双视图：`docs_design/2026-08-10-part18-unified-ops-dual-view-design.md` 将本地进程、本地 Docker 与服务器统一为“监控面板 / 运维终端”；服务器由 loopback Caddy 在同一 origin 组合 dashboard API 与 `/terminal/` ttyd。
>
> 多运行形态纠偏：`docs_design/2026-08-09-part18-multi-runtime-ops-correction-design.md` 已进入代码基线。本地终端自动 Ops、Compose 双镜像与 Docker sidecar、Linux systemd/Caddy/dashboard/ttyd、既有 Cloudflare Tunnel 和服务器长期认证链均已完成真实部署或 smoke。
>
> 前置基线：Part 5 Skill source、Part 12 RuntimeEvent、Part 16 Vue Web、Part 17 诊断与私有镜像部署

## 1. 当前定位

Part 18 收敛为三个已确认模块：

```text
Part 18A 正式 Skill Runtime
  -> Part 18B Skill source 状态与 Web 管理
  -> Part 18C 独立服务器 Ops 与 Web 投影入口
```

本阶段没有引入多 profile、keyring、Secret Manager、CLI Session 管理、`zcagent diagnose`、多服务器管理、Skill 市场或宿主机通用 Shell。

云端真实主站与 Ops 地址均属于私有部署信息，分别由 Git 忽略的 `deploy/private/cloud-target.json` 中 `PublicUrl`、`OpsUrl` 提供。公开代码和文档不记录真实 hostname，代码也不从主站推导 Ops 地址。

## 2. 正式 Skill Runtime

### 2.1 指令型与可执行型并存

没有 `runtime` 的合法 `SKILL.md` 仍是指令型 Skill。模型通过 `load_skills` 阅读说明并组合已有 Tool；存在 `scripts/` 不代表自动可执行。

可执行型 Skill 必须显式声明：

```yaml
---
name: weather-report
description: 生成指定城市的天气报告
runtime:
  type: python
  entrypoint: scripts/main.py
  protocol: ndjson-v1
  timeout_seconds: 60
---
```

第一版只接受 Python、相对入口、`ndjson-v1` 和 `1..900` 秒 timeout。非法 runtime 只关闭可执行能力，指令正文仍可加载，并记录安全加载错误。

### 2.2 协议边界

`agent/protocols/skill.py` 当前提供：

- `ExecutableSkillInfo`
- `SkillRunRequest`
- `SkillProgress`
- `SkillResult`
- `SkillRuntimeError`
- `SkillExecutor`
- `ProgressSink`

模型只能调用：

```json
{"skill":"official/weather-report","params":{"city":"上海","days":3}}
```

模型不能提交 executable、entrypoint、cwd、环境变量或 timeout。可信 actor/session/turn/cancellation/RuntimeEvent publisher 由 Tool contextual dispatch 注入。

### 2.3 执行链

```text
run_skill
  -> actor/source/Profile/Tool 可见性交集
  -> explicit executable metadata
  -> params JSON/大小/深度校验
  -> resolve 后入口仍在 Skill root
  -> 当前 Python 解释器 + shell=False + 最小环境
  -> ManagedProcessTree
  -> NDJSON progress/result 校验
  -> SkillResult -> ToolResult -> AgentLoop
```

Executor 对 stdout、stderr、行数、单行和 progress 文本均设上限；timeout、取消、协议错误、输出溢出和正常结束都回收完整进程树。非 JSON stdout 只作为有界内部日志，不伪造 progress；无 typed progress/result 时才允许“最后一行旧式结果 JSON”兼容。

`error_stack` 不进入普通 Tool 输出。progress 和错误文本先脱敏；脚本不接收完整 actor context，也不继承任意宿主环境 Secret。

### 2.4 RuntimeEvent

当前白名单增加：

```text
skill.started
skill.progress
skill.completed
skill.failed
```

Skill Event 使用顶层 `skill_run_id`，并关联外层 `run_skill` 的 `tool_call_id`、`tool_call_record_id` 和 `parent_event_id`。外层 `run_skill` Tool 状态标为 `visibility=internal`，前端和 CLI 主要显示 Skill 状态，避免重复闪烁。

瞬态 progress 不写 Session JSONL；最终 Tool 调用事实仍随 Tool message、Runtime Activity 和 trace 留证。

Part 19 已用该正式 Runtime 交付 `zhice-official/travel-planner`。其 optimizer 使用严格 object `params_schema`、UTF-8 `ndjson-v1` progress/result、60 秒 timeout 和无网络纯计算；`run_skill` 外层继续关联 Tool/Skill Event，最终计划由独立内部 `finalize_travel_plan` Tool 收口。当前应用口径见 `docs_design/zhice-agent-part19-intelligent-travel-planner-design.md`。

## 3. Skill source 状态与管理

### 3.1 持久状态

状态文件位于 workspace `state/skill_sources.json`，原子写入：

```text
source
enabled / sync_enabled
configured_target
materialized_root
current_commit
last_sync_started_at / last_sync_finished_at / last_success_at
last_status / health
skill_count / load_error_count
last_error_code / last_error_message_safe
```

对外 API 只返回安全字段，不返回 materialized 绝对路径、credential URL 或原始 stderr。同步失败只保留稳定错误码和安全摘要。

### 3.2 索引缓存

派生索引位于 workspace `state/skill_index.json`。`SKILL.md` 路径、mtime、大小和内容 fingerprint 变化时重建；同步和手工刷新会原子失效；损坏缓存删除后从 source root 真值重扫。缓存读写失败不成为新的 Skill 真值，也不阻断无 Skill 聊天。

### 3.3 权限

source 可选配置：

```yaml
allowed_roles: [owner]
allowed_permissions: [some.permission]
```

存在 role 条件时要求 actor 至少命中一个 role；存在 permission 条件时要求全部命中。Web Turn 的 system prompt、`load_skills`、`run_skill` 和管理 catalog 使用同一个 actor-filtered provider；Subagent 再与 Profile `allowed_skills` 和 Tool allow/deny 取交集。

管理权限：

- `skill.sources.read`：读取技术状态和刷新派生索引。
- `skill.sync`：同步配置内 source。
- 服务器 Ops 入口：只认唯一 Owner role，不能委派给 admin。

### 3.4 Web/API

管理后台提供 Skills 页面：source、target/commit、同步时间、health、Skill/load error 数量、安全错误摘要、同步、刷新索引和 actor 可见 Skill 列表。

主 Web 只投影 `operations.terminal` 的非敏感配置：

```yaml
operations:
  terminal:
    enabled: false
    url: ""
    presentation: both
```

生产 URL 必须 HTTPS，拒绝 userinfo、query 和 fragment；`presentation` 只允许 `new_tab|embed|both`。页面同时支持新窗口和 iframe；iframe load error/超时会提示并回退新窗口。Gateway 不代理 Ops WebSocket、日志、Docker 或重启动作。

## 4. 多运行形态独立 Ops

当前实现统一投影 `mode/target_type/target_name/url/presentation`：

- `local_process`：`zcagent gateway` 默认启动 loopback `LocalOpsSupervisor`，优先端口 `17681`、有界 fallback 到 `17690`，状态写入 workspace `state/operations.json`；supervisor 固定拥有本次 `zcagent-gateway` child，提供共享监控页、通用 restricted 命令、status、日志、诊断和受控重启，退出时回收完整进程树。
- `local_docker`：Compose 固定启动 `zhice-agent` 与独立 `zhice-agent-ops`，两端口只发布到 `127.0.0.1`；sidecar 复用同一双视图页面和通用 restricted 命令，Docker API 路径固定包含 `zhice-agent`，不接受浏览器提交容器名、Docker 参数、路径或服务器配置命令。
- 本地进程 supervisor 将 Gateway child stdout/stderr 同时 tee 到原启动终端和浏览器有界缓冲；真实 TTY 中保留原有时间/动作/告警 ANSI 配色，浏览器对去 ANSI 后的同一批 `INFO/WARNING/HTTP/agent.turn.*` 人类可读输出使用安全 DOM 分段着色，不读取结构化 JSONL。Docker 页面读取固定容器 stdout/stderr。两者默认每秒刷新并自动滚动到底部；用户向上翻阅时暂停，`Continue follow` 立即恢复并拉取最新日志，页面使用暗色细圆角 scrollbar 且不保留重复的手动刷新按钮；暂停和重启按钮具有按下、执行中和结果反馈。主 Web 的独立窗口、页面内嵌和关闭投影使用统一按钮体系，管理后台小字与技术值使用明确字体规范，长行强制换行。
- `server_docker`：宿主机 systemd Caddy/dashboard/ttyd 继续监控服务器固定 `zhice-agent`；同一 Ops origin 提供共享监控页与 `/terminal/`，主站与 Ops URL 分别来自 Git 忽略私有配置的 `PublicUrl`、`OpsUrl`。

Web 先读取有效运行态 state，再读取启动器注入的非敏感环境投影，最后才回退到静态 `operations.terminal`。本地环境不会误跳到云端地址。

### 4.1 安全拓扑

```text
Browser
  +-- configured PublicUrl -> ZhiCe-Agent container
  +-- configured Ops URL -> existing Cloudflare Tunnel -> host loopback Caddy Cookie auth
                                                          +-- dashboard adapter
                                                          +-- /terminal/ -> proxy-injected ttyd Basic Auth
                                                                            -> zhice-ops-shell
                                                                            -> fixed root wrapper
                                                                            -> zhice-agent only
```

Ops 的 Caddy、dashboard adapter 与 ttyd 由宿主机 systemd 管理，独立于 Agent 容器；既有 cloudflared connector 只负责 Tunnel 传输。`zhice-operator` 使用 nologin、不是 docker group 成员；ttyd 唯一后端是 `zhice-ops-shell`，不是 Bash。

仓库固定 ttyd 版本和 SHA256，提供 systemd、Caddy 同源组合、Origin、单会话、15 分钟 idle、主题和安装模板。服务器首次安装生成 `owner` 独立高熵 credential，root-only 保存并跨升级保留；首次登录后 dashboard adapter 签发长期 `Secure`/`HttpOnly` Cookie，Caddy 用 `forward_auth` 统一保护页面/API/终端，并只在 loopback 代理层向仍保留第二层认证的 ttyd 注入 Basic header。Cookie 不含 credential，credential 轮换自动撤销旧 Cookie，Gateway 不接触两者。15 分钟 idle 只结束 PTY，不注销浏览器登录。真实 systemd、浏览器重启复用、Tunnel 与 WebSocket 行为不能由仓库静态测试替代。

### 4.2 Restricted 命令集

只允许：

```text
status
logs [bounded options]
logs-follow
diagnose
config view <config.yml|models.json|.env>
config edit <config.yml|models.json|.env>
config validate
config diff
config backup
config restore <backup-id>
config apply
restart
help
exit
```

parser 不使用 `eval`、`sh -c` 或任意 Docker 参数。固定 wrapper 只操作 `zhice-agent` 和 `/etc/zhice-agent/runtime`；拒绝通用 Bash、`sudo -i`、任意容器名、任意路径、管道、重定向、命令替换、停止/删除容器和部署镜像。restart 需要二次确认。

### 4.3 宿主机权威配置

云端权威副本：

```text
/etc/zhice-agent/runtime/.env
/etc/zhice-agent/runtime/config.yml
/etc/zhice-agent/runtime/models.json
/etc/zhice-agent/runtime/backups/
```

首次部署从受控私有镜像安全复制三份基线到 staging，全部校验成功后原子初始化，过程不输出正文。已有权威文件默认保留；部分缺失 fail closed，避免混用版本。云端 `docker run` 把三份文件分别只读 bind mount 到 `/home/zhice/.zhice/config/` 原路径，因此容器重启和新 Digest 重建继续使用宿主机副本。

编辑使用 pending + 自动备份；`.env`、YAML、JSON 与 ZhiCe 语义校验通过后才能 apply。apply 原子替换 active、重建固定容器并做 health；失败恢复备份。审计只记录动作、固定目标和结果，不记录正文或 Secret。

### 4.4 宿主机诊断

`deploy/scripts/diagnose.sh` 即使 Agent 容器 missing/exited/restarting 也能输出有界诊断：Docker daemon、容器状态/health/exit/OOM/image/digest/restarts、固定 volumes、磁盘、host port、本地 health 和私有 `PublicUrl` 对应的公网 health。

`status.sh`、`logs.sh`、`restart.sh` 已收敛为固定容器动作；日志行数与字节数有界，Secret 经过脱敏。`stop.sh` 仍只属于发布维护链，不进入 restricted shell。

## 5. 当前验证与环境交互边界

本机必须通过：

```text
python -m ruff check .
python -m pytest --basetemp .tmp/pytest_basetemp
npm run lint
npm run typecheck
npm run test
npm run build
Ops Python/静态/parser/shell syntax/PowerShell parser/compose config
```

目标 Linux/Cloudflare 已自动验收：

- systemd install/start/restart 与 root-owned mode；
- `zhice-operator` nologin、非 docker group、固定 sudo wrapper 与 loopback 端口边界；
- Cloudflare Tunnel 路由、首次/错误登录、长期 Cookie、主动退出和 ttyd 无/错误 Basic Auth 拒绝；
- 三份配置首迁、只读挂载、编辑/校验/备份/restore/apply、容器重建和跨 Digest 保留；
- journald、ttyd 和发布日志无 Secret；
- 最终私有 `PublicUrl` 对应的公网 health 恢复。

仍需人工环境交互验收：

- 真实浏览器重启后的 Cookie 复用；
- ttyd WebSocket 输入、resize、断开、15 分钟 idle 后免登录重连、max session 和 backpressure；
- 主 Web iframe 成功、Cookie/浏览器策略失败回退新窗口；
- running/exited/restarting/missing/Docker unavailable 的完整 UI 呈现；
- Agent 容器退出后从独立 Ops URL 完成真实救援。

这些项目用于验证浏览器、PTY 和故障场景，不表示核心实现或服务器部署尚未完成。
