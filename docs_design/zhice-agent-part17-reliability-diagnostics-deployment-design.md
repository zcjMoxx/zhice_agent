# ZhiCe-Agent Part 17：运行可靠性、系统级诊断与私有镜像部署

> 文档类型：当前活文档
>
> 当前状态：实现、本地生产镜像、私有 registry 与真实云端部署验收均已进入代码基线；本地、已有镜像上云、源码完整上云三个入口均已真实端到端验收
>
> 日期设计记录：`docs_design/2026-07-29-part17-runtime-reliability-diagnostics-and-deployment-design.md`、`docs_design/2026-08-04-private-registry-cloud-release-pipeline-design.md`

## 1. Part 17 定位

Part 17 把当前功能完整的本地单进程 Agent Runtime 收敛为可诊断、可恢复、可构建私有镜像并部署到云端的生产形态。

本阶段包含四条相互依赖的主线：

1. Provider 错误分类、有限重试、退避、cooldown 和 failover 证据。
2. 系统级诊断、事故聚合、跨组件时间线和受控管理入口。
3. MCP Catalog 动态刷新、重连、取消和运行统计。
4. 单进程恢复边界、本机私有镜像组装、推送和云端部署。

Part 17 复用 Part 15 的 Session/Context 真值和 Part 16 的 Vue 管理页面，不重新实现索引或第二套 Web。

## 2. 当前代码基线

当前已经具备：

- Provider 稳定 error code、retryable、同 endpoint 有限重试、总 deadline、退避、cooldown、failover 和有界 attempts 证据；
- OpenAI-compatible 与 LiteLLM adapter 的安全错误归一化，401/403/404 不做原 endpoint 无意义重试；
- `diagnostics.system.use`、`diagnose_system_activity`、确定性事故聚合、跨组件脱敏时间线、REST API 和 Part 16 管理页入口；
- actor-scoped `diagnose_my_recent_activity`，普通用户仍不能跨用户或跨 Session 读取证据；
- MCP `tools/list_changed`、版本化 Catalog 原子刷新、reload/reconnect、活动调用取消、运行统计、OAuth 状态与 artifact 生命周期；
- Gateway 启动时遗留 running Turn 终结、Session/Context 派生状态重建、单 Gateway/单 worker/单 writer 拓扑约束；
- `deploy/` 私有配置覆盖层、Dockerfile、compose，以及 build/push/local smoke/deploy/stop/status/logs 脚本；
- Vue 与微信 sidecar 可复现构建、部署静态检查、compose 校验和 LF 固定的前端入口产物。

本地生产镜像已完成真实构建与容器验收；私有镜像已推送到阿里云 ACR，并在腾讯云 Ubuntu 单机按不可变 Digest 完成真实部署，Caddy HTTPS、公网健康、认证初始化和容器重启持久化均已验证。2026-08-04 在该真实链路上补充三入口本地流水线，减少后续升级时的手工 tag、push、scp、ssh 和 Digest 传递。

## 3. 稳定边界

- AgentLoop 仍只负责通用循环，不识别 HTTP、重试策略、Docker 或系统事故。
- LLM 调用仍只经过 `LLMProvider`；重试与 failover 位于 Provider wrapper。
- Tool 只由 AgentLoop 在成功 LLM 响应后调度，Provider 重试不得重复 Tool。
- 系统诊断位于 app/application 层，读取协议化 Activity/trace 数据。
- 普通用户诊断保持 actor-scoped；跨用户诊断必须有 `diagnostics.system.use`。
- 部署层依赖 app 入口，core 不依赖 Docker、反向代理或云平台。
- 第一版生产部署固定单 Gateway 进程、单 worker、单 workspace writer。
- Part 17 不引入 Kubernetes、多环境 overlay、共享队列或外部向量数据库。

## 4. Provider 可靠性

稳定错误分类至少包括：

```text
AUTH_FAILED
MODEL_NOT_FOUND
RATE_LIMITED
NETWORK_ERROR
TIMEOUT
INVALID_RESPONSE
PROVIDER_UNAVAILABLE
PROVIDER_ERROR
```

`LLMProviderError` 提供 code、HTTP status、retryable、安全提示、endpoint/model 和有界 attempts。可重试错误受 endpoint 最大尝试次数与调用总 deadline 双重约束，支持 `Retry-After`、指数退避、轻量 jitter 和进程内 cooldown。

