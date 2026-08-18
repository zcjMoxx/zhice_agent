# 旅行规划最终校验证据跨轮恢复

## 背景

线上真实续跑表明，交通错误已经能正确路由到 `travel-final-route`，但后续模型重建最终计划时可能丢弃上一轮已经补齐的实时天气；反向补天气时也可能覆盖已经验证的公交证据。`FinalizeTravelPlanTool` 目前只在单个 Tool 实例内保存上一次失败草稿，而每个续跑 Turn 会重新创建 Tool，进程重启后更无法恢复，导致天气与交通校验在不同 Turn 间来回切换。

## 目标

- 同一旅行 Session 的失败 Finalizer 草稿能够跨 Turn、跨进程重启恢复。
- 新草稿只补失败字段时，自动保留历史草稿中已经验证的实时天气和高德公交证据。
- 继续规划仍只执行当前失败 lane，不重新运行已经完成的候选研究。
- 最终必须通过真实公网 WebSocket、Owner 权限、外部路线查询和计划落库验证。

## 范围边界

- 不改变通用 AgentLoop、LLMProvider 或 SessionStore 协议。
- 不放宽 Finalizer 校验门槛，不伪造天气、线路或站点。
- 不把完整计划写入新的持久化文件；事实来源仍是 Session 中已持久化的 Finalizer Tool Call 参数。
- 只在旅行应用的运行态来源账本中缓存有界的草稿副本。

## 模块设计

1. `agent/app/runtime.py` 从父 Session 历史提取所有 `finalize_travel_plan` 的计划参数，并在每次旅行最终化 Turn 开始时回放到来源账本。
2. `TravelSourceLedger` 为每个 Session 保存有上限的 Finalizer 草稿副本，提供恢复、追加和只读快照接口；计划完成后随现有 `clear` 一并释放。
3. `FinalizeTravelPlanTool` 在校验前合并历史草稿中的已验证事实：
   - 当前草稿缺少实时天气时，继承最近一次有效 live forecast 摘要及相关证据；
   - 当前路线段退回估算时，继承同日期、同起终点的高德公交字段；
   - 当前草稿已有对应有效事实时不覆盖。
4. 每次失败草稿写回账本，使下一 Continuation Turn 可继续使用；不再依赖 Tool 实例生命周期。

## 数据流

`Session JSONL -> runtime 提取历史 Finalizer 参数 -> source ledger 有界回放 -> 新 Finalizer 草稿 -> 合并已验证天气/路线 -> 严格校验 -> TravelPlanV1 落库`

## 变更文件

- `agent/app/runtime.py`
- `agent/applications/travel/source_ledger.py`
- `agent/applications/travel/tools.py`
- `tests/unit_test/travel/test_intake_runtime.py`
- `tests/unit_test/travel/test_source_ledger.py`
- `tests/unit_test/travel/test_store_and_tool.py`
- `tests/unit_test/travel/test_case.md`

## 测试方案

- 单元测试覆盖 Session 历史提取多个 Finalizer 草稿。
- 单元测试覆盖账本草稿上限、深拷贝和清理。
- Tool 测试覆盖“旧草稿有实时天气、新草稿有公交”的双向合并，并确认最终校验通过。
- 运行旅行相关测试、Ruff 和前端既有回归测试。
- 部署后通过公网 WebSocket 以临时 Owner Session 继续原失败 Session，验证只执行缺失 lane、最终状态为 `completed` 且生成计划 ID。

## 验收标准

- 同一 `session_id` 不再在天气与交通错误间循环。
- 续跑日志只出现必要的 `travel-final-*` 修复任务，不重复候选研究。
- `/api/travel/generation` 返回 `completed`，计划详情可读取。
- 临时验证登录 Session 在测试结束后删除，本地 10086 不启动。
