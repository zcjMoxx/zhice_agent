# 旅行最终校验来源缺口修复闭环设计

## 背景

候选研究可能因模型错误判断日期窗口而使用历史天气。最终校验能够正确返回
`TRAVEL_WEATHER_FORECAST_REQUIRED`，但候选选择后的工具面只保留住宿、路线和
`finalize_travel_plan`，导致后续重试无法补查天气，只会重复提交同一份无效计划。

## 目标

- 服务端计算并明确注入天气预报窗口，避免模型自行估算日期范围。
- 最终校验返回可修复的来源缺口后，只开放对应的一次定向修复能力。
- 修复成功后立即恢复为仅允许最终校验，不重复住宿、路线或其它来源研究。
- 进程重启后仍可从 Session 中的最终校验错误恢复修复阶段。

## 范围边界

- 本次闭合真实 E2E 先后暴露的天气预报缺口与本地公共交通路线证据缺口。
- 不降低 `TravelPlanV1`、来源证据或候选一致性校验门槛。
- 不让父 Agent 直接串行调用全部来源；天气和路线修复仍通过只读专用 Child Profile。
- 不自动伪造、降级或把历史天气标记为实时预报。

## 模块设计

1. `runtime.py` 从服务端已确认草稿计算北京时间下的 16 日窗口事实，并加入规划上下文。
2. Runtime 从最近一次 `finalize_travel_plan` ToolResult 识别
   `TRAVEL_WEATHER_FORECAST_REQUIRED` 或 `TRAVEL_ROUTE_EVIDENCE_MISSING`，进入对应修复态。
3. `subagents.py` 新增 `travel-final-weather` Profile，只暴露地点解析和 Open-Meteo forecast，
   禁止 historical weather、铁路、住宿、地图路线和攻略来源；路线修复复用现有
   `travel-final-route`，但以单任务修复批次运行。
4. 精确委派守卫在普通最终化阶段要求住宿+路线两路；修复阶段只要求 finalizer 指出的
   天气或路线一路。
5. `source_ledger.py` 允许“历史天气已成功但 forecast 尚未成功”时追加一次 forecast，
   同时阻止其它重复天气调用。
6. forecast 或路线修复实际发起后，动态守卫立即隐藏委派入口并重新开放 finalizer。
7. 已存在 finalizer 错误的重试直接进入修复继续消息，不先重复普通住宿+路线双任务。
8. Finalizer 边界在 schema 校验前确定性清洗证据 URL 的凭据查询参数；无凭据的公开查询参数继续保留，schema 本身仍拒绝绕过该边界直接写入的敏感 URL。
9. 单次 WebSocket 旅行生成允许最多 6 个有状态阶段 Turn，覆盖候选自动收敛、最终住宿/路线、Finalizer 和一次定向修复；后续 Turn 若遇到一次 `LLMProviderError`，从服务端持久状态生成新的 continuation Turn 重试，不复用失败 Turn，也不重新开放已完成来源。第二次 Provider 错误仍立即返回。
10. 12306 当日返回中没有晚于末日活动加 60 分钟缓冲的返程车次时，确定性选择同站优先的最晚真实车次，并在不删除活动的前提下按原时长把末日活动整体前移到发车前 60 分钟结束。Finalizer 还会直接依据计划中已经带 12306 证据的返程车执行同一协调，因此进程重启、内存来源账本丢失后仍可收敛；避免每次合并真实早班车后覆盖模型修正，形成相同字段错误。
11. 重新水合的 12306 结果包含往返两次成功查询、但模型只提交了一个铁路方向时，结构化合并会为缺失方向补建最小铁路选项，再用真实车次、席别、价格和独立证据完整填充；不再要求模型从长历史里重新抄写第二个方向。
12. `walking_distance` 的 Tool 入参允许 number 或纯数字字符串，Finalizer 入口立即把纯数字字符串转换为 number，再执行原有 0–2000 米领域上限；用于兼容高德原始 JSON 字符串值，不接受单位文本或放宽最终协议。

## 数据流

```text
finalizer 返回 forecast required
  -> Session 保存结构化错误码
  -> 下一次继续时 Runtime 识别天气修复态
  -> 仅开放 travel-final-weather
  -> geocode + forecast
  -> 账本记录 forecast_successful
  -> 同一 Turn 重新开放 finalize_travel_plan
  -> 校验并保存完整计划

finalizer 返回 route evidence missing
  -> 下一次继续时仅开放 travel-final-route
  -> 独立的最多 16 段修复预算补齐缺失公交/地铁段
  -> 再次校验并保存
```

## 变更文件

- `agent/app/runtime.py`
- `agent/app/api/ws.py`
- `agent/applications/travel/subagents.py`
- `agent/applications/travel/source_ledger.py`
- `agent/applications/travel/tools.py`
- `tests/unit_test/travel/test_intake_runtime.py`
- `tests/unit_test/app/test_ws_routes.py`
- `tests/unit_test/travel/test_subagent_profile.py`
- `tests/unit_test/travel/test_source_ledger.py`
- `tests/unit_test/travel/test_store_and_tool.py`
- `tests/unit_test/travel/test_case.md`
- `docs_design/zhice-agent-part19-intelligent-travel-planner-design.md`

## 测试方案

- 日期窗口由服务端计算并进入规划提示。
- 最终校验 forecast 缺口可恢复为单一天气 Profile。
- 历史天气成功后允许一次 forecast，不允许再次 forecast。
- 路线证据缺口只开放单一路线修复，不重复普通最终化双任务。
- 普通候选研究三路、普通最终化两路的既有互斥与并发约束保持不变。
- 使用真实 Owner 会话点击“继续完成当前方案”，验证天气补查后最终计划落库并进入完成态。

## 验收标准

- 当前真实失败 Session 不再重复得到同一 forecast-required 错误。
- 最终化天气修复不重复查询铁路、酒店、路线、Tavily 或小红书。
- 路线修复不重复查询天气、铁路、酒店、Tavily 或小红书。
- 页面最终显示第 6 阶段完成和完整计划，而不是继续按钮。
