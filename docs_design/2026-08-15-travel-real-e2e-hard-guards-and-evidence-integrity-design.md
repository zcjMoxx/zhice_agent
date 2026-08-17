# 旅行真实 E2E 硬调用守卫与证据完整性设计

## 背景

2026-08-15 在 Gateway 重启后，使用新注册的 viewer QA 账号再次从 `/travel` 真实执行“北京到沈阳三天两晚”全链。流程真实调用 12306、高德、Open-Meteo、Tavily 和小红书，并经过候选选择、finalizer 重试和最终保存。

第二轮验证确认按天地图、候选按钮锁定、完成态进度保留、住宿日期与规划估算展示已经生效，同时暴露出仅依赖 Prompt 无法约束的行为：

- 接待 Agent 仍提示用户“直接回复确认”，而页面最及时的动作是点击“确认并开始规划”。
- 同一 Session 对 12306 和高德 POI/路线重复查询；已有成功结果后仍继续重试，生成时间、进度噪声和外部限频风险都显著增加。
- 最终公交段使用“高德公交规划”等自由文本绕过了仅匹配 `source == amap_transit` 的校验，因此线路号、上下车站仍未保存。
- 住宿规划估算错误引用了博物馆 POI evidence，页面把无关证据展示在酒店价格下方。
- 计划第一次 finalizer 仍需结构修正，说明 TravelPlanV1 对 transport/stay 只做松散 object list 复制，Tool schema 的严格形状没有进入领域真值。

## 目标

- 页面话术优先引导点击真实按钮，同时保留文字确认作为无障碍等价入口。
- 在旅行应用边界对完全重复的 MCP 调用和单类过量调用实施硬守卫，不依赖模型自觉。
- 高德公交来源无论使用规范枚举还是用户可读别名，都必须保存线路与站点详情。
- 住宿地点证据与价格证据分离；规划估算不得引用无关外部 POI 作为价格来源。
- TravelPlanV1 对交通和住宿执行与 finalizer Tool schema 一致的领域归一化与证据引用校验。

## 范围边界

- 守卫按 travel Session 隔离，不影响普通聊天，也不阻止不同用户或不同旅行计划并发。
- 不把查询参数、Cookie、结果正文或 Secret 写入账本；重复判断只保存规范化参数的 SHA-256 指纹和有界计数。
- 重复调用不重新请求外部服务，只返回短结构化结果，要求 Agent 复用当前 Session 已有 ToolResult。
- 超出预算时返回稳定错误码并要求使用已有证据或降级 unknown，不自动伪造结果。
- 不实现酒店预订、支付或真实价格抓取；hotel-browser 未启用时继续只允许明确标注的规划估算。

## 模块设计

### 1. MCP 调用硬守卫

`TravelSourceLedger` 为每个 Session 增加：

- 已接纳调用的 SHA-256 指纹集合；
- 按 `transport`、`weather`、`web`、`social`、`lodging`、`maps_search`、`maps_geocode`、`maps_route` 分类的调用计数；
- 原子 `admit_call`，在远端执行前完成完全重复与预算检查。

`TravelGuardedTool` 包装当前 Turn 和 child 的 MCP Tool：

- 首次且预算内：调用原 Tool；
- 完全重复：返回 `TRAVEL_SOURCE_ALREADY_QUERIED`；
- 超预算：返回 `TRAVEL_SOURCE_BUDGET_EXHAUSTED`；
- 两类守卫结果都标记为内部可折叠进度，不生成新的“来源失败”卡片。

### 2. 交通与住宿领域归一化

`TravelPlanV1.from_dict` 不再直接复制 `transport_options` 和 `stay_recommendations`，改为专用归一化函数。

交通校验：

- 限制字段、类型、金额、时长和 evidence 引用；
- 真实 12306 evidence 被引用时，必须包含车次、时刻、席别和单价；
- 规划估算可以缺少车次，但必须在 source/summary 明确标注估算或不可用。

住宿校验：

- 新增一般 `evidence_ids`，只证明酒店名称、地址和位置；
- `price_source_evidence_ids` 只证明指定日期价格；
- `planning_estimate` 必须没有观察价，价格证据只能为空或引用 `model_estimate`；
- `live_observed`/`snapshot_observed` 必须有观察价和非模型价格证据；
- 一般酒店 evidence 至少一条能匹配酒店名称或地址，防止引用博物馆等无关 POI。

### 3. 高德公交语义校验

当 route segment 同时满足：

- source 包含 `amap`、`高德` 或规范值 `amap_transit`；
- mode 包含公交、地铁、bus、subway 或 transit；

则必须至少有一条 `transit_legs`，且每腿包含线路号、上车站、下车站。自由文本别名不能再绕过校验。

### 4. 用户话术与进度

- intake Prompt 改为“点击确认按钮；也可文字回复确认”，不再把文字回复写成首选动作。
- continuation Prompt 识别两个硬守卫错误码，不重试同一查询。
- 旅行进度 Hook 对守卫短结果设为 internal，避免重复失败卡；大型 JSON 解析窗口与 Source Ledger 对齐为 128KB。

## 数据流

```text
LLM MCP tool call
  -> TravelGuardedTool.admit_call(session, tool, sha256(args))
       -> duplicate / over budget: short structured result, no remote call
       -> allowed: McpToolAdapter -> remote MCP -> Source Ledger outcome

finalize_travel_plan
  -> normalize transport/stay/evidence references
  -> enforce AMap transit legs and hotel/price evidence semantics
  -> TravelPlanStore
  -> Vue traffic/stay/timeline/map presentation
```

## 变更文件

- `agent/applications/travel/source_ledger.py`
- `agent/applications/travel/schemas.py`
- `agent/applications/travel/tools.py`
- `agent/applications/travel/progress.py`
- `agent/app/runtime.py`
- `prompts/travel_intake.md`
- `prompts/travel_planning.md`
- `prompts/travel_planning_continuation.md`
- `web/frontend/src/api/types.ts`
- `web/frontend/src/pages/TravelPlannerPage.vue`
- 对应 Python、Vitest、测试说明和 Part 19 活文档

## 测试方案

- 同 Session 完全相同 MCP 参数只允许一次远端执行，不同 Session 互不影响。
- 运输、网页、社交、酒店和高德各操作预算到达上限后返回稳定错误码。
- 守卫结果在旅行进度中隐藏，不冒充外部来源失败。
- 高德公交自由文本来源缺少 `transit_legs` 被拒绝，包含线路与站点时通过。
- 规划估算引用博物馆 POI 作为价格证据被拒绝；酒店一般证据与价格证据分别通过正确路径展示。
- 真实 12306 evidence 被引用但缺车次/时刻/席别/价格时被拒绝。
- Vue 住宿卡分别展示“住宿信息来源”和“价格来源”，规划估算不显示无关外部价格来源。
- 运行 Ruff、Pytest、ESLint、Vitest、TypeScript 和生产构建。

## 验收标准

- 接待回复不再误导必须手工回复文字才能立即开始。
- 同一查询不会产生第二次外部 MCP 请求，单来源调用数有明确上限。
- 最终标记为高德公交规划的路线一定显示线路号和上下车站，否则 finalizer 拒绝保存并要求降级为估算。
- 酒店规划估算不会展示博物馆、景点或其它无关 POI 作为价格证据。
- 不刷新完成后仍保留完整进度；新增守卫不会制造重复失败卡片。
