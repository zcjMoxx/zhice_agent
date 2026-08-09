# ZhiCe-Agent Part 18 Skill Runtime 与服务器运维控制设计

> 说明：本记录中的 Part 18A/18B 与 restricted Ops 安全边界继续有效；Part 18C 的运行形态先由 `2026-08-09-part18-multi-runtime-ops-correction-design.md` 修正，公网认证又由 `2026-08-10-part18-server-side-ops-auth-design.md` 收敛为“复用既有 Tunnel + 服务器 ttyd Basic Auth”。本文 Cloudflare Access/MFA 正文保留为当时方案记录，不再代表当前部署口径。

> 日期：2026-08-09
>
> 状态：方案已确认并完成代码落地与本机自动验证；真实 Linux/Cloudflare 外部验收仍按第 11 节执行
>
> 归属：Part 18 Skill Runtime、Skill 管理与服务器运维控制
>
> 前置基线：Part 5 Skill source、Part 12 RuntimeEvent、Part 16 Vue Web、Part 17 诊断与私有镜像部署

## 1. 背景

当前代码已经完成到 Part 17。AgentLoop、Session、Tool、SkillLoader、Skill source 同步、RuntimeEvent、系统诊断、Vue 管理后台、私有镜像和单机云部署均已落地，但最后一个核心阶段仍有两类缺口。

第一类是 Skill 运行边界。当前 Skill 是 `SKILL.md + scripts` 指令包：模型先调用 `load_skills` 阅读说明，再根据文档自行拼接 `exec` 命令执行脚本。内核只知道发生了一次 Tool/exec 调用，不知道正在运行哪个 Skill，也没有稳定的 Skill 参数、结果、取消、进度和错误协议。现有设计明确禁止根据 `exec.command` 反推 Skill 名，因此 `SkillExecutor`、`skill.*` RuntimeEvent 和 ProgressSink 尚未实现。

第二类是云端日常运维。当前服务器已经有 `status.sh`、`logs.sh`、`stop.sh`、`restart.sh` 和本地发布机到云服务器的 Paramiko SSH 发布链，但这些能力只服务发布脚本。管理后台能够查看应用级运行诊断，不能直接查看宿主机容器状态、原始 Docker 日志或进入服务器终端。服务异常时，操作者仍需从外部 SSH 客户端登录服务器。

本阶段不再建设多 profile、keyring、Secret Manager、CLI Session 管理或人工执行的 `zcagent diagnose`。ZhiCe-Agent 当前以云端容器和 Web 为主要运行形态；Session 管理继续优先留在 Web，容器失效后的诊断应从宿主机运维面完成，而不是要求操作者进入目标容器执行 CLI。

真实聊天公网入口与 Ops 入口均属于私有部署信息，只能由 Git 忽略的 `PublicUrl`、`OpsUrl` 提供。第一版服务器 Ops 公网入口确定使用 Cloudflare Tunnel + Access；受保护 IP:端口只作为部署排障备用。

## 2. 已确认的关键决策

### 2.1 Part 18 收敛为三个模块

```text
Part 18A 正式 Skill Runtime
  -> Part 18B Skill source 状态与 Web 管理
  -> Part 18C 独立服务器 Ops 与 Web 投影入口
```

不再包含：

- 多 profile 初始化；
- keyring 或云厂商 Secret Manager；
- CLI Session 归档、搜索和导出；
- 面向操作者的 `zcagent diagnose` / `zcagent diagnose --json`；
- 多服务器资产管理；
- Skill 市场、在线安装任意 URL和用户私有覆盖优先级。

### 2.2 Skill 同时保留指令型和可执行型

指令型 Skill 只要求合法 `SKILL.md`，继续由模型阅读并组合现有 Tool。存在 `scripts/` 不代表 Skill 自动成为可执行型，内核不得从文件名或示例命令猜入口。

可执行型 Skill 必须显式声明运行入口、参数协议、结果协议、timeout 和进度协议，由 `SkillExecutor` 直接调用。第一版只支持一个明确 Python 入口脚本，不建设通用工作流 DSL，也不让 Skill 直接 import `agent.*`。

建议的声明形态：

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

