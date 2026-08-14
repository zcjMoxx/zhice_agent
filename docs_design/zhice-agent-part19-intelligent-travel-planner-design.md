# ZhiCe-Agent Part 19：智能旅行规划特色应用

> 旅行工作台现以 actor-owned `channel=travel` Session 持久化生成前需求草稿，并把需求收集、生成中、候选待选、失败和已完成计划统一投影到左栏。结构化需求写入 Session sidecar，正式生成复用同一 Session；旅行 Session 仍不进入普通聊天列表。详见 `2026-08-13-travel-draft-persistence-and-work-item-list-design.md`。

> 说明：确认前主线已从“意图分类器 + 前端固定话术”升级为真正的旅行接待 Agent。新旅行 Session 默认 `travel_phase=intake`，复用当前 AgentLoop、当前 session 默认 LLM 和独立 `travel_intake` Prompt，但只开放草稿更新与主聊天交接两个受限 Tool；用户确认后才切换 `travel_phase=planning` 并开放现有正式规划能力。旧提取接口和结构化输出链仅作兼容，不再是旅行页面正常入口。详见 `2026-08-14-travel-intake-agent-and-planning-phase-design.md`。

> 文档类型：当前活文档
>
> 当前状态：代码、本地单元测试、Vue 测试和 Fake MCP Web 全链已进入当前基线；Open-Meteo、高德真实 POI/路线、Tavily search/extract、12306 查询和小红书登录/搜索/详情已完成本地显式 smoke。高德 JS 浏览器 smoke 仍需要独立 Web JS Key 与安全密钥。服务器 sidecar 与固定依赖部署方案见 `2026-08-12-travel-external-services-server-deployment-design.md`。
>
> 日期设计记录：`docs_design/2026-08-10-intelligent-travel-planner-application-design.md`；搜索展示、地图信息与候选强制确认修订见 `docs_design/2026-08-13-travel-result-presentation-and-review-enforcement-design.md`；旅行助手范围与携问返回主聊天见 `docs_design/2026-08-13-travel-assistant-scope-and-chat-handoff-design.md`。
>
> 前置基线：Part 5 Skill、Part 11 MCP、Part 13 Subagent、Part 16 Vue Web、Part 18 Skill Runtime

## 1. 当前定位

Part 19 是第一个垂直特色应用。它没有复制 AgentLoop，也没有把旅行判断写入 AgentLoop，而是在现有通用运行时上组合：

```text
Travel Vue page / ordinary chat
  -> existing WebSocket + application-scoped Travel Session
  -> travel_phase=intake
       -> AgentLoop + travel_intake Prompt + current session LLM
       -> update_travel_draft / offer_main_chat_handoff
  -> user confirms reviewed draft
  -> travel_phase=planning
  -> same AgentLoop
       -> travel_planning Prompt
       -> load_skills(zhice-official/travel-planner)
       -> read-only travel MCP Tools
       -> quick direct research / deep delegate_tasks(depth=1, max=3)
       -> run_skill(travel-planner)
       -> finalize_travel_plan
  -> actor-scoped TravelPlanStore
  -> /api/travel/plans
  -> /travel presentation
```

所有正常登录用户都能使用本人旅行计划。管理员没有跨用户正文读取入口；计划 API 只以当前 actor 的 `user_id` 和 actor context root 为真值。

## 2. 稳定边界

- AgentLoop 仍只负责加载上下文、调用 LLM、调度 Tool、回填结果和保存 Session。
- `finalize_travel_plan` 是内部领域 Tool，不访问外部服务；它只校验、重写 owner/id、持久化并发出事件。
- 外部事实继续经过 MCP ToolProvider；Skill 脚本不 import `agent.*`，不访问网络、Session、Memory 或 Secret。
- 旅行页面通过现有 WebSocket 创建 `channel=travel` 的应用 Session，仍由 SessionStore 保存完整上下文并进入正常 AgentLoop；普通聊天 Session 列表不投影该应用 Session，因此内部 Tool/assistant JSON 不在聊天界面展示。页面不从 Markdown 反向解析计划 JSON。
- 不购票、不预订、不支付、不承诺实时房态、成交价或第三方 SLA。
- 外部查询失败后不以模型常识伪造实时事实；已验证部分保留，缺失方向进入 `unknowns`。
- 旅行 Turn 预激活当前 Catalog 中地图、天气、铁路、网页与社区来源各一个首选只读 Tool，并维护不含参数和正文的进程级来源调用账本。finalizer 只有在所有已配置类别均实际尝试、至少一个外部来源成功且 TravelPlanV1 含非 `model_estimate` evidence 时才允许保存；“已配置但全部未调用”的计划不能再以纯估算正常完成。

