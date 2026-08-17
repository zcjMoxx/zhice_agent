# 旅行计划证据细节与完成态实时收敛设计

## 背景

2026-08-15 使用独立 viewer 测试账号，在正在运行的 Gateway 上真实执行了“北京到沈阳三日旅行”完整流程。测试包含 12306、高德公交、天气、Tavily、小红书、候选选择和最终保存，不使用伪造 Tool 返回。

真实结果暴露出以下问题：

- 12306 已返回出发到达时间、票价和席别，最终计划也保存了这些字段，但交通卡仍按旧字段读取，显示为 `— · — · —`。
- 高德公交原始结果包含线路号、上车站、下车站和途经站，最终 `route_segments` 只保留交通方式、起终点、时长和距离，证据在模型压缩时丢失。
- 住宿对象已经包含具体酒店、入住退房日期和规划估算价，页面只展示区域，并把估算价隐藏，造成预算缺乏来源说明。
- 地图同时绘制所有日期的地点，缺少按天切换。
- `travel.plan_ready` 到达时，新计划尚未出现在本地计划列表；`open(plan_id)` 因此拿不到 `source_session_id`，用空 Session 恢复进度，实时记录被覆盖为单条完成记录。重新加载后才从服务端 Session 历史恢复。
- 优化器可输出大于 10 的强度分，但最终计划 schema 最大为 10，导致第一次保存必然失败。
- 搜索结果较大时 Source Ledger 放弃 JSON 解析，把已有结果误判为 0 结果并重复要求缩短查询。

真实测试还确认：酒店浏览器 MCP 实现和管理能力已经存在，但当前 runtime MCP 配置未启用该服务，且测试账号没有酒店平台凭据。因此本次只能严格区分指定日期观察价与规划估算价，不得把网页摘要价格冒充实时房价。

## 目标

- 交通卡直接展示真实计划已有的车次、时间、席别、单价、总价和来源。
- 高德公共交通路线保存并展示线路号及上下车站；高德已返回这些事实时不允许降级为泛化文案。
- 住宿展示具体酒店、入住退房日期、每晚观察价或明确标注的规划估算价，并显示对应证据来源。
- 地图默认只展示第 1 天，并可切换日期；切换后地图和文字路线都只显示当天内容。
- 完成事件后不刷新也保留完整实时进度。
- 候选强度与最终计划统一为 0 到 10 的展示契约。
- 大型搜索结果仍能被 Source Ledger 判定为成功，避免无意义重复检索。

## 范围边界

- 不实现酒店预订、支付、取消或自动下单。
- 未启用酒店 MCP 或没有账号凭据时，不生成“实时房价”；只显示规划估算并明确状态。
- 本次不替用户启动、停止或重启 Gateway。
- 高德限频的通用 MCP 调度节流另行设计；本次先通过契约保证成功返回的线路详情不会丢失。
- 历史计划继续兼容旧的松散交通和住宿对象，前端使用兼容读取；新计划写入使用收紧后的字段。

## 模块设计

### 1. 完成态进度收敛

`travel.plan_ready` 处理时先保存当前 `sessionId`，并作为显式 hint 传入 `open(planId, sourceSessionId)`。`open` 不再只依赖尚未刷新的计划列表寻找来源 Session。

服务端历史尚未持久化完成时，`restoreProgress` 继续以服务端记录和当前实时记录合并；不得用空 Session 清空当前进度。

### 2. 交通与住宿协议

新交通对象采用扁平字段：

- `name`、`mode`、`from`、`to`
- `service_name`（车次或航班号）
- `departure`、`arrival`、`duration_minutes`
- `seat`、`price_cny_per_person`、`price_cny_total`
- `source`、`evidence_ids`、`summary`

住宿对象采用：

- `hotel_name`、`address`、`area`、`location`
- `check_in`、`check_out`、`nights`
- `observed_price_per_night_cny`（指定日期查询观察价，可为空）
- `planning_estimate_per_night_cny`（规划估算价，可为空）
- `price_status`、`price_source_evidence_ids`、`reason`

页面优先展示观察价；没有观察价时显示“规划估算”，不能只显示一个无标签价格。

### 3. 公共交通路线细节

`route_segments[]` 增加：

- `transit_legs[]`：`mode`、`line_name`、`departure_stop`、`arrival_stop`、`via_stops`
- `walking_distance`
- `fare_cny`

当路线 `source` 为高德且 `mode` 包含公交或地铁时，至少需要一个 `transit_leg`。没有真实线路时必须改用估算来源，并在未知项中说明，不能标记成高德结果。

### 4. 按天地图

`TravelMap` 持有选中日期，默认首日。地点、真实路径、参考虚线和路线摘要都从选中日派生。切换日期后清除旧覆盖物并重新绘制、重新缩放。

### 5. 优化器与研究门控

优化器仍使用未截断强度执行拒绝、警告和评分，但对外候选摘要把展示分限制在 0 到 10。

Source Ledger 对搜索 Tool 输出使用更大的有界解析窗口。只要合法结果数组存在，即视为该来源类别成功；后续失败不得抹掉已有成功。

## 数据流

```text
真实 Tool 返回
  -> 旅行计划严格 schema
  -> Finalizer 规范化与证据引用校验
  -> TravelPlanV1 存储
  -> 前端类型化读取
  -> 交通卡 / 住宿卡 / 每日时间线 / 按天地图

travel.plan_ready + 当前 session_id
  -> open(plan_id, session_id)
  -> 服务端历史与当前实时进度合并
  -> 不刷新也保留完整规划过程
```

## 变更文件

- `agent/applications/travel/tools.py`
- `agent/applications/travel/schemas.py`
- `agent/applications/travel/source_ledger.py`
- `prompts/travel_planning.md`
- `skill_repo/skills/travel-planner/SKILL.md`
- `skill_repo/skills/travel-planner/scripts/optimize.py`
- `web/frontend/src/api/types.ts`
- `web/frontend/src/pages/TravelPlannerPage.vue`
- `web/frontend/src/components/travel/TravelMap.vue`
- `web/frontend/src/components/travel/TravelTimeline.vue`
- `web/frontend/src/stores/travel.ts`
- 对应 Python、Vitest 和测试说明文件

## 测试方案

- 用真实 E2E 保存的 `travel-plan-5e874193bd9b4d3a93603674ecb74511` 字段形状做前端兼容回放。
- Store 测试模拟 `travel.plan_ready` 到达时计划列表尚未刷新，断言完整实时进度不丢失。
- 地图测试至少包含两天，断言默认只绘制第 1 天，点击第 2 天后清除并只绘制第 2 天。
- Schema 测试验证高德公交路线缺少 `transit_legs` 会被拒绝，带线路号和站点时保存成功。
- 优化器测试验证原始强度仍触发门控，而候选展示值不超过 10。
- Source Ledger 测试验证超过旧 20KB 阈值的合法搜索结果仍被识别为成功。
- 运行 Ruff、Pytest、TypeScript、ESLint、Vitest 和前端生产构建。

## 验收标准

- 交通卡不再出现已有数据却显示横线的情况。
- 指定日期酒店无实时价时，页面明确显示具体酒店、日期和“规划估算”；有观察价时显示观察价与来源。
- 高德返回线路信息的公交/地铁段在时间线显示线路号和上下车站。
- 地图首屏只显示第 1 天，可切换到其他日期且无跨天残留标记。
- 完成后立即展开规划过程，仍能看到完成前所有实时记录，无需刷新。
- 候选不会出现大于 10 的展示强度，最终保存不再因同一字段契约冲突失败。
- 大型有效搜索结果不会触发无意义的重复缩短查询。
