# 旅行搜索结果展示、地图信息与候选确认强制化设计

## 背景

旅行规划实跑中，Tavily 和小红书只读查询虽然成功返回，但进度卡显示“未返回可展示结果”；12306 完成事件偶尔回退为 `mcp__12306__get-tickets 执行完成`。地图只显示散落标记，底图异常时缺少地点、顺序和路线说明。天气等失败原因则以技术证据原句进入未知项。optimizer 已具备候选卡能力，但模型可以跳过 `request_travel_candidate_review` 直接调用 finalizer，导致用户没有选择机会。

根因是 MCP 统一结果可能同时包含 `structuredContent` 与文本内容，形成多个连续 JSON 文档；旅行展示层只接受单一 JSON 对象，解析失败后回退到通用事件。候选确认门禁只在候选记录已经存在时生效，无法阻止模型完全跳过候选记录。

## 目标

- 有界解析 MCP output 中的单个或多个 JSON 文档，并递归识别常见 `data/text/content/result` 包裹。
- Tavily、小红书、12306、高德和天气均展示最多五条经过筛选的标题与短摘要；真实空结果和解析失败使用不同文案。
- 已知旅行来源永远显示产品名称，不向用户暴露 `mcp__...`、底层方法名或 qualified Skill 名。
- 地图同时提供编号地点、行程顺序、路线连线和距离/时长图例；底图加载异常可见且不丢失文字信息。
- 旅行频道 finalizer 必须先存在用户已选择的候选审核记录，不能仅依赖 Prompt 要求。
- 未知项在 UI 翻译为“影响、原因、下一步”，隐藏 HTTP、ToolResult、evidence 字段等实现细节。

## 范围边界

- 不修改 AgentLoop 的业务判断；解析和展示位于旅行 Hook，候选门禁位于旅行 Tool/Service 边界。
- 不保存或展示外部完整正文、Cookie、Token、原始 MCP output；摘要仍限制数量与字符数。
- 地图路线在真实 path 缺失时只用活动坐标生成“行程顺序参考线”，不冒充导航道路路径。
- 地图底图受高德 JS Key、安全码、域名白名单和客户端网络影响；代码提供状态提示与文字降级，不伪造底图。
- 候选审核只影响旅行频道；非旅行调用保持原有 finalizer 兼容性。

## 模块设计

### MCP 结果投影

`progress.py` 使用 `JSONDecoder.raw_decode` 有界扫描最多若干 JSON 对象，从中选择包含来源结果字段的有效对象。随后递归解包 `data/result/content/text`，识别 `results/items/feeds/notes/trains/pois`。小红书 `note_card`、`user.nickname` 等嵌套字段转成短标题与摘要；Tavily 保留标题及 content/snippet 的前 100 字。

空结果分三类：来源明确返回空列表、来源调用失败、output 形态无法识别。只有第一类显示“没有匹配结果”，第三类显示“返回格式暂无法展示，已保留给规划器处理”，避免误报没搜到。

### 用户侧名称

前端对已知旅行来源的 started/completed 事件统一使用本地产品文案。后端 Hook 的显示标题继续作为细节来源，但不能覆盖为包含 `mcp__` 的通用标题。

### 地图信息

`TravelMap` 显式启用普通底图的背景、道路、建筑和地点标签。活动按日期和顺序生成编号 Marker；真实 route path 使用实线，缺失 path 时按同日活动坐标绘制虚线参考线。地图下方始终列出每日地点顺序及路线段的交通方式、距离和时长。监听地图完成与错误状态，在底图不可用时展示诊断提示。

### 候选确认门禁

`FinalizeTravelPlanTool` 在可信 `channel=travel` 上先检查当前 Session 的候选审核记录：不存在或未选择均返回 `TRAVEL_CANDIDATE_SELECTION_REQUIRED`。模型必须调用 optimizer 并把两个至五个可行候选传给 `request_travel_candidate_review`；用户选择后才能续跑 finalizer。Service 继续校验 selected ID 与最终逐日骨架一致。

### 未知项翻译

前端只做展示层归类，不修改保存数据。按 Open-Meteo、12306、高德、小红书、住宿等来源识别影响和下一步；移除时间戳、HTTP(S)、Tool 名、evidence 等实现说明。无法识别时保留有界自然语言文本。

## 数据流

1. MCP ToolResult 同时携带结构化和文本结果。
2. 旅行 Hook 有界拆分、解包并生成 `search_results`。
3. 前端按来源显示友好标题、筛选摘要与最多五项内容。
4. optimizer 生成多个可行候选；候选 Tool 保存并发出卡片事件。
5. 用户选择后，finalizer 校验审核状态与候选一致性并保存计划。
6. 页面以信息化地图和用户语言未知项展示最终结果。

## 变更文件

- `agent/applications/travel/progress.py`
- `agent/applications/travel/tools.py`
- `prompts/travel_planning.md`
- `prompts/travel_planning_continuation.md`
- `skill_repo/skills/travel-planner/SKILL.md`
- `web/frontend/src/stores/travel.ts`
- `web/frontend/src/components/travel/TravelMap.vue`
- `web/frontend/src/pages/TravelPlannerPage.vue`
- `web/frontend/src/styles/travel.css`
- 对应 Python、Vue/Pinia 测试和旅行测试说明
- `docs_design/zhice-agent-part19-intelligent-travel-planner-design.md`

## 测试方案

- 搜索：单 JSON、连续重复 JSON、`data.text` JSON、小红书嵌套 note card、Tavily content、真实空数组、不可识别文本。
- 名称：12306 completed 即使携带通用 `mcp__... 执行完成` 仍显示铁路 12306。
- 候选：旅行 finalizer 无审核、待选择、ID 不匹配均拒绝；选择并匹配后成功；非旅行兼容。
- 地图：无 Key 文字降级；有坐标时生成编号点、参考线、每日地点和路线摘要；错误状态提示。
- 未知项：天气/12306/地图/小红书技术原句转换为用户可执行文案，不出现 HTTP、evidence、Tool 名。

## 验收标准

- Tavily 和小红书有返回时至少展示标题与简短筛选内容，不能因多段 JSON 误报为空。
- 页面不出现 `mcp__12306__get-tickets` 或其它内部标识。
- 地图即使底图异常，也能看清地点编号、每日顺序、交通方式、距离和时长。
- 天气失败说明明确是调用失败或超出预报窗口，并给出临近出发重查建议。
- 每次旅行计划在保存前都必须出现至少两个可选方案卡片并等待用户选择。
- Ruff、Pytest、前端 lint/typecheck/test/build 按仓库规范执行并报告。
