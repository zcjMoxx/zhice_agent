# 旅行并发研究、携程住宿与最终化进度设计

## 背景

2026-08-15 的真实“重庆到贵阳五日”规划在候选前耗时 364 秒、候选选择后耗时 719 秒。候选前 21 次 LLM 调用占 330 秒，外部工具只占 31.5 秒；候选后 13 次 LLM 调用占 712 秒，外部工具只占 5.4 秒。运行记录还显示该计划使用 `quick` 模式，未创建任何 Subagent；运行配置虽开启通用 Subagent，却缺少旅行专用 `travel-research` Profile，也没有启用 `hotel-browser` MCP。

同一计划推荐贵阳亨特索菲特酒店并展示每晚 550 元，但该价格是 `planning_estimate`，只有高德酒店身份来源，没有携程指定日期价格证据。候选选择后，最终计划两次因公交证据和步行距离字段失败，页面长时间没有持续运行提示；前端又按每条 Tool 类型直接覆盖六步主阶段，导致校验后补查高德时从“校验”退回“基础数据”。

## 目标

- 旅行正式规划默认允许一次最多三路的只读并发研究，减少父 Agent 串行 LLM 往返。
- 携程账号已配置时，旅行 Agent无需依赖运行 MCP 模板同步即可发现只读酒店查询能力。
- 携程成功返回指定日期价格后，最终住宿必须保留观察价和价格证据；失败时才透明降级为规划估算。
- 候选选择后立即展示持续运行中的最终化状态、并发工作 Lane 和等待时长。
- 六个宏观阶段只允许单向前进；校验修复作为当前阶段的子活动，不再倒退主进度。
- 减少可由 schema 和服务端确定性处理的 finalizer 重试，不降低当前交通、住宿和证据质量门槛。

## 范围边界

- 保留通用 AgentLoop、LLMProvider、ToolProvider、SessionStore 和现有 Subagent 协议，不在 AgentLoop 中硬编码旅行判断。
- 旅行专用 Profile、携程 Tool、来源门控和进度语义位于 `agent/applications/travel/`；`agent/app/runtime.py` 只负责按旅行应用提供的配置和工具组装运行态。
- 携程能力只读取搜索列表，不执行预订、支付、收藏或对外写操作。
- 本轮不引入跨 Turn 后台 Agent Job；候选选择仍使用当前旅行 Session Turn，但通过并发研究、确定性 schema 约束和持续状态降低等待。
- 携程页面结构或登录验证失败时保持 fail-soft，不伪造实时报价。

## 模块设计

### 1. 内置旅行研究 Profile

新增旅行应用辅助模块，在通用 Subagent 已启用时为当前旅行 planning Turn 合并三个互斥安全 Profile：`travel-transport-weather` 只允许 12306/Open-Meteo，`travel-stay-poi` 只允许高德与内置携程 Tool，`travel-guides` 只允许 Tavily/小红书。三者使用 `shared_readonly`、`depth=1`、有界迭代和结果，并通过 `routing.compaction` 创建独立快速模型实例。用户显式定义同名 Profile 时保留用户配置，不覆盖其权限。

第一次落地后的真实页面 E2E 证明泛化 Profile 虽能 fan-out，但 child 会跨 Lane 调用来源，且运行配置没有 `role=fast` endpoint 时会回退父模型 `gpt-5.4`；最长 child 达 132 秒，父 Agent还会重复请求已经成功或稳定认证失败的来源。因此当前设计改为三个互斥 Profile，并在来源账本中将成功类别与稳定认证失败设为 Session 级终态；父 Agent planning Tool 集合同时移除诊断、文件与 Memory 等无关能力。