401/403/404 不在原 endpoint 重试；429、网络错误、超时和符合策略的 5xx 可以有限重试。endpoint 耗尽后再进入现有 failover 顺序。

每次尝试记录 endpoint、model、attempt、duration、result、error code、backoff 和 skip/cooldown reason，不记录 Secret、完整请求或原始敏感响应。

## 5. 系统级诊断

新增：

```text
permission: diagnostics.system.use
tool: diagnose_system_activity
service: SystemDiagnosticsService
```

Owner 默认拥有该权限；其它角色需要显式授权。系统诊断支持按时间、用户、Session、Turn、request、channel、component、endpoint/model、Tool、MCP Server、状态和错误码查询。

事故由确定性规则从 Runtime Activity 和 trace 聚合。LLM 可以解释事故证据，但不能把推断直接写成运行真值。管理页面继续使用 Part 16 的系统监控入口，增加事故列表、筛选和时间线，不建立独立 Web 应用。

## 6. MCP 可靠性

- 接收 `tools/list_changed` 或等价通知。
- 校验并原子切换版本化 Catalog snapshot。
- 支持单 Server reload/reconnect。
- 将 Turn cancellation 传递到活动 MCP 调用。
- 保存安全连接历史、OAuth 状态、Tool 错误和延迟摘要。
- artifact 使用有界预览、版本、保留和流式导入。

单 Server 故障继续局部降级。

## 7. 状态与生产拓扑

Part 17 第一版生产拓扑固定：

```text
one image
one Gateway process
one worker
one writer for one ZHICE_AGENT_WORKSPACE
```

Session JSONL、Memory、Auth/RBAC、渠道绑定属于运行真值；Context index、embedding、compaction 和 Activity 聚合属于可重建派生状态；active turn、cooldown 和临时缓存属于进程状态。

Gateway 重启时必须终结遗留 running Turn；关闭时停止接收新 Turn并有界处理 active turn、Memory、MCP 和渠道任务。派生索引损坏时从 Session 真值重建。

## 8. 私有 `deploy/` 覆盖层

```text
deploy/
+-- Dockerfile
+-- docker-compose.yml
+-- README.md
+-- .gitignore
+-- .env                 # 私有，不提交
+-- config.yml           # 私有，不提交
+-- models.json          # 私有，不提交
+-- scripts/
    +-- build-image.ps1
    +-- push-image.ps1
    +-- run-local.ps1
    +-- deploy.sh
    +-- stop.sh
    +-- status.sh
    +-- logs.sh
```

`deploy/` 不是完整 workspace 副本。仓库已有 `agent/`、`prompts/`、`skill_repo/`、`integrations/`、Vue build 和 Python 元数据由 Docker build 直接复制。只有当前部署真实 `.env`、`config.yml`、`models.json` 来自本机私有覆盖层。

三个真实文件不得进入远端 Git。部署脚本、Dockerfile、compose 和 README 正常提交；公开 runtime env 模板只维护仓库根 `config/.env.example`，`deploy/` 不保留第二份模板。

普通 `zcagent init` 已默认从公开唯一模板 `config/.env.example` 生成 `${workspace}/config/.env`，并与 `config.yml`、`models.json` 共享“缺失补齐、已有保留、`--force` 覆盖”语义；`--write-env` 只作 CLI 兼容。部署优先从当前 `${workspace}/config/` 复制三个真实文件到 `deploy/private/`，项目 `config/.env` 仅用于 legacy migration，且任一来源复制到 `deploy/private/.env` 后都不得保留 `ZHICE_AGENT_WORKSPACE`。

## 9. 私有镜像与云端数据

本机先构建包含程序、仓库资产和三个私有配置的完整镜像，再推送到受控私有镜像仓库。云端按 digest 直接拉取，不重新拼装三个配置文件。

镜像固定：

```text
default workspace=/home/zhice/.zhice
/home/zhice/.zhice/config/.env
/home/zhice/.zhice/config/config.yml
/home/zhice/.zhice/config/models.json
```