`runtime` 缺失时仍是合法指令型 Skill；`runtime` 存在但非法时，该 Skill 的可执行能力 fail closed，同时保留可诊断的加载错误。

### 2.3 Web 与 Ops 采用“表现整合、运行独立”

主 Web 是入口和显示器，不持有宿主机 Shell、SSH 密码或 Docker Socket，也不转发终端字节流。真正的容器状态、日志、PTY 和宿主机命令由独立 Ops 服务负责。

```text
configured PublicUrl
  -> ZhiCe-Agent Vue 管理后台
  -> Owner 可见“服务器运维”入口
  -> 新标签页打开或 iframe 投影 Ops UI

独立 Ops URL
  -> 独立访问控制
  -> Ops Terminal Service
  -> 宿主机 PTY / 固定运维脚本 / Docker
```

主 Web 不代理 Ops WebSocket。即使 Agent 容器退出或不断重启，独立 Ops 地址仍应可直接访问。

### 2.4 第一版只操作当前部署服务器

Part 18C 不是服务器管理平台。第一版固定操作承载当前 ZhiCe-Agent 的单台 Linux 服务器：

- 不在 Web 中录入 SSH host、密码或私钥；
- 不保存多台服务器清单；
- 不提供批量命令；
- 不提供文件分发、在线发布和远程脚本上传；
- 不让 Agent 模型自动进入或操作服务器终端。

### 2.5 Owner 使用受限 ZhiCe 运维终端，不取得宿主机通用 Shell

Web Owner 是应用最高身份，但本阶段的目标只是观察和维护固定的 `zhice-agent` 容器，而不是通过 Web 取得整台服务器控制权。第一版采用 restricted 模式：ttyd 展示一个专用 `zhice-ops-shell`，只接受固定 ZhiCe 运维子命令，不启动通用 Bash，不允许 `sudo -i`，也不把 `zhice-operator` 加入等同宿主机 root 的 `docker` 用户组。

第一版规则：

- 主 Web 入口只对唯一 Owner 可见；
- admin 委派不能获得终端入口；
- Ops 服务仍必须有独立 Cloudflare Access 保护，不能只信任入口是否隐藏；
- 只允许查看固定容器状态、跟随日志、运行宿主机诊断、编辑受控配置、校验配置、恢复配置备份和重启固定容器；
- 不允许任意容器名、任意 `docker` 参数、任意宿主机文件路径、通用 Shell、`sudo -i`、停止/删除容器或部署任意镜像；
- 若未来确实需要完整服务器终端，应建立新的日期设计记录，不在本方案中预留隐式提权入口。

## 3. 目标

1. 为可执行 Skill 建立显式、稳定、可测试的内核运行协议。
2. 新增 SkillExecutor、SkillRunRequest、SkillResult、取消、timeout、输出限制和结构化错误。
3. 新增 `skill.started/progress/completed/failed` 与真实 ProgressSink，不从 exec 字符串伪造 Skill 事件。
4. 持久记录 Skill source 最近同步结果、commit、同步时间、健康状态和加载错误。
5. 在现有 Vue 管理后台增加 Owner/特权管理员可见的 Skill source 管理页面。
6. 建立独立于 Agent 容器的宿主机 Ops Terminal Service。
7. 让操作者从浏览器查看容器状态、实时日志并进入受限 ZhiCe 运维终端。
8. 让主 Web 只承担 Ops 导航和可选投影，保持 Agent Gateway 与宿主机控制权隔离。
9. 使用 Cloudflare Tunnel + Access 提供独立 Ops 公网入口，并保留受保护 IP:端口作为部署排障备用。
10. 保证 Agent 容器失效时独立 Ops 地址仍可用于应急恢复。

## 4. 非目标