第二轮真实页面 E2E 中三个 Child 已在同一秒启动并使用 `gpt-5.4-mini`，fan-in 从旧基线的串行研究降到约 109 秒，但又暴露三个边界：交通 Child 的工具发现被大量 12306 辅助能力占满，未调用已经可用的历史天气；住宿 Child 对多个景点并列发起四次携程查询并继续扩张 POI，最终触发 Tool 迭代上限；父 Agent fan-in 后仍能看到原始来源，继续串行重查，并在候选后用高德 geocode 接受了天津、新疆等异地同名地点。当前主线因此进一步收紧：交通 Profile 只保留城市站码、车票与全部 Open-Meteo；候选住宿 Profile 只保留高德文本/详情和一次携程；候选后新增 `travel-final-stay`、`travel-final-route` 两个 Profile；Child 工厂组装完成后，父 Agent只保留旅行领域动作、Skill 和 `delegate_tasks`，不再直接持有原始来源。高德压缩层按显式 `city` 过滤候选，全部异地时返回 `TRAVEL_MAP_CITY_MISMATCH`，错误坐标不得进入模型和最终计划。

第三轮真实页面 E2E 进一步证明“把五个 Profile 同时暴露给模型”仍不可靠：候选出现前模型提前运行了 `travel-final-stay` 与 `travel-final-route`；optimizer 首次成功后又把空候选数组传给评审 Tool，触发一次完整重算；用户选择候选后，模型因历史中的提前最终化结果连续三个 Turn 不调用任何 Tool，最终以 `TRAVEL_PLAN_NOT_FINALIZED` 结束。当前方案因此增加服务端阶段裁剪：候选前的 `delegate_tasks` 只能看见三个研究 Profile，候选已选择后只能看见两个最终化 Profile；候选前不暴露 finalizer，候选后不暴露 optimizer 和候选评审。optimizer 返回至少两个可行候选后，旅行应用 ToolProvider 从受信 SkillResult 中直接保存候选评审并发出等待用户事件，不再依赖模型复制数组。候选选择后的首个 WebSocket Turn 使用服务端根据持久化选择生成的受信继续指令，并把所选候选摘要注入本轮上下文。

第四轮重启后验证中，首批三个 Child 已缩短到 46 秒，但住宿 Child 在携程人工验证失败后未继续调用高德，来源账本把地图视为候选求解前必需项，自动续跑因而又派出两个住宿 Child，仍未收敛。当前修订把地图精确坐标和公交路线明确延后到用户选择后的 `travel-final-route`：候选求解只硬等铁路、天气、住宿尝试、网页和社区五类；首批完成后隐藏 `delegate_tasks`，只开放 Skill 求解。用户选择后来源账本切换为地图/住宿最终化集合，缺口存在时只开放一次双 Child 委派，补齐后只开放 finalizer，避免模型通过自然语言提前结束或重复整批研究。

正式规划 Turn 将 `delegate_tasks` 加入预激活 Tool。quick 与 deep 都可使用一次并发批次：quick 要求最多三条精简研究 Lane；deep 允许同样三条 Lane 输出更丰富的筛选理由。三个固定方向为交通天气、住宿景点、攻略避坑。

### 2. 内置携程只读酒店 Tool

新增 `search_travel_hotels` Tool，包装现有 `integrations.hotel_browser_mcp` 携程适配器。Tool 接收城市、入住退房、关键词、入住人数、住宿类型、价格区间和结果上限；当前上游不支持的入住组合返回结构化错误，不静默篡改人数。Tool 输出压缩后的酒店名、评分、每晚观察价、摘要和原链接，并通过 `TravelSourceLedger` 执行调用预算、去重和成功观察。

Subagent 可用时只有 `travel-stay-poi` 与 `travel-final-stay` child 使用该 Tool；父 Agent只编排并复用 fan-in 结果，避免候选前后重复查询。Subagent 不可用时父 Agent才保留有界降级调用。运行配置中的 `hotel-browser` MCP仍可继续使用，但不再是携程能力的唯一发现路径。

### 3. 住宿价格质量门控

