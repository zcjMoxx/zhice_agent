# 智能旅行规划测试说明

## 测试目标

- 旅行规划 Prompt 与官方 Skill 文档必须公开同一套每日分钟、强度公式和 pace 硬上限，并要求按拒绝原因最多定向重试一次，避免真实 AgentLoop 对确定性门槛盲目循环。

验证 Milestone 19 的领域协议、纯计算 optimizer、用户隔离持久化、内部 finalizer、RuntimeEvent、只读 MCP 适配边界、quick/deep Agent 调用序列和 Travel REST API。默认测试不访问真实外网，也不读取真实 API key 或 Cookie。

## 用例覆盖

- 旅行研究 Subagent Profile：启用通用 Subagent 时自动补充交通天气、住宿景点、攻略避坑三个互斥只读 Profile，限制一个 Turn 最多三路并发和一个批次；禁用状态不被打开，operator 同名 Profile 不被覆盖。
- 携程只读 Tool：按城市与入住日期返回压缩观察价，支持住宿类型关键词和价格区间过滤；重复查询被账本拦截，认证错误保持结构化且不盲目重试。
- 多住宿区最终化：同一候选跨不同过夜城市或区域时，允许按各自入住日期分别查询一次酒店，不能因首个城市已有结果而拦截后续真实住宿区。
- `TravelRequestV1`：目的地、日期、总天数、人数、预算、节奏与 quick/deep 边界。
- `EvidenceItemV1`：HTTP(S) URL、时间戳、来源类型、live/snapshot/historical/estimate/unknown 一致性、SHA-256、短摘录截断、URL/content 去重与 evidence id 重映射。
- `TravelPlanV1`：每日活动、时间重叠、路线段、跨区域、预算区间、未知证据、Credential-like 字段、非法来源与计划大小；住宿既支持覆盖全程的备选卡，也支持按不同过夜区域连续分段，分段不得漏夜或重叠。
- 证据化交通住宿：新交通对象保存车次、时间、席别、单价与总价；住宿对象区分指定日期观察价和规划估算价；`amap_transit` 路线必须保存线路号、上下车站和途经站。
- 真实 E2E 硬守卫：同一 travel Session 的完全重复 MCP 参数不再次请求远端；铁路、天气、网页、社区、酒店和高德搜索/地理编码/路线分别有有界调用预算，不同 Session 可并发且互不影响；守卫结果不生成重复来源失败卡片。
- 证据语义完整性：`amap_transit` 和“高德公交规划”等别名都必须保存线路与站点；住宿 `evidence_ids` 证明酒店地点，`price_source_evidence_ids` 只证明指定日期价格，规划估算拒绝引用景点或普通 POI 作为价格来源；真实 12306 evidence 被引用时强制车次、时刻、席别与价格齐全。
- optimizer：正常选择、硬预算、每日时间、跨城路线、开放时间、活动重叠、明显折返、取消、错误结果与输出上限；内部原始强度继续执行门控，对外候选分数限制为 0 到 10。
- Store/Tool：actor context 派生路径、owner 重写、Session/Turn 关联、列表、读取、删除、跨用户隔离和 `travel.plan_ready`；finalizer Tool schema 明确公开 request/evidence/day/activity/route/budget 嵌套字段白名单并拒绝 `mode`、`metadata`、`total_minutes` 等非协议字段。
- Fake MCP：Open-Meteo 预报/历史标签与窗口边界；小红书只读 Catalog、Cookie volume guard、提示注入正文仅作为 untrusted content、英文筛选枚举到上游中文值映射、默认筛选 UI 绕过、`max_results` 强制截断、非 JSON 兼容和对外结构化输出上限；一个 Server schema 失败不抹掉其它来源 Catalog。
- AgentLoop：Fake LLM 走真实 tool loop；quick/deep 都可使用一批三个互斥旅行研究 child，部分失败后仍继续 optimizer/finalizer。
- API：登录壳下的 `/api/travel/plans` 列表、读取、删除与稳定错误码。
- 生成连续性：应用级旅行订阅跨路由常驻；恢复 API 仅投影当前 actor 拥有的 `channel=travel` Session，覆盖 running/pending/completed/failed/stopped/idle，并拒绝其它用户或非旅行 Session。
- 完成提醒：后台生成完成后按用户保存 `0/1` 未读状态，聊天旅行入口显示数字徽标；进入旅行页即清除，当前已在旅行页时不产生未读。
- 兼容提取：旧 `/requirements/extract` 仍覆盖严格白名单、非法结构、Provider 失败与安全错误，但不再是旅行工作台正常入口。
- 最小确认：固定公历节日加明确天数可确定性补齐日期；同行人群、节奏和模式不作为用户错误阻塞，人数仍需显式确认；系统策略在提交文本中标记为假设。
- 接待 Agent：新旅行 Session 默认 `travel_phase=intake`，继续使用当前 session 默认 LLM；真实装配只暴露 `update_travel_draft`、`offer_main_chat_handoff` 与 `confirm_and_start_travel_planning`，不创建 MCP、exec、Skill 或 subagent 能力，并注入独立 `travel_intake` Prompt。
- 渐进补全：Agent 通过增量 patch 合并、清空和校验同一草稿；每轮重新注入服务端累计草稿，模型多传的空字符串、空数组和 null 占位不得覆盖旧条件，只有 `clear_fields` 能明确清空；问候或旅行知识讨论允许空 patch，条件缺失时自然追问一至两项，完整时显示确认入口，不再由前端套固定问题列表。
- 阶段切换：actor-owned 确认接口拒绝不完整、非法、越权或非旅行草稿；成功后原子写 `travel_phase=planning`，正式 Turn 才装配旅行 Prompt、内部规划 Tool、来源 MCP、optimizer 与候选审核。
- 自然语言确认：条件齐全后用户回复“确认 / 开始执行”时，接待 Agent 必须调用确认 Tool 复用同一服务端校验，并通过 `travel.planning_confirmed` 让前端进入生成态、让 WebSocket 同请求续跑正式规划。
- 确认实时状态：接待草稿更新、主聊天交接和文字确认事件必须注册进统一 Runtime Event 协议并经 WebSocket 实时送达；补充消息处理中隐藏确认条，详情面板确认按钮禁用，避免与模型 Turn 并发确认。
- 接待恢复：`travel_intake_turn_ids` 投影 AgentLoop 保存的 user/assistant 消息；刷新和已完成计划均通过 travel draft API 恢复自然回复，完成的接待 Turn 在统一任务列表中仍是 collecting，不误标为规划失败。
- 手工入口：“补充数据”只打开表格且不调用需求提取 LLM；手工路径同样必须补齐关键字段并明确确认后才执行。
- 旅行终态：普通文本回复不能作为完成；Gateway 在同一 travel Session 内最多自动续跑两次，只有 `travel.plan_ready`、结构化澄清、用户停止或稳定错误才能结束。
- 结构化澄清：`request_travel_clarification` 一次返回全部用户问题，前端回到需求对话；Agent/Tool/MCP/Provider 自身问题不得伪装成用户信息不足。
- 输入交互：Enter 发送、Shift+Enter 换行，界面不展示快捷键说明。
- 用户视角过程：隐藏 `load_skills`/`discover_tools` 等内部动作；高德、天气、铁路、网页与小红书只读查询投影来源、查询目标、返回数量和最多五个候选摘要；optimizer 展示比较数量、采用方案、预算及路线门控；非 JSON、超长与失败结果安全降级且不影响普通 Web channel。
- 外部来源完成门槛：travel Session 对当前可用地图、天气、铁路、网页与社区来源登记 expected/attempted/successful；漏调已配置来源、全部失败或最终只有 model estimate evidence 时 finalizer 拒绝保存；账本不保留参数、完整正文或 Secret，仅对网页/社区成功结果保留最多 5 条安全标题、URL 和短摘要供 Finalizer 防漏，成功后清理且有进程级 Session 上限。
- 应用 Session：旅行 Session 以 `channel=travel` 持久化并可继续供 AgentLoop 使用，但从普通聊天 Session 列表排除；普通 Web Session 保持可见。
- 对话历史：用户确认后需求问答先写入 actor-owned `channel=travel` Session；历史计划按 `source_session_id` 把需求确认阶段的 user/assistant 文本恢复到原 TravelPlanForm 问答窗口，拒绝跨用户和非 travel 写入，并过滤 Tool、规划执行回复、空 tool-call、自动续跑与纯 JSON；相同请求幂等，超量和超长输入拒绝；删除计划同步删除关联 travel Session 与需求问答。
- 草稿与统一列表：首次有效输入即创建 travel Session，collecting 阶段可原子替换需求问答并保存严格结构化草稿；刷新可恢复且不误判为运行中；正式生成复用同一 Session，已有 Turn 后拒绝历史覆盖；左栏统一投影 collecting/running/awaiting_candidate/failed/completed，未完成任务可删除。
- 完整进度：当前规划超过 12 条用户可见记录时保留首尾全过程，不再静默删除较早来源查询；刷新或恢复候选状态后按 session 恢复过程，多个旅行 session 不互串；浏览器缓存缺失时从 actor-owned Session 的持久化 Tool 消息重建安全历史，内部工具和原始异常不进入响应。
- 子 Agent 进度恢复：Root Session 与 `${sessions}/_subagents/{root_session_id}` 下的 Child JSONL 按时间共同投影；只返回安全 Tool 摘要，不暴露 Child Prompt、完整输出或模型总结。
- 完成瞬间进度：`travel.plan_ready` 早于计划列表刷新到达时，显式沿用当前 source session，服务端历史与现有实时记录合并；完成后不刷新也不能退化成单条完成记录。
- 来源稳健性：Tavily 强制关闭原始正文、限制结果数并修正 `fast/ultra-fast + country`；小红书异常组分别映射本地上游未启动、超时、认证和限流，且不泄漏异常正文。
- 候选确认：optimizer 返回全部可行候选的受限摘要；多候选写入 actor-scoped Store 并发出等待事件，刷新可恢复；未知选择、越权 Session、未确认和最终计划不匹配均拒绝；前端卡片提交真实选择后续跑同一旅行 Session；用户文案不泄漏候选机器 ID。
- 本地上游生命周期：loopback 小红书 URL 从配置派生唯一端口，固定 workspace 二进制由 Gateway 托管；远端/Docker 上游、缺二进制和已有外部监听不被错误接管，Gateway 只关闭自己创建的进程树。
- 大结果裁剪：Tavily 风格 structuredContent 超限时删除 raw content、限制列表和摘要长度并保持 JSON 可解析，不再整次失败；非搜索 Tool 不套用搜索参数。
- MCP 结果展示：兼容 structuredContent 与 text 形成的连续多段 JSON、`data.text` 嵌套 JSON、Session 历史中的 `output` Tool 包装层、小红书 `note_card` 和 Tavily content 摘要；区分真实空结果与格式暂不可展示。
- 用户侧命名：12306 等已知来源即使收到通用 `mcp__... 执行完成` 标题，也只显示平台产品名，不暴露内部 Tool 标识。
- 强制候选审核：旅行频道 finalizer 在无候选审核、待选择或候选不匹配时拒绝保存；只有至少两个候选已经展示且用户选择后才能完成。
- 信息化地图与未知项：无 Key 时仍展示逐日地点、交通方式、距离和时长；有坐标时显示编号地点、真实路线或顺序参考线；来源失败技术原句在 UI 投影为原因和重查建议。
- 按天地图：默认只绘制第 1 天，切换日期会清除旧覆盖物，只保留选中日期的地点、路径和文字路线。
- 旅行助手边界：问候、身份、能力、目的地知识和条件修正由接待 Agent 直接生成自然回复；无关问题不回答实质内容，必须通过结构化 handoff event 携带原问题回主聊天，且不自动发送。
- 交接持续性：无关问题的交接卡不会在下一条操作追问发送前消失；只有真实旅行字段变化或用户主动关闭才退出当前交接提示。
- 能力隔离：接待阶段即使 Prompt 判断偏差也无法调用通用 Tool 或旅行外部来源；规划阶段不再看到接待 Tool，阶段切换后接待 Tool 自身也会拒绝执行。
- 固定话术清理：TravelPlanForm 不再包含 greeting/identity/capability/help/unrelated 分支，不再调用提取 API；固定文本只保留异常兜底、按钮和表单校验提示。
- 并发工作台：正式规划运行时仍可脱离当前 Session 新建独立草稿；后台 Session 的完成事件只刷新任务列表和未读提醒，不覆盖前台草稿。
- 删除竞态：运行中 Session 必须在活动 Turn 完成后才删除文件和索引，避免后台回写复活并被识别成 `cli_legacy`。
- 地图坐标：新计划每个活动必须包含可绘制经纬度，路线 `path` 使用坐标对象；历史计划缺坐标时前端地理编码后仍能绘制地点。
- 检索恢复：网页与社区首次空结果或临时失败要求一次收窄重试，第二次后不循环；认证失败不盲目重试；Tavily 已有成功结果不被后续补充超时清除。
- 大型检索账本：超过旧 20KB 阈值但仍在安全上限内的合法搜索 JSON 必须识别已有结果，不能误记为空结果并反复阻塞 finalizer。
- 小红书认证：显式空 `feeds` 会核对独立 MCP 登录态，未登录返回认证错误；普通 `items` 兼容结果不触发额外登录检查。
- 小红书 Cookie 兼容：本地 supervisor 优先选择版本最高的 RedNote 兼容二进制并回退通用文件；自有 sidecar 监听 Cookie 文件签名变化后自动重启加载新登录态，外部 listener 不接管。
- 小红书扫码闭环：Cookie 内容稳定更新后自动关闭登录助手、重载 owned sidecar，重载完成前保持 pending；同内容重写不触发重载，本地 upstream 不继承终端代理。
- 酒店账号查询：携程账号密码从跨平台进程环境或 Git 忽略的 runtime `config/.env` 读取，进程环境优先且外部 Secret 不允许后台伪删除；Owner 状态不泄漏账号原文、密码、路径或进程信息；固定 persistent profile 由进程内锁和系统文件锁跨进程串行复用，凭据更新/删除先终止旧登录助手，验证码转人工；hotel-browser 只暴露登录检查和酒店搜索，不包含预订、支付或取消能力；目的地必须模拟真实键入并选中可见的精确城市候选，忽略隐藏模板与酒店名中的同名片段。
- 页面终态：规划中和历史计划隐藏确认按钮，进入规划或完成时清理陈旧的需求对话错误；主聊天交接草稿不等待会话列表刷新。
- 结果格式兼容：小红书来源投影同时识别 snake_case 与 RedNote camelCase 的笔记卡片、标题和用户昵称，已返回的公开笔记必须形成可读筛选摘要。
- 实时状态收敛：需求回复期间可脱离旧 Session 新建独立计划；回合结束会读取权威草稿补齐交接卡，旧异步读取和晚到事件不得覆盖新工作区。
- 工作区恢复竞态：初始化生成状态或恢复轮询晚到时，若用户已经新建、打开计划或切换未完成任务，旧响应必须因 `workspaceVersion` 变化被丢弃，不得回灌会话、进度或错误。
- 完成进度：打开已保存计划时历史 `solve` 缓存不能覆盖完成态，缺少终态记录时只补一次，六个进度节点全部显示完成。

