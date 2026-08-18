# 旅行规划失败步骤断点续做修复

## 背景

失败计划页面原先把“重新开始规划”直接绑定到 `startNew()`，会清空当前 Session、需求对话和进度，实际行为等同于新建计划。刷新恢复逻辑也会丢弃普通失败 Session。后端虽然已有基于 Session 历史和来源台账的续做指令，但前端没有使用。

## 目标

- 失败后继续使用原 `session_id`，保留需求、对话、候选和成功查询结果。
- 只重新执行失败或未完成的候选研究子任务；已成功子任务不得重跑。
- 已选择候选后的失败继续沿用现有最终校验定向修复逻辑。
- 刷新页面后仍能恢复失败现场并继续。

## 范围边界

- 不改变“新建旅行计划”按钮，其仍用于主动创建空白计划。
- 不复用已经失败的 Agent Turn；在同一 Session 中创建续做 Turn，以 Session 历史和来源台账作为检查点。
- 不把运行时内部续做提示词展示给用户。

## 模块设计与数据流

1. 前端失败按钮调用 `resumeFailedPlanning()`，不再调用 `startNew()`。
2. Store 保留 Session、对话和全部既有进度，只新增一条“正在继续未完成步骤”记录并发送同 Session 消息。
3. WebSocket 入口将该消息替换为服务端校验后的旅行续做指令。
4. Runtime 从持久化 `delegate_tasks` 结果恢复已经成功的 profile，并将精确的缺失 profile 交给委派约束。
5. 只运行缺失 profile，成功结果与 Session 中已有 Child 结果合并后继续 optimizer 或 finalizer。

## 变更文件

- `web/frontend/src/stores/travel.ts`
- `web/frontend/src/pages/TravelPlannerPage.vue`
- `agent/app/runtime.py`
- `agent/applications/travel/source_ledger.py`
- 对应前后端单元测试与本设计记录

## 测试方案

- 页面按钮不会调用 `startNew()`，Session、对话和历史进度保持不变。
- 刷新恢复失败 Session 后仍保留继续入口。
- 两个候选研究 profile 成功、一个失败时，仅失败 profile 可再次委派。
- 完整成功 fan-in 和已选候选最终校验续做行为保持不变。

## 验收标准

- 用户点击“继续未完成步骤”后不会看到空白新计划。
- 已完成进度不回退、不清空，原 Session 不改变。
- 日志和持久化委派结果能够证明只重试缺失 profile。
