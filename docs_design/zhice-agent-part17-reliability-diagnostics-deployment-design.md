# ZhiCe-Agent Part 17：运行可靠性、系统级诊断与私有镜像部署

> 文档类型：当前活文档
>
> 当前状态：实现已进入代码基线；生产镜像与云端部署验收待关闭
>
> 日期设计记录：`docs_design/2026-07-29-part17-runtime-reliability-diagnostics-and-deployment-design.md`

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

当前未关闭的是生产环境验收：本机 Docker Desktop daemon 未运行，因此真实 image build/run smoke、私有 registry push 和云端 deploy 尚未执行。代码实现与不依赖 daemon 的测试已经完成，当前状态不是设计待实现。

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

普通 `zcagent init` 已默认从公开唯一模板 `config/.env.example` 生成 `${workspace}/config/.env`，并与 `config.yml`、`models.json` 共享“缺失补齐、已有保留、`--force` 覆盖”语义；`--write-env` 只作 CLI 兼容。部署优先从当前 `${workspace}/config/` 复制三个真实文件，项目 `config/.env` 仅用于 legacy migration，且任一来源复制到 `deploy/.env` 后都不得保留 `ZHICE_AGENT_WORKSPACE`。

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
```

Session、Memory、用户数据库、渠道登录态、Context index、compaction、日志和测试缓存不得从本地烘入镜像。真实 Secret 若进入私有镜像层，则私有 registry 拉取权限必须按 Secret 权限管理。

## 10. 部署脚本

- `build-image.ps1`：校验私有文件，构建 Vue/微信 sidecar/运行镜像，写版本信息并扫描误带数据。
- `push-image.ps1`：推送到私有 registry 并输出 digest，不回显 Secret。
- `run-local.ps1`：使用最终镜像完成 health、Web、配置加载和优雅退出烟测。
- `deploy.sh`：云端按 digest 启动单实例并挂运行数据 volume。
- `stop.sh`：优雅停止。
- `status.sh`：显示容器、版本、digest 和 health。
- `logs.sh`：读取脱敏日志。

## 11. 实施结果

1. Provider 错误协议、分类、有限重试、deadline、退避、cooldown 和 attempts 证据已落地。
2. 系统诊断权限、服务、Tool、API 和管理页面已落地。
3. MCP reload/cancellation、Catalog snapshot、运行统计和 artifact 生命周期已落地。
4. 重启恢复、派生状态重建和单进程边界已落地。
5. `deploy/`、私有配置加载、Dockerfile、compose、可复现构建和运维脚本已落地。
6. 全量 Python 与前端测试、Ruff、lint/typecheck/build、deploy 静态检查和 compose 校验已通过。
7. 真实 image build/run smoke、registry push 和云端部署验收因 Docker daemon 未运行而待关闭。

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
- Python 全量测试：`796 passed, 1 skipped`。
- 前端测试：`29 passed`；lint、typecheck、production build 均通过。
- deploy 静态检查与 compose 配置校验：通过。
- 真实 Docker image build/run smoke：未执行，原因是当前 Docker Desktop daemon 未运行。
- 私有 registry push 与真实云端 deploy：未执行，随生产部署验收一并关闭。
