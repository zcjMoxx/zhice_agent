# ZhiCe-Agent Part 17 运行可靠性、系统级诊断与私有镜像部署设计记录

> 说明：本文正文保留 2026-07-29 当时采用 `/opt/zhice` 和项目 `config/.env` 的历史方案；当前实现已由默认工作目录收敛方案取代：默认 workspace 为 `Path.home() / ".zhice"`（Windows `C:\Users\<user>\.zhice`，Docker `/home/zhice/.zhice`），运行态 env 为 `${workspace}/config/.env`。最新口径以 Part 17 活文档和总体设计为准。

> 日期：2026-07-29
>
> 状态：方案代码与测试已落地；真实镜像、云端部署与推送验收待 Docker daemon 可用后关闭
>
> 归属：Part 17 运行可靠性、系统级诊断、生产部署与发布
>
> 当前活文档：`docs_design/zhice-agent-part17-reliability-diagnostics-deployment-design.md`

> 实施说明（2026-07-30）：Provider 结构化错误、有限重试/deadline/backoff/cooldown/attempts，系统诊断权限/Tool/API/管理页，MCP Catalog 刷新/重连/取消/统计，遗留 Turn 恢复与派生状态重建，以及 `deploy/` 私有覆盖层、Dockerfile、compose 和运维脚本均已进入当前代码。全量 Python 测试 `789 passed, 1 skipped`，Ruff、前端 `29` 项测试、lint/typecheck/build、deploy 静态检查与 compose 校验均通过。当前机器 Docker Desktop daemon 未运行，因此真实 image build/run smoke、registry push 和云端 deploy 尚未执行；这属于生产部署验收未关闭，不代表实现缺失。

## 1. 背景

方案制定阶段以 Part 16 为既有基线，当时 ZhiCe-Agent 已具备统一 AgentLoop、Session、Tool、Skill、Memory、MCP、Hook、Subagent、QQ/微信渠道、完整 Session 上下文工程和 Vue Web 产品面，以及 endpoint 顺序 failover、RuntimeEvent、结构化 Runtime Activity、Security Audit、本人近期活动诊断、Gateway health 和优雅关闭基础。

当前缺口不再是“能不能运行”，而是以下三条链尚未闭环：

1. Provider 调用失败时只有较轻量的异常文本和 endpoint failover，没有稳定错误分类、retryable、同 endpoint 有限重试、退避、cooldown 和完整尝试证据。
2. 当前诊断只覆盖本人当前 Session 的最近活动，管理后台只展示 health/Activity 真值，没有跨用户、跨组件的系统级事故聚合和根因时间线。
3. 项目还没有一套与当前本地配置实况一致的私有镜像组装与云端部署方式。公开仓库应继续只保存源码和脱敏示例；本机真实 `.env`、`config.yml`、`models.json` 需要在仓库内的私有 `deploy/` 覆盖层中参与镜像构建，但不能提交到远端 Git。

Part 17 将这三条链按依赖顺序收敛为一个完整阶段，同时补齐 MCP 动态可靠性、状态恢复和发布验收。

## 2. 已确认的关键决策

### 2.1 Part 17 不是单纯增加 Dockerfile

容器化不能替代运行可靠性。Part 17 先稳定 Provider 错误协议和运行证据，再建立系统诊断，最后把已经可诊断、可恢复的单进程运行时装入镜像并部署。

### 2.2 `deploy/` 是本机私有配置覆盖层，不是完整 workspace 副本

`deploy/` 只保存镜像组装与部署脚本，以及当前部署独有的三个真实配置文件：

```text
deploy/.env
deploy/config.yml
deploy/models.json
```

以下内容已经存在于公开仓库，镜像构建时直接从仓库复制，不在 `deploy/` 中重复维护：

```text
agent/
prompts/
skill_repo/
integrations/
agent/web/static/
pyproject.toml
README.md
```

Session、Memory、用户数据库、渠道登录态、Context index、compaction、日志和临时文件既不复制到 `deploy/`，也不烘入镜像；它们由云端启动后产生并通过云端 volume 持久化。

### 2.3 本地构建完整私有镜像，云端直接拉取运行

