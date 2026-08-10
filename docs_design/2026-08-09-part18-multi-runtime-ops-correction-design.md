# ZhiCe-Agent Part 18 多运行形态 Ops 纠偏设计

> 说明：本文确定的三种运行形态与固定目标语义继续有效；当前共享“监控面板 / 运维终端”双视图和服务器 Caddy/ttyd 同源组合以 `2026-08-10-part18-unified-ops-dual-view-design.md` 为准。

> 说明：本文的多运行形态、私有 `OpsUrl` 与固定目标语义继续有效；公网认证层先由 `2026-08-10-part18-server-side-ops-auth-design.md` 收敛为既有 Cloudflare Tunnel 和服务器独立 credential，随后由 `2026-08-10-part18-persistent-ops-login-design.md` 改为长期签名 Cookie。本文 Access/MFA 相关正文保留为当时记录。

> 日期：2026-08-09
>
> 状态：已实现并完成本地进程、Compose/Docker sidecar 与真实 Linux/Cloudflare 部署验收；浏览器 PTY/iframe、idle 后重连与故障救援继续按 Part 18 活文档单列
>
> 修正对象：`2026-08-09-part18-skill-runtime-and-server-ops-design.md` 中 Part 18C 的运行形态、地址来源与配置 apply 语义
>
> 不变范围：Part 18A 正式 Skill Runtime、Part 18B Skill source 状态与管理，以及 restricted Ops 的安全边界

## 1. 背景与当前偏差

上一版 Part 18 已把服务器 Ops 设计为独立于 Agent 容器的受限控制面，但“Ops 监控谁、随谁启动”仍过度绑定云服务器。ZhiCe-Agent 实际存在三种正式启动形态：本地终端进程、本地 Docker、云端服务器 Docker。Ops 必须监控当前真正承载 ZhiCe-Agent 的运行目标，而不是无论在哪里都指向一台远程服务器。

当前代码与该目标仍有以下偏差：

- `zcagent gateway` 只启动 Gateway，没有自动拉起独立的本地 Ops supervisor；
- `deploy/docker-compose.yml` 只有 Agent 服务，没有独立 Ops sidecar，也没有把目标容器名固定为 `zhice-agent`；
- Web 只读取静态 `operations.terminal.url`，不能优先发现本次启动生成的本地 Ops 地址；
- 云端 Ops hostname 模板仍需人工替换，真实地址没有统一收敛到 Git 忽略的私有目标配置；
- 云端 `config apply` 若只原子替换被逐文件 bind mount 的宿主机文件再执行 `docker restart`，容器可能继续持有旧 inode；正确语义必须是按当前部署规格重建固定容器。

因此本记录只纠正 Part 18C，不扩大 Skill Runtime、权限系统或服务器管理范围。

## 2. 目标

1. 建立一套统一 Ops 产品语义，并根据实际启动方式选择 `local_process`、`local_docker` 或 `server_docker`。
2. 本地终端启动 Gateway 时，自动启动仅监听回环地址的 Ops supervisor，并监控该 Gateway 子进程。
3. 本地 Compose 启动时，同时启动独立 Ops sidecar，只监控固定的本地 `zhice-agent` 容器。
4. 云端仍由宿主机 systemd ttyd/Ops 监控服务器上的固定 `zhice-agent` 容器，并通过 Cloudflare Tunnel + Access 暴露。
5. Web 从当前运行环境解析 Ops endpoint，不硬编码、不根据主站域名推导。
6. 云端真实 `PublicUrl`、`OpsUrl` 只进入 Git 忽略的私有配置，公开仓库只保留占位示例。
7. restart/apply 对进程和容器使用各自正确的生命周期语义；容器配置 apply 必须重建而不是仅 restart。

## 3. 非目标

- 不引入多 profile 或让用户在页面上切换任意运行目标。
- 不引入 keyring、Secret Manager、CLI Session 管理、`zcagent diagnose`、Skill 市场或多服务器管理。
- 不提供完整宿主机 Shell、通用 Bash、`sudo -i`、任意 Docker 命令、任意容器名或任意路径。
- 不把 Docker Socket、SSH credential 或终端字节流交给 Agent Gateway。
- 不让本地进程模式管理 Docker，也不让 Docker 模式管理宿主机上的任意进程。
- 不在公开代码、文档或示例里提交用户的真实域名、服务器地址或凭据。