- 不改变 AgentLoop 的通用循环职责。
- 不把 Skill 业务判断硬编码进 AgentLoop。
- 不让 SkillExecutor 绕过 Tool RBAC、危险确认、Hook、workspace guard、timeout、输出截断和脱敏边界。
- 不把任意 `scripts/*.py` 自动注册为入口。
- 不建设 Skill 工作流 DSL、Skill 市场、版本签名中心或依赖安装平台。
- 不建设多 workspace、多租户或多服务器控制平台。
- 不在主 Gateway 中保存服务器 root 密码或 SSH 私钥。
- 不把 `/var/run/docker.sock` 挂载到 ZhiCe-Agent 主容器。
- 不让模型自动调用服务器终端。
- 不通过 Web 提供宿主机通用 Bash、root Shell 或任意 Docker 命令。
- 不保证宿主机断电、网络失联、Caddy/Cloudflare 整体故障时仍可 Web 救援；这类故障继续依赖云厂商控制台。

## 5. Part 18A：正式 Skill Runtime

### 5.1 协议数据结构

新增协议层数据结构，不 import 具体执行实现：

```text
ExecutableSkillInfo
SkillRunRequest
SkillProgress
SkillResult
SkillRuntimeError
SkillExecutor protocol
ProgressSink protocol
```

建议字段：

```text
SkillRunRequest
  run_id
  qualified_name
  params
  actor_context
  session_id / turn_id / request_id
  timeout_seconds
  cancellation_token

SkillResult
  status
  code
  data
  message
  error_stack
  duration_ms
  metadata
```

最终 Skill 脚本结果继续使用仓库既有字段：

```json
{"status":"success","code":"OK","data":{},"message":"完成","error_stack":""}
```

### 5.2 执行入口

新增一个模型可调用的 `run_skill` Tool。模型先通过现有 Skill Catalog/`load_skills` 判断目标能力，再对显式可执行 Skill 调用：

```json
{
  "skill": "official/weather-report",
  "params": {
    "city": "上海",
    "days": 3
  }
}
```

执行链：

```text
run_skill
  -> actor/Profile/source 可见性检查
  -> executable metadata 校验
  -> params 校验
  -> workspace 与入口路径 guard
  -> SkillExecutor 启动无 shell Python 子进程
  -> ProgressSink 转发安全进度
  -> 最终 JSON 结构校验
  -> SkillResult / ToolResult
```

SkillExecutor 固定使用当前解释器或显式允许的运行时，不从 Skill 输入接受任意 executable、cwd、环境变量名或命令片段。

### 5.3 NDJSON 进度协议

可执行 Skill stdout 使用逐行 JSON：

```json
{"type":"progress","message":"正在读取数据","percent":30}
{"type":"progress","message":"正在生成报告","percent":80}
{"type":"result","status":"success","code":"OK","data":{},"message":"完成","error_stack":""}
```

约束：

- `progress.message` 必须有长度限制并经过脱敏；
- `percent` 可选，只接受 `0..100`；
- 最终只能出现一个 `result`；
- result 后出现额外输出视为协议错误；
- 非 JSON 行默认视为受限脚本日志，不自动当作用户进度；
- 超过行数、字节数或 timeout 时终止完整进程树；
- 旧式“最后一行结果 JSON”只作为无进度兼容模式，不伪造中间进度。

### 5.4 RuntimeEvent

扩展 RuntimeEvent 白名单：

```text
skill.started
skill.progress
skill.completed
skill.failed
```

建议增加 `skill_run_id`，并让 Skill Event 关联外层 `run_skill` Tool Event。前端主要展示 Skill 事件，通用 `run_skill` Tool 包装事件可以降级为内部可见，避免同时出现“正在执行 run_skill”和“正在执行 weather-report”的重复状态。

瞬态 Skill 进度不写入 Session JSONL；最终 Skill 调用事实继续通过 Tool message、trace 和 Runtime Activity 留证。

### 5.5 安全边界

- 入口必须位于对应 Skill root 内；symlink/resolve 后越界拒绝。
- 固定 `shell=False`。
- cwd 固定为受控 workspace 或 Skill root，不能由模型覆盖。
- 使用最小环境；Secret 只允许由部署配置显式注入，不接受模型指定任意环境变量。
- timeout、stdout/stderr 上限和进程树回收复用现有 Exec/Hook 的成熟实现原则。
- 可执行 Skill 仍受 actor、source、Subagent Profile 和父能力交集限制。
- SkillExecutor 返回结构化失败，不向 AgentLoop 抛裸异常。
- `error_stack` 只进入有权限的诊断/trace，普通用户结果只返回安全 message/code。

