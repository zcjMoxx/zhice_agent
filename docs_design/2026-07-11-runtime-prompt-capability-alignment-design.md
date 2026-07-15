# 运行时 Prompt 能力口径对齐设计

## 背景

当前 Gateway 已向 LLM 提供 `list_dir`、`read_file`、`grep`、`exec`、Skill 和诊断等工具 schema，并通过 RBAC 决定具体调用是否允许。但仓库 `prompts/identity.md` 仍声明 Agent 处于“第二阶段的纯对话模式”，与实际能力冲突。

该冲突已经产生真实故障：`user001` 明确要求调用 `exec` 查询系统时间时，本轮 `llm.call` 已携带 7 个工具定义，账号也拥有 `tool.exec.safe`，模型却受旧身份 Prompt 影响直接声称“不能实际调用 exec”，没有生成 tool call。

`prompts/tool_use_policy.md` 也仍使用“当前阶段提供”的阶段化表述，并缺少两条关键约束：真实运行状态不能猜测；用户明确要求调用已提供工具时，不能未经尝试就声称工具不可用。

进一步检查发现，当前 `${ZHICE_AGENT_WORKSPACE}/prompts/` 是 6 月初始化时复制的旧模板，内容更早：`tool_use_policy.md` 明确要求模型遇到工具请求时直接声称不能执行，`skills_intro.md` 也仍声明 Skill 尚未接入。Gateway 以 workspace Prompt 为运行真值，因此本次必须同步修正当前 workspace 的三份 Prompt；仓库 Prompt 继续作为新 workspace 的默认模板。

## 目标

1. 移除运行时 Prompt 中与阶段编号绑定的过时身份描述。
2. 明确 ZhiCe-Agent 能够调用运行时所提供的工具 schema。
3. 对系统时间、文件、日志、进程、端口、测试结果等真实状态要求先验证再回答。
4. 用户明确要求调用某个已提供工具时，除非请求不安全或不适用，否则实际调用。
5. 工具被权限或安全策略拒绝时，根据真实结构化错误说明，不能预先编造“无法调用”。
6. 禁止从 `session_id`、历史回答或模型记忆猜测当前时间。

## 范围边界

- 只调整运行时 Prompt 契约，不新增 Tool、不修改 AgentLoop 调度协议。
- 不强制所有问题调用工具；纯知识问答仍直接回答。
- 不绕过 RBAC、workspace guard、危险命令确认或其它工具安全策略。
- `skills_intro.md` 当前与实现一致，本次不修改。
- 当前时间自动注入和专用时间 Tool 属于后续独立改进，不混入本次 Prompt 修复。

## 模块设计

### Identity Prompt

- 保留 ZhiCe-Agent 的身份和协作目标。
- 删除“第二阶段”“纯对话模式”。
- 明确运行时可能提供工具和 Skill，实际能力以本轮 tool schemas 为准。

### Tool Use Policy Prompt

- 去掉“当前阶段提供”的阶段化措辞。
- 增加显式工具请求规则。
- 增加真实状态验证规则。
- 增加工具不可用声明规则：只有 schema 不存在、调用被拒绝或调用失败后才能基于证据说明。
- 增加当前时间规则：使用系统查询结果，不从 session id 或历史文本推断。

## 数据流

```text
ContextBuilder loads current prompts
  -> LLM receives identity + tool policy + tool schemas
  -> user asks for real system state or explicitly requests a tool
  -> LLM emits tool_call
  -> AgentLoop applies RBAC and safety policy
  -> tool result returns to LLM
  -> LLM answers from verified result
```

## 变更文件

- `prompts/identity.md`
- `prompts/tool_use_policy.md`
- `${ZHICE_AGENT_WORKSPACE}/prompts/identity.md`
- `${ZHICE_AGENT_WORKSPACE}/prompts/tool_use_policy.md`
- `${ZHICE_AGENT_WORKSPACE}/prompts/skills_intro.md`
- `tests/unit_test/prompt_loader/test_prompt_loader.py`
- `tests/unit_test/prompt_loader/test_case.md`

## 测试方案

| 用例 | 预期 |
|---|---|
| identity 内容审计 | 不含阶段编号和“纯对话模式”，声明按运行时 schemas 使用工具 |
| tool policy 显式请求 | 用户明确要求可用工具时要求实际调用 |
| tool policy 真实状态 | 时间、日志、进程等必须先验证，禁止猜测 |
| 工具拒绝 | 只能根据实际结构化拒绝/失败结果说明原因 |
| skills prompt | 保持现有 source-aware Skill 使用规则不变 |

## 验收标准

1. 运行时 Prompt 不再声明旧阶段或纯对话模式。
2. 明确要求 `exec` 的安全请求不会再因 Prompt 自我限制而直接拒绝。
3. 当前时间等真实状态不会从 session id 或历史回答推断。
4. 权限与安全策略仍由现有运行链路执行。
5. Prompt 契约测试、Ruff 和全量 pytest 通过。
