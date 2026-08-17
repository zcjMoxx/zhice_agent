# 旅行子 Agent 证据与进度恢复修复设计

## 背景

旅行候选与最终化已经改为子 Agent 并行执行，但真实运行暴露了四条断链：Child RuntimeEvent 使用 Child Session 作为 WebSocket 信封归属，被只关注 Root Session 的旅行页过滤；刷新恢复只投影父 Session，未读取持久化的 Child Session；搜索来源虽然成功写入子任务结果，最终计划却依赖模型再次手工复制引用，遗漏后会被硬门禁拒绝；失败任务重新打开时前端未恢复服务端仍然保留的已选候选，导致无法显示续跑入口。自动续跑还可能在最终子任务已经完成后继续要求重新委派。

## 目标

- 子 Agent 的安全查询进度实时显示在所属旅行 Root Session。
- 刷新后从父 Session 与其 Child Session 一起重建完整、脱敏、有界的旅行进度。
- Tavily 与小红书成功结果形成服务端保管的安全引用；模型漏抄时由 Finalizer 确定性补齐，不重复查询。
- 已完成最终住宿与路线委派后，后续 Turn 只复用已有结果并重试 Finalizer，不再错误要求重复委派。
- 失败任务从历史列表、Session URL 或自动恢复入口打开时，恢复已选候选并提供明确的继续完成操作。

## 范围边界

- 不向前端暴露 Child 原始 Prompt、完整 Tool 输出或模型内部文本。
- 不取消最终计划的来源、路线、住宿、天气等质量门禁。
- 不引入后台 Job、递归 Subagent 或新的数据库表。
- 搜索证据仅保留最多 3 条标题、URL 与短摘要，不持久化 Secret 或整页正文。

## 模块设计

### RuntimeEvent 归属

RuntimeEvent payload 保留真实 Child `session_id`、`turn_id` 与 Subagent 标识；WebSocket envelope 对 Child 事件改用 `root_session_id/root_turn_id`，让当前旅行页接收事件，同时不丢失技术关联信息。

### 持久进度恢复

`travel_progress_history` 在确认父 Session 所有权后，读取父 Session 以及 `${sessions}/_subagents/{root_session_id}` 下的 JSONL，按消息时间排序后交给现有安全投影器。投影器仍只返回 Tool 展示摘要，原始 Child 文本不进入 API。

### 搜索证据保管与合并

`TravelSourceLedger` 在 Tavily/小红书成功时额外保存有界、安全的引用摘要。Finalizer 在质量校验前，仅当模型计划缺少对应类别时，按目的地相关性补入最多 3 条引用；已有模型引用保持不变。Ledger 在计划成功或 Session 清理时一并清除。

### 最终化续跑

Selected Candidate 的最终化阶段只有在 `missing_attempts` 非空时才强制一次 `delegate_tasks`。住宿与路线已完成时，续跑提示明确禁止重复委派，只允许复用历史 Child 结果并调用 Finalizer。

### 失败任务候选恢复

前端以服务端 Candidate Review 为候选状态事实源。失败 Session 打开或自动恢复时查询 Candidate Review；若状态仍为 `selected`，保留 planning phase 与 validate stage，并显示“继续完成当前方案”。候选不存在、未选择或接口不可用时安全降级到原有的修改需求后重新开始流程，不阻塞历史记录打开。

### 酒店价格与城市候选恢复

Owner 携程档案已认证但大理查询失败的根因是适配器要求“精确城市文本的父节点也必须完全等于城市名”，真实下拉父节点包含省州说明，因此误删了有效候选。适配器改为点击第一个可见精确城市文本，并用目的地输入框回填值确认选择；`HOTEL_CITY_RESOLUTION_FAILED` 不再被当成永久认证失败，允许一次不同参数的有界恢复。纯“舒适型、位置便利、中低价位”等偏好词不再作为酒店名关键词发送。

最终住宿先按城市、日期和预算取得携程真实候选，再用候选的准确名称到高德核验身份；不得默认全季/汉庭等品牌或为已查到的高价酒店编造低价估算。只有价格源确实不可用时才透明降级。

### 地图行政区与结构化事实账本

高德跨城市同名防护继续保留，但大理市、大理白族自治州及州内县级行政字段按保守别名集合匹配，避免“双廊镇/洱源县”被误判为异地。正常 MCP ToolResult 在压缩后必须写入来源账本；账本只保留有界 POI、公交路线和酒店观察字段，不保存原始页面或 Secret。