## 关键检查点

- 模型提交的 `owner_user_id` 和 `plan_id` 永远不作为可信身份；finalizer 用 ToolExecutionContext 重写。
- 酒店 POI、未开售车票和历史天气的语义由 Prompt/计划标签保留，不以实时房态、无票或预报展示。
- 小红书 Server Catalog 不存在发布、评论、点赞、收藏或删除 Tool。
- API key、Cookie、Authorization 和 Token 字段不能进入 TravelPlanV1。
- Finalizer 在严格校验前确定性移除证据 URL 中的 key、token 等凭据查询参数，保留公开路线参数，避免模型为同一安全错误反复重提整份计划。
- 12306 没有晚于末日行程加 60 分钟缓冲的返程车次时，选择同站优先的最晚真实车次，并保持活动顺序和原时长整体前移末日安排，不让真实车次合并反复覆盖模型修正。
- 12306 往返查询均成功但模型只提交一个铁路方向时，结构化合并从已解析真实结果补齐缺失方向及独立证据，不因长上下文抄漏而拒绝最终计划。
- Finalizer Tool 入参允许高德风格的纯数字 `walking_distance` 字符串并立即转为 number；真实公交方案可能包含超过 2 公里的景区接驳步行，协议按 0–50000 米保存真实值，带单位或任意文本继续拒绝。
- 高德城市 geocode 的 `return` 包装必须展示行政区与坐标；超长公交结果插入 `[truncated middle]` 时，只从保留的 route envelope 读取总距离，禁止把内部 155 米步行 step 当成全程。
- 小红书首搜成功后可额外读取恰好一篇已选笔记详情用于摘要；详情读取不算关键词重试，第二篇详情仍由独立预算拒绝。
- 来源 ToolResult 只持久化无 Secret 的真实生效查询参数，历史恢复优先使用它，保证关键词归一化后刷新前后一致；详情 token 等敏感参数不得进入 Session。
- 真实外部 smoke 位于 `tests/integration_test/travel`，只有显式环境变量开启；缺少凭据时默认跳过。
- Prompt 与 Skill 同时列出 TravelRequestV1、EvidenceItemV1 精确 allowlist，避免模型在长响应后依赖 schema 错误逐字段猜测。
- 非 model_estimate evidence 的 source URL 缺失时，finalizer 错误正文与 metadata 都包含安全字段路径，供唯一一次定向修正使用。
# 2026-08-15 来源守卫复测补充

