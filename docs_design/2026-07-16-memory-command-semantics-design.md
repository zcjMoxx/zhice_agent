# `/memory` 命令语义统一设计

> 说明：当前代码已改为 `/memory` 展示长期 Memory、`/memory session [session_id]` 保存 Session Summary，并删除手动提取命令入口；本文正文保留当时方案，当前口径参考 `2026-07-16-memory-command-display-and-session-summary-design.md` 和 Part 10 活文档。

> 状态：已确认，进入代码落地。

## 1. 问题

现有 CLI/Web 只有 `/memory summarize [session_id]`，默认展示冗长用法；用户通常不知道 Session ID。同时该命令生成的是 Session Summary，并不是把长期偏好提取到 `MEMORY.md`，命令名称和用户预期不一致。

## 2. 统一命令

```text
/memory
  立即从当前 Session 提取长期 Memory

/memory list
  展示当前用户长期 Memory

/memory extract <session_id>
  从当前用户可访问的指定 Session 提取长期 Memory

/memory summarize [session_id]
  生成或更新可重建 Session Summary
```

主 `/help` 只展示：

```text
/memory - extract long-term Memory from the current session
```

`/memory` 和 `/memory list` 的结果末尾显示高级 Tip，不把子命令全部塞进主帮助。

## 3. 提取行为

- `/memory` 同步调用现有 `MemoryExtractionService`，复用至少三个用户 Turn、证据校验、安全策略和去重规则。
- Web 手动提取前取消该 Session 尚未执行的 idle job，避免紧接着重复后台提取。
- 已经审查到最新检查点时返回“没有新的长期信息”，不重复调用 LLM。
- 指定 Session 必须属于当前 actor；Owner/CLI 继续使用 workspace Memory。
- 手动提取失败不修改检查点和 Memory。

## 4. 列表展示

新增与 transport 无关的轻量 formatter，按固定 category 输出纯内容：

```text
Memory:
- preferences: 喜欢吃西瓜
- preferences: 回答时先给结论，最多三点
```

无内容时显示 `Memory is empty.`。不显示内部路径、检查点或 Session Summary。

## 5. 变更文件

- `agent/memory/presentation.py`
- `agent/app/runtime.py`
- `agent/cli.py`
- CLI/Web command tests、帮助测试和测试说明。
- README、Part 10 活文档。

## 6. 验收标准

1. CLI/Web `/memory` 都提取当前 Session 的长期 Memory。
2. `/memory list` 都展示当前 actor 的长期 Memory。
3. 指定 Session 和 Summary 入口只放在 Tip/详细用法中。
4. 主帮助保持单行简洁命令。
5. 不混淆长期 Memory 和 Session Summary。
6. Ruff、相关测试和全量测试通过。
