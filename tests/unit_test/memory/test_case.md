# Memory 测试说明

## 测试目标

- 验证 CLI/Owner 共用 workspace Memory，普通用户使用隔离的私有 Memory。
- 验证 Markdown Memory 的初始化、增删改查、格式保护、人工文本保留和原子写入。
- 验证敏感内容、容量限制和结构化错误不会泄露原文或覆盖损坏文件。
- 验证用户明确要求和自然语言同意后的对话授权写入规则。

## 用例覆盖

- 文件不存在时创建固定模板；合法条目只返回分类和内容。
- 重复 add 返回已有内容；replace/delete 使用 category 和原内容精确定位。
- 管理标记损坏时拒绝写入，标记外人工文本保持不变。
- `mode=list` 按固定类别整理返回并提供 total/returned/offset/has_more；`mode=search` 使用具体中文关键词检索，无匹配返回空列表。
- token、private key、完整日志和超长内容被拒绝。
- `user_explicit` 和 `user_confirmed` 可以写入；缺少授权或模型自行推断不能写入。
- 普通聊天不负责主动识别；Session 空闲提取器只自动写入有两到三条用户 Turn 证据的高可信长期信息。
- 少于三个用户 Turn 不调用提取模型，检查点避免重复审查，自动写入通知只消费一次。
- 缺少内置 `memory_extraction.md` 时返回不可重试的 `MEMORY_EXTRACTION_PROMPT_NOT_FOUND`，空白或不可读时返回 `MEMORY_EXTRACTION_PROMPT_INVALID`，不误报 Provider 故障或调用 LLM。
- 后台 extraction 使用与当前 endpoint failover 链一致的 ContextBudget，超长 source Turns 只裁剪本次输入，不改写 Session。
- startup checker 在专属 Prompt 缺失或为空时只禁用后台提取，Memory read/write 不受影响，并提供安全 CapabilityStatus。
- 统一调度器只使用固定数量 Worker，同一用户串行、不同用户受控并行；重复 Session 调度合并，取消、队列上限和有限重试行为稳定。

## 关键检查点

- `MEMORY.md` 只保存分类和内容，不保存 ID、时间、来源或 sidecar 元数据。
- Owner 的 Memory 路径不能派生 `contexts/users/{owner_id}`。
- session metadata 不保存 Memory 候选、confirmation id 或抑制状态。
- trace/audit 和 ToolResult metadata 不复制完整 Memory 内容。
- “我的记忆有什么”使用明确 list 语义，不依赖空 query 或泛化查询猜测。
- 默认测试只使用 Fake LLM，不访问网络。