- 验证 12306 站码辅助查询与往返车票查询使用独立预算，正常站码准备不会挤占两次车票查询。
- 验证高德地理编码允许最多 16 个不同地点，第 17 个不同地点被预算守卫拒绝，完全重复参数仍优先去重。
- 验证旅行 Prompt 与 Skill 明确要求复用真实 `station_code`，并禁止把高德跨城公交冒充铁路或放入 optimizer 日内路线。
- 前端对应测试验证 internal 守卫完成事件会撤回同 `tool_call_id` 的运行中进度，不留下永久“正在查询”。

## 2026-08-15 第四轮真实 E2E 收敛补充

- optimizer 的每个 `feasible_candidates` 同时返回有界 `itinerary` 与 `budget_items`；候选评审内部持久化这些字段，但发给前端卡片的 RuntimeEvent 必须剥离内部骨架。
- 候选前只暴露三路研究 Profile，候选选择后只暴露住宿/路线最终化 Profile；两阶段严格互斥，optimizer 的受信结果由旅行 Provider 直接保存候选评审，不再依赖模型复制数组。
- 候选求解不因地图类别留待所选路线阶段而重复整批研究；候选研究完成后隐藏 `delegate_tasks`，选择后则只在地图/住宿补缺完成前暴露一次最终化委派，完成后只暴露 finalizer。
- 最终化 Child 已完成但 Finalizer 返回字段或证据错误时，自动续跑复用历史 Child 结果并只重试 Finalizer，不得因进入新 Turn 再次要求委派。
- 用户总预算作为硬约束时，候选预期总价不得超过预算；上界超出只保留风险提示，不能推荐预期价格已超预算的方案。
- 候选仅因餐饮、市内交通等可调预算 expected 小幅超限时，optimizer 可在原 lower–expected 区间内压缩到硬预算并保留 upper 风险，不得修改高铁、门票、酒店等固定事实。
- finalizer 从所选候选继承日期、区域、活动顺序/时间和预算；最终高德路线时长、距离变化不触发候选不匹配，也不得为了候选汇总值凑路线数字。
- 超过 12306 十五天预售窗口的车票查询在本地返回 `not_on_sale` 和 `sale_open_date`，不调用远端；窗口内、去重、独立预算和 Session 隔离继续生效。
- 12306 站码辅助 Tool 的完成进度为 internal；未开售车票展示预计开售日，不显示成远端错误或无票。
- 来源账本只记录是否取得过高德公交线路，不保存结果正文；取得线路后，完全没有 `amap_transit + transit_legs` 的最终计划必须被 `TRAVEL_TRANSIT_EVIDENCE_MISSING` 拒绝。
- 候选研究和最终方案细化各有一次有界的酒店、坐标和本地路线预算；首次选择候选只重置细化类计数，保留来源成功状态与重复指纹，重复进入最终化不会再次扩容。
- 候选卡证据覆盖率由服务端按当前 Session 已配置且真实成功的来源类别计算；模型自报的 `evidence_coverage` 不能把已查询来源显示成 0%，也不能虚增覆盖率。
- 行程位于 16 日预报窗口内且 forecast Tool 可用时，只查历史天气会被拒绝；forecast 成功后最终天气必须为 live。Tavily/小红书返回可用结果时，最终计划必须分别保留网页文章/社区笔记的标题、链接与简短筛选摘要。
- 旅行来源包装层会压缩高德公交、POI 和 geocode 返回体：保留坐标、距离、耗时、线路、上下车站和途经站，移除逐步步行说明、polyline、图片等大字段，避免多轮最终化因上下文膨胀连续触发 LLM 超时。
- 多日住宿完整性：未明确免住宿的多日计划不得以空 `stay_recommendations` 保存；高德酒店关键词使用独立保留额度，景点搜索耗尽后仍可查询具体酒店；明确住亲友家等场景不误伤。
- 天气来源完整性：天气来源成功后，每条最终天气摘要必须保留 provider 与非 unknown freshness，否则以 `TRAVEL_WEATHER_EVIDENCE_MISSING` 拒绝并要求复用已有结果。
- 进度路线单位：公交响应只有嵌套步行段距离、没有路线级总距离时不展示误导性总距离；仍展示路线级时长、线路及上下车站。
- 路线与铁路时间完整性：不少于 2 公里的公交/地铁段不得用 `planning_estimate` 保存，必须保留高德线路和上下车站；去程到达不晚于首日首活动，返程发车不早于末日末活动。
- 来源缺口反馈：`TRAVEL_RESEARCH_INCOMPLETE` 必须在内部错误中列出具体 missing/retry category，模型只补相应来源，不能在不知道缺口时原样重提 finalizer。
- 携程目的地下拉的精确城市文本即使父节点还包含省州说明也必须可点击，并通过输入框回填确认；城市解析失败不是认证类永久失败，允许一次有界窄化重试。
- Tavily/小红书安全引用进入来源账本前必须移除 URL 中 token、cookie、secret 等凭证型查询参数；小红书保留公开 `/explore/{note_id}` 路径，不把 `xsec_token` 复制到最终计划。
- 失败方案点击继续后，等待计时从本次 `retry-finalizing-*` 事件重新计算，不沿用首次候选最终化的旧 `finalizing-*` 开始时间。
- 旅行时间线把 `walking_distance` 解释为米：`742` 显示 `742 米`，`1200` 显示 `1.2 公里`。