Finalizer 在模型计划校验前确定性合并同一 Session 的结构化事实：同名酒店用携程观察价替换 `planning_estimate`，高德酒店 POI 补齐名称、地址和坐标；同坐标端点且步行不超过 2 公里的公交路线补齐线路、上下车站、耗时、距离和实时来源。无法可靠配对或步行过长的结果不强行写入。

### 候选选择后的进度定位

选择候选卡后，页面立即滚动到最终化进度区域，再等待后台选择请求和并行子任务；滚动只由本次点击触发，不在后续进度事件中反复抢夺用户滚动位置。

### 旅行基调与住宿上限

需求确认前必须由用户明确选择一次旅行基调，不由模型默认：

- `economy`：经济实惠，住宿每间每晚建议上限约 250 元，公共交通优先。
- `balanced`：舒适均衡，住宿每间每晚建议上限约 450 元，公共交通为主、必要时短途打车。
- `comfortable`：轻松品质，住宿每间每晚建议上限约 700 元，减少换乘和长距离步行，默认轻松节奏。

继续复用现有 `budget_level` 协议字段，避免引入同义字段；确认时由服务端把基调确定性投影到 `stay_preferences`、`transport_preferences` 和缺省 `pace`。前端在自然语言输入下方直接展示三张基调卡，并把旅行基调加入必填条件。

### 动态候选数量与真实差异

optimizer 先按景点集合重合率、预算差、通勤时长差和平均强度差去掉伪差异，再按时间覆盖压力保留一至三个：1～3 天且存在真实取舍时最多三个；4～5 天通常两个；6 天以上且一个候选覆盖 85% 以上候选并集时保留一个，否则保留两个。景点重合且指标差异不足时直接合并，不让用户选择换序方案。

每个候选摘要增加策略标签、核心取舍、独有景点和省略景点。一个候选时服务端自动选中，并由前端在当前 Turn 完成后自动开启最终化；多个候选才暂停等待用户。

### 持久决策记录

候选未选时展示完整卡片；选择后立即切换为窄决策记录，包含旅行基调、策略标签、核心取舍、预算和通勤时间。原候选对比改为只读折叠内容，所有选择按钮失效，避免最终化期间重复点击。

最终计划保存时不再删除 `travel_candidate_reviews`。计划通过 `source_session_id` 恢复需求草稿和候选记录，因此刷新或历史打开后仍能看到当时为什么选择该方案。删除最终计划时再连同对应候选记录清理；单候选自动决策显示“无需方案取舍”。

### 搜索引用 URL 脱敏

来源账本生成 Tavily/小红书引用时，统一剔除 URL 查询参数中的 token、cookie、secret、credential 等凭证型字段。小红书只持久化公开 `/explore/{note_id}` 路径；登录态 token 仅用于只读搜索过程，不进入计划证据、前端或数据库。

失败方案续跑的等待时间以最新 `retry-finalizing-*` 进度事件为准；历史 `finalizing-*` 仅保留过程记录，不再让新一轮续跑显示累计数分钟的旧等待时间。

### 真实跑测补充：高德语义防错与最终住宿阶段

当前使用的高德 MCP 0.0.8 在工具 schema 中未暴露 `citylimit`，且声明的 `types` 未实际传入 REST 请求。旅行边界层因此在显式城市文本搜索时确定性注入 `citylimit=true`；非住宿景点检索再拒绝明显属于酒店、餐饮和停车场的候选，全部类别错配时返回可重试的 `TRAVEL_MAP_POI_MISMATCH`，不得把弱相关 POI 当成景点坐标。

候选研究与最终化是两个独立证据阶段。酒店来源在候选阶段成功后，最终化仍允许一次指定候选的日期价格核验；同一最终化阶段的后续重复调用继续拒绝。最终阶段的调用指纹使用阶段前缀隔离，避免候选阶段的成功状态把最终住宿尝试永久挡住，进而造成 `delegate_tasks` 循环与 Subagent 上限耗尽。

前端已知酒店 Tool 的完成标题始终使用“携程酒店房价查询完成”，不采信后端通用的技术函数名标题，避免 `search_travel_hotels 执行完成` 暴露到用户进度。

12306 返回全天车次时可能超过通用 MCP 的 12000 字符展示上限，列表头部不能代表适合行程的返程。来源账本从已返回片段确定性提取有界车次、时间、席别和价格；最终化在 schema 校验前按行程首末活动选择兼容车次：去程必须在首项活动前抵达，返程必须晚于末项活动并默认预留 60 分钟进站时间。生成的新铁路 evidence 替换模型误选的早班车引用，避免“真实列表里有下午车但摘要只选凌晨车”的假冲突。

