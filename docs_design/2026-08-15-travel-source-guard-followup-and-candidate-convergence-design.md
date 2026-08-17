# 旅行来源守卫复测与候选收敛修正设计

## 背景

第三轮真实 E2E 使用“北京到沈阳 3 天 2 晚”完整走过需求确认、外部查询和候选求解。运行日志证明重复查询指纹和来源预算守卫已经实际生效，但也暴露出三个新的联动问题：

- 12306 站码辅助查询与往返车票查询共用 `transport=2`，站码查询消耗一次预算后，返程车票被错误拦截。
- 守卫结果在 post-tool 展示中标记为 internal，但对应的 `tool.started` 已经进入前端进度列表，完成事件被忽略后留下“正在查询”的悬空记录。
- 模型把跨城高德公交结果写入 optimizer 的日内路线，并同时遗漏跨日区域切换路线，造成 `CROSS_CITY_ROUTE_MISSING`、`DAILY_TIME_LIMIT_EXCEEDED` 和 `DAILY_INTENSITY_EXCEEDED`，候选连续两次无法通过。

第四轮重启后又确认，Gateway 仍从 `${ZHICE_AGENT_WORKSPACE}/prompts` 读取初始化时期的旧旅行 Prompt，仓库 Prompt 修正不会随普通重启进入运行态，导致确认话术和新候选边界都实际未生效。

第四轮最终方案复测进一步暴露出候选收敛协议的问题：候选评审只持久化地点列表、预算总额和路线总分钟/总公里，丢弃了 optimizer 已校验的活动时间与分段路线。finalizer 因而只能重新生成分段并用总数做严格相等校验，真实记录中连续两次失败，第三次通过时实际是在凑出 `155` 分钟和 `22.5` 公里。这既增加无意义重试，也会诱导模型用 `planning_estimate` 覆盖已经查询到的高德公交线路。同期页面还把 `walking_distance` 的米值直接标成公里，并把 12306 站码辅助查询当成车票查询展示；超过 15 天预售窗口的车票请求继续打到远端后触发其 JavaScript 空值异常。

第五轮使用预售和天气窗口内日期再次真实执行后确认：模型仍可能跳过站码 Tool，直接凭记忆向 `get-tickets` 传 `IFP/SYT`；高德 POI 已含坐标时，旧 Prompt 仍驱动模型逐个 geocode，产生跨省误匹配、限流和预算耗尽；`maps_geo` 的 `return[]` 成功结果在进度中被误写成“无可展示候选”；12306 MCP 返回可读文本车次表而非 JSON 时，页面也无法提取车次摘要。optimizer 的内部骨架字段还可能在 LLM 转抄候选摘要时被省略，因此候选 Tool 必须兼容这一传输降级，不能让用户看到内部 Tool 名。

## 目标

- 12306 往返车票各允许一次真实查询，站码辅助查询使用独立预算；完全重复参数仍立即拦截。
- 高德地理编码预算覆盖三日行程的景点、酒店和车站坐标，同时继续阻止重复调用和无界扩张。
- internal 守卫结果到达时删除对应的前端运行中记录，不向用户暴露内部错误，也不留下伪运行状态。
- 明确 optimizer 只接收目的地内的日内/跨日接驳路线；跨城铁路进入顶层交通方案，不使用高德公交冒充铁路。
- 12306 车票 Tool 只能使用站码查询返回的真实 `station_code`，禁止模型自行猜测站码。
- 候选评审在内部保存 optimizer 已校验的完整活动与路线骨架；用户卡片仍只展示有界摘要，finalizer 以服务端骨架为准合并模型补充字段，不再靠模型凑路线总数。
- 超过 12306 预售窗口的查询在本地归一为 `not_on_sale`，不调用会崩溃的远端接口；站码辅助过程不进入用户可见进度。
- 前端按米/公里正确展示步行接驳距离；真实高德公交结果必须保留线路号、上下车站和途经站，不能降级成无来源的规划估算来绕过校验。

## 范围边界

- 不修改 AgentLoop 的通用工具调度语义。
- 不取消来源守卫，也不通过无限增加预算掩盖模型重复调用。
- 不改变 12306、高德或其它 MCP 服务本身。
- 不在 optimizer 中放宽预算、时间、强度等硬门槛；修正的是输入边界和路线语义。
- 本轮不宣称酒店账号观察价已可用；无酒店价格来源时仍明确降级为规划估算。

## 模块设计

### 1. 来源预算细分

`source_operation()` 将铁路来源拆为：