## 2026-08-16 并行研究与城市一致性补充

- 旅行内置 Profile 将铁路能力收窄为城市站码与车票，保留全部 Open-Meteo，避免工具发现上限挤掉天气；候选住宿只允许一次携程和有界高德文本/详情查询。
- 候选选择后使用 `travel-final-stay` 与 `travel-final-route` 两个独立快速 Child，并行补一处具体住宿、价格状态、目标城市坐标和必要公共交通线路。
- Child 工厂仍可见来源 Tool；Subagent 可用时父 Agent只可见旅行领域动作、Skill 与 `delegate_tasks`，不得在 fan-in 后串行重查外部来源。
- 高德文本搜索与 geocode 携带显式城市时，只保留 province/city/district 等字段命中目标城市的候选；全部为异地同名结果时返回 `TRAVEL_MAP_CITY_MISMATCH`，不得把错误坐标传给计划。
- 高德 0.0.8 文本搜索在显式城市时由旅行边界补入 `citylimit=true`；景点词若只命中酒店、餐饮或停车场，返回 `TRAVEL_MAP_POI_MISMATCH`，不得把弱相关 POI 当成景点。
- 酒店来源在候选研究与最终化阶段各允许一次有界查询；候选阶段的成功不能把最终阶段核验挡成 `TRAVEL_SOURCE_ALREADY_SATISFIED`，同一最终化阶段仍禁止重复。
- 12306 长结果从已返回片段提取全天有界车次；最终化应选择首日活动前抵达的去程和末日活动结束后至少预留 60 分钟的返程，不得因列表头部是凌晨车就制造行程冲突。
- 酒店 POI 与携程价格只能按严格名称合并，禁止把相似酒店的地址与价格拼接；铁路总价按 `travellers[].count` 计算；已有小红书/Tavily evidence 时删除相互矛盾的“未查到” unknown。
- 前端对 `delegate_tasks` 完成事件始终使用“并行旅行资料已汇总”，即使后端默认标题包含内部工具名也不得展示。
- 12306 站码成功不能替代票务查询；候选研究必须记录去程和返程两次 dated ticket attempt，`not_on_sale` 同时保留查询日期与 `sale_open_date`。
- Open-Meteo `geocode_place` 只解析位置，不能替代 forecast/historical 天气数据尝试，也不能单独把 weather 标记为成功。
- finalizer 在两次 12306 查询成功后必须保留两个铁路选项及对应 12306 evidence；未开售 evidence 允许估算字段为空，但不得伪造车次、时刻、席别或票价。
- 不少于 2 公里的本地段即使把 mode 改写成“规划估算”，也不能绕过高德公交线路与站点门槛；远郊景点返程缺失会返回 `TRAVEL_ROUTE_EVIDENCE_MISSING`。
- 候选阶段已尝试地图和住宿后，选择候选必须开启独立 finalization attempt 集；重启恢复已选择任务时仍先只暴露一次住宿/路线双 Child 委派，两类本阶段尝试完成后才暴露 finalizer。
- `delegate_tasks` 在创建 Child 前必须校验完整固定批次：候选阶段恰好包含交通天气、住宿景点、攻略三个互异 Profile，最终化阶段恰好包含住宿与路线两个互异 Profile；缺失、重复或混入其它 Profile 均返回 `TRAVEL_SUBAGENT_BATCH_INVALID`。
- 小红书或 Tavily 的 MCP 成功结果即使嵌套在外层 `output` 字段中也必须识别为成功行，不得误记为需要窄化重试；最终化阶段只检查当前地图/住宿 expected 集合的重试项。
- Tavily 进度卡使用旅行守卫归一后真正执行的查询词，不继续展示模型提交的多景点组合词；未来 16 日预报窗口内的历史天气请求在远端调用前被拒绝，且不消耗天气尝试状态。
- 高德长公交结果若被通用层截成中段片段，进度卡只保留可辨认的线路与上下车站，不把片段中的步行距离或耗时冒充整段路线总量。
- `travel-final-route` 的高德公交 ToolResult 在进入 Child 下一轮上下文前只保留一条步行不超过 2 公里的最佳方案、线路名、上下车站、时长/距离与最多八个途经站；逐步步行说明和备选线路必须移除。
- 旅行 Child 不复用 `routing.compaction`；显式存在 `role=fast` endpoint 时按 Profile 选择快速模型，否则继承当前会话主模型。父级最终完整 JSON 编排保留当前主模型，并仍必须经过 finalizer 全部结构与证据门槛。
- 同一 `role=fast` 存在多个 enabled endpoint 时按较小 `priority` 选择，配置顺序只作为同优先级稳定顺序。
- 持久进度识别住宿/路线双 Profile 的最终化 `delegate_tasks`，刷新后分别恢复 lodging/transport Lane；运行态恢复 selected candidate review 后，在新 WebSocket 事件到达前也立即显示两条 running Lane。
- 泛化住宿偏好（如“舒适型、位置便利、中低价位”）不会冒充酒店名发送给携程；具体酒店关键词继续保留。
- 高德城市防护接受大理市、大理白族自治州和州内县级别名，同时继续拒绝博乐等异地同名结果。
- 来源账本保存有界酒店观察、POI 和公交路线快照；Finalizer 能把同名携程观察价及同坐标高德公交线路确定性合并进计划，替换模型估算。
- 候选方案按钮点击后在请求仍未完成时即滚动到最终化进度区域，不等待后台长耗时结束。
- 旅行基调是确认前必填条件；经济实惠、舒适均衡、轻松品质分别投影为约 250、450、700 元每晚上限和对应交通/节奏默认值，用户显式偏好不被覆盖。
- optimizer 对短途最多保留三个真实差异候选；景点换序或指标差异不足的候选收敛为一个；中等天数通常两个，长途核心覆盖充分时一个。
- 单候选由服务端自动选择并在当前回合结束后自动进入最终化；多候选仍等待用户，不产生双重最终化。
- 最终保存不删除候选评审；计划刷新后通过 source Session 恢复窄决策记录和只读原方案对比，删除计划时再清理候选记录。
- 恢复已选候选时必须根据父 `delegate_tasks` 的 Profile 与结果中的 `child_session_id` 区分候选 Child 和最终化 Child；重放候选住宿/地图结果不得把最终化 attempt 提前标成完成，也不得因此隐藏住宿/路线双 Child 委派。
- Tavily 的 MCP 输出即使在通用尾部截断前只保留了完整的前几条 `results`，旅行边界也要恢复并压缩这些完整行，使进度卡展示标题与摘要并保存安全引用，不显示“当前格式暂无法生成摘要”。
- 确定性替换返程车次时优先保持原计划的重庆北/重庆西等具体车站；价格变化必须同步调整铁路预算项和总预算，不允许车次合计与预算卡继续沿用旧金额。
- 最终路线 Lane 只有实际尝试高德公交路线后才算完成；文本搜索、详情和 geocode 只能补锚点，不能提前隐藏最终化委派。`travel-final-route` 提供 12 次有界工具迭代，避免多锚点计划在首次公交调用前撞上 6 次上限。
- AgentLoop 因 Child 工具迭代耗尽而生成的 `TOOL_ITERATION_LIMIT` 占位 ToolResult 不是真实高德调用，不计入 finalization route attempt；跨重启恢复后仍应允许路线 Child 重试。
- 天气修复闭环：服务端按北京时间注入 16 日 forecast 窗口；候选误用历史天气并被 finalizer 返回 `TRAVEL_WEATHER_FORECAST_REQUIRED` 后，只开放一次 `travel-final-weather` 定向补查，forecast 成功后同一轮重新开放 finalizer，禁止重复铁路、住宿、路线、网页或社区研究。
- 路线修复闭环：finalizer 返回 `TRAVEL_ROUTE_EVIDENCE_MISSING` 后只开放一次 `travel-final-route`，用独立的最多 16 段修复预算补齐缺失的本地公交/地铁段；重试入口不得先重复普通住宿+路线双任务。
- 远郊景区中心 POI 的高德公交返回 `transits=[]` 时，路线 Child 只允许改查同景区游客中心、主入口、售票处或接驳点一次；仍为空则以高德驾车距离和时长生成透明的出租车/网约车兜底，不伪造公交线路或站点。
- WebSocket 当前旅行 Turn 收到 `done/error/stopped` 后必须立即用 Generation API 的持久化终态覆盖本地推断；路线修复期间只恢复交通 Lane，已完成住宿 Lane 不得回退为运行中。
- 最终模型写出的酒店名若与携程观察候选不一致，单住宿计划只允许确定性改用“携程名称与高德酒店 POI 严格同名”的最低观察价候选；缺少同名双来源时继续拒绝保存，不能把相似酒店的价格与地址拼接。
- 候选选择锁定日期、地点和活动身份，但不能在 Finalizer 已按真实 12306/携程价格归一后再次用候选阶段估算覆盖预算；最终预算项和总额必须保留来源账本校正后的金额。
- 候选住宿景点 Child 允许六轮有界工具迭代和 150 秒，足以覆盖一次酒店与三个 POI 的单次窄化，但每个缺失 POI 只窄化一次；攻略 Child 允许 180 秒容纳小红书首次空结果后的唯一短词重试，重试词必须是“城市 + 单景点/主题”且最多六条，不能再次携带多景点组合。
- 最终住宿任务由服务端附带本 Session 最近两次有界携程观察；本轮 dated 查询若发生城市下拉偶发失败，Child 必须复用候选阶段有观察价的准确酒店名继续高德身份核验，不能因重查失败把住宿卡整个删除。
- 携程准确酒店名在高德无严格同名 POI 时，最终住宿 Child 最多改试另外五个不同的观察候选，恰好受 1 次携程 + 6 次高德的七轮硬上限约束；优先普通品牌名，选首个高德可核验酒店，不得改写别名或拼接相似酒店。
- 最终化住宿高德严格同名检索前 6 次均可执行，第 7 次才返回来源预算耗尽；普通景点检索独立保留 18 次预算，携程最终化查询仍只允许一次成功调用。