## 6. Part 18B：Skill source 状态与 Web 管理

### 6.1 状态模型

当前 `SkillSourceResult` 只描述一次同步调用。Part 18B 增加持久化 source 状态：

```text
source
enabled / sync_enabled
configured_target
materialized_root
current_commit
last_sync_started_at
last_sync_finished_at
last_success_at
last_status
skill_count
load_error_count
last_error_code
last_error_message_safe
```

状态不得记录 git credential、完整带凭据 URL、Secret、原始 stderr 或 workspace 外绝对路径。

### 6.2 索引缓存

SkillLoader 仍以 source root 为真值。缓存只加速索引和状态展示，不成为新的权威数据源：

- source 同步成功后原子失效；
- `SKILL.md` mtime/content fingerprint 变化时重建；
- 缓存损坏时删除并重新扫描；
- 缓存失败只局部降级，不能阻断无 Skill 聊天。

### 6.3 权限

第一版只做 source 级可见性和同步权限：

- 已认证普通用户只看到对其角色/权限开放的 Skill；
- source 同步仍要求 `skill.sync`；
- source 状态技术详情要求管理权限；
- Subagent 继续取父 actor 可见 Skill 与 Profile allow/deny 的交集；
- 不做用户私有 source 和跨 source 覆盖优先级。

### 6.4 Web 页面

在现有管理后台增加“Skills”页面：

```text
Skill Sources
  source/name
  enabled
  target / current commit
  last sync
  health
  skill count
  safe error summary
  [同步] [刷新索引] [查看 Skills]
```

普通 `/skills` 保持紧凑，只显示 `source/name` 和短描述；commit、路径、错误明细等运维字段只在管理页面展示。

## 7. Part 18C：独立服务器 Ops 与 Web 投影

### 7.1 拓扑

推荐生产拓扑：

```text
Browser
  |
  +-- configured PublicUrl --------------> Cloudflare/Caddy -> ZhiCe-Agent container
  |
  +-- configurable Ops URL --------------> Access gate -> Ops Terminal Service
                                                           |
                                                           +-> host PTY
                                                           +-> journal/status
                                                           +-> fixed deploy scripts
                                                           +-> Docker CLI
```

Ops Terminal Service 与 tunnel/access connector 应作为宿主机 systemd 服务运行，不放入 ZhiCe-Agent 容器。这样 Agent 容器退出、重启循环或 Docker 中的 Agent 服务异常时，Ops 仍可访问。若 Docker daemon 自身失效，宿主机 systemd Ops 仍能用于修复；若宿主机或公网入口整体失效，则转用云厂商控制台。

### 7.2 地址策略

主站当前固定事实：

```text
ZHICE_PUBLIC_URL=${private PublicUrl}
```

Ops 地址必须配置化，例如：

```yaml
operations:
  terminal:
    enabled: true
    url: "https://由部署者填写的地址"
    presentation: "both"
```

或者：

```env
ZHICE_TERMINAL_URL=https://由部署者填写的地址
```

地址选择优先级：

1. Cloudflare Tunnel + Cloudflare Access，推荐用于当前未确定独立域名/备案入口的阶段；
2. 未来独立 Ops 子域名，仍由私有 `OpsUrl` 提供；
3. 受 VPN、Tailscale 或防火墙可信 IP 保护的 `IP:端口`；
4. 不允许公网裸露的 HTTP 终端。

代码和前端不假设任何真实 hostname，也不从 `PublicUrl` 自动推导 Ops 地址。

### 7.3 主 Web 展示

管理后台新增仅 Owner 可见的“服务器运维”入口：

```text
服务器运维
  当前部署：configured PublicUrl
  Ops 状态：已配置 / 未配置
  [独立窗口打开]
  [页面内嵌]
```

第一版同时提供独立窗口和 iframe 投影：

- Ops 响应必须显式允许主站 origin 的 `frame-ancestors`；
- Ops WebSocket 必须严格校验 Origin；
- 独立窗口是完整、稳定和应急入口；
- iframe 是主 Web 内的日常投影入口；
- Cloudflare Access 或浏览器第三方 Cookie 阻止 iframe 时，自动提示并回退到新标签页；
- 主 Web 不复制、不缓存、不持久化终端输出；
- 终端关闭或 Agent 页面刷新不应杀死服务器上的其它非本次进程。