如果住宿来源查询成功并返回酒店卡，最终计划必须至少有一个 `live_observed` 或 `snapshot_observed` 住宿价格，并引用非 `model_estimate` 的价格来源。没有总预算或住宿档位时，Prompt 要求优先从观察结果的中低价位选择舒适型住宿；除非用户明确要求，不默认推荐豪华酒店。

酒店来源不可用、需要人工验证或没有结果时，允许 `planning_estimate`，但必须继续展示“无外部实时报价”。

### 4. 候选后最终化收敛

候选确认后的继续 Prompt 只允许复用既有证据并补查选定候选的缺口，不得重新跑整套研究。`travel-final-stay` 与 `travel-final-route` 在一个两任务委派批次中并发；住宿只返回一处具体身份和价格状态，路线只返回目标城市坐标及不少于 2 公里的必要公交段。父 Agent fan-in 后先确认来源完整，再一次构造最终结构，不用 finalizer 反复试探缺口。Tool JSON schema补齐后端已经执行但未在 schema 声明的数值上限，减少无意义的完整计划重写。

服务端继续以 `TravelPlanV1` 和来源账本做最终硬门控；不通过时返回具体字段和可恢复动作。不会为了提速放松真实公交线路、酒店身份、价格来源或天气证据要求。

Subagent 配置按候选持久化状态生成，而不是仅靠 Prompt 约束。候选尚未创建时只保留三路研究 Profile；候选处于 `selected` 时只保留住宿与路线两个最终化 Profile，并且父层 Tool 集合只保留 `delegate_tasks`、`finalize_travel_plan` 等本阶段动作。候选 Skill 成功后由旅行 Provider 原子衔接 `request_travel_candidate_review`，避免模型丢失或改写 `feasible_candidates`。总预算是硬约束时，optimizer 以预期总价不超过预算作为可行门槛；预算上界超出可保留为风险提示，但不能推荐预期价格已经超预算的方案。

真实 3500 元预算用例的第二次候选修正已消除时间和强度问题，但两个候选 expected 都是 3550 元，仅超出 50 元。为避免让大模型再次重写整套日程，optimizer 在预算项目原有区间内执行有界投影：只允许下调餐饮、市内交通等可调项目的 expected，且不得低于各自 lower；高铁、门票、酒店等固定事实不参与自动压缩。可调容量足够时把 expected 压到硬预算并保留 upper 风险，容量不足时仍以 `BUDGET_EXPECTED_EXCEEDS_HARD_LIMIT` 拒绝。

预算投影后的真实 E2E 已在 197 秒内自动展示两张候选卡，expected 分别为 3380 与 3400，均不超过 3500。选择后双 Child 确实并发，但父模型连续两个无 Tool Turn 后才委派；`travel-final-route` 又在 5 次迭代上限处只返回 “Tool call limit reached”，导致父层两次把四段长距离公交全部写成 `planning_estimate`。当前修订把候选已选择视为 Subagent force-once Turn；最终住宿先查高德身份再查一次携程，迭代上限增至 7；最终路线必须覆盖全部不少于 2 公里的公交段后再返回，上限增至 12、超时 180 秒、结果上限 14KB。候选卡标题始终从首日区域与地点生成，不展示内部 candidate_id；评审状态已经 selected 时按钮永久锁定，即使最终化失败也不能重复点击。

第五轮复验中，候选选择后首个 Turn 已立即委派，住宿与路线 Child 同批启动；路线 Child 在 90 秒内取得三条真实高德线路，但漏掉兵马俑返程。它同时暴露了两个更底层问题：固定 Profile 虽已允许目标 Tool，小模型仍可能用缩写别名调用 `discover_tools`，造成住宿高德和 12306 车票“已允许但未激活”；来源账本又把站码成功误当成票务研究完成。父层最终器因此经历多次 90～117 秒的大 JSON 重写，并一度保存了没有铁路卡、仍含 34 公里 `planning_estimate` 返程的计划。