## 2026-08-17 查询规则与最终选择交互补充

- 小红书首次搜索由旅行边界确定性收敛为“目的地旅游攻略”，只有首次真实空数组时才允许一次“单景点攻略”重试；认证、超时、限流或已有结果均不得换词连搜。
- 小红书 camelCase 长 feed 在 MCP 边界先删除封面、头像等大对象，最多保留 6 条标题、作者、短摘要、公开链接与内部详情 token；压缩后必须仍是合法 JSON，不能因通用字符截断把真实结果误报为 0 条。
- 高德搜索、详情与路线使用独立预算；同一进程的高德调用经过共享最小间隔门控，遇到 `CUQPS_HAS_EXCEEDED_THE_LIMIT` 只做一次有界等待重试。
- 高德 `citylimit=true` 返回缺少 province/city/district 等显式行政字段时保留；只有显式行政字段确认异地才返回城市不匹配，普通街道地址不能作为拒绝依据。
- 旅行基调三选一卡仅在出发地、目的地、有效日期与人数全部齐全后出现；卡片先提示可补充预算、交通、住宿、兴趣、节奏和硬约束，单击后立即折叠为一句选择记录并只触发一次正式规划提交。
- 旅行最终页、进度卡、时间线和证据抽屉必须把高德、携程、小红书、估算与时效代码转换为用户可读中文；未知内部英文标识不得原样暴露。
- Tavily 组合查询收敛为单一目的地攻略意图，摘要剔除英文点赞、订阅和登录模板；天气同名地点在行政区域校验前不展示，天气 Profile 优先复用高德城市坐标。
- 高德公交响应中的 `distance` 按米解析，合并到最终计划时除以 1000 转为公里，禁止把 1245 米显示为 1245 公里。
# 2026-08-17 候选研究持久化恢复