### 7.4 Ops 页面能力

终端层采用成熟 `ttyd`，不自行重写 xterm.js、WebSocket 和 Linux PTY。仓库负责 ttyd 的固定版本、校验、systemd、Cloudflare 接入、启动命令、主题覆盖和安全配置；前端样式调整保持 ZhiCe-Agent 的颜色、字体、标题和状态语义，但不 fork ttyd 核心协议。

Ops 服务提供独立最小页面：

```text
服务器状态
  hostname / uptime / load / disk / memory

ZhiCe-Agent 容器
  exists / image / digest / status / health / restart count / started_at
  [刷新] [日志] [重启]

ZhiCe 运维终端
  themed ttyd + zhice-ops-shell
  [连接] [断开] [全屏]
```

第一版固定容器名或由宿主机只读部署配置提供，浏览器不能提交任意容器名。状态、日志和重启继续复用/调用当前 versioned `status.sh`、`logs.sh`、`restart.sh`，并新增宿主机 `diagnose.sh`：

```text
Docker daemon
container exists/status/health/exit code/OOMKilled
image/digest/restart count
latest bounded logs
named volumes
disk space
host port
local health
public health (${PublicUrl}/health)
```

删除面向用户的容器内 `zcagent diagnose` 设计；`diagnose.sh` 在目标容器退出时仍可运行。

### 7.5 持久配置编辑

当前 `.env`、`config.yml`、`models.json` 随私有镜像构建进入 `/home/zhice/.zhice/config/`，完整 `config/` 没有挂载 volume。直接在运行容器层修改这些文件不是稳定运维方式：同一容器 restart 后可能保留，但下一次按新 Digest 重建容器时一定丢失。

Part 18C 将云端运行配置调整为宿主机受控持久副本：

```text
/etc/zhice-agent/runtime/.env
/etc/zhice-agent/runtime/config.yml
/etc/zhice-agent/runtime/models.json
/etc/zhice-agent/runtime/backups/
```

部署时把三个文件分别只读 bind mount 到容器原路径。私有镜像中的配置继续作为首次迁移/灾难恢复基线，但云端启动后的权威配置改为宿主机副本。首次迁移必须从当前受控私有镜像安全初始化，不把 Secret 打印到终端或发布日志；后续发布默认保留服务器上已编辑的配置，只有显式配置同步流程才覆盖。

