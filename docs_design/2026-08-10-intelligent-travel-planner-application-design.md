# 智策 Agent 智能旅行规划特色应用设计

> 说明：本文方案已完成代码落地，当前实现基线见 `docs_design/zhice-agent-part19-intelligent-travel-planner-design.md`。本地单元测试、Vue 测试和 Fake MCP 全链已完成；真实高德、Tavily、12306、小红书登录态及高德 JS 浏览器 smoke 仍按显式环境变量和运行时凭据单列执行，不在本文中伪写为已验收。

> 日期：2026-08-10
>
> 状态：代码已实现并完成本地/Fake MCP 验证；真实外部 smoke 待运行时凭据
>
> 归属：Milestone 19，智策 Agent 第一项特色应用
>
> 前置能力：Part 5 Skill、Part 11 MCP、Part 13 Subagent、Part 16 Vue Web、Part 18 Skill Runtime
>
> 调研说明：本文的社区活跃度、版本和产品状态是 2026-08-10 的公开网页快照；实施前必须重新核对上游版本、许可证、服务条款和 API 可用性。

## 1. 背景

智策 Agent 已完成通用 AgentLoop、Tool、Skill、Memory、MCP、Hook、Subagent、外部渠道、上下文工程和 Vue Web 产品面，但还缺少一个能够同时展示这些能力的垂直应用。智能旅行规划被确定为第一项特色应用和主要演示重点。

典型输入：

```text
国庆期间，2 个大学生从重庆出发去大理旅游 5 天左右，预算不要太高。
```

目标不是让模型凭常识输出一篇泛化攻略，而是让 Agent：

1. 识别时间、人数、预算、出发地、目的地和偏好；
2. 调用真实外部能力查询天气、地图、交通、住宿区域、景点和美食；
3. 搜索公开网页和经过只读隔离的小红书近期内容；
4. 区分官方事实、实时查询、网页快照、个体体验和模型估算；
5. 经过路线、时间和预算可行性校验；
6. 保存结构化计划并用专属页面展示。

本应用继续复用同一个 AgentLoop，不建立第二套旅行 Agent 内核。

## 2. 在线调研结论

### 2.1 MCP 与外部能力

MCP 官方 Tool 规范明确：MCP Server 暴露可发现、可调用且带 JSON Schema 的 Tool，用于访问 API、数据库或执行计算；MCP 不负责规定具体用户交互，也不等于业务规划本身。Tool annotations 不能默认视为可信，客户端仍需提供可见的调用状态和必要的人类控制。

这确认了本应用的边界：

- MCP 负责“从哪里得到信息”；
- Skill 负责“如何查、如何比较、如何规划”；
- 内部领域 Tool 负责“如何校验和保存结构化计划”。

### 2.2 候选组件实况

| 候选 | 2026-08-10 实况 | 可用能力 | 本项目结论 |
| --- | --- | --- | --- |
| `@amap/amap-maps-mcp-server` | NPM `0.0.8`，ISC；高德官方命名空间 | 高德地图 Web Service 的 MCP 接入 | 地图、POI、路线和短期天气确定组件，固定版本并做真实 Tool Catalog smoke |
| `tavily-ai/tavily-mcp` | GitHub 2309 stars，MIT，2026-08-09 仍有提交 | search、extract、map、crawl；远程 MCP 与 OAuth/API key | 通用网页搜索与正文提取确定组件 |
| `microsoft/playwright-mcp` | GitHub 35953 stars，Apache-2.0，2026-08-09 仍有提交 | 持久浏览器、现有浏览器扩展、可访问性快照 | 只作为开发 POC 或受控回退；官方明确它不是安全边界，不进入默认多用户运行链 |
| `xpzouying/xiaohongshu-mcp` | GitHub 15173 stars，Apache-2.0，2026-08-10 仍有提交 | 登录、搜索、详情、发布、评论、点赞、收藏等 13 个工具 | 能证明直接搜索可行，但依赖 Cookie、单网页账号登录且含大量写工具；不能原样接入默认运行链 |
| `Joooook/12306-mcp` | GitHub 1162 stars，MIT，2026-07-31 仍有提交 | 车票查询、过站、中转查询 | 只作社区查询型数据源；不负责登录、购票、支付，不宣称官方 SLA |
| Open-Meteo | 官方 Forecast API 默认 7 天、最多 16 天；非商业免费层有日调用限制 | 逐小时、逐日天气、历史天气与地理编码 | 天气交叉校验和长预报窗口确定组件；超过预报窗口只返回历史气候参考 |
| `geekjourneyx/travel-guidebook` | GitHub 76 stars，MIT，2026-04-19 仍有提交 | 旅行 Skill、并行调研、高德 MCP、可行性门控、HTML/PDF | 证明“Skill + 并行调研 + 地图 + 专属输出”可行；不采用其失败后用 LLM 知识补事实的降级方式 |
| Amadeus Self-Service | 官方站已声明 Self-Service Portal 下线并转 Enterprise API | 企业级航旅 API | 本应用不采用，不承诺实时机票或酒店预订 |