酒店地点合并只接受标准化名称完全相同或一方完整包含另一方，不再用“共有若干汉字”匹配相似酒店；否则保留携程返回的准确酒店名，不允许把“重庆嘉玺江景酒店”的房价拼到“重庆解放碑嘉遇酒店”的地址上。总票价按 `travellers[].count` 汇总。若账本已补回小红书或 Tavily evidence，同时删除“社区/网页资料未取得”这类与事实矛盾的 unknown，不在最终页一边展示搜索结果一边声称未查到。

真实第二轮跑测进一步发现：候选选择接口开启 finalization budget 后，Runtime 又把候选阶段 Child JSONL 全量重放，旧酒店/地图结果因此被错误计入 `finalization_attempted`，使父 Agent 看不到 `delegate_tasks`，随后续跑又误称最终 Child 已完成并诱发路线伪证据。恢复流程改为根据父 `delegate_tasks` 参数中的 Profile 和结果中的 `child_session_id` 把 Child 分成候选与最终化两组：候选组只在空账本时于预算前重放，最终化组只在预算后重放。在线已有账本不重复累计候选 attempt。

Tavily 通用 MCP 返回可能在 12000 字符处截断尾部，导致整体 JSON 不完整，但前几条结果对象仍完整。旅行边界在结果进入 Hook、Child 上下文和 Ledger 前，容错读取 `results` 数组中完整的前置对象，压缩标题、URL 与摘要为有效 JSON；不再把真实 5 条结果展示成“格式暂无法生成摘要”。小红书首轮空结果的第二次关键词只保留单个景点或主题词，避免继续使用“城市 + 景点”的过窄组合。

铁路兼容选择除首末活动时间外优先保持候选原有具体车站，防止返程从重庆北切换到重庆西后仍沿用去重庆北的公交路线。若可靠车次替换改变双人票价，Finalizer 同步调整铁路预算项及 lower/expected/upper 总预算，确保车次卡、路线终点和预算卡一致。

最终路线 Child 的 6 次工具迭代在多地点缺坐标时会全部消耗于文本搜索、详情与 geocode，尚未调用公交路线就提前结束。最终化 `maps` attempt 因此收紧为只有 `maps_route` 调用才满足；地点类调用仅作为锚点准备。内置 `travel-final-route` 上限提高到 12 次，仍受单 Session 地图预算、150 秒超时和一次最终化批次约束，足以覆盖锚点补齐与必要公交段而不开放无限重试。

AgentLoop 达到迭代上限时会为尚未真正执行的批量 ToolCall 写入 `code=TOOL_ITERATION_LIMIT` 的占位 ToolResult。Ledger 在在线观察和跨重启重放时均排除该 code，以及重复/预算等内部守卫 code；只有确实进入来源边界的路线调用才满足最终路线 attempt。

## 数据流

1. Child Tool 执行，RuntimeEvent payload 记录 Child/Root 关联。
2. WebSocket 以 Root Session 投递，旅行页即时展示安全 Hook 摘要。
3. Child 消息继续写入独立 JSONL；刷新时父子消息合并后安全投影。
4. 搜索 ToolResult 同时向 Ledger 写入有界引用；地图、路线和酒店 ToolResult 写入有界结构化事实。
5. Finalizer 确定性补齐遗漏的搜索引用、酒店观察价和可可靠配对的公交路线，再执行原有完整质量校验与持久化。

## 变更文件

- `agent/app/api/ws.py`
- `agent/app/runtime.py`
- `agent/applications/travel/source_ledger.py`
- `agent/applications/travel/store.py`
- `agent/applications/travel/service.py`
- `agent/app/runtime.py`
- `agent/applications/travel/hotel_tool.py`
- `agent/applications/travel/subagents.py`
- `agent/applications/travel/tools.py`
- `integrations/hotel_browser_mcp/ctrip.py`
- `prompts/travel_planning_continuation.md`
- `tests/unit_test/app/test_ws_routes.py`
- `tests/unit_test/travel/test_conversation_history.py`
- `tests/unit_test/travel/test_store_and_tool.py`
- `web/frontend/src/stores/travel.ts`
- `web/frontend/src/stores/travel.test.ts`
- `web/frontend/src/pages/TravelPlannerPage.test.ts`
- `web/frontend/src/pages/TravelPlannerPage.vue`
- `web/frontend/src/components/travel/TravelPlanForm.vue`
- `web/frontend/src/components/travel/TravelProgress.vue`
- `web/frontend/src/stores/travel.ts`
- `web/frontend/src/styles/travel.css`
- 对应 `test_case.md` 与前端恢复测试（如行为受影响）

