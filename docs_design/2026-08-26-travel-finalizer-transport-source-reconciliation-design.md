# 旅行终稿交通来源确定性归一设计

> 说明：本记录对应当日已上线的交通字段应急收口。当前整体方案已改为服务端 revision 草稿与批量 repair，并取消静默丢项、无语义名称和超限截断；参见 `2026-08-26-travel-finalizer-server-draft-repair-design.md` 与 Part 19 活文档。

## 背景

线上 owner 旅行 Session `session-e65440ba482446c3` 在最终化阶段连续提交完整计划，`finalize_travel_plan` 依次因多个 `plan.transport_options[].source` 为空而拒绝。公共 Tool JSON Schema 只声明该字段为 `string`，空字符串能够通过参数校验；领域 Schema 则要求非空。模型收到单个字段错误后重新生成整份计划，修复一项时又可能让其他项回退，形成数分钟一次的最终化循环。

## 目标

- 对交通方案的空来源标签执行服务端确定性归一，一次处理全部交通项。
- 保留模型已经提供的非空来源，不覆盖真实来源表达。
- 有铁路证据时标记为 12306 已核验查询；有其他外部证据时使用证据 provider；没有外部证据时明确标记为规划估算。
- 区分模型草稿契约与持久化契约，使来源缺失仍能进入可信领域层完成归一。
- 同一字段修复不得让模型通过重写完整交通数组制造空名称、重复项或超出 20 项上限。

## 范围边界

- 不降低 TravelPlanV1 对交通来源、铁路证据和估算透明度的校验要求。
- 不重新查询地图、铁路、酒店、天气、网页或社区来源。
- 不改变 AgentLoop、SessionStore、候选选择和旅行计划持久化协议。
- 不自动填充无法安全推导的车次、时间、价格、路线或证据引用。

## 模块设计

`FinalizeTravelPlanTool` 在来源账本合并完成后先执行交通来源归一，`TravelApplicationService.finalize()` 在候选身份合并完成后、领域 Schema 校验前再次执行同一幂等归一。服务边界是最终防线，避免历史草稿或候选合并在 Tool 归一之后重新带回无效来源：

1. 深拷贝计划，不修改模型原始参数。
2. 建立 `evidence_id -> evidence` 索引。
3. 遍历全部 `transport_options`，只跳过已有非空字符串 `source` 的项目；空白或非文本值都视为待归一草稿。
4. 引用 12306/铁路证据时写入 `12306 已核验查询`。
5. 引用其他非 `model_estimate` 证据时使用有界、去重后的 provider 标签。
6. 其余情况写入 `planning_estimate`，继续接受既有“铁路无核验证据必须明确标为估算”校验。
7. 空白名称优先使用车次，其次使用起终点和交通方式生成；完全相同的交通语义只保留一次。
8. 去重后仍超过 20 项时，按外部证据和车次结构完整度保留最高的 20 项，再恢复原相对顺序。

公共 `_TRANSPORT_OPTION_SCHEMA` 不再把 `source` 作为模型输入的必填项；字段存在时仍必须是字符串。模型参数是待归一草稿，不等同于持久化 TravelPlanV1。无论 Provider 是省略字段还是输出空字符串，调用都会进入可信领域层完成确定性归一；持久化 Schema 继续要求最终来源非空。

## 数据流

```text
LLM 完整计划
  -> 安全 URL / 数值归一
  -> 来源账本与结构化结果合并
  -> Tool 层全量交通来源确定性归一
  -> 候选身份合并
  -> Service 持久化边界幂等归一
  -> TravelPlanV1 严格校验
  -> 保存并发出 travel.plan_ready
```

## 变更文件

- `agent/applications/travel/tools.py`
- `agent/applications/travel/service.py`
- `tests/unit_test/travel/test_store_and_tool.py`
- `tests/unit_test/travel/test_case.md`
- `docs_design/zhice-agent-part19-intelligent-travel-planner-design.md`
- 本日期设计记录

## 测试方案

- 多个交通方案同时为空来源时一次全部补齐。
- 空白名称、完全重复项和超过 20 项的模型草稿在同一遍归一中收口。
- 铁路证据、普通外部证据和无外部证据分别得到可解释来源。
- 非空来源保持不变。
- `finalize_travel_plan` 使用空来源计划时直接成功保存，不进入逐项修复循环。
- 绕过 Tool 直接调用应用服务时，空白来源仍在持久化边界补齐。
- Tool 草稿 Schema 允许省略来源，领域 Schema 仍拒绝归一后为空的来源。
- 运行旅行单元测试和 Ruff；部署前按风险补充更大范围验证。

## 验收标准

- 复现计划不再返回 `plan.transport_options[n].source is required`。
- 一次 finalizer 调用可处理多个空来源交通项。
- 保存后的来源标签与证据类型一致，估算项保持透明。
- 线上原 owner Session 最终获得真实 `plan_id`、计划详情可读且 generation 状态为 `completed`。