当前方案在通用 `SubagentProfile` 增加只收窄权限的 `initial_tools`：必须是 `tools` 内的精确项，Child 工厂只预激活这些 schema，其余能力继续懒发现；如果当前有效工具已经全部激活，则隐藏无意义的 `discover_tools`。五个旅行固定 Profile 本身已经按 Lane 收窄，因此分别预激活其全部真实查询集，消除错误别名和发现往返。来源账本独立记录 ticket attempt/success 数量及 `not_on_sale`，站码不再满足候选票务门槛；Open-Meteo geocode 同样不能替代 forecast/historical 数据调用。去程、返程必须各查询一次。finalizer 新增铁路证据保留门槛，真实车次必须保留完整字段，未开售则保留查询日期与起售日而不虚构票价。路线门槛同时识别被改写成“规划估算”的长距离本地接驳，远郊景点去返程必须完整覆盖。

第六轮真实 E2E 暴露了一个阶段账本缺陷：候选阶段已经查询过地图和住宿后，`begin_finalization_budget` 只重置调用次数却继续复用全局 `attempted`，导致候选选择后的 Provider 直接只暴露 `finalize_travel_plan`，没有暴露 `delegate_tasks`。父模型因此单独生成 169 秒后才试探 finalizer，并因为小红书成功响应外层的 `output` 包装未被账本递归解析而被误判为 social 空结果，后续自动续跑又没有可用来源 Tool。修订方案为：账本新增 finalization 阶段独立的地图/住宿 attempt 集；候选选择和重启恢复都幂等开启该阶段，只有本阶段两类都实际尝试或稳定不可用后才从双 Child 委派切换到 finalizer。MCP JSON 展开同时识别 `output` 包装，避免把已有小红书/Tavily 行误记为空结果；最终化时只考虑当前 expected 集合的重试项，不能让候选阶段的旧检索状态形成不可恢复死锁。服务端候选继续消息也直接要求先执行恰好两个任务的单批委派，避免短消息与系统 Prompt 口径冲突。

同轮重启复测又证明“只把 `delegate_tasks` 设为唯一工具”仍不足以保证并行：父模型可能拒绝调用，也可能只提交一个交通天气任务。候选缺口存在时，运行时因此启用 force-once Subagent 契约；旅行专用 ToolProvider 在创建任何 Child 前校验整批 Profile 集合，候选阶段必须恰好为三条互异 Lane，最终化阶段必须恰好为两条互异 Lane。部分批次、重复 Profile 或跨阶段 Profile 直接以 `TRAVEL_SUBAGENT_BATCH_INVALID` 返回给模型修正，且不消耗真正的批次额度。

第七轮真实 E2E 中固定三路研究已在 66 秒内 fan-in，但父模型使用主模型生成候选和定向重写分别耗时约 63 秒与 59 秒；两次失败都不是来源或预算问题，而是第二日“兵马俑 + 额外市区活动”的活动窗口与真实往返接驳合计后略超 `balanced` 强度硬上限。当前方案把候选骨架阶段路由到已配置的 `routing.compaction` 快速模型，接待与需求确认、用户选定后的最终完整计划继续使用会话主模型。optimizer 对不超过 150 分钟的有限活动窗口超限做有界确定性投影：路线时长、距离、活动顺序和硬门槛全部不变，每项活动至少保留 45 分钟，投影后重新执行原校验；更大的超限仍拒绝。Prompt 同时禁止用重复必去景点填满返程日，降低无效强度和后续公交查询量。

第八轮 E2E 首次使用快速候选模型时，三路研究分别在 32、63、97 秒完成，optimizer 345 毫秒内成功，但两个输入候选只有一个通过，因而未达到候选卡至少两项的产品门槛。随后 WebSocket 自动续跑仍使用“候选缺失就重新三路委派”的旧文案，而来源账本已经隐藏 `delegate_tasks`，造成两个无工具 Turn。当前续跑按来源账本分支：确有来源缺口时才要求固定三路委派；研究已完成但候选不足时只复用既有 fan-in，定向修正被拒候选并重跑 optimizer，不重复任何外部查询。