星数只用于观察社区采用情况，不作为安全和质量证明。所有社区 MCP 都必须经过源码、许可证、Tool schema、凭据和副作用审计。

### 2.3 数据可行性结论

本应用完整交付能力：

- 地理编码、POI、距离和路线；
- 近期天气；
- 12306 候选班次、余票或中转查询快照；
- 公开网页搜索、正文提取和来源链接；
- 小红书只读搜索和详情；
- 预算区间、路线可行性和每日节奏校验；
- 结构化计划和专属可视化页面。

明确不纳入本应用范围：

- 直接购买火车票、机票或酒店；
- 所有酒店的实时房态与成交价；
- 所有小红书内容都能稳定搜索；
- 超出天气预报窗口的精确天气；
- 第三方平台 Cookie 永久有效；
- 外部查询失败后仍给出“看似真实”的实时数据。

## 3. 目标

1. 所有正常登录用户都能通过聊天或旅行页面生成本人的旅行计划。
2. 复用现有 AgentLoop、actor-scoped ToolProvider、MCP Runtime、SkillLoader、SkillExecutor 和 Subagent。
3. 不自建包办全部业务的通用旅行 MCP；组合成熟外部 MCP、必要的只读安全适配层和一个官方旅行 Skill。
4. 对每个时效性事实保存来源、发布时间、查询时间、数据时间和可信度。
5. 对行程做预算、时间、距离、开放时间和路线折返校验。
6. 输出 `TravelPlanV1`，由专属页面展示日程、地图、预算、天气和来源。
7. 外部能力部分失败时保留已验证结果，并明确显示缺失项和降级状态。
8. 快速模式保证成本和速度，深度模式展示同一 Turn 内的有界并行调研。

## 4. 非目标

- 不实现购票、下单、支付、退改签和酒店预订。
- 不将旅行规划逻辑硬编码进 AgentLoop。
- 不让 Skill 脚本直接 import `agent.*` 或绕过 MCP 调外部服务。
- 不把“生成完整攻略”封装成单个 MCP Tool。
- 不默认启用需要个人登录态的浏览器 MCP。
- 不调用小红书发布、评论、点赞或收藏工具。
- 不绕过验证码、风控、频率限制或平台访问控制。
- 不长期保存完整社交平台正文、图片或视频副本。
- 不承诺模型估算等同于实时价格、余票、房态或营业信息。
- 不引入重型路线优化器、向量数据库或旅行供应链聚合平台。

## 5. 核心决策

### 5.1 使用“通用 MCP + 必要只读适配层 + 旅行 Skill”

默认能力组合：

