# ZhiCe-Agent Part 19：智能旅行规划特色应用

> 说明：2026-08-17 起，最终校验返回可修复的来源缺口时，运行时按 `2026-08-17-travel-finalizer-source-repair-loop-design.md` 进入单来源修复态；不再重复提交同一份失败计划，也不降低最终计划校验门槛。

> 说明：2026-08-16 的第三轮真实并发 E2E 证明五个旅行 Profile 同时暴露会导致候选前误跑最终化、候选数组复制失败和选择后无 Tool 调用。当前主线已改为服务端按候选状态严格裁剪 Profile 与父层 Tool：候选前三路研究，候选后两路最终化；optimizer 的受信结果直接进入候选评审，选择后的首个 Turn 使用服务端继续消息与所选候选摘要。硬总预算按预期总价门控。详见 `2026-08-16-travel-parallel-research-hotel-and-finalization-progress-design.md`。

> 旅行工作台现以 actor-owned `channel=travel` Session 持久化生成前需求草稿，并把需求收集、生成中、候选待选、失败和已完成计划统一投影到左栏。结构化需求写入 Session sidecar，正式生成复用同一 Session；旅行 Session 仍不进入普通聊天列表。详见 `2026-08-13-travel-draft-persistence-and-work-item-list-design.md`。

> 说明：2026-08-15 已使用独立 viewer 账号走通一次真实 Gateway 全链，覆盖 12306、高德公交、天气、Tavily、小红书、候选选择和最终保存。当前主线已据此收紧交通、住宿和公共交通字段，地图改为按天切换，完成事件显式携带 Session hint 以保留实时进度，并修正候选强度与大型搜索结果识别。详见 `2026-08-15-travel-evidence-rich-plan-presentation-design.md`。酒店浏览器 MCP 已有实现和管理入口，但运行态未启用或未认证时只能展示明确标注的规划估算，不得宣称实时房价。

> 说明：Gateway 重启后的第二轮真实 E2E 再次覆盖完整接待、外部查询、两张候选卡、用户选择、finalizer 修正与完成页。它确认候选锁定、完成态进度保留、按天地图、小红书/Tavily 展示和住宿规划估算已生效，同时证明 Prompt 无法单独阻止重复 MCP 查询、公交别名绕过线路详情校验和住宿价格 evidence 错配。当前代码因此新增按 travel Session 隔离的调用指纹/硬预算守卫，并把交通与住宿严格归一化提升到 TravelPlanV1 领域边界。详见 `2026-08-15-travel-real-e2e-hard-guards-and-evidence-integrity-design.md`。

> 说明：第四轮真实 E2E 发现候选评审丢失 optimizer 完整骨架，导致 finalizer 为严格匹配路线总分钟/总公里连续重试并凑数；同时确认 12306 超预售窗口会触发远端异常、站码辅助查询误展示和步行米值误标公里。当前主线改为内部持久化并继承所选候选的日期/活动/预算身份，允许真实高德路线替换候选估算；新增未开售本地归一、公交线路证据门控、站码进度隐藏和步行单位换算。详见 `2026-08-15-travel-source-guard-followup-and-candidate-convergence-design.md`。

> 说明：确认前主线已从“意图分类器 + 前端固定话术”升级为真正的旅行接待 Agent。新旅行 Session 默认 `travel_phase=intake`，复用当前 AgentLoop、当前 session 默认 LLM 和独立 `travel_intake` Prompt，但只开放草稿更新与主聊天交接两个受限 Tool；用户确认后才切换 `travel_phase=planning` 并开放现有正式规划能力。旧提取接口和结构化输出链仅作兼容，不再是旅行页面正常入口。详见 `2026-08-14-travel-intake-agent-and-planning-phase-design.md`。

