# Part 15 Session 上下文工程测试主题

## 测试目标

- 验证预算允许时完整 Session Turn 全量、按序进入上下文，旧固定 Turn 数参数不再主动删历史。
- 验证中文/英文高置信历史元问题通过确定性 Session 扫描生成原文证据。
- 验证结构化 compaction 的严格 JSON、原子存储、source digest 失效和增量输入。
- 验证 SQLite FTS5/BM25、float32 embedding BLOB、精确 cosine 与 entity/anchor/recency 混合排序。
- 验证旧 Session 懒回填、用户物理隔离、clear/delete 派生状态清理和 embedding/compactor/index 失败降级。
- 验证 ContextPlan 与 `context.selection`、compaction、index、retrieval trace 不记录正文或 embedding。

## 用例覆盖

- 正常：完整历史、牛顿历史盘点、问题计数、长历史 compaction、词法/语义/实体/anchor 召回。
- 异常：embedding 失败、compactor 非法 JSON、损坏/不可用索引、固定内容超预算。
- 边界：空/单 Turn、无 turn_id 旧消息、完整 Tool block、多用户同名 Session、模型身份切换、clear/delete。
- 性能：批量 1000 Turn 建索引和 Session 内精确 cosine 检索保持有界；10k 基准留作显式性能回放，不进入默认高频单测。
- 真实缺陷回归：Recency 只作为 RRF 同量纲的小幅 tie-break，不能把 semantic rank 1 的早期 Turn 挤出 top-k。
- Query 边界：完整的短问题保持原文检索；只有含短代词或明显省略标记的查询才拼接最近用户 Turn，避免近期长文本污染语义向量和 entity 候选。
- Compaction 水位：完整历史达到输入预算 85% 才首次压缩；近期原始 Turn 目标约 15%，压缩刚完成的可复用基础状态不高于约 35%；已有 compaction 与原始增量尾部未再次达到 85% 时必须复用，不能每新增一个 Turn 调一次 LLM。
- Compaction 延迟：首次压缩在选择 raw/compacted 边界前必须为结构化摘要预留预算；35% 是软目标，轻微超出只写 `context.compaction.low_watermark_missed`，不得立即发起第二次 LLM compaction。只有压缩后基础状态仍超过 85% 时才允许紧急扩展覆盖边界；超过 32 个新增 Turn 的有界增量批次不受此限制。
- Compaction专用Provider：`models.json.routing.compaction`支持`endpoint`与`endpoint/model`，只用于前台/后台compaction；answer继续使用当前Turn Provider；端点不可用时安全回退，不把端点选择写入AgentLoop。
- Compaction usage：兼容`prompt_tokens/completion_tokens`与`input_tokens/output_tokens`，trace使用不会触发Secret键脱敏的count字段；价格未配置时`cost_available=false`，不能伪造费用。
- 后台预热：79.9%不启动，达到80%每个用户隔离Session最多一个daemon任务；85%以下当前回答仍走完整历史，85%到达时等待/复用同一Future；失败、失效、clear/delete安全降级。
- Compaction 降级：SessionStore、Prompt 或 LLM 不可用而无法压缩时，不套用 15% raw 目标；应在最终输入硬预算内尽量保留连续近期原文。
- 配置边界：`config.yml.context`明确拒绝旧`target_budget_ratio`和歧义水位；价格只来自实际Chat端点，Embedding批次只来自实际Embedding端点，不能在Context重复配置。
- SQLite 生命周期：每次索引操作必须显式 commit/rollback 并关闭连接；Windows 下完成检索后索引文件不能继续被遗留句柄占用。
- 启动反馈：缺少 Part 15 Prompt、embedding 配置、可用端点或凭据时，CLI 明示 degraded，Web/渠道 capability status 与 trace 使用同一安全状态；不得记录 Secret 或环境变量名。

## 关键检查点

- JSONL 消息不因 compaction 或索引而删除或改写。
- 检索只接收当前已授权 Session id，不能从自然语言切换 Session。
- embedding 未配置或失败时 FTS/确定性历史查询继续工作，并在 ContextPlan 标记 degraded。
- 选择后的 retrieved Turn 按原始时间顺序注入，tool call/result 不拆散。