- `transport_lookup`：当前日期、城市/车站站码等辅助查询；
- `transport_ticket`：`get-tickets` 等真实车票查询。

车票预算固定为 2，对应去程和返程各一次。辅助查询独立计数，避免正常准备步骤挤占车票预算。高德地理编码预算从 10 调整为 16，覆盖三日行程常见的 6～10 个活动地点、酒店和交通枢纽；完全重复指纹仍优先拦截。

### 2. internal 完成事件收口

旅行前端收到 `tool.completed`、`tool.failed` 等 internal 事件时，按 `tool_call_id` 删除此前记录的同一 `tool.started` 项并立即持久化。`tool.started` 本身仍保留，确保真实慢查询能持续显示；只有最终确认被守卫内部拦截时才撤回。

### 3. 候选与跨城交通边界

旅行规划 Prompt 和 travel-planner Skill 说明统一补充：

- optimizer 的 `days[].route_segments` 表示目的地内相邻活动、酒店和交通枢纽之间的本地接驳；区域切换必须有对应路线段。
- 北京到沈阳这类跨城铁路只进入最终 `transport_plan` 及铁路 evidence，不把高德跨城公交结果作为高铁路线，也不把 800 公里级距离塞入日内强度计算。
- 12306 先查站码，随后 `get-tickets` 必须原样使用返回的 `station_code`；去程和返程各查询一次，不得猜测 `SHE` 等未返回站码。
- optimizer 首次失败后读取稳定拒绝原因定向修正一次；第二次仍失败必须终止本轮，不继续盲目续跑。

### 4. 代码耦合旅行 Prompt 同步

Gateway 构建 Web runtime 前，把仓库内三个代码耦合的旅行 Prompt 原子同步到运行 workspace：`travel_intake.md`、`travel_planning.md`、`travel_planning_continuation.md`。这些文件与旅行 Tool、schema、候选阶段协议共同演进，不能保留初始化快照；`identity.md`、通用 Tool 策略和其它用户可定制 Prompt 不在覆盖范围。

### 5. 候选骨架内部继承

optimizer 的 `feasible_candidates` 为每个候选增加仅供服务端使用的 `itinerary` 和 `budget_items`。候选评审 Tool 对这些字段做有界归一化后持久化，但发给前端的 `travel_candidates` 事件会剥离内部骨架，只保留现有卡片摘要。

用户选择后，finalizer 不再要求模型重建并碰巧匹配路线总数，而是按所选候选在服务端覆盖真正代表用户选择的稳定字段：日期、区域、活动起止时间与地点、每日预算及总预算项。候选路线骨架继续内部保存，供模型和审计了解原候选，但路线总分钟/总公里不再作为候选身份；它们必须允许被后续取得的高德真实路线替换。模型负责补充原因、坐标、证据引用、公交线路细节、天气调整和备选方案。合并后继续执行完整 `TravelPlanV1` 校验，并按日期、区域、活动顺序和预算再次校验候选一致性，因此用户选择门控没有被取消，只有错误的“凑路线总数”条件被移除。

### 6. 12306 未开售与辅助进度

旅行来源包装层仅对 `get-tickets` 类车票查询检查日期。按 15 天预售窗口（含查询当日）计算，超过 `今天 + 14 天` 时直接返回结构化成功结果：`status=not_on_sale`、旅行日期、预计开售日和明确复核提示；不消耗远端连接，也不把远端实现异常暴露给用户。站码和当前日期 Tool 仍正常执行、记入独立预算，但 post-tool 展示为 internal，前端收到终态后移除对应 started 记录。

### 7. 路线证据与距离单位

来源账本只额外保存“本 Session 是否取得过含公交线路的高德公共交通结果”这一布尔事实，不保存原始结果正文。当该事实为真时，finalizer 至少要求最终计划保留一段带 `transit_legs` 的高德公交路线，阻止模型把已取得的线路号和站点全部降级成 `planning_estimate`。`walking_distance` 的协议单位统一解释为米，前端小于 1000 时显示米，大于等于 1000 时换算成公里。

### 8. 站码来源与坐标复用

来源账本从成功站码 ToolResult 中只提取三位大写站码集合，不保存结果正文。`get-tickets` 使用 `fromStation/toStation` 时，两端代码都必须已出现在当前 Session 集合中，否则返回 internal 的 `TRAVEL_STATION_CODE_UNVERIFIED`，且不访问远端、不消耗车票预算。模型随后 discover/调用站码 Tool，再复用原值。