> 文档类型：当前活文档
>
> 当前状态：代码、本地单元测试、Vue 测试和 Fake MCP Web 全链已进入当前基线；Open-Meteo、高德真实 POI/路线、Tavily search/extract、12306 查询、小红书登录/搜索/详情和携程指定日期观察价均已完成本地显式 smoke，并已用 viewer QA 账号执行多轮真实旅行规划 E2E。当前主线按阶段互斥注入候选前三路与选择后两路旅行 Profile，Child 的 `fast` 角色没有可用端点时继承主模型；Docker 镜像安装携程浏览器 extra 与 bundled Chromium，登录 profile 随 state volume 持久化。最新配置、Prompt、部署和管理页收敛见 `2026-08-16-runtime-config-prompt-example-convergence-design.md`。
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
       -> quick/deep delegate_tasks(depth=1, candidate max=3)
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
- `finalize_travel_plan` 首次接受完整终稿；失败后服务端在 actor-scoped 旅行 SQLite 中有界保存带 SHA-256 revision 的规范草稿，并一次返回全部 JSON Schema 结构 issue 与当前领域 issue。后续同名 Tool 只接受 revision 和一个受限 JSON Pointer `repairs` 数组，可批量 `set/remove`，不得再提交完整 plan；revision 条件更新冲突 fail closed，跨 Turn/重启继续读取服务端规范草稿，旧 Session 历史重放仅作为升级兼容路径。Tool 合并后及 `TravelApplicationService.finalize()` 持久化边界仍幂等归一交通来源：铁路证据固定标为 12306，其他外部证据固定使用 provider，无外部证据透明标为规划估算；名称只在车次或起终点与方式可推导时补齐，语义重复项合并 evidence。非对象、无语义名称和超过 20 项不再静默删除、造占位或截断，而是进入批量 issue 后由一次 repair 明确修正。
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
- Evidence 只按包含 provider、标题、规范化 URL、摘要与 facts 的完整 content hash 去重并重映射引用；同一 provider 和入口 URL 下语义不同的 POI、酒店、路线证据分别保留，不因共享来源首页而错误合并。
- `TravelPlanV1` 校验每日日期、活动不重叠、路线、跨区域交通、预算 lower/expected/upper、证据引用、来源数量、计划大小和 credential-like 字段。
- 新计划的跨区域交通使用扁平字段保存线路、车次/航班、出发到达时间、时长、席别、单价、总价、来源和 evidence id；引用真实 12306 evidence 时这些字段必须齐全，未取得铁路结果时必须明确标为估算或不可用。
- 住宿分别保存具体酒店、地址、入住退房日期、晚数、指定日期观察价与规划估算价；一般 `evidence_ids` 只证明酒店名称/地址/坐标，`price_source_evidence_ids` 只证明指定日期价格，规划估算不能引用景点或普通 POI 作为价格来源。
- 公交/地铁路线可保存 `transit_legs`、步行接驳距离和票价；`amap_transit` 与“高德公交规划”等用户可读别名都必须保留线路号、上下车站和途经站，缺少真实细节时只能降级为估算并进入 unknowns。
- 活动可保存坐标，路线可保存有界 path，用于高德地图；没有坐标或地图失败时正文路线不受影响。
- 地图默认只绘制第 1 天，可按日期切换；切换时清理旧 marker、path 和文字路线，只显示选中日。真实 path 使用导航实线，缺失 path 时仅显示不冒充道路导航的顺序参考线。高德底图异常会给出 Key、安全码、域名或网络复核提示。

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

发布、评论、点赞、收藏和删除在 Server Catalog 层不存在。适配器要求受控 upstream URL、隔离 Cookie volume、进程级限流、结果上限和登录失效降级；upstream 身份只使用 Cookie，不保留或发送独立 Authorization。所有正文标记为 `untrusted_content`。仓库保留上游项目链接、Apache-2.0 许可证快照和只读改造说明，但不 vendoring 上游可执行文件。

Owner 登录管理位于“管理后台 → MCP 与 Skills → 外部平台账号 → 小红书”；`xhs-readonly` 的连接、Catalog、调用统计和服务重启仍位于“MCP 服务监控”。本地扫码由 Cookie 内容稳定更新驱动：登录助手写入后先重载 Gateway-owned sidecar，再通过只读登录 Tool 复检。管理 API 兼容 MCP structured content 与兼容 text content 形成的连续 JSON 文档，仅缓存 authenticated/auth_required/unavailable 等安全状态；Cookie 值、路径、PID、二维码和原始 Tool 输出均不进入前端或审计。

高德、Tavily 和 12306 继续作为外部 MCP Server 接入。模板给出高德/Tavily 字段形态和固定高德版本，但默认不写入真实 key；12306 必须由 operator 在运行态配置已审查的 query-only 命令。真实 Tool Catalog 由显式 smoke 重新确认。

