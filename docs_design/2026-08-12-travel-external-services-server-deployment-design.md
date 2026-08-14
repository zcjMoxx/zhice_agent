# 旅行外部服务真实验收与服务器部署设计

## 背景

Part 19 已完成应用、Fake MCP 全链和本地真实数据源 smoke。2026-08-11 的真实验收进一步确认：Open-Meteo、高德 Catalog、Tavily Catalog、12306 查询和小红书只读链可用；小红书官方 v2.4.3 当前把站点域名硬编码为 `xiaohongshu.com`，而部分账号登录态实际签发到 `rednote.com`，需要固定提交上的兼容构建。现有服务器镜像只包含 Python Agent 与微信 sidecar，不具备高德/12306 的固定 Node MCP 包，也没有小红书浏览器 sidecar 的持久运行边界。

## 目标

- 真实执行高德 POI/路线、Tavily search/extract、12306、Open-Meteo、小红书登录/搜索/详情和高德 JS 地图 smoke。
- 本地运行配置只引用 `${ZHICE_AGENT_WORKSPACE}/config/.env`，真实 Secret 不进入仓库。
- 服务器继续只发布一个受控 ZhiCe 私有镜像 Digest。
- 主容器内提供 Open-Meteo、高德、Tavily、12306和小红书只读适配器 Catalog。
- 小红书浏览器自动化放在独立 sidecar，Cookie 与浏览器缓存持久化，端口只在私有 Docker 网络可见。
- 保留单源故障降级，不让小红书或任一外部服务阻断 Gateway。

## 范围边界

- 不提交高德、Tavily、Cookie、Authorization、SSH 或 registry Secret。
- 不把 Windows `.exe` 复制到 Linux；服务器镜像从固定上游提交构建 Linux 二进制。
- 小红书 sidecar 只提供登录状态、搜索和详情的上游能力；ZhiCe Catalog 仍只暴露三个只读 Tool。
- 12306 只使用已审查的查询版本，不提供登录、购票、支付或官方 SLA。
- 不把 sidecar 端口映射到公网或宿主机公共地址。
- 不改变 AgentLoop、Session、LLMProvider、Prompt 或 workspace 身份边界。

## 模块设计

### 完整计划的 LLM 超时边界

optimizer 通过后，最终 `TravelPlanV1` 会一次生成完整多日结构化结果，真实兼容端点可能需要超过 60 秒。运行态所有启用 endpoint 统一配置 `request_timeout_seconds=240`、`total_deadline_seconds=300`、`max_attempts=1`，避免单个慢请求被重复放大；Failover 总 deadline 仍取启用端点配置的最小值。

具体 Provider 未显式传入调用级上限时必须直接采用 endpoint 的 `request_timeout_seconds`，不得再用构造函数默认值把配置暗中截断为 60 秒；测试显式注入上限时仍取该上限与 endpoint 配置的较小值。该变化只修正 `LLMProvider` 适配层的超时传递，不改变 AgentLoop、Prompt 或旅行应用边界。

### 主镜像

`deploy/Dockerfile` 在构建阶段固定安装：

- `@amap/amap-maps-mcp-server@0.0.8`；
- `12306-mcp@0.3.1`；
- RedNote 兼容的小红书 Linux 二进制。

最终镜像继续以非 root `zhice` 用户运行。高德、Tavily和小红书凭据只由运行态 `.env` 与只读配置文件解析。

### 小红书 sidecar

同一镜像用 entrypoint 覆盖启动 `/opt/zhice/bin/xiaohongshu-mcp-rednote`：

- 固定容器名 `zhice-xhs-readonly`；
- 只加入 `zhice-travel` 私有 bridge network；
- 容器内监听 `:18060`，不做 `-p` 端口发布；
- `zhice-xhs-data` volume 挂载到 `/home/zhice/.zhice/integrations/xhs/data`；
- `zhice-xhs-cache` volume 挂载到 `/home/zhice/.cache/xiaohongshu-mcp`；
- `COOKIES_PATH` 固定指向 data volume 内的 `cookies.json`；
- `no-new-privileges`、cap drop 和资源限制由 Compose/部署脚本设置。