```text
高德地图 MCP
  -> 地理编码 / POI / 路线 / 距离 / 可用天气

Tavily MCP
  -> 通用搜索 / 网页正文提取 / 站点地图 / 抓取

12306 社区 MCP
  -> 查询型车次 / 余票 / 中转快照

Open-Meteo read-only MCP adapter
  -> 逐日/逐小时天气 / 16 天窗口 / 历史气候

xhs-readonly-mcp
  -> 登录状态 / 搜索 / 详情，严格不暴露写操作

travel-planner Skill
  -> 需求确认 / 查询计划 / 证据归一化 / 规划 / 质量门控
```

不增加 `generate_travel_plan` 之类的远端 MCP Tool，因为那会把智策 Agent 的编排价值隐藏在外部 Server 中。

### 5.2 小红书直接搜索通过只读适配层进入完整交付

调研中的小红书 MCP 具有以下事实边界：

- 第一次必须人工登录并保存 Cookie；
- 同一账号不能同时在多个网页端登录，否则现有会话可能失效；
- Tool Catalog 同时包含搜索、详情、发布、评论、点赞和收藏；
- 社区项目自身报告可能出现 Cookie 过期、网页登录风控等情况。

而当前智策 Agent MCP 设计会把 Server 发现的全部有效 Tool 放入 Catalog，不提供逐 Tool allowlist，因此不得把该 Server 原样配置进 workspace。

本方案直接交付 `integrations/xhs_readonly_mcp/`：保留上游 Apache-2.0 版权与许可证，只暴露登录状态、搜索和详情三个只读 Tool。适配器使用独立服务账号、专用 Cookie volume、全局限流和人工重新登录；发布、评论、点赞、收藏和删除工具在 Server Catalog 层不存在，而不是仅靠 Prompt 约束模型不调用。

Tavily 同时搜索公开索引中的小红书链接和其它平台攻略，作为独立证据源与运行时降级链。Cookie失效时必须标记小红书源 unavailable 并继续通用网页调研，不伪装为“小红书实时搜索成功”。完整验收同时包含一次真实只读登录、搜索、详情和Cookie失效降级。

### 5.3 社交内容只提供经验，不作为实时事实权威源

来源分级：

```text
official_api      官方或明确授权 API
live_query        查询型外部服务的当前快照
official_page     官方网页
web_article       普通网页攻略
social_post       社交平台个体内容
model_estimate    模型或规则估算
```

使用规则：

- 票价、余票、天气、路线和营业状态优先使用 `official_api` 或 `live_query`；
- `social_post` 用于拥挤、体验、避坑、小众路线和消费感受；
- 单一社交来源只能标注为个体体验；
- 两个以上独立来源出现相同问题时才能提升为高频提示；
- 广告倾向、过旧内容、无原链接和无日期内容降低可信度；
- 所有结论保留原始来源链接和查询时间。

### 5.4 结构化结果由内部领域 Tool 收口

新增内部 Tool：

```text
finalize_travel_plan(plan: TravelPlanV1)
  -> schema validation
  -> actor ownership
  -> per-user TravelPlanStore
  -> plan_id + view_url
```

它不是 MCP Tool，不访问外部平台，只负责本地业务协议、持久化和页面投影。Agent 的最终自然语言回答引用已经保存的 `plan_id`，前端不从 Markdown 中反向解析业务 JSON。

### 5.5 同时交付快速与深度两种模式

- `quick`：主 Agent 串行或小批量查询，限制搜索结果数量和 Tool 预算；
- `deep`：通过现有 `delegate_tasks` 启动最多三个 child，分别研究交通天气、住宿景点、攻略避坑，父 Agent 在同一 Turn 内 fan-in。

child 深度仍为 1，使用独立 Session 和现有能力交集。任一 child 失败只丢失对应调研方向，不抹掉其它结果。失败方向必须标记缺失，不允许用无来源模型知识伪造实时事实。

## 6. 总体架构

```text
Web Travel Page / normal chat
          |
          v
WebRuntime -> AgentLoop -> ContextBuilder
                         -> load_skills(travel-planner)
                         -> discover_tools
                         -> MCP ToolProvider
                              +-- AMap MCP
                              +-- Tavily MCP
                              +-- 12306 query MCP
                              +-- Open-Meteo read-only MCP
                              +-- xhs-readonly MCP
                         -> quick direct research / deep delegate_tasks(depth=1)
                         -> run_skill(travel-planner optimizer)
                         -> finalize_travel_plan
                                      |
                                      v
                           actor-scoped TravelPlanStore
                                      |
                                      v
                         Travel REST API + Vue presentation
```