本机使用真实 `deploy/.env`、`deploy/config.yml`、`deploy/models.json` 组装完整镜像，然后推送到受控的私有镜像仓库。云端按镜像 digest 拉取并运行，不再手工逐项复制这三个配置文件。

这意味着真实 Secret 可能按操作者明确选择进入私有镜像层。它们不得进入公开 Git、公开 wheel 或公开镜像；私有镜像仓库的拉取权限等同于 Secret 读取权限。后续若安全要求提高，可以把 Secret 改为平台 Secret 注入，但不改变 `config.yml`、`models.json` 和 `${ENV_VAR}` 的运行协议。

### 2.4 第一版生产拓扑固定为单 Gateway 进程

当前 active turn、Session JSONL 写入、Memory 文件锁和部分后台调度仍有进程内状态。Part 17 第一版明确：

```text
replicas = 1
gateway workers = 1
允许进程内线程和后台任务
不允许多个 Gateway 同时写同一 ZHICE_AGENT_WORKSPACE
```

Part 17 负责诚实声明并强制这一边界，不为“看起来生产化”而提前引入 Redis、共享队列、多副本或外部向量数据库。

### 2.5 复用 Part 16 管理页面

系统诊断数据继续接入 Part 16 的 Vue 管理后台。Part 17 不建立第二套 Web，不建立第二套 AgentLoop，也不把容器或诊断业务写入 AgentLoop。

## 3. 目标

1. 为 Provider 错误建立稳定、结构化、可测试的协议。
2. 为 retryable 错误增加受总 deadline 约束的有限重试、退避、cooldown 和 endpoint failover。
3. 确保任何 LLM 重试都不会重复执行已经完成的 Tool。
4. 建立可按用户、Session、Turn、request、component、endpoint、Tool、MCP Server 和时间范围查询的系统诊断服务。
5. 新增受 `diagnostics.system.use` 保护的 `diagnose_system_activity` Tool，并保留普通用户本人诊断边界。
6. 补齐 MCP Catalog 动态刷新、重连、活动调用取消和运行统计。
7. 明确 Session、Auth、Memory、Context 派生状态的备份、恢复、重建和进程边界。
8. 建立本机私有 `deploy/` 覆盖层、镜像构建、推送、云端启动和日常运维脚本。
9. 证明镜像无需源码工作目录即可运行，并且升级镜像不丢失云端运行数据。

## 4. 非目标

- 不改变 AgentLoop 的通用循环职责。
- 不让 AgentLoop 直接识别 HTTP 状态、Docker、MCP SDK 或云平台。
- 不重新实现 Part 15 的 Context index、compaction 或 Session 真值。
- 不重新实现 Part 16 的管理页面。
- 不引入 Kubernetes、多环境 overlay、自动发布流水线或复杂容器编排。
- 不支持多 Gateway 副本共同写一个 workspace。
- 不把本地 Session、Memory、用户数据库、日志或渠道登录态打进镜像。
- 不在公开仓库提交真实 `.env`、`config.yml`、`models.json` 或 Secret。
- 不把 Part 18 的 SkillExecutor、完整 `zcagent diagnose` CLI 和多 profile 本地运维提前塞入本阶段。

## 5. 总体架构与依赖方向

```text
Vue Admin / REST / privileged diagnostic chat
                    |
                    v
        app SystemDiagnosticsService
          |          |           |
          v          v           v
   RuntimeActivity  trace   Provider/MCP snapshots
                    |
                    v
              protocols only

AgentLoop -> LLMProvider -> retry/failover wrapper -> concrete provider SDK/HTTP
AgentLoop -> ToolProvider / SessionStore / RuntimeEvent

local repository + private deploy overlay
                    |
                    v
             private runtime image
                    |
                    v
          single cloud Gateway process
                    |
                    v
       cloud runtime-data volumes
```

依赖继续保持：

```text
cli/app -> agent core -> protocols
tools   -> protocols/message/base types
deploy  -> app entrypoint and public package artifacts
```

`agent/protocols/` 只增加中性错误、诊断和尝试数据结构，不 import Provider SDK、FastAPI、Docker 或数据库实现。

## 6. Provider 错误与重试设计

