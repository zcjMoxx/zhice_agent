# 旅行规划用户视角过程展示设计

## 背景

旅行页面当前直接展示通用 RuntimeEvent 的 Tool/Skill 名称，用户会看到 `load_skills`、`discover_tools`、`mcp__...` 等内部实现词；外部查询完成后也只显示“工具执行完成”，没有说明查询目标、来源、候选数量和有界结果摘要。信息虽然真实，但不能回答用户最关心的“正在为我的计划核对什么、查到了什么、最终如何筛选”。

## 目标

- 隐藏 Skill 加载、Tool 发现等内部编排事件。
- 把外部查询投影为用户可理解的来源、查询目标、结果数量和有界候选摘要。
- 明确展示高德地图、Open-Meteo、12306、Tavily、小红书只读等实际来源。
- optimizer 完成后展示比较的候选数量、采用方案、预算与路线质量门控摘要。
- 失败时显示来源和可理解的降级结果，不暴露内部异常、请求参数、Secret 或外部完整正文。

## 范围与边界

- 不在 AgentLoop 中写旅行来源或筛选规则。
- 复用既有 PostTool Hook 协议；旅行应用层只生成 RuntimeEvent presentation patch，不修改 ToolResult、Session 消息或 LLM 上下文。
- 仅对持久化 `channel=travel` 的 Turn 启用旅行投影；普通聊天和其它渠道不受影响。
- UI 摘要最多展示五个结果，每个字段有长度上限；不传 URL、Cookie、Authorization、Token、完整网页正文或原始请求对象。
- “查询候选”与“最终采用”分开表述：外部搜索只说明返回/保留的候选，只有 optimizer 的结构化输出可以声明采用某个候选方案。

## 模块设计

### 旅行展示 Hook

新增 `agent/applications/travel/progress.py`：

- `TravelProgressHookRuntime` 组合现有配置 Hook Runtime。
- PreTool 完全委托现有 Runtime。
- PostTool 先保留现有 Hook patch，再在 `request.channel == "travel"` 时按 ToolResult 生成旅行展示 patch。
- 支持高德地点/详情/路线、天气、铁路、网页搜索、小红书只读、optimizer 和 finalizer。
- JSON 解析和字段遍历全部有界；任何异常都 fail-open 返回基础展示。

### Runtime 装配

WebRuntime 向 AgentLoop 传递实际解析出的 Session channel，而不是浏览器 actor 的固定 `web` channel。运行时构建时用旅行展示 Runtime 包装配置 Hook Runtime。

### 前端过程卡片

- `visibility=internal` 的事件不进入旅行时间线。
- 前端额外屏蔽旧后端可能发出的 `discover_tools`、`load_skills`、`request_travel_clarification` 内部 Tool 外壳。
- `search_results` detail_data 渲染来源标签、查询目标、结果数量、候选标题/短说明和筛选结论。
- started/completed 使用同一 tool_call_id 更新同一条记录，不重复堆积。
- Skill 生命周期改为“比较候选行程”等用户文案，不显示 qualified Skill 名称。

## 数据流

```text
MCP / ToolResult
  -> AgentLoop PostTool Hook seam
  -> TravelProgressHookRuntime (only channel=travel)
  -> bounded display + search_results detail_data
  -> validated RuntimeEvent
  -> travel Pinia store
  -> user-facing progress card
```

## 变更文件

- `agent/applications/travel/progress.py`
- `agent/app/runtime.py`
- `web/frontend/src/api/types.ts`
- `web/frontend/src/stores/travel.ts`
- `web/frontend/src/components/travel/TravelProgress.vue`
- `web/frontend/src/styles/travel.css`
- `tests/unit_test/travel/test_progress.py`
- `tests/unit_test/travel/test_case.md`
- `web/frontend/src/stores/travel.test.ts`
- `docs_design/zhice-agent-part19-intelligent-travel-planner-design.md`

## 测试方案

- 正常：高德 POI 多结果、天气、Tavily、小红书、optimizer 和 finalizer 输出用户摘要。
- 异常：非 JSON、错误 ToolResult、恶意或超长外部字段安全降级，不中断 Tool Turn。
- 边界：空结果、超过五个结果、嵌套小红书 JSON、普通 web channel 不增强、配置 Hook patch 合并。
- 前端：内部事件不展示；高德 started/completed 合并；来源、查询、数量与候选项渲染；无 detail_data 时保持兼容。

## 验收标准

- 页面不出现 `load_skills`、`discover_tools`、qualified Skill 名或 `mcp__...`。
- 高德查询至少展示“高德地图 + 查询目标 + 返回数量 + 最多五个地点名称/短说明”。
- 其它来源同样明确平台及查询目的；失败显示可降级文案。
- optimizer 明确显示比较数量和结构化选中候选，不把普通搜索结果误称为最终采用。
- RuntimeEvent 不包含 Secret、外部完整正文、URL 或绝对路径。
- 后端、前端定向测试和全量质量命令通过。