容器与本地使用同一 workspace 解析协议：`--workspace > ZHICE_AGENT_WORKSPACE > Path.home() / ".zhice"`。普通运行先确定 workspace，再加载 `${workspace}/config/.env`；该文件不得反向定义 `ZHICE_AGENT_WORKSPACE`。只有显式 `--env-file` 可以兼容提供 workspace。项目 `config/.env` 仅作为遗留迁移 fallback。

云端只对运行后产生的数据使用 volume：

```text
/home/zhice/.zhice/contexts
/home/zhice/.zhice/state
/home/zhice/.zhice/logs
/home/zhice/.zhice/extends
/home/zhice/.zhice/config/channels/weixin/accounts
```

Session、Memory、用户数据库、Context index、compaction、日志和测试缓存不得从本地烘入镜像。微信扫码生成的账号凭据只进入独立命名卷，不与完整 `config/` 混挂；`.env`、`config.yml`、`models.json` 继续随私有镜像更新。真实 Secret 若进入私有镜像层或运行卷，则 registry、宿主机和 Docker volume 的访问权限必须按 Secret 权限管理。

## 10. 部署脚本

- `build-image.ps1`：校验私有文件，按需传入经过白名单检查的 APT 镜像主机，构建 Vue/微信 sidecar/运行镜像，写版本信息并扫描误带数据。
- `deploy/*.cmd`：根目录三个 Windows 双击薄入口，名称显式区分是否构建镜像与本地/云端目标。
- `deploy/pipelines/build-and-deploy-local.ps1`：固定使用阿里云 APT 镜像与标准镜像/端口，完成当前源码 build、隔离 smoke、Compose 更新和有界 health 验收。
- `deploy/pipelines/deploy-existing-image-to-cloud.ps1`：复用操作者确认过的 `zhice-agent:local`，不执行 build，默认不重复 smoke，自动生成发布标签、推送私有 registry 并按 Digest 部署云端。
- `deploy/pipelines/build-and-deploy-cloud.ps1`：从当前源码 build、隔离 smoke 后进入同一云端发布模块，不调用本地 Compose。
- `deploy/private/cloud-target.json`：Git 忽略的本机目标配置；`SshPassword` 是其中唯一允许的明文部署 Secret，只依赖本机文件权限和 Git ignore 保护，Token、其他 Secret 与私钥仍禁止进入。Paramiko 从该文件读取 SSH/sudo 共用密码，加载 `%USERPROFILE%/.ssh/known_hosts` 并以 `RejectPolicy` 校验主机密钥；sudo 密码只经 PTY 的 stdin 传入并在输出中脱敏。公开 example 的待填写值直接使用中文。
- `deploy/pipelines/invoke-cloud-release.ps1` 与 `deploy/scripts/remote_ops.py`：前者校验固定镜像名与 `linux/amd64`、生成时间戳与 Git 短提交号标签并精确取得目标 RepoDigest；后者强制要求 `RemoteOpsDir`，将 `deploy/status/logs/stop/restart` 五个运维脚本上传到 `RemoteOpsDir/releases/<release>`，逐个 `sh -n` 后原子切换 `current`，再部署并从云服务器侧受控 curl 验证公网 HTTPS `status=ok`。本机 health 只作附加诊断，本机代理、TUN DNS 或 TLS 异常不再覆盖已通过的远端公网判定。
- `push-image.ps1`：推送到私有 registry 并输出 digest，不回显 Secret。
- `run-local.ps1`：使用最终镜像完成 health、Web、配置加载和优雅退出烟测。
- `deploy.sh`：云端按 digest 启动单实例，挂通用运行数据 volume 和独立微信账号凭据 volume；发布、回滚和重启均不删除凭据卷。
- `stop.sh`：优雅停止。
- `status.sh`：显示容器、版本、digest 和 health。
- `logs.sh`：读取脱敏日志。

## 11. 实施结果