### 6.1 稳定错误分类

Provider adapter 至少归一化以下错误：

| code | 典型来源 | 同 endpoint 重试 |
| --- | --- | --- |
| `AUTH_FAILED` | HTTP 401/403、无效凭据 | 否 |
| `MODEL_NOT_FOUND` | HTTP 404、模型不存在 | 否 |
| `RATE_LIMITED` | HTTP 429 | 是 |
| `NETWORK_ERROR` | DNS、连接拒绝、连接重置 | 是 |
| `TIMEOUT` | 连接或读取超时 | 是 |
| `INVALID_RESPONSE` | 非法 JSON、缺失关键响应字段 | 有限一次，由策略决定 |
| `PROVIDER_UNAVAILABLE` | HTTP 5xx、上游不可用 | 是 |
| `PROVIDER_ERROR` | 无法进一步分类的安全兜底 | 默认否 |

### 6.2 `LLMProviderError` 元数据

错误对象新增或等价提供：

```text
code
http_status
retryable
safe_message
endpoint
model
attempts
```

原始响应体、Authorization header、API key、完整请求 messages 和敏感 Tool 参数不得进入异常、Runtime Activity、普通日志或用户提示。

### 6.3 重试与 failover

调用顺序：

```text
resolve endpoint order
  -> skip endpoints still in cooldown
  -> call current endpoint
  -> retryable? bounded retry with backoff
  -> endpoint exhausted? enter cooldown when policy matches
  -> try next enabled endpoint
  -> success or raise structured aggregate error
```

策略要求：

- 每个 endpoint 有有限 `max_attempts`，默认值保持保守。
- 所有 endpoint 尝试共享一个总 deadline，不能让 endpoint 数量线性放大无上限等待。
- 优先遵循合法 `Retry-After`，否则使用指数退避和轻量 jitter。
- 401/403/404 不在原 endpoint 重试，但仍可按 endpoint failover 策略尝试其它已配置 endpoint。
- cooldown 是进程内运行优化，不作为持久真值；重启后可以恢复为健康未知。
- Provider wrapper 只重复 LLM 请求。Tool 调用仍只由 AgentLoop 在收到成功响应后调度，因此 Provider 重试不得重新进入 Tool dispatch。

### 6.4 尝试证据

每次 LLM 调用记录有界安全证据：

```text
endpoint_name
model
attempt_index
started_at / duration_ms
result
error_code
http_status
retryable
backoff_ms
skip_reason
cooldown_until
```

成功响应和最终异常都携带尝试摘要，使 AgentLoop 仍只消费一个 `LLMProvider` 调用结果，同时 Runtime Activity 可以还原实际尝试链。

## 7. 系统级诊断设计

### 7.1 权限

新增：

```text
diagnostics.system.use
```

Owner 默认拥有；Admin、Developer、Auditor 不默认继承，需要 Owner 或受控角色配置显式授予。`turn.read.any` 只表示读取跨用户运行摘要，不自动等价于查看系统根因证据。

### 7.2 两级诊断边界

```text
diagnose_my_recent_activity
  -> 当前 actor
  -> 当前 Session / 本人最近活动
  -> 普通用户可用

diagnose_system_activity
  -> diagnostics.system.use
  -> 跨用户、跨 Session、跨组件
  -> 强制字段白名单、脱敏和有界查询
```

### 7.3 查询维度

系统诊断至少支持：

- 时间范围；
- user/session/turn/request；
- channel；
- component/stage；
- endpoint/model；
- Tool/MCP Server；
- running/completed/stopped/error；
- error code 和 incident id。

### 7.4 事故与时间线

事故先由确定性规则聚合事实，例如：

- 同 endpoint 连续限流或网络失败；
- 多 endpoint 全部失败；
- Tool 或 MCP Server 在窗口内持续超时；
- Session 保存失败；
- Context compaction/index 连续降级；
- Gateway 重启时遗留 running Turn。

LLM 可以读取事故证据并解释根因，但不能把模型推断直接写成新的运行真值。管理页面提供事故列表、筛选和 Turn/request/component 时间线，继续复用 Part 16 的管理路由和组件。