第九轮选择候选后在 598 毫秒内展示了最终化计时与三条 Lane，住宿/路线双 Child 同批执行，四次高德公交均返回真实线路；但候选阶段耗尽的通用地图搜索额度没有在最终化阶段重置，路线 Child 的精确酒店区 POI 检索全部被拒。住宿 Child 使用 `types=酒店` 查询“钟楼饭店”时，高德又返回了同名观光巴士站，模型正确拒绝将公交站当酒店，finalizer 最终以 `TRAVEL_STAY_REQUIRED` 拒绝。修订后最终化同时重开通用地图、住宿地图、地理编码和路线四类预算，同时保留跨阶段指纹去重；住宿查询固定使用高德住宿大类 `types=100000` 和具体中档酒店名，压缩层按 type/typecode 丢弃公交站、停车场等非住宿实体，确保规划估算也建立在真实酒店身份上。

同轮路线 Child 虽在约 51 秒时完成四次真实高德请求，却继续用约 73 秒整理包含大量途经站和备选线路的 14KB 结果，父模型输入随之变大；前端又只看到父层 `delegate_tasks`，住宿和交通 Lane 一直停在“等待”。当前路线 Profile 只保留每段最佳线路、最多八个代表性途经站并把结果限制为 8KB；父层批次开始/完成事件同步映射到住宿和交通两条 Lane，使用户能看到并发阶段已经启动和何时 fan-in，而不是只看持续计时。

第十轮在最新酒店类型与最终化预算修复后真实续跑，住宿 Child 已正确取得“全季酒店（西安钟楼北大街地铁站店）”的名称、地址和坐标，并在携程仍需人工验证时透明保留规划估算；路线 Child 也一次批量取得多段高德公交结果。但每条高德原始公交响应约 13KB，六条结果累计超过 60KB，快速模型最终整理恰好越过 180 秒批次边界约 5 秒：Child 随后完成，父批次却已经返回 `partial`，最终器因未收到该结果而以 `TRAVEL_ROUTE_EVIDENCE_MISSING` 拒绝。当前修订在 `travel-final-route` Child 的 ToolProvider 边界确定性压缩每条高德结果，只保留一条步行不超过 2 公里的最佳方案、线路名、上下车站、时长、距离和最多八个途经站；原始备选路线和逐步步行指令不进入下一轮 LLM 上下文。路线超时上界提高到 240 秒只用于消除边界竞态，实际耗时由压缩上下文降低。候选已选择后的最终结构编排同时路由到 `routing.compaction` 快速模型，并继续由 `TravelPlanV1`、来源账本和 finalizer 硬门槛兜底，不放松证据要求。

同轮还验证了旧失败记录恢复路径：后端已进入双 Child 最终化，但前端尚未加载 selected candidate review，于是把 `delegate_tasks` 误投影为候选研究，Lane 没有立即出现。恢复运行态时前端现在主动读取持久化候选评审；若状态为 selected，直接恢复 validate 阶段、最终化计时和住宿/交通两条 running Lane。服务端持久进度投影也识别最终化双 Profile 批次，刷新后能把两条 Lane 恢复为 done，而不是退回泛化的“并行旅行资料已汇总”。

### 5. 单向进度与等待反馈

前端为六个宏观阶段定义稳定 rank，普通 Tool/Skill 事件只能推进到更高阶段，不能回退。恢复历史时取全部进度项的最高宏观阶段，不再使用最后一条项目决定主阶段。

选择候选后立即新增一条 `running` 的“正在完善所选方案”记录，并重置最终化计时。进度组件显示等待时长和三条 Lane：住宿与房价、交通路线、最终校验。Lane 根据 Tool 名和阶段更新；长时间没有 Tool 事件时仍显示本地计时与“后台仍在工作”，不伪造百分比。

