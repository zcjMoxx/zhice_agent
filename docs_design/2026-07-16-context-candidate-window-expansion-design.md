# 对话上下文候选窗口扩展设计

> 说明：本文记录当时扩展到 30 个候选 Turn 的方案。2026-07-21 当前代码已进一步调整为 50 个候选、最多 5 个相关 Turn，并增加中文短追问保留紧邻 Turn；当前口径以 `docs_design/zhice-agent-part7-turn-context-design.md` 和 `docs_design/2026-07-21-on-demand-tool-discovery-design.md` 为准。

> 日期：2026-07-16
> 状态：已确认，进入实现

## 背景

当前 `ContextBuilder` 默认从最近 8 个 user turn 中选择最多 3 个相关 turn。随着 Session 变长，8 个候选容易遗漏稍早但仍与当前问题直接相关的上下文。

## 目标

- 默认候选窗口从 8 个 user turn 扩大到 30 个。
- 默认最多相关 turn 从 3 个扩大到 5 个。
- 保持本地确定性相关性筛选、`max_history_messages=60` 和单消息字符裁剪不变。

## 修改范围

- 修改 `ContextBuilder` 默认参数。
- 更新 Part 7 当前活文档。
- 增加默认参数回归测试和测试说明。

## 数据流

```text
当前 Session 历史
  -> 最近 30 个 user turn 作为候选
  -> 本地相关性评分
  -> 最多保留 5 个相关 turn
  -> 继续受 max_history_messages=60 限制
  -> 注入本轮 LLM messages
```

## 验收标准

1. `ContextBuilder` 默认值为 `max_history_turns=30`、`max_relevant_turns=5`。
2. 显式传参、`0` 和 `None` 的既有语义不变。
3. ContextBuilder 单元测试和全量测试通过。