## 8. MCP 动态可靠性

Part 17 在当前 MCP 启动发现和共享 Runtime 基础上增加：

1. 接收 `tools/list_changed` 或等价变更通知。
2. 生成版本化 Catalog snapshot，校验完成后原子替换。
3. 旧 snapshot 的活动调用完成或被取消后再释放连接。
4. 支持显式配置 reload 和单 Server 重连，不重启整个 Gateway。
5. 将当前 Turn cancellation 传递到活动 MCP 调用。
6. 记录连接历史、OAuth 状态、Tool 调用次数、错误率和延迟摘要。
7. artifact 增加有界预览、版本和保留策略；大文件导入使用流式或有界缓冲。

单个 MCP Server 失败继续局部降级，不改变基础聊天、其它 MCP Server 或本地 Tool 的可用性。

## 9. 状态、备份与恢复

### 9.1 数据分类

| 类型 | 内容 | 策略 |
| --- | --- | --- |
| 镜像配置 | `deploy/.env`、`config.yml`、`models.json` | 私有镜像携带，不进入公开 Git |
| 运行真值 | Session JSONL、Memory、Auth/RBAC、渠道绑定与必要状态 | 云端 volume 持久化和备份 |
| 派生状态 | context index、embedding、compaction、Activity 聚合 | 可备份，但必须支持从真值重建 |
| 临时状态 | active turn、cooldown、临时 artifact、缓存 | 重启后清理或重新计算 |
| 日志 | trace、终端和应用日志 | 独立保留策略，不进入镜像 |

### 9.2 重启恢复

- Gateway 启动时识别并终结上一次进程遗留的 running Turn，使用稳定原因码标记为 interrupted/error。
- 当前进程关闭时先停止接收新 Turn，再取消或有界等待 active turn、Memory、MCP 和渠道任务。
- Session JSONL 保持会话真值，不因 compaction/index 重建被改写。
- Context index 损坏或版本不兼容时隔离旧文件并从 Session 重建。
- 备份与恢复不得把本地开发工作区内容混入云端运行数据。

## 10. `deploy/` 目录设计

### 10.1 目录结构

```text
deploy/
+-- Dockerfile
+-- docker-compose.yml
+-- README.md
+-- .env.example
+-- .gitignore
+-- .env                 # 本机真实文件，不提交
+-- config.yml           # 本机真实文件，不提交
+-- models.json          # 本机真实文件，不提交
+-- scripts/
    +-- build-image.ps1
    +-- push-image.ps1
    +-- run-local.ps1
    +-- deploy.sh
    +-- stop.sh
    +-- status.sh
    +-- logs.sh
```

### 10.2 Git 边界

`deploy/.gitignore` 至少包含：

```gitignore
.env
config.yml
models.json
```

部署脚本、Dockerfile、compose、README 和无 Secret 的 `.env.example` 正常提交，供公开仓库使用者理解和复用部署方式。

真实文件由本地操作者从当前环境复制：

```text
当前项目 config/.env                -> deploy/.env
当前 ZHICE_AGENT_WORKSPACE/config/config.yml -> deploy/config.yml
当前 ZHICE_AGENT_WORKSPACE/config/models.json -> deploy/models.json
```

复制后必须把 `.env` 中的 Windows 本地 workspace 路径调整为容器固定路径，或由 Dockerfile 固定覆盖。

### 10.3 镜像内路径

```text
/app/agent
/app/prompts-source
/app/skill_repo
/app/integrations/weixin_sidecar
/app/agent/web/static

/opt/zhice/config/.env
/opt/zhice/config/config.yml
/opt/zhice/config/models.json
/opt/zhice/prompts
/opt/zhice/contexts
/opt/zhice/state
/opt/zhice/logs
/opt/zhice/extends
```

镜像固定：

```text
ZHICE_AGENT_WORKSPACE=/opt/zhice
```

当前配置加载需要同步收敛为：进程环境优先，其次读取 `${ZHICE_AGENT_WORKSPACE}/config/.env`；仓库 `config/.env` 继续只作为源码启动兼容入口和示例来源。这样私有镜像不依赖 Python 安装位置或源码工作目录寻找真实 `.env`。

