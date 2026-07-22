# ZhiCe-Agent 直接 Trace 证据诊断设计

> 说明：当前代码已将诊断专用调用与归因规则从通用 `tool_use_policy.md` 拆到可选的 `prompts/diagnostics.md`；Prompt 边界调整见 `2026-07-22-diagnostics-prompt-boundary-design.md`。本文其余正文保留首次落地方案。

> 日期：2026-07-22
>
> 状态：已实现并进入当前代码基线
>
> 承接：`2026-07-16-self-diagnostics-activity-audit-separation-design.md`、`2026-07-21-startup-capability-and-subagent-diagnostics-design.md`

## 1. 背景

当前 `diagnose_my_recent_activity` 已读取 Runtime Activity、Tool records 和关联 Trace，但在 Tool 返回前先由规则引擎压缩为固定的 `summary/cause_code/probable_cause`。如果 Trace 只记录 `error_type` 而没有安全异常消息，或失败类型不在规则表中，最终只能返回 `SUBAGENT_INTERNAL_ERROR` 等泛化结论，模型无法根据原始时间序列继续判断。

最新真实失败中，child 在 15ms 内发生 `TypeError`，Trace 仅保留 `error_type=TypeError`。实际原因是 `AgentLoop` 向 `SubagentContextBuilder.build()` 传入非空 `context_budget`，而该 builder 没有对应参数；诊断工具无法从现有证据恢复这个参数名。

## 2. 目标

1. 诊断 Tool 继续自动限定当前 actor、Session、Turn，不要求模型自己拼内部 ID 或路径。
2. Tool 返回经过字段白名单、脱敏和数量限制的按时间排序 Trace 事件，让模型直接基于证据归因。
3. 内部异常 Trace 增加安全截断的 `error_message`，不能只记录异常类型。
4. Prompt 明确要求诊断时先调用 Tool，再分析 Trace 因果链，禁止只复述泛化 code。
5. 修复 Subagent ContextBudget 接口不匹配，并增加真实非空预算回归测试。

## 3. 边界

- 不让 Skill 或普通文件 Tool 直接读取整个 workspace logs。
- 普通用户仍只能读取本人、当前 Session 关联证据；Subagent 内部 cause 继续按角色脱敏。
- Owner、本地操作者和具备 `audit.read` 的管理员可查看安全的内部错误消息。
- 不返回 traceback、完整 Prompt、请求体、credential 或其它 actor 的事件。

## 4. 数据流

```text
用户问上一轮为什么失败
-> discover_tools 激活 diagnose_my_recent_activity
-> Tool 自动选择上一轮/最近失败
-> Activity 定位 Turn + Trace 沿 root IDs 关联 child
-> 字段白名单 + secret redaction + 事件数量上限
-> 返回规则摘要 + chronological trace_events
-> LLM 根据 error_message/stage/code/前后事件解释真实原因
```

规则摘要保留用于快速定位，但不再是唯一证据。模型必须优先使用 `trace_events` 中更具体的安全事实；证据不足时明确说明缺失字段，不能把包装码当根因。

## 5. 变更文件

- `agent/subagents/coordinator.py`：记录安全 `error_message`。
- `agent/subagents/context.py`：接受 `context_budget`，保持 AgentLoop 通用接口一致。
- `agent/auth/diagnostics.py`：白名单开放 `error_message`，返回 chronological `trace_events`。
- `agent/tools/diagnostics.py`：说明 Tool 返回直接 Trace 证据供模型判断。
- `prompts/tool_use_policy.md`：增加失败诊断调用与证据解释规则。
- 对应 Subagent、Auth diagnostics、Prompt 测试和测试说明。

## 6. 测试

- 非空 ContextBudget 的 child context 构建不再 TypeError。
- 未分类异常 Trace 包含脱敏、截断的 `error_message`。
- 诊断结果返回按时间排序的安全 Trace 事件。
- API key、Authorization、secret 字段和跨 actor 事件不能进入 ToolResult。
- 普通用户遇到 Subagent 内部错误仍只获得通用联系管理员文案。
- 全量 Ruff、pytest、前端 JavaScript 和 diff check 通过。

## 7. 验收标准

针对本次错误，新的 Trace/诊断结果必须能直接提供：

```text
TypeError
SubagentContextBuilder.build() got an unexpected keyword argument 'context_budget'
```

模型据此应解释为 ContextBuilder 接口不匹配，而不是只复述“子代理内部执行错误”。

## 8. 验证结果

- 非空 `ContextBudget` 的真实 `ChildAgentFactory` 测试通过，不再发生 builder 参数 TypeError。
- 未分类 child 异常测试确认 Trace 保留脱敏后的具体 `error_message`。
- 诊断测试确认返回 chronological `trace_events`，并再次过滤 secret 与跨 actor 事件。
- `python -m ruff check .` 通过。
- `python -m pytest --basetemp .tmp/pytest_direct_trace_diagnostics_full`：`609 passed, 1 skipped`。
- 两个前端 JavaScript 文件的 `node --check` 与 `git diff --check` 通过。