主容器同样加入 `zhice-travel`，`XHS_READONLY_UPSTREAM_URL` 使用 `http://zhice-xhs-readonly:18060/mcp`。适配器仍拒绝非 loopback 的明文 HTTP，因此服务器容器网络需允许显式受信私网主机名；该放宽只接受配置列出的容器 DNS 名称，不接受任意远端 HTTP。

### Cookie 迁移

本地和服务器 Cookie 都只存在运行 volume。首次服务器部署前，由操作者通过受控 SSH/SCP 把本地隔离 `cookies.json` 上传到 `/etc/zhice-agent/xhs/cookies.json`，权限 `0600 root:root`；部署脚本复制到 `zhice-xhs-data` volume 并改为镜像内 `zhice` uid/gid。日志、诊断和配置查看均不输出正文。

### 旅行计划持久化

`TravelPlanStore` 位于 `${ZHICE_AGENT_WORKSPACE}/travel`，必须由专用命名卷 `zhice-travel-data` 挂载到 `/home/zhice/.zhice/travel`。Compose 和 Digest 发布脚本都幂等创建并挂载该卷；更新、回滚和重启不得删除它。否则容器替换会丢失已成功保存的 TravelPlanV1，即使 Session/日志卷仍存在。

## 数据流

```text
Travel Agent / Subagent
  -> xhs-readonly stdio adapter in main container
  -> http://zhice-xhs-readonly:18060/mcp
  -> isolated browser + Cookie/cache volumes
  -> rednote.com public read pages
```

高德、12306 与 Open-Meteo继续由主容器内 stdio MCP 进程按需启动；Tavily 使用 HTTPS streamable HTTP。

## 变更文件

- `deploy/Dockerfile`
- `deploy/docker-compose.yml`
- `deploy/scripts/deploy.sh`
- `deploy/scripts/status.sh`
- `deploy/scripts/diagnose.sh`
- `deploy/scripts/run-local.ps1`
- `config/.env.example`
- `config/config.example.yml`
- `deploy/README.md`
- `tests/integration_test/travel/README.md`
- `docs_design/zhice-agent-part19-intelligent-travel-planner-design.md`

## 测试方案

- Dockerfile 固定版本与构建产物静态检查。
- Compose config 校验，确认 sidecar 无 host port、使用独立 volume/network。
- Shell `sh -n` 与 PowerShell parser 检查。
- 主镜像 smoke 验证 Gateway、固定 Node 包可执行和小红书 Linux 二进制存在。
- Fake MCP 默认回归。
- 显式环境变量开启真实高德、Tavily、12306、Open-Meteo、小红书 smoke。
- 真实浏览器加载高德 JS SDK，确认安全密钥注入、地图实例、路线 overlay 和降级行为。
- 服务器部署后检查两个容器、私有网络、volume、Gateway health 和小红书登录/搜索/详情。
- Provider 单元测试验证 OpenAI/LiteLLM 默认采用 endpoint 长超时，显式调用级上限仍可收紧。
- 真实外部模型完整执行五天请求，optimizer 通过后必须成功调用 `finalize_travel_plan` 并持久化 `TravelPlanV1`。

## 验收标准

- 所有本地真实数据源 smoke 有明确通过记录；外部抖动按有限重试报告。
- 高德真实 POI/路线与 Tavily search/extract 返回非空、结构可解析结果。
- 高德 JS 地图真实浏览器加载成功，不泄露安全密钥到日志。
- 小红书完整只读 smoke 与失效 Cookie 降级通过。
- 服务器主容器和 sidecar 均健康，sidecar 无公网/宿主机端口发布。
- 容器重建后 Cookie 与浏览器缓存保留。
- 仓库、镜像构建日志和交付说明不包含 Secret/Cookie 正文。
- `python -m ruff check .`、`python -m pytest`、前端 lint/typecheck/test/build 和部署静态检查全部通过。

## 真实完整计划验收记录

本地私有镜像真实执行重庆到大理五天 quick 规划：外部查询存在 Tavily 单次超时/输出过大并按单源降级继续；optimizer 首次拒绝后按原因唯一一次定向修正并返回 `OK`；optimizer 后的完整 `TravelPlanV1` 模型调用耗时约 102 秒，证明 60 秒隐藏截断已消除；`finalize_travel_plan` 最终返回 `code=OK`、生成 plan_id 和 view URL。全程未输出 Secret、Cookie 或 Authorization 正文。
