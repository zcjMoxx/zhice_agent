# ZhiCe-Agent 紧邻上一轮回指保留设计

> 日期：2026-07-22
>
> 状态：已实现并进入当前代码基线
>
> 承接：`docs_design/2026-07-06-context-relevance-selection-design.md`、`docs_design/zhice-agent-part7-turn-context-design.md`

## 1. 背景

Owner Web 实测中，用户先问“苏格拉底是谁”，紧接着问“我刚刚问了什么”，模型却回答当前问题本身。Session JSONL 已完整保存上一轮，但 Trace 显示第二次 `llm.call messages=2`，只有 system 与当前 user message，证明问题发生在本地相关性筛选，而不是 Session 持久化或长期 Memory。

当前 `_is_contextual_followup()` 已覆盖“刚才”“上一条”“为什么没调用”等短追问，但漏掉“刚刚”“上一轮”“前一条”和常见英文 immediate-reference 表达。由于当前问题与上一轮主题没有词法重叠，上一轮得分为零并被过滤。

## 2. 目标

1. 短问题明确回指紧邻上一轮时，强制给最新 Turn 增加 follow-up bonus。
2. 覆盖常见中文“刚刚/上一轮/前一条”和英文“what did I just ask/last question”等表达。
3. 不无条件携带最新 Turn，继续让“你好”等新话题省略无关历史。
4. 不把长期 Memory 通知当作 Session 历史替代品。

## 3. 范围边界

本次仅修改本地上下文相关性选择与测试，不修改：

- SessionStore 持久化格式。
- 50 Turn 候选、最多 5 个相关 Turn、60 messages 硬上限。
- 长期 Memory 提取或通知协议。
- AgentLoop、LLMProvider 或 Tool 调用边界。

## 4. 模块设计

扩展 `agent/core/context_relevance.py` 的 contextual follow-up markers。判断仍满足：

```text
query compact length <= 32
and query contains an immediate-turn reference marker
and candidate is the latest Turn
```

命中后沿用现有 `_FOLLOWUP_BONUS`，不引入新的模型调用或业务判断。

中文覆盖至少包括：

- 刚刚、我刚刚问、我刚才问
- 上一轮、上轮、前一条
- 你刚刚说、刚刚说了什么

英文覆盖至少包括：

- what did i just ask
- what was my last question
- previous message / last question

## 5. 数据流

```text
SessionStore full history
  -> recent 50 user Turn candidates
  -> local relevance selection
       -> lexical overlap
       -> confirmation rule
       -> immediate-turn reference rule
  -> latest Turn retained
  -> system + previous user/assistant + current user
```

## 6. 变更文件

- `agent/core/context_relevance.py`
- `tests/unit_test/context_builder/test_context_relevance.py`
- `tests/unit_test/context_builder/test_case.md`
- `docs_design/README.md`
- `docs_design/zhice-agent-part7-turn-context-design.md`
- `docs_design/zhice-agent-overall-design.md`

## 7. 测试方案

- “我刚刚问了什么”保留紧邻上一轮。
- “上一轮我问的是什么”保留紧邻上一轮。
- “what did I just ask”保留紧邻上一轮。
- 无关“你好”仍不注入上一轮。
- 原有词法相关、确认追问和“为什么没调用”用例继续通过。

## 8. 验收标准

1. 上述复现场景的 LLM context 至少包含上一轮 user 与 assistant。
2. 不扩大到无条件保留最近 Turn。
3. 全量测试和静态检查通过。

## 9. 实现结果

- `_CONTEXTUAL_FOLLOWUP_MARKERS` 已覆盖“刚刚”“上一轮”“上轮”“前一条”及常见英文 immediate-reference 表达。
- 复现用例“苏格拉底是谁 → 我刚刚问了什么”能够选择并注入紧邻上一轮；“上一轮我问的是什么”和“What did I just ask?”同样覆盖。
- “你好”等无关输入仍不注入上一轮，没有改成无条件 latest-Turn 保留。
- Session 与长期 Memory 边界保持不变：Session Turn 是对话连续性真值，Memory 通知只呈现持久偏好或事实。
- 验证结果：`python -m ruff check .` 通过；`python -m pytest --basetemp .tmp/pytest_immediate_turn_reference_final` 为 `588 passed, 1 skipped`；两个前端 JavaScript 文件通过 `node --check`；`git diff --check` 通过。