## 测试方案

- WebSocket 测试断言 Child payload 通过 Root Session envelope 投递。
- 旅行历史测试写入真实 Child JSONL，断言刷新结果包含 Tavily/小红书等安全摘要且不包含原始 Child 文本。
- Finalizer 测试让模型故意遗漏 Web 引用，断言服务端从 Ledger 补齐并成功保存。
- 携程单测覆盖父节点含省州文本仍可选择城市、泛化偏好关键词清空；Owner 已登录档案执行大理 2026-08-18 至 2026-08-20 的真实只读查询。
- 高德测试覆盖大理自治州/县别名放行且博乐异地同名仍被拒绝；结构化合并测试覆盖酒店观察价和公交线路替换模型估算。
- 前端测试断言候选选择请求尚未完成时已经滚动到最终化进度区域。
- optimizer 测试覆盖短途三个真实差异方案、重复方案收敛为一个和单方案自动选中。
- intake 与前端测试覆盖基调必填、三个基调默认值、窄决策记录、只读展开对比和自动续跑。
- 最终化续跑测试覆盖“仍缺任务”与“任务已完成”两种工具可见性和提示。
- 最终化恢复测试覆盖候选 Child 不计入 finalization attempt、最终 Child 可跨重启恢复；截断 Tavily 结果覆盖前置完整行恢复与安全引用。
- 铁路合并测试覆盖同城多车站优先保持候选车站，并校验双人票价变化同步到铁路预算项和总预算。
- 前端测试覆盖失败 Session 有已选候选、没有候选以及页面续跑按钮展示。
- 运行 Ruff、后端全量 Pytest、前端 Lint/Typecheck/Vitest/Build，并重启真实 Gateway 验收。

## 验收标准

- 单住宿最终计划即使模型改写了酒店名，也能从本轮来源账本中选择携程价格与高德酒店 POI 严格同名的最低价候选，确定性补齐身份、坐标与日期价格；不存在双来源同名候选时保持失败，不做模糊拼接。
- 候选身份合并只锁定日期、城市和活动骨架；候选预算是选择阶段估算，不得覆盖 Finalizer 根据 12306 双人票价与携程观察价得到的最终预算。
- 真实账号查询可能让小红书首次组合词耗时四十秒以上、唯一短词重试再耗时一分钟；攻略 Child 使用 180 秒硬上限，住宿景点 Child 使用六轮/150 秒硬上限，并同时限制窄化次数，避免正常晚到被误判超时或无边界换关键词。
- 最终住宿 Child 的任务由服务端注入最近两次有界携程观察，避免当前轮携程城市下拉偶发失败时 Child 看不到候选阶段已成功的准确酒店名；注入内容只含来源账本已脱敏的酒店字段，不含账号、Cookie 或浏览器状态。
- 单个携程酒店可能未收录于高德；最终住宿 Child 在七轮硬上限内对严格同名携程候选最多尝试六个不同名称并取首个高德可核验项，保持有界且不做相似名称拼接。
- 来源账本为最终住宿保留 6 次独立的高德酒店严格同名检索额度，与 Child 的六候选策略一致；普通地图检索仍为 10 次，携程日期房价核验仍为最终阶段一次，不因扩大地图核验而增加账号查询。

- 子 Agent 查询记录实时可见，刷新后仍存在。
- 候选卡存在但 Finalizer 遗漏 Tavily 引用时，不再陷入 `TRAVEL_WEB_EVIDENCE_MISSING` 循环。
- 已完成最终子任务后不会再次要求重复委派。
- 最终失败只在真实质量门禁仍未满足时出现，候选卡与失败状态不会互相矛盾。
- 已选择候选的失败任务无需刷新或重新选方案即可看到“继续完成当前方案”。
- Owner 携程登录态能返回大理指定日期房价；最终卡片不得把已观察的价格或已配对公交路线重新标成模型估算。
- 点击候选卡后立即看到最终化进度和耗时，无需手动向下寻找。
- 用户未选择旅行基调时不能开始；真实酒店查询使用对应每晚上限。
- 短途最多出现三个真实差异方案，长途覆盖充分时直接生成；最终计划刷新后决策记录仍存在。