## 4. 统一运行模型

Ops endpoint 对 Web 的运行态投影统一为：

```json
{
  "enabled": true,
  "configured": true,
  "mode": "local_process | local_docker | server_docker",
  "target_type": "process | container",
  "target_name": "zcagent-gateway | zhice-agent",
  "url": "http://127.0.0.1:17681",
  "presentation": "both"
}
```

三种模式的真值如下：

| mode | 启动者 | Ops 运行位置 | 固定目标 | 地址 |
|---|---|---|---|---|
| `local_process` | `zcagent gateway` | 本机独立 supervisor/受限 Web 进程 | 本次 Gateway 子进程 | `127.0.0.1:<selected-port>` |
| `local_docker` | Docker Compose | 独立 Ops sidecar | 本机固定 `zhice-agent` 容器 | 默认回环发布端口 |
| `server_docker` | 宿主机 systemd | 宿主机 ttyd/Ops | 服务器固定 `zhice-agent` 容器 | 私有配置中的 `OpsUrl` |

这是同一个 Ops 产品的三种 adapter，不是三个互不兼容的运维系统。命令、Web 信息架构、状态字段和错误码尽量一致；只有目标适配器和生命周期动作不同。

## 5. 地址与私有配置

### 5.1 云端地址

真实云目标继续使用 Git 忽略文件：

```text
deploy/private/cloud-target.json
```

其中公开地址字段统一为：

```json
{
  "PublicUrl": "https://实际主站地址",
  "OpsUrl": "https://实际运维地址"
}
```

`deploy/private/cloud-target.example.json` 只保留占位内容。代码、模板和文档不得写入用户的真实域名，也不得从 `PublicUrl` 拼接或猜测 `OpsUrl`。Cloudflare Tunnel hostname、Access application 和主 Web 投影均消费同一个私有 `OpsUrl`。

### 5.2 本地地址

本地进程模式优先使用稳定回环端口 `17681`，占用时只在一个有界范围内顺序选择可用端口，例如 `17681..17690`。最终选择写入 workspace 运行态文件：

```text
${ZHICE_AGENT_WORKSPACE}/state/operations.json
```

文件只记录本次实例可公开给本机 Web 的非敏感 endpoint、mode、target、PID/instance id 和更新时间；原子写入，进程正常退出时失效，异常退出后由 PID/instance 校验拒绝陈旧记录。

本地 Docker 使用 Compose 明确发布到 `127.0.0.1`，不能默认监听所有网卡。端口同样可由本地 `.env` 覆盖，但 Web 读取的是 Compose 注入或 runtime state 生成的最终地址，不猜测端口。

### 5.3 Web 解析优先级

Web API 按以下优先级返回 Ops endpoint：

1. 当前实例验证有效的 `${workspace}/state/operations.json`；
2. 当前启动器显式注入的非敏感 Ops endpoint；
3. `config.yml` 中静态 `operations.terminal`，仅作为手工部署/兼容 fallback；
4. 未配置状态。

运行态 endpoint 必须覆盖静态配置，避免本地 Web 错误跳转到云端。API 同时返回 `mode`、`target_type` 和 `target_name`，前端不再显示固定“服务器”文案，而是显示“本地进程”“本地容器”或“服务器容器”。

## 6. 本地终端模式

### 6.1 启动与生命周期

`zcagent gateway` 的正式启动链调整为：

```text
zcagent gateway
  -> 校验 Gateway 配置
  -> 选择 loopback Ops 端口
  -> 启动 LocalOpsSupervisor
  -> supervisor 启动并监控 Gateway child
  -> 写 state/operations.json
  -> 任一侧收到退出/取消信号
  -> 停止接受 Ops 动作
  -> 优雅终止 Gateway 与 Ops，超时后回收进程树
```

supervisor 是本地启动器和受限 Ops adapter，不是通用进程管理器。它只保存当前 Gateway 的启动规格和 child handle，只能重启自己创建的 Gateway。`--check` 只做配置检查，不启动 Ops；测试、一次性管理子命令和普通 CLI chat 也不隐式启动 Ops。

若 Ops 启动失败，Gateway 默认 fail closed 并给出明确错误，避免用户以为已获得监控能力；可为开发测试保留显式关闭选项，但默认正式启动必须同时可运维。