### 10.4 镜像内容

镜像构建直接复制公开仓库中的代码、Prompt source、内置 Skill source、Vue production build 和微信 sidecar source/build 产物。只有 `.env`、`config.yml`、`models.json` 来自私有 `deploy/` 覆盖层。

微信 sidecar 的 `dist/` 当前是本地忽略产物；Docker 多阶段构建必须从受审计的 `integrations/weixin_sidecar` source/vendor 清单可复现生成 `dist/main.js`，不能依赖开发机残留文件。

### 10.5 云端运行数据

云端不覆盖镜像内 `config/` 和 `prompts/`，只为运行后产生的数据挂载 volume：

```text
/opt/zhice/contexts
/opt/zhice/state
/opt/zhice/logs
/opt/zhice/extends
```

镜像升级替换程序和私有配置；volume 保留 Session、Memory、用户数据库和运行状态。首次部署和升级都固定单 Gateway worker。

## 11. 部署脚本职责

### 11.1 `build-image.ps1`

- 检查 `deploy/.env`、`config.yml`、`models.json` 存在。
- 拒绝明显的 Example 占位值和 Windows 容器路径错误。
- 构建 Vue production assets。
- 构建并验证微信 sidecar。
- 构建运行镜像并写入版本、Git commit、构建时间标签。
- 扫描构建 context 和镜像清单，拒绝 Session、Memory、SQLite 用户库、日志和测试临时目录。

### 11.2 `push-image.ps1`

- 登录受控私有镜像仓库。
- 使用明确版本 tag 推送。
- 输出不可变 image digest。
- 日志不打印 `.env` 内容、registry password 或模型 API key。

### 11.3 `run-local.ps1`

- 使用最终镜像而不是源码挂载启动本地烟测。
- 固定单 worker。
- 验证 `/health`、Web 首页、配置加载、模型路由和优雅退出。
- 使用临时运行数据 volume，不复用开发 Session/Memory。

### 11.4 `deploy.sh`

- 按明确 digest 拉取镜像。
- 创建或复用云端运行数据 volume。
- 启动单实例 Gateway。
- 等待 readiness；失败时保留可回退的上一镜像信息。
- 不在云端重新拼装或修改镜像内三个私有配置文件。

### 11.5 日常脚本

- `stop.sh`：有界优雅停止。
- `status.sh`：输出容器、版本、digest 和 health。
- `logs.sh`：读取脱敏日志，不回显 Secret。
- backup/restore 在状态恢复实现稳定后增加，或由同一目录中的明确脚本承接。

## 12. 配置与 Secret 优先级

容器运行时采用：

```text
平台/显式进程环境
  > 私有镜像内 /opt/zhice/config/.env
  > config.yml / models.json 中的非敏感默认值
```

`models.json` 继续允许 `api_key` 写明文或 `${ENV_VAR}`。推荐保留 `${ENV_VAR}`，真实值在私有 `.env`；即使二者最终进入同一个私有镜像，这也能保持配置结构清楚，并为未来切换平台 Secret 留出兼容路径。

公开 `.env.example` 只能包含空值或明显占位符。构建与推送日志不得输出三份私有文件内容。

## 13. 计划变更文件

设计落地预计涉及但不限于：

```text
agent/protocols/llm.py
agent/protocols/diagnostics.py
agent/protocols/runtime_event.py
agent/llm/openai_provider.py
agent/llm/litellm_provider.py
agent/llm/failover_provider.py
agent/auth/schema.py
agent/auth/store.py
agent/auth/diagnostics.py
agent/tools/diagnostics.py
agent/mcp/catalog.py
agent/mcp/runtime.py
agent/app/runtime.py
agent/app/api/schemas.py
agent/app/api/routes.py
agent/config.py
web/frontend/src/api/types.ts
web/frontend/src/stores/admin.ts
web/frontend/src/layouts/AdminLayout.vue
deploy/*
tests/unit_test/llm_provider/*
tests/unit_test/auth/*
tests/unit_test/mcp/*
tests/unit_test/app/*
tests/unit_test/deploy/*
```

新增或扩展测试主题目录时同步维护对应 `test_case.md`。