高德文本和详情检索已有 `location` 时作为正式 POI 坐标复用，只对缺坐标的最终活动调用 geocode；解析省市与目的地不一致时丢弃。规划必须至少核对抵达站到酒店/首个活动、最后活动到返程站的本地接驳，避免只查询同街区短步行而遗漏真正需要线路号的公交段。

### 9. 结果摘要兼容

高德 geocode 的 `return[]` 映射为“省市区街道 + 坐标”的有界候选，错误城市会直接在页面可见。高德路线把米/秒转换为公里或米/分钟，并在存在公交线路时展示第一条线路与上下车站。12306 文本表格解析前五条车次、站名、时刻和历时。候选审核失败使用产品话术，不展示 `request_travel_candidate_review`；optimizer 骨架字段存在时持久化并继承，传输层省略时仍以日期、区域、活动有序列表和预算摘要完成强制候选身份校验。

### 10. 候选确认后的地图细化预算

第八轮真实 E2E 证明 `maps_route=8` 的 Session 级预算会被候选研究阶段消耗，用户确认方案后最终化仅成功补到两条路线，后续四条正常的高德公交请求被误判为预算耗尽。修正后，首次候选确认会为酒店账号/酒店 POI、缺失坐标和本地路线开启一次新的有界细化预算；来源成功状态、已取得公交证据和调用指纹全部保留，因此相同参数仍返回 `TRAVEL_SOURCE_ALREADY_QUERIED`。该阶段只允许开启一次，重复提交候选选择不能反复扩容，也不重置 12306、天气、Tavily 或小红书预算。

同轮复测还出现“地图、铁路、天气、网页和社区均已成功，候选卡仍显示证据 0%”。候选 `evidence_coverage` 因此不再信任模型或 optimizer 输入，而由服务端按当前 Session 的 `successful ∩ expected / expected` 计算；没有注册任何外部来源的纯单元场景才保留原值。该比率只表示来源类别覆盖，不宣称所有价格和房态都已实时核验。

第九轮完成页进一步证明 Prompt 约束不足：行程日期距当前仅 5～7 天，模型仍误称超出 16 日窗口并只调用历史天气；小红书实际返回 5 条笔记、Tavily 也成功，最终 evidence 却完全丢弃两类结果并显示“社区经验暂未补充”。来源账本现在额外记录 forecast 是否已配置、尝试和成功。预报窗口内未尝试 forecast 时拒绝 finalizer，forecast 成功后 weather_summary 必须全部为 live。网页或社区类别成功时，最终 evidence 分别必须含 web_article/Tavily 或 social_post/小红书记录，保留 1～3 条筛选后的标题、来源链接和短摘要，禁止用 unknowns 抹掉成功结果。

同一轮最终化连续三个 Turn 在 240 秒 Provider 上限触发超时。量化 Session 后确认，11 条高德公交结果每条约 13.5k 字符，仅路线正文就超过 148k 字符；其中大部分是逐步步行 instruction、polyline 等最终计划不使用的展示字段。旅行来源包装层因此在回填 LLM 前压缩高德结果：公交保留总距离/耗时/步行距离、线路名、上下车站和途经站；POI 保留名称、地址、坐标和行政区；geocode 只保留前 8 个地址坐标候选。MCP 结果观察器仍先读取原始结果更新来源账本，压缩不改变成功判定和公交证据标记。

## 数据流

```text
站码 Tool -> transport_lookup 预算
往返车票 Tool -> transport_ticket 预算（2 次）
重复/超限 -> internal ToolResult -> 前端移除同 tool_call_id 的 started 项

高德 POI/地理编码/日内路线 -> 目的地本地事实
12306 往返铁路 -> 顶层 transport_plan
本地 route_segments + activities + budget -> optimizer -> 两个可行候选卡
optimizer 完整候选骨架 -> 服务端候选缓存（内部）
用户选择 + 模型展示/证据字段 -> 服务端合并骨架 -> TravelPlanV1 校验
超预售窗口 get-tickets -> 本地 not_on_sale（不访问远端）
```

## 变更文件

- `agent/applications/travel/source_ledger.py`
- `agent/applications/travel/service.py`
- `agent/applications/travel/tools.py`
- `agent/applications/travel/progress.py`
- `agent/config.py`
- `agent/app/runtime.py`
- `prompts/travel_planning.md`
- `prompts/travel_planning_continuation.md`
- `skill_repo/skills/travel-planner/SKILL.md`
- `skill_repo/skills/travel-planner/scripts/optimize.py`
- `web/frontend/src/stores/travel.ts`
- `web/frontend/src/components/travel/TravelTimeline.vue`
- `tests/unit_test/travel/test_source_ledger.py`
- `tests/unit_test/config/test_config.py`
- `tests/unit_test/travel/test_prompt_contract.py`
- `web/frontend/src/stores/travel.test.ts`
- `web/frontend/src/components/travel/TravelTimeline.test.ts`
- `tests/unit_test/travel/test_case.md`
- 本设计记录和 Part 19 当前活文档