依赖方向：

```text
web/app -> travel application service -> agent core/protocols
travel Tool -> protocols/message/base types
travel Skill script -> no agent imports
external MCP -> protocol boundary only
```

## 7. 领域数据协议

### 7.1 `TravelRequestV1`

```text
schema_version
origin
destinations[]
start_date / end_date
date_flexibility
duration_days
travellers[{type, count}]
budget_total_cny
transport_preferences[]
stay_preferences[]
interest_tags[]
pace
hard_constraints[]
soft_preferences[]
planning_mode
```

日期、目的地、人数或总天数缺失时必须补问。预算缺失时可以让用户选择“经济 / 均衡 / 舒适”，不能静默假设精确金额。

### 7.2 `EvidenceItemV1`

```text
evidence_id
source_type
provider
title
source_url
published_at
retrieved_at
data_as_of
excerpt
facts[]
confidence
freshness
content_hash
```

`excerpt` 只保存支持结论所需的短片段。`content_hash` 用于同文转载和重复搜索结果去重，不保存完整受版权保护内容。

### 7.3 `TravelPlanV1`

```text
schema_version
plan_id
owner_user_id
request
assumptions[]
freshness_summary
transport_options[]
stay_recommendations[]
days[]
budget{lower, expected, upper, items[]}
weather_summary[]
fallbacks[]
avoidance_tips[]
evidence[]
unknowns[]
generated_at
```

每日计划至少包含：

```text
date
city_or_area
activities[{start, end, place, reason, evidence_ids[]}]
route_segments[{mode, from, to, duration, distance, source}]
meal_suggestions[]
daily_budget
weather_adjustment
fallback_plan
```

### 7.4 时效性标签

所有动态数据必须归一为：

```text
live       当前查询结果
snapshot   某次网页或社区查询快照
historical 历史数据或历史气候
estimate   规则或模型估算
unknown    没有可靠证据
```

UI 不得把 `estimate` 和 `live` 使用相同样式。

## 8. `travel-planner` Skill 设计

目录：

```text
skill_repo/skills/travel-planner/
  SKILL.md
  scripts/optimize.py
```

frontmatter 使用现有正式 Skill Runtime：

```yaml
name: travel-planner
description: 基于真实外部数据、攻略证据和可行性校验生成个性化旅行计划
runtime:
  type: python
  entrypoint: scripts/optimize.py
  protocol: ndjson-v1
  timeout_seconds: 60
  params_schema: {}
```

正式实现时 `params_schema` 必须展开为严格 object schema，不能保留空对象。

### 8.1 Skill 阶段

```text
Stage 1 需求抽取与必要补问
Stage 2 数据源计划和 Tool 发现
Stage 3 基础数据与攻略调研
Stage 4 EvidenceItemV1 归一化、去重和冲突标记
Stage 5 run_skill 执行预算、时间和路线求解
Stage 6 质量门控
Stage 7 finalize_travel_plan
Stage 8 用户可读总结与未知项说明
```

### 8.2 查询规则

- 搜索词包含目的地、时间、人群、预算和明确主题；
- 攻略至少覆盖交通、住宿区域、景点、美食、避坑和雨天替代；
- 优先最近一年内容，同时保留目的地官方页面；
- 交通查询必须保存出发日期和查询时间；
- 天气超过外部预报窗口时改查历史气候，只能标记 `historical`；
- 12306 尚未开售、接口失败或无结果时显示 `not_on_sale / unavailable / no_result`，不能推断余票；
- 酒店 POI 结果只证明位置和类别，不证明实时房价与房态；
- 搜索摘要不能代替原文，关键避坑必须读取原页面或详情。

### 8.3 可执行脚本职责