1. Provider 错误协议、分类、有限重试、deadline、退避、cooldown 和 attempts 证据已落地。
2. 系统诊断权限、服务、Tool、API 和管理页面已落地。
3. MCP reload/cancellation、Catalog snapshot、运行统计和 artifact 生命周期已落地。
4. 重启恢复、派生状态重建和单进程边界已落地。
5. `deploy/`、私有配置加载、Dockerfile、compose、可复现构建和运维脚本已落地。
   Docker Runtime 显式安装 `gateway` WebSocket extra；微信 Sidecar 直接入口使用 Node `pathToFileURL`，并由真实子进程 NDJSON 二维码链覆盖 Linux 入口语义。
   Dockerfile 默认保留 Debian 官方源，同时允许构建脚本或 Compose 显式传入经过白名单检查的 APT 镜像主机；该参数不进入容器运行环境。
   Windows 根目录提供三个 CMD 用户入口；PowerShell 流水线位于 `pipelines/`，底层参数化脚本继续保留在 `scripts/` 供排障和非默认环境使用。
6. 全量 Python 与前端测试、Ruff、lint/typecheck/build、deploy 静态检查和 compose 校验已通过。
7. 真实生产验收已关闭：本地 Compose 容器为 healthy；私有镜像成功推送阿里云 ACR；腾讯云实例按 Digest 运行且重启后认证数据保留；`https://agent.zouzhou.xyz/health` 经 Caddy 返回 `status=ok`，QQ/微信渠道 available。2026-08-04 三入口又分别完成真实验收：本地入口 build/smoke/Compose healthy，已有镜像入口完成 push/原子同步/远端健康与公网 health，源码完整入口完成 build/smoke/push/deploy 全链；现场发现的 PowerShell 5.1 RepoDigest 数组嵌套、Paramiko 2.8 warning stderr 和发布端 TUN fake DNS `198.18.1.0` 假阴性均已修复并纳入自动化契约。
   QQ 公网部署还要求每个启用账号在镜像私有 `config.yml` 中显式设置可访问的 HTTPS `web_base_url`；当前云端 `main` 使用 `https://agent.zouzhou.xyz`，避免裸 `/bind` 继承本地 `127.0.0.1` 默认值。该账号不得同时由本地 Compose 与云端实例消费。

## 12. 验收标准

1. Provider 错误分类、重试、cooldown 和 failover 可观测且不泄露 Secret。
2. LLM 重试不会重复执行 Tool。
3. 授权管理员可以从事故下钻完整时间线，普通用户不能越权。
4. MCP Catalog 可安全刷新，活动调用可取消，单 Server 失败局部降级。
5. 重启后没有永久 running Turn，派生索引可从 Session 重建。
6. 公开 Git 不包含三个私有 deploy 文件和真实 Secret。
7. 本地脚本能构建、烟测并推送完整私有镜像。
8. 云端按 digest 直接启动，镜像升级不丢运行数据。
9. 镜像不携带本地 Session、Memory、用户数据库、日志或测试缓存。
10. Python、前端、镜像和部署烟测全部通过。

## 13. 后续 Part

Part 18 继续 Skill Runtime、CLI 和本地运维优化，复用 Part 17 的系统诊断服务和部署状态，但不回头改变 Part 17 的私有镜像与单进程边界。

## 14. 当前验证状态

- `python -m ruff check .`：通过。
- Python 首轮全量并行测试：`798 passed, 1 skipped, 1 failed`；唯一失败是 Memory Windows retry 用例的并行偶发失败，该用例随后单独复跑通过。
- 前端测试：`37 passed`；lint、typecheck、production build 均通过。
- deploy 静态检查与 compose 配置校验：通过。
- 微信 Sidecar Node 测试：`14 passed`，build 通过；真实子进程覆盖 hello、health、二维码连接与 shutdown。
- 既有 Docker image build/run 基线：通过，镜像内 `websockets=15.0.1`，日志包含 `[weixin] channel ready`，Gateway routes 包含 `/ws`。2026-07-31 新增的可配置 APT 镜像参数已通过静态测试，真实参数化构建由本地部署教学流程继续验收。
- 私有 registry push 与真实云端 deploy：已完成；云端镜像锁定不可变 Digest，Gateway 只在宿主机 loopback 暴露 10086，公网由 Caddy 80/443 提供 HTTPS。
- 三入口真实端到端验收：改名前的三条等价流水线全部退出码 `0`；当前 `build-and-deploy-local` 对应 build/smoke/Compose healthy，`deploy-existing-image-to-cloud` 与 `build-and-deploy-cloud` 对应 ACR push、Paramiko 五脚本原子同步、sudo 部署、远端 running/healthy 和云服务器侧公网 HTTPS `status=ok`。
