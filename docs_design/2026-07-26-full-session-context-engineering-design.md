# 完整 Session 上下文工程设计记录

> 日期：2026-07-26
>
> 状态：方案已按当前真实接口完整实现，最终验证结果维护在 Part 15 活文档。
>
> 当前完整施工口径：`docs_design/zhice-agent-part15-context-engineering-design.md`
>
> 说明：本文记录的 `80% -> 70%` 初版水位已被同日后续设计替代；当前代码采用 `85%` 触发、约 `15%` recent raw、压缩后基础状态约 `35%` 的分离水位，详见 `docs_design/2026-07-26-session-compaction-watermark-strategy-design.md` 与 Part 15 活文档。本文正文保留当时方案原貌。
>
> 承接：`docs_design/2026-07-06-context-relevance-selection-design.md`、`docs_design/2026-07-22-immediate-turn-reference-retention-design.md`、`docs_design/2026-07-22-endpoint-context-budget-and-hybrid-turn-selection-design.md`

## 背景

现有“最近 3 + 旧相关最多 3”显著改善了近期连续对话。截图中的短会话未超过单轮最多 6 个历史 Turn 的上限，且历史元问题与旧回答存在可匹配文本，因此这次能够正确得到 5 个问题；这说明当前混合策略在小窗口内有效，不代表已经做了全历史扫描。真实 Session 回放仍显示，完整历史只有约 3522 tokens 时，系统会删除较早的“介绍牛顿”Turn，导致历史盘点错误。

问题不是缺少更多中文 marker，而是固定 Turn 数裁剪先于真实预算决策。当前上下文窗口足够容纳完整历史时，应优先保留完整状态；只有历史真的过长时，才需要 compaction 和 retrieval。

## 本次决策

本次不再做单点补丁，而是把以下能力作为同一完整功能：

1. 预算内完整历史。
2. 自然语言 Session 历史查询的确定性执行。
3. Provider-neutral 结构化 compaction。
4. SQLite FTS5/BM25 + embedding + entity/anchor 的混合 Turn 检索。
5. 本地向量存储、索引生命周期和未来外部向量后端协议。
6. 可解释 `context.selection` trace。

中间阶段可以按依赖顺序开发，但不以只完成其中一两层作为最终交付。

## 向量数据库取舍

语义 embedding 本次即实现；独立向量数据库服务不作为本地第一实现的前置条件。

第一实现使用用户隔离的 SQLite：FTS5 做词法检索，embedding 以 float32 BLOB 保存，在单 Session/用户候选内做精确 cosine scan。这样保持 Windows 本地部署轻量，也避免在数百 Turn 的规模下为检索增加一个常驻服务。

当出现十万级 embedding、多进程/多实例共享、p95 超过 100ms 或需要分片副本时，通过 `TurnSearchIndex` Protocol 切换。已有 PostgreSQL 时优先 pgvector；需要独立检索服务时再评估 Qdrant。

## 官方依据

- OpenAI Conversation state：多轮状态可通过完整历史、`previous_response_id` 或 Conversations API 持续携带。
- OpenAI Compaction：长交互在接近窗口时压缩状态，以平衡质量、成本和延迟。
- OpenAI Retrieval：语义搜索支持 query rewriting、ranking、threshold、metadata filter 和 embedding/关键词混合权重。

ZhiCe 保留 JSONL SessionStore 和 Provider failover，因此不把 OpenAI opaque compaction item 作为唯一方案，而采用通用结构化 compaction；Provider-specific 能力只能作为协议实现。

## 范围、模块、数据流、测试与验收

完整内容统一维护在 `docs_design/zhice-agent-part15-context-engineering-design.md`。本记录保留 2026-07-26 从局部相关性补丁转向完整上下文工程的决策背景，代码落地后的新增调整不回写本正文。

## 当日实现对齐说明

代码落地时确认仓库 Prompt 位于根 `prompts/`，且当前 CLI、Web、QQ、微信最终都进入同一个 `AgentLoop.run_turn()`。因此实现保持原方案边界，但做了以下最小接口对齐：Prompt 沿用根目录；已授权 `SessionStore`、当前 LLM 和 Tool schemas 由 app/loop 传给 ContextBuilder；词法索引提交后同步写入，embedding 在首次长历史检索前按有界批次懒回填；首次 compaction 同样拆成有界增量批次。没有引入后台常驻 worker 或渠道专用上下文实现。精确 cosine 按原方案使用新增直接依赖 NumPy 向量化计算。SQLite 派生库损坏时隔离后从 JSONL 重建，旧无 turn id Session 只做运行时懒推导。完整文件、测试和验收状态以 Part 15 活文档为准。

真实百炼冒烟进一步发现：原实现把归一化 Recency 直接乘以 `0.02`，而 semantic/lexical 使用 `weight / (60 + rank)`，两者量纲不一致，导致最近无关 Turn 能挤掉 cosine rank 1 的早期事实。当天修正为 `recency_weight / (60 + recency_rank)`，使 Recency 只承担小幅 tie-break，并补充强旧语义命中不能被近期噪声挤出 top-k 的回归用例。旧运行工作区还必须通过非覆盖式 prompt 同步补齐 Part 15 的 compaction、history query 和 query rewrite Prompt。

同一冒烟的复核还发现：原 `_retrieval_query()` 对所有短于 24 字的查询无条件拼接最近两轮，导致完整短问题被近期长文本污染，产生无关 entity 命中。实现改为复用 query rewrite 的省略标记判断，只有短代词或明显省略时才引入最近用户 Turn；完整短问题始终保持原文检索，并补充对应边界回归。

真实 compaction 延迟复核显示，旧实现一旦进入长历史模式，raw/compaction 边界每前进一个 Turn 就再次调用 LLM，单轮可额外增加数十秒。当前改为 Token 水位状态机：完整历史达到输入预算 80% 才首次压缩到约 70%；已有 compaction 后保留原始增量尾部，只有 compaction 与尾部再次达到 80% 才增量刷新。水位以下复用记录并写 `context.compaction.reused` trace，既不丢未压缩 Turn，也不按每轮或固定 Turn 间隔重复压缩。

旧工作区升级方面，`zcagent init` 继续非覆盖式复制根目录全部 Prompt，并新增从 example 补齐 `config/context.yml`、`config/embedding_endpoints.json`。上下文启动检查统一校验三个 Part 15 Prompt 与 EmbeddingProvider；缺失或无可用凭据时，CLI、Web/渠道 capability status 和 trace 明确报告安全的 degraded 原因，保留 full history、确定性 history query 与 FTS 降级，不暴露 Secret 或环境变量名。