### 6.2 本地允许动作

进程 adapter 只实现与目标有关的子集：

- `status`：Gateway PID、started_at、uptime、host/port、health；
- `logs` / `logs-follow`：当前 workspace 的受控 Gateway 日志；
- `diagnose`：进程、端口、workspace、配置可读性和本地 health；
- `config view/edit/validate/diff/backup/restore/apply`：只操作当前 workspace 三份固定配置；
- `restart`：由 supervisor 终止并重新创建 Gateway child；
- `help` / `exit`。

本地进程模式不接受容器名、Docker 参数或宿主机任意路径。配置 apply 成功后由 supervisor 使用原启动参数重启 child。

## 7. 本地 Docker 模式

### 7.1 Compose 拓扑

```text
Browser -> 127.0.0.1:10086 -> zhice-agent
        -> 127.0.0.1:<ops-port> -> zhice-ops sidecar
                                      -> fixed Docker adapter
                                      -> zhice-agent only
```

`deploy/docker-compose.yml` 必须：

- 把 Agent 的容器名固定为 `zhice-agent`；
- 新增独立 Ops service/sidecar，并有固定版本或由仓库构建的受限入口；
- Ops 端口只发布到 `127.0.0.1`；
- 主 Agent 容器不持有 Docker Socket；
- Ops 即使 Agent unhealthy/exited 也保持运行；
- status/logs/recreate 只能指向固定 Compose project/service/container。

若 sidecar 需要访问 Docker API，必须使用最小权限的 socket proxy 或等价的固定宿主机 wrapper；禁止把原始 Docker Socket 暴露给浏览器、Agent 容器或通用 Shell。实现不能仅因方便就给予任意 Docker API 权限。

### 7.2 本地容器生命周期

`restart` 可重启固定服务；涉及原子替换 bind-mounted 配置的 `config apply` 必须按已保存的 Compose project/spec 执行固定服务 recreate，并等待 health。浏览器不能提交 project、service、container、compose file 或 image 参数。

## 8. 服务器 Docker 模式

云端拓扑保持宿主机独立：

```text
Browser
  -> private OpsUrl
  -> Cloudflare Access
  -> Cloudflare Tunnel
  -> host systemd ttyd
  -> zhice-ops-shell
  -> root-owned fixed wrapper
  -> server zhice-agent container only
```

ttyd 使用固定版本和完整性校验，唯一后端仍是 `zhice-ops-shell`。`zhice-operator` 为 nologin、非 docker group 用户；需要提权的动作只经精确 sudoers/root-owned wrapper。主 Web 只提供独立窗口和 iframe；Access、CSP 或第三方 Cookie 阻止 iframe 时明确回退到独立窗口。

Cloudflare Tunnel、Access policy、真实 hostname 和证书属于私有部署环境，不进入公开仓库。公开模板只引用环境/私有生成文件，不包含用户信息。

## 9. Restricted 命令与目标适配

允许的顶层命令保持不变：

```text
status
logs
logs-follow
diagnose
config view
config edit
config validate
config diff
config backup
config restore
config apply
restart
help
exit
```

不提供 `config list` 或任何文件浏览扩展；`config view/edit/restore` 只能接受 `.env`、`config.yml`、`models.json` 三个固定逻辑名。所有 mode 共用严格 parser，随后分派到固定 adapter：

```text
local_process -> GatewayProcessAdapter
local_docker  -> LocalComposeAdapter
server_docker -> ServerContainerAdapter
```

禁止把 adapter 选择、容器名、PID、路径、可执行文件或 Docker 参数作为浏览器输入。禁止 `eval`、`sh -c`、命令替换、管道、重定向、通配符和额外位置参数。

## 10. 配置持久化与安全 apply

三种模式都只允许 `.env`、`config.yml`、`models.json` 三个逻辑配置名，并遵守：备份、pending 编辑、语法校验、ZhiCe 语义校验、diff 脱敏、原子替换、健康检查、失败回滚。Secret 和文件正文不得进入日志或审计。

目标生命周期必须区分：

```text
local_process
  -> 原子替换 workspace 权威文件
  -> supervisor 使用原启动规格重建 Gateway child

local_docker
  -> 原子替换本地主机权威文件
  -> 固定 Compose service recreate
  -> health check / rollback

server_docker
  -> 原子替换 /etc/zhice-agent/runtime 权威文件
  -> 使用当前 immutable Digest、固定 mounts/ports/volumes 重建 zhice-agent
  -> health check / rollback
```

