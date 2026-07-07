# 2026-07-06 Context Relevance Selection 设计记录

## 1. 背景

第七部分已经把 Session 历史从单纯 message 列表推进到显式 turn 运行单元：

```text
user input
  -> context built
  -> LLM / tools
  -> assistant result
  -> same turn_id persisted to JSONL
```

当前 `ContextBuilder(max_history_turns=...)` 的问题是：它只按最近 N 个 user turn 取历史。只要用户隔一段时间继续打开同一个 session，新的输入即使只是“你好”“谢谢”或一个完全无关的问题，也会把最近历史带进 LLM，容易让模型主动复述旧话题。

这次改动把 turn 历史视作短期记忆候选集：先在本地做相关性检索，只有和当前输入相关的 turn 才进入 LLM context。

## 2. 目标

1. 保留显式 turn 作为上下文裁剪单位，不回到按 message 裁剪。
2. `max_history_turns` 表示最近候选 turn 数，而不是无条件注入的 turn 数。
3. 在本地比较当前 user 输入和候选 turn 的完整文本，相关才带入。
4. 对代码名、文件名、命令、错误名、路径、英文标识符等 anchor 给予更高权重。
5. 中文普通文本也参与匹配，不能只抽关键词或只看 anchor。
6. 对“好的/ok/嗯”这类短确认，不做简单禁用；只有当最近 assistant turn 明显是在提问或等待确认时，才通过邻接关系加分。
7. 给 LLM 的系统 prompt 增加约束：历史只在直接相关时使用，不相关时不要主动延续旧话题。

## 3. 非目标

- 不引入 LLM judge 判断相关性，避免额外延迟和 token 成本。
- 不引入向量数据库、embedding 服务或长期 memory。
- 不设计复杂 topic/segment 边界系统。
- 不保留旧 JSONL、metadata fallback 或 legacy grouping 的迁移逻辑；本地开发按新 turn 字段直接前进。

## 4. 数据流

```text
Session history
  -> group_messages_by_turn(history)
  -> keep turns containing user message
  -> take latest max_history_turns as local candidates
  -> score(current_user_text, full_turn_text)
  -> keep relevant turns up to max_relevant_turns
  -> drop oldest selected turns if max_history_messages is exceeded
  -> _history_to_llm_dicts()
  -> append current user message
```

关键点：

- 相关性选择发生在 `_history_to_llm_dicts()` 之前，仍然以 turn 为原子单位。
- turn 的完整文本包含该 turn 内 user、assistant、tool 的 content，用于覆盖“我不懂你刚才回答里的 b”这类追问。
- tool-call block 的 OpenAI-compatible 检查继续由 `_history_to_llm_dicts()` 兜底。

## 5. 本地相关性算法

算法保持为纯函数，便于单元测试：

```python
select_relevant_turns(query, candidate_turns, max_selected_turns)
```

### 5.1 文本特征

- ASCII / 代码 token：如 `jsonl`、`metadata`、`ContextBuilder`、`turn_id`、`pytest`、`/sessions`、`agent/core/context.py`。
- CJK bigram：对中文连续文本生成二字窗口，例如“生成文档”得到“生成”“成文”“文档”。
- 短 token：允许 `b` 这类短追问参与匹配，但权重低于明显 anchor。

### 5.2 加权信号

| 信号 | 用途 |
| --- | --- |
| full-text feature overlap | 覆盖普通中文追问和自然语言复述 |
| anchor overlap | 提升代码、路径、错误、命令等精确对象 |
| recency bonus | 同等相关时优先最近 turn |
| adjacency confirmation bonus | 当前输入很短且最近 assistant 明显等待确认时，允许“好的/ok/嗯”接上上一轮 |

### 5.3 阈值原则

- 默认阈值偏保守，宁可少带历史，也不要把无关旧上下文塞给 LLM。
- “你好”“在吗”“谢谢”没有和历史文本或邻接确认形成有效信号时，不带任何历史。
- 相关性分数只决定本地候选是否进入 context；最终回答仍由 LLM 按当前问题组织。

## 6. 变更文件

预计新增：

```text
agent/core/context_relevance.py
docs_design/2026-07-06-context-relevance-selection-design.md
```

预计修改：

```text
agent/core/context.py
prompts/tool_use_policy.md
docs_design/zhice-agent-part7-turn-context-design.md
tests/unit_test/context_builder/test_context_builder.py
tests/unit_test/context_builder/test_case.md
```

## 7. 测试方案

| 用例 | 输入/场景 | 期望 |
| --- | --- | --- |
| unrelated greeting | 当前输入“你好”，历史讨论 JSONL / turn_id | 不带历史 |
| direct follow-up | 当前输入“讲讲 b”，上一轮回答包含 b | 带上一轮 turn |
| code/error anchor | 当前输入提到 pytest 错误，历史包含 pytest / PermissionError | 带相关 turn |
| short confirmation | 上一轮 assistant 问“需要我生成文档吗？”，当前输入“好的” | 带上一轮 turn |
| unrelated recent turn | 最近 turn 和当前问题无特征重叠 | 不带历史 |
| message fallback | `max_history_turns=None` | 保持原 message-count 行为 |
| hard cap | 相关 turn 超过 `max_history_messages` | 从旧 turn 开始整体丢弃 |

## 8. 验收标准

1. 当前输入和历史无关时，LLM context 只包含 system 与当前 user message。
2. 当前输入引用上轮回答中的术语、代码名或错误时，相关 turn 会进入 context。
3. “好的/ok/嗯”只在最近 assistant 明确等待确认时接上上一轮，不会无条件触发历史注入。
4. 不新增外部依赖、不调用 LLM 做相关性判断。
5. `python -m ruff check .` 和相关 `pytest` 通过；若全量存在无关历史失败，交付说明中写明。