第六轮真实 E2E 证明路线 Child 已取得兵马俑去返程完整线路，但父级最终结构仍把首日西安站到酒店、末日酒店到西安站保留为 `planning_estimate`，导致 finalizer 持续拒绝；同时父级 `gpt-5.4-mini` 首次完整 JSON 编排约 226 秒，比主模型旧样本约 149 秒更慢。当前主线因此明确：路线 Child 必须逐条覆盖所有不少于 2 公里的本地公交、地铁或未定交通段，包含车站与酒店接驳；快速模型只用于 Child，父级最终完整 JSON 编排保留当前主模型。前端恢复出来的住宿/路线 Lane 在真实委派事件到达时原位替换，不能同时保留恢复占位和实时记录。

最新路线收敛策略把路线 Child 收紧为“少量缺失地点一次批量解析，再将全部剩余路线一次并行查询”，不再针对每个地点反复 geocode/search；迭代上限同步降为 6，超时上限收敛到 150 秒。真实页面复验中，住宿和路线双 Lane 从候选选择到 fan-in 约 128 秒，路线结果完整覆盖车站到酒店、酒店到车站、市区景点以及兵马俑去返程。

旅行 Child 的业务模型选择不再复用 `routing.compaction`。五个内置 Profile 只定义旅行场景的固定职责和所需 Tool，实际模型遵循通用 `model_role`：显式配置 `role=fast` 的 endpoint 时使用该 endpoint；没有快速角色时继承当前会话主模型。`routing.compaction` 继续只服务上下文压缩和候选骨架等原本语义，用户未配置业务分工时不得静默把旅行 Child 切到压缩模型。

最终器还需要处理同一 Turn 内的定向修正：当模型为修正酒店、社交来源或其他字段重新提交完整 JSON 时，服务端按日期及 `from/to` 对齐上一版计划，把已经由高德验证的 `mode`、`duration`、`distance`、`path`、`transit_legs`、上下车站、票价和 evidence id 合并回来。该保留只作用于本轮 finalizer 暂存，不跨 Session 复用，也不会把估算路线提升为真实证据。

最终真实 E2E 使用“重庆到西安、2026-09-15 至 09-17、2 人、总预算 3500 元、住宿不超过 300 元/晚、高铁与公共交通、兵马俑/西安城墙/回民街”完成。计划 `travel-plan-cbdef5a8c51a4111988d49091911be5f` 无刷新自动展示：住宿为真实高德酒店身份“全季酒店（西安钟楼北大街地铁站店）”，携程人工验证未通过时透明标注 ¥280/晚为非实时规划估算；去返程 12306 均保留未开售状态和起售日；本地交通保留地铁线路、上下车站及兵马俑去返程；地图按第 1/2/3 天切换并只展示当天地点与路线；六阶段最终归并为完成。候选选择后立即出现住宿、交通和最终校验三条 Lane，最终校验在双 Child 完成后保持 running，直到完整计划通过硬校验。

Owner 的平台账号管理继续位于 MCP 页面，但 Ctrip 不再作为 MCP 网格之外的独立宽卡片。`hotel-browser · Ctrip` 紧跟 `xhs-readonly` 放进同一个双列网格；凭据已配置时默认只展示登录状态、掩码账号、更新时间和登录/更新/删除操作，账号密码表单按“更新账号密码”展开。未配置时才直接展示首次保存表单，移动端仍退化为单列。

携程真实登录后的重启复验确认持久 Profile 可以继承登录态，但首页目的地输入不能仅用 `fill` 后直接搜索：新页面必须触发真实键入、从联想结果中选择可见的精确城市候选，才能写入 `cityId` 并进入酒店列表。适配器因此忽略隐藏模板和酒店名中的同名片段，选中精确城市后再等待受限的 `/hotels/list` HTTPS 结果页；页面控件变化继续返回结构化错误，不回退伪造价格。

## 数据流

