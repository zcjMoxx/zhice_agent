# Turn 完成日志输出预览设计

> 状态：已确认，进入代码落地。

## 1. 背景

Trace 收敛时删除了 `turn.done.output_preview`，避免回答正文与 Session 重复。但终端只剩 Turn 耗时，开发者无法快速判断模型实际回答方向，收敛过度。

## 2. 方案

- 在现有 `turn.done` 恢复 `output_preview`，不新增事件。
- 终端和 JSONL trace 都保留该字段。
- 只取最终回答的第一条非空文本行。
- 折叠该行内部连续空白，最多 80 字符。
- 完整回答仍只保存在 Session。
- 不恢复 `llm.direct`，不记录完整回答。
- error/stopped 继续使用各自状态字段，不伪造正常输出预览。

## 3. 示例

```text
[2026-07-16 15:55:09] | INFO | agent.turn.done | turn=6 duration_ms=9969 output_preview=结论：抽象是提取多个对象的共同特征。
```

```json
{"event":"turn.done","turn_index":6,"duration_ms":9969,"output_preview":"结论：抽象是提取多个对象的共同特征。"}
```

## 4. 变更文件

- `agent/core/loop.py`
- AgentLoop 与日志 formatter 测试。
- Part 8 日志活文档和 README。

## 5. 验收标准

1. 普通回答和 Tool 后最终回答的 `turn.done` 都有 `output_preview`。
2. Tool iteration limit 的最终总结也有预览。
3. 预览只取第一条非空行且不超过 80 字符。
4. 终端和 trace 内容一致。
5. 不恢复 `llm.direct` 或成功 `session.save`。
