# Part 13 Subagent 基础层测试说明

## 测试目标

验证 Subagent 协议、Profile 配置加载与 `FilteredToolProvider` 的 fail-closed 边界，确保 child 只能使用父 Provider 已暴露且 Profile 明确允许的能力。

## 用例覆盖

### Case 1：缺少配置

- 输入：运行态`config/config.yml`不存在或`subagents`分区缺失。
  - 预期：Subagent 是正常 disabled 状态，不影响应用启动。
- 输入：Subagent 配置无效或必需 Prompt 缺失。
  - 预期：仅 Subagent capability 标记为 unavailable，返回稳定错误码和修复提示，不抛出启动异常。
- 输入：子 Agent 构建上下文时缺失 `subagent.md`。
  - 预期：任务结果保留 `SUBAGENT_PROMPT_NOT_FOUND` 与 `context` stage，不折叠为通用失败。
- 预期：返回禁用配置，不创建隐式 Profile。

### Case 2：合法 Profile

- 输入：包含 explorer/developer、Skill allowlist、MCP server pattern 和资源限制的 YAML。
- 预期：保持 Profile 顺序，字段归一化，`delegate_tasks` 始终进入 denylist。

### Case 3：配置 fail closed

- 覆盖：未知字段、重复 YAML key、非法 glob、硬上限越界、空 Profile、非法 preload、`shared_readonly + exec`。
- 预期：统一抛出 `SubagentConfigurationError`，不部分启用。

### Case 4：schema 过滤

- 输入：父 Provider 同时暴露本地 Tool、多个 MCP server Tool 和 `delegate_tasks`。
- 预期：只返回 exact 与 `mcp__server__*` 命中的交集；denylist 和 kernel deny 再收窄；返回 schema 是深拷贝。

### Case 5：dispatch 双重过滤

- 输入：模型伪造未暴露 Tool、其它 MCP server Tool 或 `delegate_tasks` 调用。
- 预期：父 Provider 不被调用，返回 `SUBAGENT_TOOL_NOT_ALLOWED`。

### Case 6：可信上下文透传

- 输入：实现或未实现 `execute_with_context` 的父 Provider。
- 预期：新 Provider 收到完整 child/root/parent identity；旧 Provider 保持两参数兼容。

## 关键检查点

- 配置不能提升 actor、permission、credential 或 workspace 路径。
- Tool matcher 只接受 exact 和 `mcp__server__*`，不接受任意正则或宽泛 `*`。
- `shared_readonly` 禁止有效 `exec`，但允许 Profile 显式 deny 父侧存在的 `exec`。
- Profile 只能收窄父 Provider 当前 definitions，不生成父侧不存在的 Tool。

### Case 7：真实有界 fan-out/fan-in

- 输入：三个各耗时约 0.2 秒的独立 child，默认并发 3。
- 预期：墙钟耗时显著低于顺序总和，结果仍按输入顺序返回。
- 异常：一个 child 失败时，其它 completed 结果保留，形成 partial 语义。
- 并发上限：第四个 child 等待前三个 worker 中的空闲 slot。
- 取消/超时：单 child timeout 返回 bounded 结果；父 token 取消会把未完成 child 标为 cancelled。

### Case 8：AgentLoop 上下文感知委派

- 输入：Fake LLM 调用 `delegate_tasks`，随后基于 ToolResult 生成最终回答。
- 预期：Coordinator 收到可信 parent/root session、turn 和父 `tool.started` event id；父 Agent继续完成归纳。
- unavailable：startup 检查已关闭真实 Coordinator 时，auto Turn 只暴露不创建 child 的 `delegate_tasks` facade；用户明确要求 Subagent 时必须调用该 facade，禁止用 `exec` 冒充。
- presentation：CLI、本地操作者、Owner 和具备 `audit.read` 的管理员保留真实 message/hint；普通用户的 Web 命令与 unavailable facade 只返回通用联系管理员文案，内部 cause 继续写入 trace。

### Case 9：独立 child AgentLoop、Session 与 Event scope

- 输入：Child factory 使用独立 Fake LLM 和经过父能力交集过滤的 ToolProvider。
- 预期：child 只看到允许 Tool；transcript 写入 `_subagents/{root_session}` 且不进入普通 Session 列表；消息记录 parent turn，RuntimeEvent 带 agent/task/depth scope；父 Turn 的非空 ContextBudget 可传入 child，不因 builder 参数不匹配产生 TypeError。
- 未分类 child 异常：terminal Trace 保留经过脱敏和截断的 `error_message`，不能只留下 `error_type` 和通用包装码。