`zhice-ops-shell` 只允许：

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
help / exit
```

配置修改规则：

- 目标文件名固定，拒绝任意路径、`..`、symlink 和额外参数注入；
- edit 前自动创建权限受控的时间戳备份；
- 保存后先解析 `.env`、YAML 和 JSON，再执行 ZhiCe 运行配置语义检查；
- 校验失败不允许 apply/restart，并给出不泄露 Secret 的定位信息；
- apply 使用原子替换并调用固定容器 restart；
- `.env` 和 `models.json` 含 Secret，只有 Owner/Cloudflare Access 身份能进入，Ops 日志和审计不得记录文件正文；
- 容器只读挂载运行配置，Agent 进程不能反向修改宿主机权威副本。

### 7.6 ttyd 与 Linux 身份

ttyd 以专用宿主机用户 `zhice-operator` 运行，不以 root 启动，不保存 SSH 密码，也不 SSH 回环连接本机。ttyd 的唯一后端命令是 `zhice-ops-shell`，不是 `/bin/bash`。

需要 root 的固定操作由 root-owned wrapper 或精确 sudoers 条目完成，参数在提权前完成结构化校验。`zhice-operator` 不获得通用 `docker`、`sh`、`bash`、编辑任意文件或执行任意 sudo 命令的能力。配置目录通过专用 group/ACL 只开放上述固定编辑流程需要的最小权限。

### 7.7 访问控制

主 Web 中只有 Owner 看得到入口，但隐藏入口不是 Ops 鉴权。Ops 必须由独立访问层保护。

第一版确定使用 Cloudflare Tunnel + Access，不在 ZhiCe-Agent 中建设新的服务器密码库：

- Cloudflare Access 使用独立身份/MFA/设备或 IP 策略；
- 主 Web 只导航或投影，不向浏览器下发 SSH 密码；
- Agent 容器失效时操作者仍可直接访问 Ops URL；
- IP:端口只作为部署排障备用，不作为第一版正式公网入口；
- 不通过 URL query 传递长期 credential。

后续如确实需要 ZhiCe Owner 单点跳转，可单独设计 30 秒、单次使用的 terminal ticket；它不是第一版前置条件，不能取代 Agent 失效时的独立应急认证。

### 7.8 审计与会话限制

Ops 服务至少记录到 journald：

```text
terminal.opened
terminal.closed
terminal.rejected
container.logs_read
container.restart_requested
container.restart_completed / failed
host_diagnose_requested
```

字段只包含访问身份摘要、来源 IP、时间、Session ID、动作、目标固定容器和结果；不记录认证凭据。第一版不默认录制完整终端字节流，因为终端内容可能包含 Secret。若未来要求命令级审计，应使用宿主机 `auditd`/`tlog` 等系统能力单独设计，而不是从 WebSocket 字节猜 Bash 命令。

限制：

- 默认最多一个交互终端会话；
- 15 分钟无输入自动断开；
- WebSocket 有最大帧、速率和输出缓冲限制；
- 日志 tail 有最大行数和字节数；
- 重启要求二次确认；
- Agent 容器重启后主 Web 轮询私有 `${PublicUrl}/health` 并恢复页面状态。

## 8. 数据流

### 8.1 可执行 Skill

```text
LLM -> run_skill Tool -> SkillExecutor
                      -> explicit runtime metadata
                      -> safe subprocess
                      -> ProgressSink
                      -> skill.* RuntimeEvent
                      -> validated SkillResult
                      -> ToolResult -> AgentLoop
```

### 8.2 Skill source 状态

```text
SkillSourceSync -> materialized source
                -> commit/sync status store
                -> SkillLoader scan/cache
                -> source permission filter
                -> Web management / Agent context
```

### 8.3 Ops 日常入口

```text
Owner -> ${PublicUrl}/admin
      -> server operations card
      -> configurable Ops URL
      -> independent access gate
      -> Ops UI/WebSocket
      -> host PTY or fixed operation
```

### 8.4 Agent 故障救援

```text
Agent container unavailable
  -> direct Ops URL
  -> independent Cloudflare Access authentication
  -> host terminal or diagnose.sh
  -> docker logs/inspect/restart/deploy
  -> public health recovers
```

## 9. 配置设计

### 9.1 Agent 侧公开展示配置

只保存非敏感展示信息：

```yaml
operations:
  terminal:
    enabled: false
    url: ""
    presentation: both