## 3. 当前实现模块

### 3.1 领域应用层

```text
agent/applications/travel/
  config.py
  schemas.py
  store.py
  service.py
  tools.py
  presentation.py
```

- `TravelRequestV1` 校验出发地、目的地、完整日期、总天数、人数、预算/显式预算档位路径、偏好、节奏和 quick/deep。
- `EvidenceItemV1` 固定来源类型、HTTP(S) 原链接、发布时间、查询时间、数据时间、短摘录、facts、confidence、freshness 和 SHA-256。
- URL 和 content hash 去重会重映射活动/路线 evidence id，不保存重复正文。
- `TravelPlanV1` 校验每日日期、活动不重叠、路线、跨区域交通、预算 lower/expected/upper、证据引用、来源数量、计划大小和 credential-like 字段。
- 活动可保存坐标，路线可保存有界 path，用于高德地图；没有坐标或地图失败时正文路线不受影响。
- 地图按日期与活动顺序展示编号地点；真实 path 使用导航实线，缺失 path 时仅显示不冒充道路导航的顺序参考线，并在地图下方固定展示每日地点、交通方式、距离和时长。高德底图异常会给出 Key、安全码、域名或网络复核提示。

### 3.2 Store 与 API

每个 actor 的数据库位于：

```text
${actor_context_root}/travel/plans.sqlite3
```

表 `travel_plans` 保存 plan id、owner、来源 Session/Turn、schema、标题、目的地摘要、完整 JSON 和创建/更新时间。Store 所有读取、列表和删除都同时匹配可信 owner；另一个用户即使知道 plan id 也得到 not found。

API：

```text
GET    /api/travel/plans
GET    /api/travel/plans/{plan_id}
DELETE /api/travel/plans/{plan_id}
```

生成继续走现有 `/ws` 或 `/api/chat` 的 AgentLoop 链，不增加独立生成 API。

### 3.3 正式旅行 Skill

```text
skill_repo/skills/travel-planner/
  SKILL.md
  scripts/optimize.py
```

frontmatter 提供完整 `params_schema`，运行时为 Python + `ndjson-v1` + 60 秒。optimizer 使用有界候选过滤和加权评分，检查：

- 活动重叠、每日可用时间和日期完整性；
- 开放时间窗口；
- 跨城/跨区域交通缺失；
- A→B→A 明显折返；
- 路线距离、路线分钟和每日强度；
- 预算区间与硬预算；
- evidence coverage warning；
- 多候选拒绝原因和最佳可行候选。

全部候选失败时返回 `TRAVEL_OPTIMIZATION_FAILED`，不伪造通过。SkillExecutor 继续负责取消、timeout、UTF-8 NDJSON、stdout/stderr/行数上限和进程树回收。

### 3.4 MCP 数据源

当前仓库直接交付：

```text
integrations/open_meteo_mcp/
integrations/xhs_readonly_mcp/
```

Open-Meteo 适配器只提供地理编码、16 天窗口内预报和历史天气。超出预报窗口返回 `TRAVEL_WEATHER_OUT_OF_RANGE`，历史结果固定标为 `historical`。

小红书适配器自身 Catalog 只有：

```text
check_login_status
search_notes
get_note_detail
```

发布、评论、点赞、收藏和删除在 Server Catalog 层不存在。适配器要求受控 upstream URL、可选独立 Authorization、隔离 Cookie volume、进程级限流、结果上限和登录失效降级；所有正文标记为 `untrusted_content`。仓库保留上游项目链接、Apache-2.0 许可证快照和只读改造说明，但不 vendoring 上游可执行文件。

Owner 登录管理位于“管理后台 → MCP 与 Skills → xhs-readonly”。本地扫码由 Cookie 内容稳定更新驱动：登录助手写入后先重载 Gateway-owned sidecar，再通过只读登录 Tool 复检。管理 API 兼容 MCP structured content 与兼容 text content 形成的连续 JSON 文档，仅缓存 authenticated/auth_required/unavailable 等安全状态；Cookie 值、路径、PID、二维码和原始 Tool 输出均不进入前端或审计。