旅行 MCP 调用使用应用层 `TravelGuardedTool`，不修改 AgentLoop。每个 travel Session 只保存 Tool 名称与规范化参数的 SHA-256 指纹和分类计数，不保存参数正文或结果正文；完全重复调用返回 `TRAVEL_SOURCE_ALREADY_QUERIED`，单类预算耗尽返回 `TRAVEL_SOURCE_BUDGET_EXHAUSTED`，均要求模型复用已有 ToolResult 或安全降级。铁路站码辅助查询与往返车票查询分别计数，去程和返程车票各允许一次；天气、网页、社区、酒店以及高德搜索、地理编码、路线也分别计数。不同 Session 与不同用户互不影响，可以正常并发。守卫结果在用户进度中折叠为 internal，前端同时撤回同 `tool_call_id` 的 started 项，不制造来源失败卡片或永久“正在查询”。optimizer 的日路线只承载目的地内接驳，跨城铁路进入顶层交通方案，不使用高德跨城公交冒充铁路。

旅行应用 Prompt 与 `tool_use_policy.md`、`skills_intro.md`、`memory_policy.md` 等核心协议 Prompt 会在 Gateway 构建 Web runtime 前从仓库受信模板原子同步到运行 workspace；同步只覆盖代码耦合清单，不覆盖 `identity.md` 和其它用户可定制 Prompt，避免普通重启继续加载初始化时期的旧工具、Skill 或旅行规则。

酒店只读浏览适配器位于 `integrations/hotel_browser_mcp/`，支持由 operator 配置平台账号并复用登录态查询指定日期住宿；账号和 Secret 只由运行环境注入，不进入计划或前端。Gateway 启动后对已配置账号只执行一次后台 profile 检查，不提交密码；Owner 可显式检查登录或使用保存凭据登录。有图形会话时登录助手允许可见人工验证，服务器容器默认使用 headless Chromium，不依赖 X Server。旅行应用通过内置 `search_travel_hotels` Tool 直接复用已认证 profile，不再要求 operator 额外声明 `hotel-browser` MCP server，也不会在旅行查询中静默提交密码重登；登录失效返回结构化认证错误。外部 MCP 运行方式仅保留为可选互操作入口。服务未启用、登录失败或没有指定日期可核验结果时，计划只能使用带 `planning_estimate` 状态的估算，不得把酒店 POI、搜索摘要或模型值展示为实时观察价。若指定日期查询已成功，finalizer 会拒绝仍使用规划估算或缺少携程价格 evidence 的住宿卡。

管理页把携程只展示在独立“外部平台账号”区域，不进入 MCP Server 数、Catalog 或 MCP 卡片。小红书与携程账号安全投影统一走 `/api/admin/external-platforms/*`；携程顶层操作收敛为状态感知的“检查登录/使用已保存凭据登录”和“管理账号”，更新、取消与删除位于管理区。小红书 MCP 服务重启仍走 MCP 技术接口。

## 4. quick 与 deep

- `quick` 与 `deep`：只要通用 Subagent 已启用，旅行应用会在 Turn 边界自动补充候选前 `travel-transport-weather`、`travel-stay-poi`、`travel-guides` 三个互斥 Profile，并只创建一个最多 3 个 child 的并发批次；quick 返回精简事实，deep 可补筛选理由。运行时按候选持久化状态裁剪 Profile，候选前看不到最终化能力；候选选择后只保留 `travel-final-stay`、`travel-final-route` 两个窄 Profile 并行补住宿与路线，不能再运行 optimizer。
- 候选前三个 Profile 分别只允许必要的 12306/Open-Meteo、高德文本/详情/携程、Tavily search/小红书 search/detail；候选后两个 Profile 分别只允许住宿身份/价格和高德坐标/公交路线。全部使用 shared-readonly、depth 固定 1，并请求可选 `model_role=fast`；存在多个 enabled fast endpoint 时按较小 `priority` 选择，没有 fast 时继承当前主模型。`routing.compaction` 只服务上下文压缩和候选骨架，不作为 Child 模型路由。operator 在运行配置声明同名 Profile 时视为显式覆盖，应用不改写它。
- Child 工厂保留完整来源 Tool，返回给父 Agent的 planning Tool surface 按阶段只保留必要动作：候选前为 Skill、澄清和 `delegate_tasks`，候选后为 finalizer 与 `delegate_tasks`；父 Agent不能在 fan-in 后串行重复查询。optimizer 成功返回至少两个候选时，旅行 Provider 直接保存候选评审并发出等待用户事件，避免模型再次复制结构。高德文本和 geocode 结果必须与显式 `city` 一致，异地同名候选在 Tool 边界被拒绝。
- child 仍与父 actor 可见 Tool 取交集，不能获得 inline credential、任意 server、`exec`、finalizer 或更高权限。
- 一个 child 失败时父 Agent保留其它结果，把失败方向写入 unknowns，然后继续质量门控。