```

校验：

- `enabled=true` 时 URL 必填；
- production 只接受 HTTPS；
- URL 不允许 credentials、query 或 fragment；
- `presentation` 只允许 `new_tab|embed|both`，第一版生产值使用 `both`；
- 配置只控制入口展示，不授予宿主机权限。

### 9.2 Ops 宿主机配置

Ops 配置独立于 Agent workspace，建议位于 root 管理的 `/etc/zhice-ops/`：

```text
container_name=zhice-agent
public_health_url=${PublicUrl}/health
idle_timeout_seconds=900
max_sessions=1
allowed_origin=${PublicUrl}
ttyd_version=<pinned-version>
runtime_config_dir=/etc/zhice-agent/runtime
```

文件权限由 systemd 和宿主机用户控制，不烘入 ZhiCe-Agent 镜像，也不提交真实生产值到公开 Git。

## 10. 变更文件

实施时预计涉及至少以下模块；最终文件名可在代码落地设计复核时微调：

### Skill Runtime

- `agent/protocols/skill.py`
- `agent/protocols/runtime_event.py`
- `agent/skills/loader.py`
- `agent/skills/executor.py`（新增）
- `agent/skills/status.py`（新增）
- `agent/tools/skill.py`
- `agent/core/loop.py` 或 Tool Provider 组装边界
- `tests/unit_test/skills/`
- `tests/unit_test/runtime_events/`
- 对应 `test_case.md`

### Web Skill/Ops 入口

- `agent/app/api/routes.py`
- `agent/app/api/schemas.py`
- `web/frontend/src/layouts/AdminLayout.vue`
- `web/frontend/src/stores/admin.ts`
- `web/frontend/src/api/client.ts`
- `web/frontend/src/api/types.ts`
- `web/frontend/src/styles/app.css`
- 相关 Python/Vitest 测试

### 独立 Ops

- `deploy/ops/`（新增 ttyd 固定版本/校验、ZhiCe 主题、`zhice-ops-shell`、root-owned wrapper、systemd 与 Cloudflare 模板）
- `deploy/scripts/diagnose.sh`（新增）
- `deploy/scripts/status.sh`
- `deploy/scripts/logs.sh`
- `deploy/scripts/restart.sh`
- `deploy/scripts/deploy.sh`（增加宿主机持久配置迁移与三个只读 bind mount）
- `deploy/README.md`
- Ops 命令解析、配置编辑/校验/备份恢复、访问限制和脚本专项测试

### 文档落地同步

- `docs_design/zhice-agent-overall-design.md`
- 新建 Part 18 当前活文档
- `docs_design/README.md`
- `README.md`
- Part 5、Part 12、Part 16、Part 17 的当前活文档中与 Part 18 交叉引用

即使是旧日期记录，真实个人部署域名也必须在公开前替换为 `${PublicUrl}`/`${OpsUrl}` 占位；隐私要求优先于保留历史 hostname 字面值。

## 11. 测试方案

### 11.1 Skill Runtime

- 指令型 Skill 不要求 runtime 且行为保持兼容。
- 可执行 Skill 的入口、协议、timeout 和参数校验。
- 入口越界、symlink 越界、非法 runtime、非 JSON、重复 result、超量输出和进程树回收。
- 正常 progress、无 percent progress、脱敏、取消和失败路径。
- `skill.*` sequence、parent 关联、WS/SSE/CLI renderer 和前端 reducer。
- actor/source/Profile 权限交集和未激活 Tool fail closed。

### 11.2 Skill source 管理

- 同步成功/失败/unchanged 后状态持久化。
- git commit、同步时间和安全错误摘要。
- 缓存命中、失效、损坏重建和并发原子性。
- 普通用户、管理员、Owner 和 child Profile 可见性。
- Web 管理页权限和状态展示。

### 11.3 Ops 服务

- Ops systemd 服务独立于 Agent 容器启动。
- ttyd 使用固定版本和完整性校验，只启动 `zhice-ops-shell`。
- 固定容器名，拒绝浏览器提供任意容器目标。
- ttyd resize、输入输出、断开、idle timeout 和最大会话数。
- Origin、访问层 identity header/断言和无认证拒绝。
- 日志 tail 上限、WebSocket backpressure、异常断线和敏感输出不进入应用 Audit。
- `zhice-ops-shell` 拒绝通用 Shell、任意 Docker 参数、任意路径和未知子命令。
- 精确 sudoers、root-owned wrapper 和 `zhice-operator` 非 docker-group 身份静态校验。
- 三个宿主机权威配置文件首次迁移、只读 bind mount 和发布后保留。
- 配置编辑自动备份、YAML/JSON/env 校验、失败不 apply、原子替换、恢复和重启生效。
- 配置正文与 Secret 不进入 ttyd/Ops/发布日志和审计。
- `diagnose.sh` 在 running、exited、restarting、missing 和 Docker unavailable 下输出有界结果。
- Agent 容器退出后 Ops 仍可直接访问并执行诊断。
- Agent 重启后主 Web health 轮询与恢复。

### 11.4 验收命令

```bash
python -m ruff check .
python -m pytest --basetemp .tmp/pytest_basetemp
cd web/frontend
npm run lint
npm run typecheck
npm run test
npm run build
```

Ops 还需完成 Linux 真实验收，Windows 单元测试不能替代：

```text
systemd start/restart/status
Cloudflare Tunnel + Access 访问
ttyd 交互和 resize
主 Web 新标签页与 iframe；iframe 不可用时正确回退
docker status/logs/restart
宿主机配置编辑、校验、备份、恢复、只读挂载和跨 Digest 保留
通用 Bash、sudo -i、任意 Docker 和任意路径均不可用
Agent 容器退出后的独立救援
私有 PublicUrl 的公网 health 恢复
```

## 12. 验收标准

1. 指令型 Skill 保持兼容，可执行 Skill 只有显式 runtime 才进入 SkillExecutor。
2. 模型通过稳定 `run_skill` 参数调用可执行 Skill，不再拼接 Skill exec 命令。
3. Skill 正常、进度、失败、取消和 timeout 均产生正确 `skill.*` RuntimeEvent。
4. Skill source 页面能展示真实 commit、同步时间、健康和安全错误摘要。
5. 普通用户无法看到未授权 source 或管理详情。
6. 主站与 Ops 真实入口只来自 Git 忽略的 `PublicUrl`、`OpsUrl`，公开仓库不存在个人域名硬编码。
7. Ops URL 完全由部署配置提供，第一版使用 Cloudflare Tunnel + Access，代码不硬编码未来域名。
8. 主 Web 只显示/投影 Ops，不持有 SSH 密码、root key、Docker Socket 或终端字节流。
9. 独立 Ops 服务通过成熟 ttyd 显示容器状态、日志和受限 ZhiCe 运维终端，并保持整体视觉一致。
10. Agent 容器退出后，操作者仍可直接访问 Ops URL执行 `diagnose.sh`、查看日志并恢复服务。
11. 非 Owner 在主 Web 看不到服务器运维入口；直接 Ops 地址仍由独立 Cloudflare Access 策略保护。
12. `zhice-operator` 不能取得宿主机通用 Shell、`sudo -i` 或任意 Docker 权限，只能运行明确的 ZhiCe 运维命令。
13. 不引入多 profile、keyring、Secret Manager、CLI Session 管理、多服务器管理或 Skill 市场。
14. 云端三份权威配置可以受控编辑、校验、备份和恢复，并在容器重启和新 Digest 重建后保持。
15. Python、前端和 Ops 专项测试通过，并完成真实 Linux/Cloudflare Access 验收。

## 13. 实施顺序

1. 固化可执行 Skill metadata、请求、结果和错误协议。
2. 实现 SkillExecutor、`run_skill`、ProgressSink 与 `skill.*`。
3. 补齐 Skill source 状态、缓存、权限过滤和 Web 页面。
4. 固定 ttyd 版本，设计 `zhice-ops-shell`、最小 sudoers/root wrapper 和宿主机 systemd 部署。
5. 将云端运行配置迁移为宿主机权威副本和容器只读 bind mount，补齐编辑、校验、备份、恢复与 apply。
6. 新增 `diagnose.sh`，复用既有 status/logs/restart 运维脚本。
7. 在主 Web 增加 Owner 运维入口、配置化 URL、独立窗口和 iframe 投影及回退。
8. 接入 Cloudflare Tunnel + Access，完成 Agent 容器失效救援验收。
9. 同步 Part 18 活文档、总体设计、索引、README 和相关 Part 交叉引用。

Part 18 完成的判定不是“页面上出现一个终端按钮”，而是 Skill 已成为正式可观测运行单元，且服务器运维控制面在不扩大 Agent Gateway 宿主机权限的前提下，能够在 Agent 容器失效时独立完成诊断和恢复。

## 14. 最终确认项

1. 终端采用 restricted 模式，只观察和维护固定 `zhice-agent` 容器及其持久配置、日志和健康；不提供整个服务器的通用 Shell。
2. 第一版 Ops 公网入口采用 Cloudflare Tunnel + Access。
3. 主 Web 第一版同时提供独立窗口和 iframe 投影，iframe 受浏览器或 Access 策略阻止时回退到独立窗口。
4. 终端采用成熟 ttyd，并通过主题覆盖与 ZhiCe-Agent 整体视觉保持一致；不自行重写 PTY/WebSocket 核心。
5. 可执行 Skill 正式接受 `runtime` frontmatter 与 `ndjson-v1`，后续演进保持向后兼容。