高德、Tavily 和 12306 继续作为外部 MCP Server 接入。模板给出高德/Tavily 字段形态和固定高德版本，但默认不写入真实 key；12306 必须由 operator 在运行态配置已审查的 query-only 命令。真实 Tool Catalog 由显式 smoke 重新确认。

## 4. quick 与 deep

- `quick`：Prompt 明确禁止 `delegate_tasks`；主 Agent 用有界 Tool 预算完成调研、optimizer 和 finalizer。
- `deep`：当前模板提供 `travel-research` Profile，只允许五类只读 MCP server pattern，shared-readonly，最多 3 个 child、depth 固定 1。三个方向是交通天气、住宿景点、攻略避坑。
- child 仍与父 actor 可见 Tool 取交集，不能获得 inline credential、任意 server、`exec`、finalizer 或更高权限。
- 一个 child 失败时父 Agent保留其它结果，把失败方向写入 unknowns，然后继续质量门控。

## 5. Prompt 与不可信输入

长旅行规则位于 `prompts/travel_planning.md`。它规定必要补问、Skill/MCP/finalizer 顺序、quick/deep、来源等级、未开售/无结果/不可用区别、酒店 POI 边界、天气窗口、质量门控和最终摘要。

网页、搜索摘要、MCP description、ToolResult 和社交正文均被视为不可信数据。正文中的角色切换、调用 Tool、发布互动、索取 Secret 或忽略既有规则指令不得执行。计划 schema 同时拒绝 `api_key`、Authorization、Cookie、credential、password、secret、token 字段，以及来源 URL 中的 credential query 参数。

## 6. Vue 旅行页面

旅行页左侧仅列已保存计划，默认进入新建状态，不自动打开最近结果；只有 URL 指定或用户点击后才展示历史计划。自然语言输入卡与下方内容使用相同全宽，位于主内容顶部；空白新建态不再显示独立的大幅宣传卡，“生成可执行计划 · 来源与时效可核验”降级为输入卡 footer 内的辅助说明。过程与完整计划在其下方顺序展示并随主区滚动。过程面板生成时展开、TravelPlanV1 就绪后自动收起，并可由用户手动再次展开。Session 隔离的设计背景见 `2026-08-12-travel-session-isolation-and-bottom-composer-design.md`。

左侧“我的计划”可折叠为窄栏；输入卡的“已自动补充”摘要打开右侧旅行条件 inspector，结构化字段不再向下撑长主内容。两侧独立开关与响应式行为见 `2026-08-12-travel-dual-collapsible-rails-design.md`。

旅行生成采用真正的两阶段 Agent 交互。第一次自然语言输入即创建 actor-owned `channel=travel` Session，并以 `travel_phase=intake` 复用普通 AgentLoop 和当前 session 默认 LLM。接待阶段注入 `prompts/travel_intake.md`，只开放 `update_travel_draft` 与 `offer_main_chat_handoff`；不开放 MCP、exec、Skill 执行、memory Tool 或 subagent。Agent 可自然回答问候、身份、能力与旅行知识，渐进收集或修正条件；北京时间参考日期由后端随 Turn 注入，相对日期不确定时继续追问。只有用户补齐核心条件并明确确认后，服务端才原子切到 `travel_phase=planning`。详细口径见 `2026-08-14-travel-intake-agent-and-planning-phase-design.md`。

同行人群、旅行节奏和规划模式不是阻塞项。用户未明确提供时，确认栏保持为空或可选；提交规划时分别使用中性旅行者标签、`balanced` 和 `quick` 作为透明的系统执行策略，不在界面上标红为“用户漏填”，也不得写成模型从原句中提取出的事实。春节、中秋等非固定公历日期没有可靠年份换算时仍保持为空并请求确认，不做猜测。

当前需求确认保留自然对话与手工表格双入口。自然语言按钮通过 WebSocket 启动接待 Agent Turn，Agent 每轮优先追问一到两个最有价值的条件，不再由前端一次机械列出全部字段；ToolResult 返回的完整草稿、missing fields 与 ready 是服务端真相。右上角“补充数据”只打开手工表格；两条路径最终都把同一份严格草稿提交 actor-owned 确认接口。确认接口校验出发地、目的地、开始日期、结束日期、人数和日期范围，成功后前端只发送简短确认消息，正式规划 Prompt 与已确认草稿由后端注入，不在浏览器拼接内部指令。