## 5. Prompt 与不可信输入

长旅行规则位于 `prompts/travel_planning.md`。它规定必要补问、Skill/MCP/finalizer 顺序、quick/deep、来源等级、未开售/无结果/不可用区别、酒店 POI 边界、天气窗口、质量门控和最终摘要。

网页、搜索摘要、MCP description、ToolResult 和社交正文均被视为不可信数据。正文中的角色切换、调用 Tool、发布互动、索取 Secret 或忽略既有规则指令不得执行。计划 schema 同时拒绝 `api_key`、Authorization、Cookie、credential、password、secret、token 字段，以及来源 URL 中的 credential query 参数。

## 6. Vue 旅行页面

旅行页左侧仅列已保存计划，默认进入新建状态，不自动打开最近结果；只有 URL 指定或用户点击后才展示历史计划。自然语言输入卡与下方内容使用相同全宽，位于主内容顶部；空白新建态不再显示独立的大幅宣传卡，“生成可执行计划 · 来源与时效可核验”降级为输入卡 footer 内的辅助说明。过程与完整计划在其下方顺序展示并随主区滚动。过程面板生成时展开、TravelPlanV1 就绪后自动收起，并可由用户手动再次展开。Session 隔离的设计背景见 `2026-08-12-travel-session-isolation-and-bottom-composer-design.md`。

主阶段采用单向等级 `requirements → data → guides → solve → validate → complete`，迟到的地图或来源事件不能把 validate 退回 data。候选按钮点击后立即显示确认状态；服务端接受选择后显示累计等待时长，并把最终化拆成“住宿与房价、交通路线、最终校验”三条事实状态，不伪造百分比。并行任务各自保持 running，只有相同调用完成或整个计划结束时才收敛；完成事件通过 WebSocket 或恢复轮询直接打开结果，无需手动刷新。

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
- 顶部交通卡展示具体线路、车次、时刻、席别、单价、总价和来源；住宿卡展示具体酒店、地址、日期、晚数，并明确区分“指定日期观察价”和“规划估算”；每日时间线展示公交/地铁线路、上下车站、途经站与步行接驳；
- 地图使用日期页签，默认第 1 天且只显示当天地点、路线和文字摘要，切换日期不会残留其它天覆盖物；
- 来源抽屉：平台、来源等级、发布时间、查询时间、数据时点、confidence、原链接和 freshness；
- freshness TTL：过期 live/snapshot/estimate 或 unknown 显示“需要重新查询”，historical 保持历史参考样式；
- 高德 JS API 2.0 marker/polyline；缺 key、无坐标或脚本失败时显示文字路线模式。

旅行 Store 在登录后的应用层常驻订阅，页面卸载不取消正在运行的旅行任务监听。生成中返回聊天再进入 `/travel` 时保留当前 Session、过程和结果，不因缺少 `plan` 查询参数而清空。浏览器整页刷新只在 `sessionStorage` 按用户保存非敏感 Session id，并必须经后端当前 actor 所有权和 `channel=travel` 校验后，才能恢复 running/pending 或打开该 Session 已生成的计划；不在浏览器保存完整计划、消息、来源正文或 Secret。详细设计见 `2026-08-12-travel-generation-continuity-design.md`。

`travel.plan_ready` 到达时，前端必须把事件对应的当前 Session id 显式传给计划打开流程，不能等待计划列表刷新后再反查 `source_session_id`。服务端历史与内存中的实时事件采用单调合并，因此完成后无需刷新即可保留需求、查询、优化、校验和完成的完整过程；候选待选状态进入生成后保持 busy，不重新出现“确认并开始规划”。

历史计划同时使用其 `source_session_id` 从 actor-scoped SessionStore 恢复接待对话，并直接回填页面顶部原有 `TravelPlanForm` 问答窗口，不另设“规划对话”卡。接待 Agent 的 user/assistant 消息由普通 AgentLoop 保存；两个接待 Tool 及运行时兜底共同把最近 Turn id 写入 `travel_intake_turn_ids`，travel draft API 只投影这些 Turn 和旧版显式 conversation 消息。Tool、规划执行回复、空 tool-call 占位、自动续跑指令和纯 JSON 不进入问答窗口。计划与关联 travel Session 同生命周期，删除计划会在重新验证 actor 所有权与 `channel=travel` 后同步删除 Session 和需求问答。消息正文不写入浏览器长期存储，travel Session 仍不进入普通聊天侧栏。详细设计见 `2026-08-13-travel-conversation-history-design.md` 与 `2026-08-14-travel-intake-agent-and-planning-phase-design.md`。