`scripts/optimize.py` 只处理已经收集和归一化的 JSON：

- 硬约束过滤；
- 每日可用时间计算；
- 路线折返和跨城冲突检查；
- 预算 lower / expected / upper 汇总；
- 景点开放时间与路线时间冲突；
- 每日强度评分；
- 候选方案评分和拒绝原因。

脚本不访问网络、不读取 Session、不读取 Memory、不 import `agent.*`。最后一行按现有 NDJSON 协议输出 `status`、`code`、`data`、`message`、`error_stack`。

使用有界候选过滤、加权评分和有限回溯，不引入 OR-Tools。

## 9. Travel 应用模块

建议新增：

```text
agent/applications/travel/
  __init__.py
  schemas.py
  store.py
  service.py
  tools.py
  presentation.py

agent/app/api/travel_routes.py
```

### 9.1 Store

计划属于用户私有数据。`TravelPlanStore` 从当前 `UserContext` 派生，默认位于：

```text
${actor_context_root}/travel/plans.sqlite3
```

表：

```text
travel_plans
  id
  owner_user_id
  source_session_id
  source_turn_id
  schema_version
  title
  destination_summary
  plan_json
  created_at
  updated_at
```

访问必须同时满足当前 actor 与 `owner_user_id`。管理员诊断默认只能查看计划元数据和错误码，不能读取计划正文。

### 9.2 API

```text
GET    /api/travel/plans
GET    /api/travel/plans/{plan_id}
DELETE /api/travel/plans/{plan_id}
```

计划生成继续走现有聊天 WebSocket 和同一个 AgentLoop。旅行表单将结构化字段转换成用户可见的自然语言请求，并在正常 Session 中发送；`finalize_travel_plan` 成功后发出 `travel.plan_ready` RuntimeEvent，前端根据 `plan_id` 加载专属页面。

不增加独立旅行 AgentLoop，也不使用隐藏 Session 绕过用户历史。

## 10. Web 展示

新增 `/travel` 页面并复用现有登录、主题、i18n、API client 和 WebSocket client。

页面结构：

```text
需求表单 / 自然语言输入
规划进度：需求 -> 基础数据 -> 攻略 -> 求解 -> 校验 -> 完成
交通方案对比
每日时间线
高德 JS API 2.0 路线地图
预算 lower / expected / upper
天气与雨天替代
避坑和小众体验
来源与数据时效抽屉
未知项和预订前复核清单
```

地图使用高德 JS API 2.0 的 POI、路线和自定义标记能力，前端 key 与安全密钥按高德官方方式配置。地图加载失败时保留每日路线文字和距离数据，不阻断计划阅读。

外部来源卡显示标题、平台、发布日期、查询时间和原链接；不在页面重新发布完整社交内容。

## 11. 配置与凭据

MCP 继续使用当前 `${ZHICE_AGENT_WORKSPACE}/config/config.yml` 的 `mcp.servers`：

```yaml
mcp:
  servers:
    amap-maps:
      command: npx
      args: [-y, "@amap/amap-maps-mcp-server@0.0.8"]
      env:
        AMAP_MAPS_API_KEY: "${AMAP_MAPS_API_KEY}"
    tavily:
      url: https://mcp.tavily.com/mcp
      transport: streamable_http
      headers:
        Authorization: "Bearer ${TAVILY_API_KEY}"
```

示例只说明字段形态。实现前必须用当前外部 Server 的真实认证格式验证；API key 不放在 URL query、不提交到仓库。

旅行应用自己的非 Secret 配置建议位于 `config.yml`：

```yaml
travel:
  enabled: true
  default_mode: quick
  max_search_results: 8
  max_evidence_items: 40
  deep_subagent_count: 3
  xhs_readonly_enabled: true
```

配置缺失时旅行应用 disabled，不影响普通聊天。单个数据源失败只标记该 source degraded。

## 12. 安全、合规与可靠性