旅行执行具有应用层终态守卫：通用 AgentLoop 的普通文本 Turn 结束不代表旅行完成；Gateway 对 `channel=travel` 的 Session 在未收到 `travel.plan_ready` 或 `travel.clarification_required` 时，使用运行时 Prompt 在同一 Session 内最多续跑两次。只有完整计划保存、结构化用户澄清、用户停止或稳定错误可以结束。续跑耗尽返回 `TRAVEL_PLAN_NOT_FINALIZED`，不得在页面上显示为绿色完成。用户信息确实不足时，Agent 必须调用 `request_travel_clarification` 一次列出全部问题；内部执行问题不得借此推给用户。详细口径见 `2026-08-13-travel-terminal-guard-and-clarification-design.md`。

`/travel` 复用登录、Pinia、Vue Router、WebSocket client、主题 token 和 QuickPreferences。页面包含：

- 主区域以聊天式自然语言输入触发旅行接待 Agent；Agent 自然讨论旅行并通过受限 Tool 增量维护可展开修改的结构化字段，不再调用独立分类器作为正常入口；
- 该输入区身份统一为“智策旅行助手”。问候、身份、能力、旅行讨论和条件修正由当前默认 LLM 在旅行 Prompt 下直接回复，不使用前端固定长话术；无关问题不回答实质内容，通过结构化事件携带原问题返回主聊天草稿，不自动发送，也不扩展成第二个通用聊天 Agent；
- 需求、基础数据、攻略、求解、校验、完成的真实 RuntimeEvent 阶段与有界事件时间线；
- 过程时间线使用用户视角展示：隐藏 Skill 加载与 Tool 发现等内部编排；外部查询明确显示高德地图、Open-Meteo、铁路 12306、Tavily、小红书只读等来源、查询目标、返回数量和最多五个有界候选摘要；optimizer 单独展示比较数量与结构化采用方案，搜索候选不冒充最终采用结果；
- MCP 搜索展示兼容 structuredContent 与 text 的连续多段 JSON 及常见 `data/text/content` 包裹；真实空结果、来源失败和格式不可展示分别表述，已知来源不会回退显示 `mcp__...` 内部名称；
- 旅行 finalizer 强制要求 actor-owned 候选审核已由用户选择；每次规划必须先展示至少两个有取舍差异的可行方案卡片，不能只靠 Prompt 自觉或直接采用 optimizer 推荐项；
- 左栏只显示本人最近生成计划和删除，不固定占用首屏展示长表单；
- 交通、住宿、每日时间线、路线距离/时长、预算、天气、雨天替代、避坑、未知项和复核清单；
- 来源抽屉：平台、来源等级、发布时间、查询时间、数据时点、confidence、原链接和 freshness；
- freshness TTL：过期 live/snapshot/estimate 或 unknown 显示“需要重新查询”，historical 保持历史参考样式；
- 高德 JS API 2.0 marker/polyline；缺 key、无坐标或脚本失败时显示文字路线模式。

旅行 Store 在登录后的应用层常驻订阅，页面卸载不取消正在运行的旅行任务监听。生成中返回聊天再进入 `/travel` 时保留当前 Session、过程和结果，不因缺少 `plan` 查询参数而清空。浏览器整页刷新只在 `sessionStorage` 按用户保存非敏感 Session id，并必须经后端当前 actor 所有权和 `channel=travel` 校验后，才能恢复 running/pending 或打开该 Session 已生成的计划；不在浏览器保存完整计划、消息、来源正文或 Secret。详细设计见 `2026-08-12-travel-generation-continuity-design.md`。

历史计划同时使用其 `source_session_id` 从 actor-scoped SessionStore 恢复接待对话，并直接回填页面顶部原有 `TravelPlanForm` 问答窗口，不另设“规划对话”卡。接待 Agent 的 user/assistant 消息由普通 AgentLoop 保存；两个接待 Tool 及运行时兜底共同把最近 Turn id 写入 `travel_intake_turn_ids`，travel draft API 只投影这些 Turn 和旧版显式 conversation 消息。Tool、规划执行回复、空 tool-call 占位、自动续跑指令和纯 JSON 不进入问答窗口。计划与关联 travel Session 同生命周期，删除计划会在重新验证 actor 所有权与 `channel=travel` 后同步删除 Session 和需求问答。消息正文不写入浏览器长期存储，travel Session 仍不进入普通聊天侧栏。详细设计见 `2026-08-13-travel-conversation-history-design.md` 与 `2026-08-14-travel-intake-agent-and-planning-phase-design.md`。