- 完整的三路候选研究 `delegate_tasks` 调用与 `completed/OK` fan-in 结果可作为跨 Turn、跨进程恢复的完成事实。
- 任一 Child 失败、缺失或批次为 partial 时不得误判为完成，仍允许按受控流程补齐。
- 进程内来源账本丢失后，续跑提示必须进入 optimizer，不得再次委派相同的三路研究。
- MCP 外层执行成功但小红书上游 payload 为 error 时，进度必须展示“暂未取得结果”，不得误报为“空结果”。
- 最终计划中的 `metro`、`bus`、`coach+bus`、`taxi` 与 `amap_driving_fallback` 必须转换为中文展示，不暴露内部枚举。
- 小红书上游页面 `ERR_CONNECTION_CLOSED` 时只允许用完全相同的关键词做一次传输恢复；不得把它当作空结果，也不得改写为另一组搜索词。

# 2026-08-17 最终化 Lane 完成与恢复

- 最终住宿/路线 Child 的 fan-in 结果只有在对应 task 为 `completed/OK` 时才映射为 lodging/maps 完成；failed、timeout、cancelled 不得误标。
- 最终住宿复用候选携程观察、没有产生新的 lodging ToolResult 时，成功 Child Lane 仍能满足最终化编排门槛。
- 同一 Turn 的双 Lane 完成后，外层工具定义立即从 `delegate_tasks` 切换为 `finalize_travel_plan`。
- 父 Session 的持久化调用参数与 tool fan-in 可在重启后恢复完成类别；partial 结果只恢复成功项。
- 显式 Lane 标记只接受最终化允许类别，并且在 finalization budget 开启前不会提前生效。
- 接待 Tool 对当前用户 Turn 做核心字段落地校验：仅说“我想去洛阳玩”时保留洛阳目的地，拒绝模型臆造的洛阳出发、当天日期、1 人、100 元和 Schema `string` 占位，避免基调卡提前出现。