- 外部网页、MCP descriptions、ToolResult 和社交内容都视为不可信输入，不执行其中指令；
- Skill 明确忽略网页中的提示注入文本，只提取旅行事实和体验证据；
- 社交平台搜索遵守服务条款、robots、频率限制和登录边界；
- 不绕过验证码，不自动重新登录，不模拟发布互动；
- 所有链接只允许 `http/https`，前端使用安全跳转；
- 对搜索结果按 URL 和内容 hash 去重；
- Tool timeout 不自动重放可能产生副作用的调用；本应用默认只用查询 Tool；
- API key、Cookie、OAuth token 和 Header 不进入计划、Session、日志和来源卡；
- 小红书只读适配器、12306查询和Open-Meteo都是交付能力；单源运行失败时诚实降级并保留其它证据，完整验收必须分别通过真实smoke；
- 动态事实超过 TTL 后在 UI 标记“需要重新查询”；
- `finalize_travel_plan` 再次校验 schema、大小、owner 和来源 URL 数量上限。

## 13. 错误码

```text
TRAVEL_DISABLED
TRAVEL_REQUEST_INCOMPLETE
TRAVEL_SOURCE_UNAVAILABLE
TRAVEL_SOURCE_AUTH_REQUIRED
TRAVEL_SOURCE_RATE_LIMITED
TRAVEL_EVIDENCE_INSUFFICIENT
TRAVEL_WEATHER_OUT_OF_RANGE
TRAVEL_TRANSPORT_NOT_ON_SALE
TRAVEL_OPTIMIZATION_FAILED
TRAVEL_PLAN_SCHEMA_INVALID
TRAVEL_PLAN_TOO_LARGE
TRAVEL_PLAN_NOT_FOUND
TRAVEL_PLAN_ACCESS_DENIED
```

来源失败错误包含安全的 `source_id`、stage 和 retryable，不包含原始响应体或凭据。

## 14. 变更文件规划

```text
skill_repo/skills/travel-planner/SKILL.md
skill_repo/skills/travel-planner/scripts/optimize.py
agent/applications/travel/*
agent/app/api/travel_routes.py
agent/app/runtime.py
prompts/travel_planning.md                  # 长领域规则不内嵌Python
integrations/open_meteo_mcp/*
integrations/xhs_readonly_mcp/*
integrations/xhs_readonly_mcp/LICENSES/*
config/config.example.yml
web/frontend/package.json
web/frontend/src/pages/TravelPlannerPage.vue
web/frontend/src/components/travel/*
web/frontend/src/stores/travel.ts
web/frontend/src/api/types.ts
web/frontend/src/router/index.ts
tests/unit_test/travel/test_case.md
tests/unit_test/travel/*
tests/integration_test/travel/*
docs_design/README.md
docs_design/zhice-agent-overall-design.md
README.md
```

## 15. 测试方案

### 15.1 单元测试

- `TravelRequestV1` 必填、日期、预算和人数边界；
- `EvidenceItemV1` URL、时间、类型、hash 和截断；
- 同源、转载和重复内容去重；
- 实时、快照、历史、估算和未知标签不混淆；
- optimizer 正常、超预算、跨城冲突、开放时间冲突、路线折返；
- `finalize_travel_plan` schema、大小、owner 和非法来源拒绝；
- per-user Store 隔离、删除和 Session/Turn 关联；
- Skill NDJSON 正常、异常、取消和输出上限。

### 15.2 Fake MCP 集成测试

- Fake AMap、Tavily、12306 返回稳定 fixture；
- 单 Server 失败不阻断其它来源；
- 远端描述和正文中的提示注入不改变规划规则；
- 小红书 read-only Catalog 不出现发布、评论、点赞和收藏；
- Tool output 超限、timeout、非法 schema 和 credential 缺失正确降级。

### 15.3 Agent 与 Web 测试