后台完成且用户当前不在旅行页时，聊天侧栏的旅行入口显示一个按用户隔离的数字未读徽标 `1`；进入旅行页后清除。该状态只表示“有一份未查看完成结果”，不表示历史计划总数，也不持久化计划正文。MCP Server 的连接、Catalog、调用和自动重连安全统计在管理后台 Skills 页只读展示，具体边界见 `2026-08-12-travel-unread-and-mcp-admin-monitor-design.md`。

前端 key 和安全密钥只从构建环境 `VITE_AMAP_JS_API_KEY`、`VITE_AMAP_JS_SECURITY_CODE` 读取。来源跳转仅接受浏览器解析后的 HTTP(S)，使用 `noopener noreferrer`。

## 7. 配置

运行态 `${ZHICE_AGENT_WORKSPACE}/config/config.yml`：

```yaml
travel:
  enabled: true
  default_mode: quick
  max_search_results: 8
  max_evidence_items: 40
  deep_subagent_count: 3
  xhs_readonly_enabled: true
  max_plan_bytes: 524288
```

缺少 `travel` 分区时能力 disabled，不影响普通聊天；显式非法配置只把 travel 标为 unavailable。Open-Meteo 在模板中作为无 Secret 的 bundled stdio MCP 启用，其它真实源在运行态按注释配置。

## 8. RuntimeEvent 与错误

`finalize_travel_plan` 成功发出：

```text
travel.plan_ready
  status=completed
  metadata.plan_id=<trusted generated id>
```

前端收到后通过 API 读取计划。Tool 外层仍保留通用 `tool.*`；事件不携带计划正文、URL、输入参数或 Secret。

旅行错误码：

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

## 9. 测试基线

默认稳定测试覆盖：

- request/evidence/plan 正常、异常和边界；
- hash/URL 去重、非法 URL、Credential-like 字段和 freshness 混淆；
- optimizer 的预算、时间、开放时间、折返、跨城、强度、取消和输出上限；
- Store owner 隔离、Session/Turn、删除和 finalizer owner 重写；
- Open-Meteo 标签/窗口、小红书只读 Catalog/Cookie guard/提示注入、Fake catalog 单源失败；
- Fake LLM quick/deep Tool 序列；
- Travel API 与 `travel.plan_ready`；
- Vue 表单、地图无 key 降级、来源安全跳转、TTL 和 Store。
- 路由往返时旅行 Store 常驻、运行态不清空、非当前 Session 事件隔离，以及整页刷新后的 running/pending/completed/failed/stopped/idle 恢复。

显式 integration 使用真实 Web `/api/chat` 入口、真实 AgentLoop、正式 Skill source、SkillExecutor、actor-scoped Store 和本地 Fake MCP，不直接 import 内部实现绕过主链。

## 10. 真实外部 smoke 条件

真实 smoke 不进入默认 pytest。分别设置：

```text
ZHICE_TRAVEL_SMOKE_AMAP=1 + AMAP_MAPS_API_KEY
ZHICE_TRAVEL_SMOKE_TAVILY=1 + TAVILY_API_KEY
ZHICE_TRAVEL_SMOKE_12306=1 + ZHICE_12306_MCP_COMMAND + ZHICE_12306_MCP_ARGS_JSON
ZHICE_TRAVEL_SMOKE_OPEN_METEO=1
ZHICE_TRAVEL_SMOKE_XHS=1 + isolated upstream/Cookie/feed id/xsec token
```

小红书需分别用有效 Cookie 和失效 Cookie 验证登录/搜索/详情与 `TRAVEL_SOURCE_AUTH_REQUIRED` 降级。高德地图浏览器 smoke 还需构建时两个 VITE 变量、真实登录 Gateway、含坐标计划、正常加载和故意失败后的文字路线回退。仓库不保存这些值。

## 11. 当前完成定义

代码层已满足：同一 AgentLoop、quick/deep、最多 3 个 depth=1 child、正式 Skill、只读适配器、结构化计划、owner 隔离、API、RuntimeEvent、专属页面、地图降级、来源/时效、提示注入与单源故障边界。真实外部服务可用性、服务条款、账号登录态、上游 Tool schema 和 JS 地图加载属于部署环境验收；只有实际执行后才能报告通过，不由 Fake 测试替代。