## 测试方案

### 住宿与天气最终完整性补充

真实第五轮 E2E 发现最终计划可以同时出现“住宿 2 晚预算”和空 `stay_recommendations`，且成功的 Open-Meteo 结果在页面显示为 `UNKNOWN · —`。当前方案补充两道最终化门槛：未明确免住宿的多日计划必须包含具体酒店身份、地址、坐标和身份来源；成功天气摘要必须保留 provider 与 freshness。高德酒店关键词另设 3 次保留额度，避免景点与详情查询抢占住宿发现能力。接待阶段同时禁止在正式查询前输出 A/B/C 逐日预方案，避免与真实候选卡重复。

第六轮 E2E 随后证明住宿和天气来源已补齐，但模型仍可把全部市内地铁写为规划估算，并选择与首末日活动重叠的真实列车。方案因此继续增加：不少于 2 公里的公交/地铁段必须保留真实高德线路和站点；去程列车到达不得晚于首日第一项活动，返程列车发车不得早于末日最后一项活动。二者都在服务端最终化前校验，拒绝后要求复用已有车次或只补缺失路线。

第七轮 E2E 中 finalizer 连续两次返回通用 `TRAVEL_RESEARCH_INCOMPLETE`，模型因不知道具体缺少 lodging 而原样重提。错误反馈现改为明确列出 `Missing categories` 或 `Retry categories`，仅供内部模型恢复使用，不进入用户进度文案。

- 单元测试：站码查询不消耗车票预算；第三次不同车票查询被拦截；不同 Session 相互独立。
- 单元测试：高德地理编码第 16 次允许，第 17 次拒绝；重复参数始终优先返回 already queried。
- 前端测试：internal 完成事件会删除同 ID 的 started 项，不影响其它已完成来源记录。
- Prompt 合同测试：站码必须来自真实 ToolResult、跨城铁路不得进入 optimizer 日内高德路线。
- optimizer/Tool 测试：可行候选携带完整内部骨架，前端候选事件不泄露骨架；最终方案自动继承所选候选的活动、路线与预算稳定字段。
- 来源包装测试：超过预售窗口返回 `not_on_sale` 且不调用 delegate；窗口内照常调用；站码结果为 internal。
- finalizer 测试：取得高德公交线路后，完全缺失 `transit_legs` 的最终计划被拒绝；规划估算和无公交结果不误伤。
- finalizer 测试：空的多日住宿被 `TRAVEL_STAY_REQUIRED` 拒绝；天气来源成功但 provider/freshness 缺失时被 `TRAVEL_WEATHER_EVIDENCE_MISSING` 拒绝。
- 来源预算测试：10 次普通高德搜索耗尽后，酒店关键词仍可使用独立住宿保留额度。
- 接待 Prompt 测试：确认前不得生成 A/B/C、逐日行程或无来源预算拆分。
- 前端测试：`742` 显示为 `742 米`，`1200` 显示为 `1.2 公里`。
- 运行 Ruff、旅行单测、前端 ESLint/Vitest/TypeScript/build；完成后由用户重启 Gateway，再进行第四轮真实 E2E。

## 验收标准

- 日志中最多出现两次实际 `get-tickets` 远端调用，站码 Tool 不挤占这两次预算。
- 重复/超限调用日志可见守卫错误，但页面不出现内部错误卡，也没有永久“正在查询”。
- optimizer 能生成至少两个可行候选；候选不再因 800 公里跨城高德路线导致日强度超限。
- 最终高德公交路线只有在含线路号、上下车站时标记为高德公交；否则明确降级为规划估算。
- finalizer 首次调用即可继承用户所选候选，不再因为路线总数变化出现 `TRAVEL_CANDIDATE_SELECTION_MISMATCH`，也不会为匹配总数改造已查询路线。
- 超过预售窗口时页面显示“尚未开售”和预计开售日，不出现远端 `Cannot read ... result`；站码辅助查询不显示为“铁路 12306”。
- 步行接驳距离不再把米误标为公里。
- 住宿地点证据与价格证据继续分离，规划估算不引用景点或普通 POI。