- Fake LLM 跑通 quick 和 deep 两条调用序列；
- deep 模式最多三个 child，部分失败仍能输出带未知项的计划；
- RuntimeEvent 真实反映阶段，不展示虚假进度；
- `/travel` 的表单、计划卡、来源抽屉、地图降级和移动端布局；
- 不同用户不能查看或删除彼此计划；
- E2E 走真实 Web 入口、真实 AgentLoop、Fake MCP 和正式 Skill source。

### 15.4 真实外部 smoke

由显式环境变量开启，不进入默认 pytest：

- 高德 API key 和真实 POI/路线查询；
- Tavily 搜索与正文提取；
- 12306 查询型 MCP；
- 小红书只读登录、搜索、详情和 Cookie 失效降级；
- 高德 JS 地图实际加载。

提交前运行：

```bash
python -m ruff check .
python -m pytest
cd web/frontend
npm run lint
npm run typecheck
npm run test
npm run build
```

## 16. 实施依赖顺序（不分期发布）

以下顺序只表达代码依赖；第1～9项必须一次全部完成，中间状态不发布、不验收。

1. 固化 `TravelRequestV1`、`EvidenceItemV1`、`TravelPlanV1` 和 fake fixtures。
2. 编写 `travel-planner` SKILL.md 与纯计算 optimizer。
3. 配置并 smoke 高德、Tavily和12306查询型 MCP，实现Open-Meteo只读适配。
4. 实现 `xhs-readonly-mcp`、许可证保留、Cookie隔离、限流和只读Catalog测试。
5. 同时完成 quick 模式和最多三个child的deep模式、部分失败合并。
6. 实现 `finalize_travel_plan`、per-user Store、API 和 RuntimeEvent。
7. 实现旅行结果页、来源抽屉、地图和无地图降级。
8. 运行全量测试、全部真实外部smoke和浏览器演示验收。
9. 全部验收关闭后创建旅行特色应用活文档，并同步当前实现基线。

## 17. 验收标准

1. 所有正常登录用户都能生成和查看本人的旅行计划。
2. 示例“国庆、两个大学生、重庆到大理、五天”能形成完整 `TravelPlanV1`。
3. 输出包含交通方案、每日时间线、住宿区域、预算区间、天气、替代方案、避坑和来源。
4. 每个动态事实能看到来源类型、查询时间和 live/snapshot/historical/estimate/unknown 标签。
5. 酒店 POI 不被误称为实时房态，未开售车票不被误称为无票，历史天气不被误称为预报。
6. 通用网页搜索与小红书只读搜索均完成真实验收；任一来源失败时诚实降级且不伪造成功。
7. 小红书直接连接启用时只暴露只读搜索和详情能力。
8. quick 和 deep 两种模式均可用；quick 不启动 child，deep 最多三个 child，depth 仍为 1。
9. optimizer 能拒绝明显超时、折返、跨城和超预算方案。
10. 计划持久化按用户隔离，管理员默认只能查看诊断元数据。
11. 专属页面具备进度、日程、地图、预算、天气、来源和未知项展示，地图失败不影响主要内容。
12. 外部网页提示注入、超大内容、失效凭据和单 Server 故障均不会破坏 Agent 主链。

## 18. 调研来源

以下链接均于 2026-08-10 实际访问：

- MCP Tool 规范：<https://modelcontextprotocol.io/specification/2026-07-28/server/tools>
- 高德地图 MCP NPM：<https://www.npmjs.com/package/@amap/amap-maps-mcp-server>
- 高德地图 JS API 2.0：<https://lbs.amap.com/api/javascript-api-v2/summary>
- Tavily MCP：<https://github.com/tavily-ai/tavily-mcp>
- Microsoft Playwright MCP：<https://github.com/microsoft/playwright-mcp>
- 小红书 MCP 社区实现：<https://github.com/xpzouying/xiaohongshu-mcp>
- 12306 查询型 MCP：<https://github.com/Joooook/12306-mcp>
- Open-Meteo Forecast API：<https://open-meteo.com/en/docs>
- Travel Guidebook Skill：<https://github.com/geekjourneyx/travel-guidebook>
- Amadeus API Portal：<https://developers.amadeus.com/>