## 14. 测试方案

### 14.1 Provider

- 401/403 -> `AUTH_FAILED`，不在原 endpoint 重试且不泄露 Secret。
- 404 -> `MODEL_NOT_FOUND`。
- 429 -> `RATE_LIMITED`，遵循有界 `Retry-After`。
- 网络失败、超时、5xx 按策略重试并 failover。
- 非法 JSON 产生 `INVALID_RESPONSE`。
- 总 deadline 到达后停止新增尝试。
- cooldown endpoint 被跳过并记录原因。
- Provider 重试和 failover 不重复执行 Tool。

### 14.2 系统诊断

- 普通用户不能发现或调用系统诊断 Tool。
- Owner 或显式授权 actor 可以按维度查询。
- 跨用户证据经过字段白名单和再次脱敏。
- 事故聚合可稳定复现 endpoint、Tool、MCP、Session 和 Context 故障。
- Part 16 monitor 仍能在诊断部分失败时展示基础真值。

### 14.3 MCP

- `tools/list_changed` 生成新 snapshot。
- 非法新 Catalog 不替换当前可用 snapshot。
- 旧活动调用完成或取消后释放连接。
- 单 Server 重连失败只降级自身。

### 14.4 恢复

- 遗留 running Turn 在重启后进入明确终态。
- Session JSONL 能重建 Session index 和 Context index。
- 损坏派生数据库被隔离且不删除 Session 真值。
- 优雅退出停止 Gateway、渠道、MCP 和后台任务。

### 14.5 镜像与部署

- Git 不跟踪三个私有 deploy 文件。
- 构建缺少任一私有文件时 fail closed。
- 干净机器只用镜像即可启动，不依赖源码挂载。
- Vue、Prompt、官方 Skill 和微信 sidecar 均来自可复现构建。
- 镜像不包含本地 Session、Memory、用户数据库、日志和测试缓存。
- 本地最终镜像烟测通过 `/health`、Web、LLM 配置加载和优雅退出。
- 云端替换镜像后 volume 中的运行数据保持可用。

提交前至少运行：

```text
python -m ruff check .
python -m pytest
npm run lint
npm run typecheck
npm run test
npm run build
```

并执行真实镜像 build/run/stop smoke。

## 15. 实施顺序

1. 稳定 Provider 错误协议、分类和测试。
2. 实现有限重试、总 deadline、退避、cooldown 和安全尝试证据。
3. 扩展 Runtime Activity、系统诊断权限、服务、Tool、API 和 Part 16 管理页面。
4. 实现 MCP Catalog reload/cancellation 和运行统计。
5. 收敛重启恢复、派生状态重建和单进程强制边界。
6. 创建 `deploy/` 私有覆盖层、Dockerfile 和配置加载收敛。
7. 实现 build/push/local smoke/cloud deploy/stop/status/logs 脚本。
8. 完成公开仓库、私有镜像、云端 volume 和故障恢复验收。

## 16. 验收标准

1. Provider 错误拥有稳定 code、retryable、安全提示和完整有界尝试证据。
2. 429、网络错误、超时和 5xx 按策略重试并可 failover；401/403/404 不在原 endpoint 无意义重试。
3. 任意 Provider 重试路径都不会重复执行 Tool。
4. 授权管理员可以从事故下钻到 Turn/request/provider/tool/context/MCP 时间线。
5. 普通用户仍只能诊断本人当前 Session 范围。
6. MCP Catalog 可以安全刷新，活动调用可以取消，单 Server 失败局部降级。
7. Gateway 重启后没有永久 running Turn，Session 真值可重建派生索引。
8. `deploy/` 中只有三个真实配置属于本机私有覆盖；仓库已有资产不重复复制。
9. 本地脚本能构建、烟测并推送带私有配置的完整镜像。
10. 云端按 digest 直接启动镜像，不再逐项复制三个配置文件。
11. 云端镜像升级不丢失 Session、Memory、用户数据库和运行状态。
12. 公开 Git、公开 wheel、公开镜像和构建日志不包含真实 Secret 或本地用户数据。
13. Python、前端、镜像和部署烟测全部通过，或明确记录与本次无关的历史失败。