容器模式不能用单纯 `docker restart` 作为配置 apply，因为逐文件 bind mount 的宿主机原子替换会改变 inode，而已存在容器可能继续引用旧文件。普通 `restart` 和 `config apply` 是两个不同动作：前者重启现有目标，后者在配置变更后重建目标。

云端首次安全迁移、备份目录、只读 bind mount 和跨新 Digest 保留继续沿用上一版设计，但部署脚本必须保存/重用可审计的固定容器规格，不能允许 Ops UI 拼接 `docker run`。

## 11. 数据流

### 11.1 本地终端

```text
CLI -> LocalOpsSupervisor -> Gateway child
                         -> state/operations.json
Web -> Gateway API -> validated runtime endpoint -> loopback Ops UI
Ops command -> strict parser -> GatewayProcessAdapter -> fixed child/config
```

### 11.2 本地 Docker

```text
docker compose up -> zhice-agent
                  -> zhice-ops sidecar
Web -> Gateway API -> injected/runtime endpoint -> loopback Ops UI
Ops command -> strict parser -> fixed Compose adapter -> zhice-agent only
```

### 11.3 云端服务器

```text
private cloud-target.json -> deploy/tunnel rendering
                          -> PublicUrl / OpsUrl projection
Browser -> OpsUrl -> Access -> host ttyd -> fixed server adapter
                                      -> zhice-agent only
```

## 12. 预计变更文件

实现阶段预计涉及：

- `agent/cli.py`：Gateway 启动接入本地 supervisor；
- 新增本地 Ops supervisor、状态协议和 process adapter 模块；
- `agent/runtime_config.py`、`agent/app/api/routes.py`、API schema：运行态 endpoint 优先级和 mode/target 投影；
- `deploy/docker-compose.yml`：固定 Agent 容器和本地 Ops sidecar；
- `deploy/ops/`：复用 restricted parser，增加 process/local Compose/server adapter；
- `deploy/scripts/remote_ops.py`、`deploy/scripts/deploy.sh`：消费私有 `OpsUrl`，保存固定部署规格，apply 重建；
- `deploy/private/cloud-target.example.json`：增加占位 `OpsUrl`，删除任何真实个人信息；
- 所有受版本控制的代码、活文档、日期记录、示例和生成模板：迁移完成时执行仓库级隐私扫描并移除真实域名、服务器地址与凭据，不因“历史记录”保留个人部署信息；
- 本地部署 PowerShell/README：输出实际 loopback Ops endpoint；
- Web API/types/store/Admin 页面：按 mode 展示和 iframe 回退；
- Python、前端与 Ops 测试及相应 `test_case.md`；
- Part 18 活文档、总体设计、设计索引和根 README。

具体文件名可在实现时按现有模块边界微调，但不得改变上述依赖和安全边界。

## 13. 测试方案

### 13.1 本地进程

- `zcagent gateway` 自动启动 Ops；`--check` 不启动；
- 首选端口、占用后有界 fallback、范围耗尽明确失败；
- runtime state 原子写入、陈旧 PID/instance 拒绝、退出清理；
- Gateway crash/restart/cancel/timeout 和完整进程树回收；
- status/logs/diagnose/config/restart 只作用于当前 child/workspace；
- 无 Docker 或云端依赖时仍可完整使用本地进程 Ops。

### 13.2 本地 Docker

- Compose config 中 Agent 固定为 `zhice-agent`，Ops 为独立 service；
- 两个对外端口只绑定 `127.0.0.1`；
- Agent exited/unhealthy 时 Ops 仍存活；
- 任意容器名、project、service、image、路径和 Docker 参数均被拒绝；
- `restart` 与 `config apply/recreate` 语义分离；
- 原子替换 bind-mounted 文件后，recreate 内看到新内容。

### 13.3 云端与私有配置

- example 只有占位值，仓库全量扫描不出现真实 `PublicUrl`/`OpsUrl`、个人域名或服务器地址；
- 私有 JSON 缺字段、URL 非法或 hostname 未配置时 fail closed；
- Tunnel/Access 模板只消费 `OpsUrl`，不从 `PublicUrl` 推导；
- 当前 immutable Digest、只读 mounts、volumes、ports 和 restart policy 在 apply/recreate 后保持；
- 配置正文、Secret、SSH 密码和 Access credential 不进入日志。

