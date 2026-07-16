# 删除未闭环 Session Summary 能力设计

> 状态：已确认，进入代码落地。

## 1. 背景

Part 10 曾实现 `/memory session [session_id]`、`SessionMemorySummaryService` 和 `session_summaries/` 派生文件，目标是把一个 Session 整理成目标、决定、约束、待办和相关文件。

该实现只完成了“生成摘要文件”，没有接入 token 预算、上下文替换、当前 Session 续接、新 Session checkpoint 恢复、自动触发、失败回退和多次压缩治理，因此不属于完整 Context Compaction。独立保留会让 Memory 边界变重，也没有稳定消费者。

## 2. 目标

- 删除 `/memory session [session_id]`，`/memory` 只展示长期 Memory。
- 删除 Session Summary 服务、Prompt、协议、Markdown 存储和错误码。
- 删除 `memory_read(mode=session_summary)`，只保留 `list/search`。
- 删除相关测试、文档和运行事件。
- 保留 Session JSONL 真值、长期 Memory、`memory_write` 和后台高置信 Extractor。

## 3. 范围边界

- 本次不实现 `/compact`。
- 本次不实现自动 token 阈值和跨 Session checkpoint。
- 真正的 Context Compaction 作为后续上下文优化能力单独设计，不放在 Part 10 Memory。
- 不删除已经存在的用户运行目录；代码停止创建和消费 `session_summaries/`。

## 4. 模块变更

- 删除 `agent/memory/summary.py`。
- 删除 `prompts/memory_summary.md`。
- 删除 `SessionMemorySummary` 和 MemoryStore summary 方法。
- 删除 MarkdownMemoryStore summary render/parse/path 逻辑。
- 删除 `MemoryContext.summaries_dir`。
- `MemoryReadTool` 只接受 `list/search`。
- CLI/Web `/memory` 只展示当前作用域 Memory；其它子命令返回 `/memory` 用法。

## 5. 测试方案

- `/memory` 在 CLI/Web 展示空或非空 Memory，不调用摘要 LLM。
- 旧 `/memory session` 返回简洁用法。
- `memory_read` 拒绝 `session_summary` mode。
- 后台提取、写入、调度和通知测试继续通过。
- 全量 pytest、Ruff 和 diff 检查通过。

## 6. 验收标准

1. 运行代码不再出现 Session Summary 类型、服务、Prompt 和目录依赖。
2. Part 10 活文档只描述长期 Memory 闭环。
3. Context Compaction 明确留到后续上下文优化，不以 Session Summary 半成品占位。