```text
确认需求
  -> parent delegate_tasks(one batch)
       -> transport-weather child
       -> lodging-attractions child (Ctrip + AMap)
       -> guides child (Tavily + XHS)
  -> parent fan-in
  -> optimizer
  -> candidate review
  -> user selects candidate
  -> immediate running progress + elapsed timer
  -> bounded gap research (lodging/routes only, parallel when needed)
  -> one final plan synthesis
  -> TravelPlanV1 + evidence gates
  -> saved plan + complete event
```

## 变更文件

- `agent/applications/travel/subagents.py`：旅行 Profile 合并。
- `agent/protocols/subagent.py`、`agent/subagents/config.py`、`agent/subagents/factory.py`：Profile 最小工具预激活协议、配置校验和 Child 装配。
- `agent/applications/travel/hotel_tool.py`：携程只读 Tool。
- `agent/applications/travel/service.py`：父/子研究 Tool 注册。
- `agent/applications/travel/tools.py`、`source_ledger.py`、`schemas.py`：酒店价格与最终化门控、schema约束。
- `agent/app/runtime.py`：旅行 Turn 使用有效 Profile，并向 child 注入只读研究 Tool。
- `skill_repo/skills/travel-planner/scripts/optimize.py`：小幅活动窗口超限的有界投影，投影后仍执行原硬门槛。
- `agent/app/api/ws.py`：候选已选择时使用服务端受信继续消息启动最终化 Turn。
- `prompts/travel_planning.md`、`prompts/travel_planning_continuation.md`、旅行 Skill：并发研究和候选后收敛指令。
- `web/frontend/src/stores/travel.ts`、`TravelProgress.vue`、`travel.css`：单向阶段、最终化运行项、Lane 和计时。
- 对应后端、前端测试与 Part 19 活文档。

## 测试方案

- 单元测试旅行 Profile 合并、权限 denylist、用户同名覆盖和禁用边界。
- 单元测试 `initial_tools` 只能引用 Profile 精确允许项，Child 首轮仅预激活明确工具且不会扩大权限。
- 单元测试候选前/候选后 Profile 严格互斥、optimizer 成功后自动进入候选评审，以及空候选不再触发模型重算。
- 单元测试预期总价超过硬预算的候选被拒绝，预算范围上界仅作为风险提示。
- 单元测试携程 Tool 参数、成功压缩、登录失败、调用去重和来源账本结果。
- 单元测试携程成功后最终计划必须保留观察价证据。
- 单元测试 quick/deep Prompt 均要求一次三路并发且候选后禁止整套重查。
- 前端测试候选选择后立即出现 running 状态、等待反馈、Lane，以及 validate 后收到 data Tool 事件不倒退。
- 运行 Ruff、后端 Pytest、前端 ESLint、TypeScript、Vitest 与生产构建。
- 重启 Gateway 后使用真实页面完成需求确认、三路研究、两张候选卡、候选选择、携程价格、最终计划和进度单向性的 E2E。

## 验收标准

- 真实 planning Turn 创建三个互斥并发旅行研究 child，日志中可核验 fan-out/fan-in、各 Lane 无跨源调用，且 child 使用 compaction 快速模型路由。
- 携程登录有效时计划展示“指定日期观察价”和携程价格来源；不可用时明确降级且不冒充实时价。
- 候选卡等待时间相较当前串行基线显著下降，具体结果以 E2E 记录为准。
- 选择候选后 300 毫秒内出现最终化运行提示，等待期间持续显示时长或状态。
- 六段主进度不倒退；补查路线显示为“完善与校验”的子活动。
- 最终计划仍通过交通、住宿、地图、天气、网页、社区和结构化 schema 的现有质量门控。
- 站码查询不能替代去返程票务查询；`not_on_sale` 作为 12306 有效证据展示起售日，不显示成无票，最终铁路卡不得被模型删除。
- 所选候选中所有不少于 2 公里的本地公交/未定接驳均有高德线路与站点，远郊景点同时覆盖去程和返程。
