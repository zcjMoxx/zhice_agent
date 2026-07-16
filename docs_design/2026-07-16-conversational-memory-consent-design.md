# ZhiCe-Agent 对话式 Memory 授权设计记录

> 说明：当前代码继续保留“用户明确要求时直接写入”的对话授权，但不再依赖普通聊天模型主动询问高频行为。重复长期信息改由 Session 空闲后的独立 Extractor 依据两到三条用户 Turn 证据自动写入，并在下次对话通知；未闭环的 Session Summary 已删除。参考 `docs_design/2026-07-16-background-memory-extraction-and-trace-convergence-design.md`、`docs_design/2026-07-16-remove-unclosed-session-summary-design.md` 和 Part 10 活文档。

> 日期：2026-07-16
>
> 状态：已完成设计并落地代码。
>
> 承接记录：`docs_design/2026-07-15-memory-boundary-design.md`
>
> 当前活文档：`docs_design/zhice-agent-part10-memory-design.md`

## 1. 背景

Part 10 已实现 Markdown Memory、用户作用域、`memory_read`、`memory_write`、安全过滤和显式 session summary。原方案把所有 LLM 发起的 Memory 写入都接入 tool confirmation，并在 Web 中展示独立确认弹窗、编辑、批准和拒绝操作。

该交互对本地轻量 Agent 过重。Memory 本质上是对话能力：用户明确说“记住”时，这句话本身已经构成授权；模型自行总结出长期偏好时，只需要在正常回答后自然询问，并根据用户下一轮自然语言决定是否写入。

## 2. 目标

1. 用户明确要求记忆、修改或忘记时，直接执行 `memory_write`，不再弹出工具确认框。
2. 用户对上一轮 Memory 询问自然语言同意时，直接执行用户同意后的具体内容。
3. 模型自行识别到长期偏好、频繁行为或稳定约束时，只在完成当前回答后追加自然语言询问，不直接写入。
4. 用户拒绝、表达不清或转入其它话题时不写入，不额外要求按钮操作。
5. 保留 Memory 作用域、RBAC、安全过滤、原子写入和隐私化日志边界。

## 3. 范围边界

包含：

- 将 Memory 写入授权改为 `user_explicit` / `user_confirmed`。
- 移除 Memory confirmation、候选编辑 API 和前端 Memory 确认表单。
- 移除 session 级 MemoryProposal pending/cooldown/suppression 状态机。
- 更新 `prompts/memory_policy.md`，把主动识别约束为回答末尾的自然语言询问。
- 更新测试和当前活文档。

不包含：

- 不增加独立意图分类模型或隐藏 LLM review。
- 不增加 Memory 候选数据库、后台任务或新的 Web 管理页面。
- 不改变危险 `exec` 等其它工具的 confirmation 机制。
- 不自动把完整 Memory 注入每轮上下文。

## 4. 授权语义

`memory_write` 使用字段：

```text
authorization=user_explicit
authorization=user_confirmed
```

- `user_explicit`：当前用户明确要求记住、修改或忘记某项内容。
- `user_confirmed`：上一轮助手询问是否保存某项 Memory，当前用户通过自然语言同意或给出修改后的表述。

不再接受 `assistant_inferred` 写入来源。模型推断本身不是授权。

## 5. 对话流程

### 5.1 用户明确要求

```text
user: 记住我希望回答先给结论
  -> LLM calls memory_write(authorization=user_explicit)
  -> RBAC + MemorySafetyPolicy
  -> write Memory
  -> assistant: 好的，我现在将“回答时先给结论”存入记忆。
```

### 5.2 模型主动识别

```text
user request
  -> assistant completes the requested answer
  -> assistant appends: 我注意到你经常要求先给结论，需要我把它存入记忆吗？
  -> no Memory mutation
```

下一轮：

```text
user: 可以
  -> LLM judges the reply in conversation context
  -> memory_write(authorization=user_confirmed)
  -> write Memory

user: 不用
  -> no tool call

user: 改成先给结论，再补必要依据
  -> memory_write(authorization=user_confirmed, content=用户的新表述)
```

## 6. 模块调整

删除或撤销：

```text
agent/memory/proposal.py
MemoryProposalPolicy
JsonMemoryProposalStateStore
MemoryProposalToolExecutionPolicy
MemoryProposalConfirmationBroker
Memory confirmation edit REST API
Web Memory confirmation editor
ToolConfirmationResult replacement arguments
```

保留：

```text
MarkdownMemoryStore
MemorySafetyPolicy
MemoryReadTool
MemoryWriteTool
memory.read.own / memory.write.own / memory.summarize.own
危险 exec confirmation broker
```

## 7. 依赖与数据流

```text
LLM conversation judgment
  -> memory_write Tool call with user authorization kind
  -> ToolExecutionPolicy checks memory.write.own and authorization enum
  -> MemoryWriteTool validates content safety
  -> MarkdownMemoryStore atomic mutation
  -> ToolResult returned to LLM
  -> assistant acknowledges in normal conversation
```

AgentLoop 只执行通用 policy、tool dispatch 和结果回填，不增加 Memory 业务分支。

## 8. 变更文件

```text
agent/tools/memory.py
agent/auth/tool_policy.py
agent/app/runtime.py
agent/cli.py
agent/protocols/tool.py
agent/core/loop.py
agent/auth/confirmation.py
agent/auth/store.py
agent/app/api/schemas.py
agent/app/api/routes.py
prompts/memory_policy.md
web/static/index.html
web/static/app.js
web/static/styles.css
tests/unit_test/memory/
tests/unit_test/tools/
tests/unit_test/auth/
tests/unit_test/agent_loop/
tests/unit_test/app/
README.md
docs_design/README.md
docs_design/zhice-agent-part10-memory-design.md
docs_design/zhice-agent-overall-design.md
```

## 9. 测试方案

| 场景 | 预期 |
| --- | --- |
| 用户明确要求记忆 | `memory_write` 直接执行，不请求 confirmation |
| 用户自然语言同意上一轮询问 | `authorization=user_confirmed` 直接执行 |
| 缺少用户授权类型 | policy 拒绝，不写入 |
| `assistant_inferred` 试图写入 | 参数或 policy 拒绝 |
| 模型主动识别 | prompt 要求先回答，再自然语言询问，不调用写工具 |
| 用户拒绝或语义不清 | prompt 要求不调用写工具 |
| Memory 内容含 secret/完整日志 | MemorySafetyPolicy 继续拒绝 |
| 普通用户/Owner | 继续使用各自既有 Memory 作用域 |
| 危险 exec | 原 confirmation 弹窗和确认行为不变 |

## 10. 验收标准

1. Web 不再为 Memory 展示独立 confirmation 或编辑控件。
2. Memory 不再创建 tool confirmation 数据或等待批准。
3. 用户明确要求或自然语言同意时，Memory 写入在当前 turn 直接完成。
4. 模型推断只能形成对话询问，不能直接写入。
5. 用户可以用自然语言同意、拒绝或改写候选，不需要 UI 按钮。
6. Memory 作用域、权限、安全过滤、摘要和日志脱敏保持有效。
7. 危险 `exec` confirmation 行为不受影响。
8. Ruff、相关测试和全量测试通过。