后台完成且用户当前不在旅行页时，聊天侧栏的旅行入口显示一个按用户隔离的数字未读徽标 `1`；进入旅行页后清除。该状态只表示“有一份未查看完成结果”，不表示历史计划总数，也不持久化计划正文。MCP Server 的连接、Catalog、调用和自动重连安全统计在管理后台 Skills 页只读展示，具体边界见 `2026-08-12-travel-unread-and-mcp-admin-monitor-design.md`。

前端 key 和安全密钥只从构建环境 `VITE_AMAP_JS_API_KEY`、`VITE_AMAP_JS_SECURITY_CODE` 读取。来源跳转仅接受浏览器解析后的 HTTP(S)，使用 `noopener noreferrer`。

## 7. 配置

运行态 `${ZHICE_AGENT_WORKSPACE}/config/config.yml`：

```yaml
travel:
  enabled: true
  max_evidence_items: 40
  max_plan_bytes: 524288
```

缺少 `travel` 分区时能力 disabled，不影响普通聊天；显式非法配置只把 travel 标为 unavailable。旧工作区中的 `default_mode`、`max_search_results`、`deep_subagent_count`、`xhs_readonly_enabled` 在兼容期继续校验后忽略并记录 warning，示例不再宣传它们。Open-Meteo 在模板中作为无 Secret 的 bundled stdio MCP 启用，其它真实源在运行态按注释配置。

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
TRAVEL_RAIL_EVIDENCE_MISSING
TRAVEL_OPTIMIZATION_FAILED
TRAVEL_PLAN_SCHEMA_INVALID
TRAVEL_PLAN_TOO_LARGE
TRAVEL_PLAN_NOT_FOUND
TRAVEL_PLAN_ACCESS_DENIED
```

## 9. 测试基线

默认稳定测试覆盖：

- request/evidence/plan 正常、异常和边界；
- 完整 content hash 去重、同 provider/入口 URL 的不同语义证据保留、非法 URL、Credential-like 字段和 freshness 混淆；
- optimizer 的预算、时间、开放时间、折返、跨城、强度、取消和输出上限；
- optimizer 使用原始强度做门控，但公开候选强度固定在 0 到 10；Source Ledger 能解析超过旧 20KB 阈值的大型合法搜索结果；
- Store owner 隔离、Session/Turn、删除和 finalizer owner 重写；
- Open-Meteo 标签/窗口、小红书只读 Catalog/Cookie guard/提示注入、Fake catalog 单源失败；
- Fake LLM quick/deep 均使用候选前三个互斥 Profile 的 Tool 序列；
- Travel API 与 `travel.plan_ready`；
- Vue 表单、地图无 key 降级、来源安全跳转、TTL 和 Store。
- Vue 交通/住宿真实字段展示、公交线路与站点、按天地图切换、完成事件先于计划列表刷新时的进度保留，以及候选确认按钮 busy 状态。
- 路由往返时旅行 Store 常驻、运行态不清空、非当前 Session 事件隔离，以及整页刷新后的 running/pending/completed/failed/stopped/idle 恢复。

仓库级基线以每次交付实际执行的 Ruff、Pytest、ESLint、Vitest、TypeScript 和 Vite 生产构建结果为准，不在活文档固化会迅速过期的测试数量。Windows 下使用 repo-local 独立 basetemp，避免退出清理噪声掩盖真实退出码。

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

代码层已满足：同一 AgentLoop、quick/deep 共用内置旅行并发研究、最多 3 个 depth=1 child、Profile 最小工具预激活、正式 Skill、携程指定日期只读价格 Tool、结构化计划、owner 隔离、API、RuntimeEvent、专属页面、按天地图、交通/住宿证据细节、单向进度与候选后持续反馈、按 Session 来源去重/硬预算、12306 去返程尝试与未开售证据保留、来源/时效、提示注入与单源故障边界。真实外部服务可用性、服务条款、账号登录态、上游 Tool schema 和 JS 地图加载属于部署环境验收；只有实际执行后才能报告通过，不由 Fake 测试替代。携程指定日期观察价仍取决于有效认证；调用守卫、公交别名校验、住宿证据分离、旅行 child fan-out/fan-in 与最终化进度都必须以重启后的真实页面 E2E 为最终验收，不能用 Fake 测试代替。