### 13.4 通用验证

```text
python -m ruff check .
python -m pytest --basetemp .tmp/pytest_basetemp
npm run lint
npm run typecheck
npm run test
npm run build
PowerShell parser/static tests
Shell syntax/static tests
docker compose config
local terminal real-process smoke
local Docker real-container smoke
```

## 14. 验收标准

1. 本地执行 `zcagent gateway` 后，无需手工配置即可得到一个实际可访问的 `127.0.0.1:<port>` Ops 地址，并能监控和重启本次 Gateway。
   本地进程 Ops 必须 tee Gateway stdout/stderr，Docker Ops 读取固定容器 stdout/stderr；不得把 trace JSONL 直接当作人类日志。两者默认持续跟随，位于底部时自动滚动，用户向上查看历史时暂停并可显式恢复。
2. 本地执行正式 Compose 启动后，Agent 与 Ops 同时启动；Ops 只监控本机固定 `zhice-agent` 容器。
3. 部署到服务器后，Ops 只监控该服务器固定 `zhice-agent` 容器，Agent 容器退出时宿主机 Ops 仍可用。
4. 主 Web 显示当前环境的 mode、target 和实际 endpoint，不把本地用户导向云端，也不从主站推导 Ops hostname。
5. 云端真实 `PublicUrl`、`OpsUrl` 和凭据只存在于 Git 忽略的私有配置；包括历史日期设计记录在内的公开仓库全量扫描无用户信息。
6. restricted 命令集不增加通用 Bash、sudo、任意 Docker、任意容器名或任意路径入口。
7. 本地进程 restart 由 supervisor 重建 child；容器 config apply 通过固定规格 recreate，原子替换后的配置真实生效。
8. 三份权威配置可备份、校验、脱敏 diff、恢复、apply，并在普通重启和新 Digest 后保留。
9. Ruff、全量 pytest、前端 lint/typecheck/test/build、Ops 静态/脚本和本机两种真实启动 smoke 全部通过。
10. Linux systemd/ttyd、Cloudflare Tunnel + Access、iframe、跨 Digest 与 Agent-down 救援明确留作目标云环境真实验收；未执行前不得声称生产验收完成。

## 15. 外部权限与验收边界

本机可完成的代码、单测、静态检查、真实进程和 Docker smoke 必须全部完成。以下项目需要目标 Linux/Cloudflare 权限，不能用 Windows mock 代替：

- systemd unit、专用用户、sudoers、root-owned wrapper 与 journald；
- 固定 ttyd 的 PTY/resize/idle/max-session 行为；
- Cloudflare Tunnel hostname、Access identity/MFA、Origin/CSP 与 iframe Cookie 行为；
- 真实服务器容器退出救援、宿主机配置首次迁移和跨 Digest 保留；
- 公网健康恢复与 Secret 不进入生产日志。

实现交付时必须将“本机已通过”和“云端待执行”分别报告。云端未验收不阻止完成可自动验证的实现，但核心本地实现或测试失败时不得宣称 Part 18 纠偏完成。

## 16. 迁移与回滚

1. 旧静态 `operations.terminal` 保留一个兼容周期，但运行态 endpoint 优先。
2. 首次启动新本地 supervisor 前不删除旧配置；启动失败不写有效 runtime state。
3. Compose 新增 sidecar 时保留现有 data volumes，先验证 Agent 与 Ops health，再切换日常入口。
4. 云端先渲染并校验私有 `OpsUrl`、systemd/Tunnel 配置，再切换 Access；旧 SSH/云控制台救援路径在验收完成前保留。
5. 配置 apply 前强制备份；新进程/容器 health 失败时恢复配置并按旧固定规格重建。
6. 回滚代码时删除或失效 `state/operations.json`，避免旧 Web 读取陈旧本地 endpoint；不删除用户 workspace、volume 或宿主机权威配置。

本次纠偏完成后的判定不是“配置里多了一个 Ops URL”，而是无论用户从终端、本地 Docker 还是服务器 Docker 启动，Ops 都会自动落到同一台机器上实际承载 ZhiCe-Agent 的那个固定目标，并保持可用、安全、可恢复。
