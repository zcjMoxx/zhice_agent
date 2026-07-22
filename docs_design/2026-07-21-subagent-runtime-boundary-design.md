# ZhiCe-Agent 并行 Subagent Runtime 边界设计记录

> 日期：2026-07-21
>
> 状态：方案已按当前活文档落地；实现与后续维护以 `docs_design/zhice-agent-part13-subagent-design.md` 为准
>
> 当前实施依据：`docs_design/zhice-agent-part13-subagent-design.md`
>
> 后续补充：启动能力分级、child terminal trace 与父 Turn 诊断下钻见 `docs_design/2026-07-21-startup-capability-and-subagent-diagnostics-design.md`。当前代码没有实现本记录中设想的 Profile 可配置 MCP per-server semaphore，而是复用现有 workspace-shared MCP Runtime 和 server 状态边界；该项不属于 Part 13 已关闭范围。
>
> 承接：`docs_design/zhice-agent-part12-hooks-design.md`、`docs_design/zhice-agent-part11-mcp-design.md`、`docs_design/zhice-agent-part9-user-auth-permission-design.md`

## 1. 背景

Part 12 已完成统一 AgentLoop、actor-scoped Tool、Memory、MCP、RuntimeEvent 和 Hook Runtime。Part 13 要让主 Agent 把多个边界清晰的子任务交给独立子代理，并在子代理完成后统一归纳。

初稿采用“同步、单层、只读、顺序执行”，能够形成最小委派闭环，但存在两个明显问题：

1. 多个互不依赖的任务仍顺序等待，没有发挥 Subagent 的并行价值。
2. 一刀切禁用 `exec`、MCP 和 Skill，使代码验证、测试、数据分析和专业工作流无法真正交给子代理。

本记录在代码尚未落地的前提下直接修订同日方案，改为“有界并行 fan-out/fan-in + 能力 Profile + 父权限交集”。

## 2. 成熟项目参考

### 2.1 OpenAI Agents SDK

官方 Agent orchestration 文档把“Agents as tools”定义为：manager 保持用户对话控制，把 specialist 当作 Tool 调用，组合多个 specialist 输出并统一执行 guardrail。对于互不依赖的任务，官方建议通过 `asyncio.gather` 等代码编排原语并行执行。示例 Agent 能力包括文件检索、计算机操作和代码执行。

参考：<https://openai.github.io/openai-agents-python/multi_agent/>

### 2.2 LangChain / LangGraph

官方 Subagents 文档采用 supervisor-as-tools：主 Agent 决定调用哪些子代理、给什么输入、如何合并结果。文档明确说明主 Agent 可以在一个 Turn 调用多个子代理并行执行；子代理可以拥有与主 Agent 相同的能力，委派的核心价值之一是隔离上下文并只返回精简结果。

参考：<https://docs.langchain.com/oss/python/langchain/multi-agent/subagents>

### 2.3 Claude Code

Claude Code 子代理支持：

- 前台或后台执行；
- 并行 research；
- 独立上下文和 transcript；
- `tools` allowlist、`disallowedTools` denylist；
- Bash、Edit、Write、Skill、MCP；
- 继承父会话权限并进一步收窄；
- PreToolUse Hook；
- worktree 隔离并行文件修改。

其官方示例既有只读 code reviewer，也有使用 Bash/Edit 的 debugger、使用 Bash/Read/Write 的 data scientist，以及通过 Hook 把 Bash 限制为只读 SQL 的 database validator。

参考：<https://docs.anthropic.com/en/docs/claude-code/sub-agents>

### 2.4 对 ZhiCe-Agent 的结论

成熟方案的共同点不是“子代理必须只读”，而是：

```text
子代理能力 = 父代理当前可用能力
            ∩ 子代理 Profile 明确允许能力
            - Profile 明确拒绝能力
            - 内核永远禁止的越权能力
```

因此 Part 13 不应硬编码禁用 Skill、`exec` 或 MCP。它们应按 Profile、actor 权限、workspace 隔离、确认、Hook 和 Tool 自身 guard 受控开放。

## 3. 修订后的核心结论

1. 主 Agent 通过批量工具 `delegate_tasks` 一次提交 1～4 个子任务。
2. 子任务默认使用有界 Worker Pool 并行执行，默认并发数为 3。
3. 父 Turn 在同一次委派调用中等待所有 child 进入终态，再统一获得结果；这属于并行 fan-out/fan-in，不是脱离父 Turn 的后台任务。
4. 每个 child 使用独立 Session、独立 AgentLoop 实例和 call-scoped LLMProvider，但复用同一个 AgentLoop 实现。
5. 子代理能力由命名 Profile 决定，模型不能直接提交任意 tool names、权限或 workspace。
6. `exec`、Skill、MCP 可以按 Profile 开放；有效能力永远不超过父 Turn 当前可见能力和 actor 权限。
7. 可能写 workspace 的并行 child 必须进入独立 worktree；无法创建 worktree 时只能进入 shared-exclusive 串行通道，不能与同 workspace 的其它 child 并发。
8. child Tool 继续经过 schema、RBAC、危险确认、Hook、workspace guard、timeout、脱敏、SSRF 和 MCP artifact 边界。
9. 第一阶段仍固定最大 depth=1；child 不暴露 `delegate_tasks`。
10. child 结果按输入顺序形成结构化数组，允许部分成功；主 Agent负责交叉检查和最终归纳。
11. 主 Agent默认直接完成任务；只有并行、上下文隔离、专业能力或独立复核收益明显时才调用 Subagent。
12. 不增加一次“是否需要委派”的预判 LLM 调用；决策由主 Agent现有请求理解、Prompt 规则和 Tool schema 共同约束。
13. 顶层帮助只新增主命令 `/subagent`；裸命令显示状态和 Profile，并在 `Tip:` 中提示 `auto/off/once`。`once` 只强制下一条普通消息使用一次 Subagent，不永久开启，也不绕过任何安全限制。

## 4. 并行语义

### 4.1 调用决策门槛

自动调用 Subagent 至少满足以下一项：

1. 存在 2 个及以上互不依赖、可以同时执行的任务分支。
2. 一个子任务会读取大量文件、日志或搜索结果，放在父上下文会造成明显膨胀。
3. 子任务需要专业 Profile、Skill、MCP 或隔离 workspace。
4. 用户明确要求独立复核、多角度审查或并行调查。

以下情况默认禁止自动委派：

- 简单事实问答、解释、翻译或改写。
- 读取一个明确文件、查一个函数、运行一个明确命令。
- 主 Agent已经拥有完成任务所需上下文。
- 子任务存在严格先后依赖，无法并行。
- 委派开销预计高于直接完成成本。
- 用户明确要求快速、简短、不要展开。
- 只是为了“看起来像多代理”而拆分同一件事。

长规则放在 `prompts/subagent_orchestration.md`，不写入 AgentLoop。主 Prompt 的核心要求是：默认直接完成；只有委派存在具体收益时才调用 `delegate_tasks`。

### 4.2 用户主动触发通道

所有渠道复用同一命令语义，但主帮助列表只展示 `/subagent`，不展开子命令：

```text
/subagent
```

裸命令输出参考当前 `/model` 风格：

```text
Current subagent mode: `auto`
Force once: `false`

Available profiles:
- `explorer` - read, search, inspect, and summarize
- `developer` - run commands and tests in an isolated worktree

Tip: use `/subagent auto` to allow automatic delegation, `/subagent off` to disable it, or `/subagent once` to use Subagent for the next message.
```

Tip 中的详细形式：

```text
/subagent auto      默认模式：主 Agent按收益自动决定
/subagent off       当前 Session 禁止自动委派
/subagent once      下一条普通消息要求使用一次 Subagent，消费后恢复原模式
```

状态存入当前 Session sidecar metadata，不写入 Session Message：

```text
subagent_mode = auto | off
subagent_force_once = true | false
```

规则：

- `once` 是一次性显式用户意图，可允许单 task 委派，reason=`explicit_user_request`。
- `once` 不要求硬拆成多个 child；如果任务只有一个合理分支，可使用一个专业或隔离 child。
- `once` 不提升 actor 权限，不改变 Profile，不绕过确认、Hook、workspace mode、timeout 或并发上限。
- Subagent 配置不可用时，下一条消息应返回明确说明，不能静默伪装成已委派。
- `/subagent off` 只关闭自动调用；用户之后执行 `/subagent once` 可显式覆盖一次。
- Web 可把同一状态映射为“自动 / 关闭 / 下轮使用”控件，但服务端命令和 Session metadata 是语义真值。
- 外部渠道只有声明支持该命令时才展示；不支持命令的渠道仍可通过普通自然语言明确要求，最终由主 Agent调用同一 Tool。

### 4.3 fan-out/fan-in

```text
父 Agent 调用 delegate_tasks
  ├─ child A 并行运行
  ├─ child B 并行运行
  └─ child C 并行运行
         ↓
等待全部 completed / failed / cancelled / timed_out
         ↓
按任务输入顺序返回结果数组
         ↓
父 Agent 归纳并回答用户
```

父 Agent仍拥有最终答案。child 不直接回复用户。

### 4.4 与后台任务的区别

Part 13 第一阶段不实现“父 Agent先回复用户、child 后台继续跑、稍后通知”的 Job 模式。父 Turn 需要 child 结果完成归纳，因此当前调用会等待 fan-in。

界面不会静默阻塞：RuntimeEvent 持续展示每个 child 的启动、LLM、Tool、等待确认、完成或失败状态。

### 4.5 并发实现

当前 LLMProvider 和 AgentLoop 是同步接口。第一阶段使用有界 `ThreadPoolExecutor` 或等价 Worker Pool，不为了并行强行把整个内核改写为 async。

每个 child 必须拥有：

- 独立 AgentLoop 实例；
- 独立 call-scoped LLMProvider；
- 独立 SessionStore root；
- 独立 ToolProvider wrapper；
- 独立 CancellationToken；
- 独立 RuntimeEventEmitter；
- 独立 worktree 或明确的共享访问模式。

共享的配置、Skill catalog、MCP catalog、auth store、Hook 定义和日志 sink 必须只读或自身线程安全。对非线程安全 MCP connection 按 server 建立 semaphore。

## 5. delegate_tasks Tool

工具参数：

```json
{
  "reason": "parallel_independent",
  "tasks": [
    {
      "id": "implementation",
      "task": "检查 AgentLoop 当前 Tool 调用链并给出代码锚点",
      "profile": "explorer",
      "expected_output": "结论、证据、风险"
    },
    {
      "id": "tests",
      "task": "运行相关单元测试并分析缺口",
      "profile": "developer",
      "expected_output": "测试结果、失败原因、建议"
    }
  ]
}
```

约束：

- `reason` 必须是 `parallel_independent`、`context_isolation`、`specialist_capability`、`independent_verification` 或 `explicit_user_request`。
- `reason=parallel_independent` 时必须至少包含 2 个 task。
- `tasks` 数量 1～4。
- `id` 在当前调用内唯一，1～64 字符。
- `task` 1～4000 字符。
- `expected_output` 最多 1000 字符。
- `profile` 必须是运行态配置中存在且允许模型调用的 Profile。
- 模型不能提交 tool names、Skill 路径、MCP credential、workspace path、permission mode、model 或并发数。

## 6. Profile 设计

运行态配置位于：

```text
${ZHICE_AGENT_WORKSPACE}/config/subagents.yml
```

仓库只提交：

```text
config/subagents.example.yml
```

示例结构：

```yaml
enabled: true
max_parallel: 3
max_tasks_per_call: 4
max_depth: 1

profiles:
  explorer:
    description: Read, search, inspect Skills, and summarize.
    tools: [list_dir, read_file, grep, load_skills, memory_read]
    denied_tools: [delegate_tasks]
    workspace_mode: shared_readonly
    model_role: fast
    max_tool_iterations: 10
    timeout_seconds: 180

  developer:
    description: Inspect code, use Skills, run commands, test, and prepare isolated changes.
    tools: [list_dir, read_file, grep, load_skills, memory_read, exec]
    denied_tools: [delegate_tasks, sync_skills]
    workspace_mode: worktree
    model_role: inherit
    max_tool_iterations: 20
    timeout_seconds: 600

  operator:
    description: Use explicitly configured MCP or operational tools under inherited permissions.
    tools: [list_dir, read_file, grep, load_skills, memory_read, "mcp__approved__*"]
    denied_tools: [delegate_tasks]
    workspace_mode: shared_exclusive
    model_role: inherit
    max_tool_iterations: 15
    timeout_seconds: 300
```

配置加载失败、Profile 名称重复、未知字段、非法 glob、危险 permission override 或 workspace mode 无效时 fail closed。

`model_role` 只能选择运行态已有的受控角色，例如 `inherit`、`fast` 或 `reasoning`；模型不能在 Tool 参数中直接选择 endpoint。默认 explorer 使用更快、更便宜的 endpoint role，降低并行委派开销。

## 7. Tool 能力

### 7.1 有效集合

```text
effective_tools = parent_visible_tools
                  ∩ profile.tools
                  - profile.denied_tools
                  - kernel_denied_tools
```

`kernel_denied_tools` 第一阶段至少包含 `delegate_tasks`，保证 depth=1。其它 Tool 不因名称被内核一刀切禁止。

### 7.2 exec

`exec` 可以开放，但必须同时满足：

- 父 actor 当前可见并有权调用 `exec`；
- Profile 明确允许 `exec`；
- child workspace mode 满足并行隔离规则；
- 命令继续经过 schema、RBAC、危险确认、Hook、shell policy、workspace guard、timeout 和输出截断；
- child 不能通过 Profile 获得父 Agent没有的确认豁免。

`exec` 不等于自动写入。只读诊断、ruff、pytest、git diff/status 等可以正常使用；真正修改文件的命令仍受现有安全链和 workspace 隔离约束。

### 7.3 Skill

Part 13 复用当前 Skill 模型：`SKILL.md + scripts + load_skills + exec`。

- Profile 可允许 `load_skills`。
- Profile 可配置 `allowed_skills` 和 `preload_skills`。
- 有效 Skill 是父 actor 当前可见 Skill 与 Profile 规则的交集。
- preload 只把选定 Skill 正文放入 child context，不意味着自动授权脚本执行。
- Skill 脚本仍通过已有 `exec` 示例执行，并继续经过全部安全检查。
- Part 13 不伪造 SkillExecutor、`skill.*` 或 ProgressSink；这些仍属于 Part 18。

因此“子代理可使用 Skill”与“Part 18 独立 Skill Runtime 尚未实现”不冲突。

### 7.4 MCP

MCP 不再全局禁用，但默认 Profile 不使用宽泛 `mcp__*`：

- Profile 必须明确列出 server/tool 或受控 pattern。
- Tool 必须已经在父 actor 当前 catalog 中可见。
- credential、连接和 OAuth 继续由 workspace MCP Runtime 管理。
- artifact 继续写入当前 actor 范围。
- Elicitation/确认必须显示具体 child id 和任务 id。
- 同一 server 并发是否允许由 MCP Runtime capability/semaphore 决定。
- 未声明副作用的 MCP Tool 不获得额外信任，仍由 Profile 作者承担显式开放责任。

### 7.5 Memory 和全局变更

`memory_read` 可按 Profile开放。`memory_write`、`sync_skills` 等共享状态变更不是内核永久禁止项，但默认 Profile 应拒绝；若未来显式开放，必须使用 `shared_exclusive`、原有权限和确认，并补充专项测试。

## 8. workspace 并行隔离

### 8.1 shared_readonly

适用于不包含潜在写 Tool 的 Profile。多个 child 可直接并行读取同一个 actor workspace。

### 8.2 worktree

适用于允许 `exec`、Edit/Write 类能力或代码修改的 Profile：

- 每个 child 创建独立 git worktree。
- child 的 workspace guard 指向自己的 worktree。
- child 不能访问其它 child worktree。
- child 完成后返回 changed files、diff summary 和 worktree id。
- 不自动 merge、commit 或覆盖主 checkout。
- 主 Agent后续根据用户请求决定是否审查、应用或丢弃改动。

worktree 创建失败时不能悄悄回退到共享可写目录。

### 8.3 shared_exclusive

用于非 Git workspace 或必须操作共享状态的 Profile。调度器为 actor workspace 获取独占 lane：

- 同一 workspace 同时只允许一个 shared-exclusive child。
- exclusive 运行期间不启动同 workspace 的 shared_readonly child，避免读取不一致状态。
- 其它 actor/workspace 的任务仍可并行。

## 9. 父子 Session 与上下文

每个 child 使用独立 Session：

```text
{actor_sessions_dir}/_subagents/{root_session_id}/{child_session_id}.jsonl
```

child 默认获得 fresh context：

- 自己的 system prompt；
- Profile prompt；
- 委派 task；
- expected output；
- 必要 workspace/session facts；
- 允许的 Skill 摘要或 preload Skill；
- 不自动复制父 Session 完整历史。

父 Agent应在 task 中提供必要事实。未来如需 fork 整个父上下文，应单独增加 `context_mode=fork`，不能与默认隔离模式混为一谈。

## 10. AgentLoop 与协议

新增：

```text
SubagentCoordinator
SubagentProfileProvider
SubagentRequest / SubagentTask / SubagentTaskResult
ContextualTool / ContextualToolProvider
FilteredToolProvider
RuntimeEventScope
```

并行时不是让多个线程共享同一个 AgentLoop 对象，而是：

```text
同一个 AgentLoop 类和协议
  -> 每个 child 构造独立实例
  -> 注入独立 llm/session/context/tools/workspace/token/event scope
```

这样满足“不写第二套 Loop”，同时避免共享实例的隐式可变状态和线程安全风险。

`ToolExecutionContext` 增加可选：

```text
tool_started_event_id
source=subagent
root_session_id/root_turn_id
parent_session_id/parent_turn_id
subagent_id/task_id
```

## 11. RuntimeEvent

继续使用 Part 12 的 turn/context/LLM/tool 类型。增加可选父子字段：

```text
agent_id
parent_agent_id
parent_session_id
parent_turn_id
root_session_id
root_turn_id
task_id
depth
```

父 `delegate_tasks.tool.started` 是整个 batch 的 parent event。每个 child 有独立 sequence；前端按 `(agent_id, session_id, turn_id, sequence)` 排序。

前端展示并行状态：

```text
正在并行执行 3 个子任务
  implementation  正在读取代码
  tests           正在运行测试
  docs            已完成
2/3 已完成，1 个运行中
```

不展示思维链、完整 prompt、敏感工具参数或绝对 worktree 路径。

## 12. 结果、失败和归纳

ToolResult 输出为有界 JSON 文本或稳定可解析结构：

```json
{
  "status": "partial",
  "results": [
    {
      "id": "implementation",
      "status": "completed",
      "output": "...",
      "subagent_id": "subagent-...",
      "duration_ms": 12000
    },
    {
      "id": "tests",
      "status": "timed_out",
      "code": "SUBAGENT_TIMEOUT",
      "output": "已完成的部分结果..."
    }
  ]
}
```

规则：

- 一个 child 失败不自动取消其它 child。
- 结果按输入 tasks 顺序返回，不按完成先后打乱。
- 超时/失败可返回安全的 partial output。
- 父 Agent收到全部结果后进行去重、冲突判断和最终归纳。
- 不增加隐藏的第二次“摘要模型调用”；父 Agent正常下一轮 LLM 负责归纳。

## 13. 取消与确认

- 父 Turn 取消时，Coordinator 取消所有 child token。
- 尚未启动的 task 标记 cancelled。
- 正在运行的 child 在现有 cancellation check 处停止。
- 同步 provider 阻塞调用仍受当前 provider 取消能力限制。
- child 的确认请求必须显示 task id、Profile、Tool 和 child id。
- 任何 Agent 消息都不能代替用户批准高风险确认。
- 一个 child 等待确认时，其它 child 可以继续执行。
- batch 总超时到达后取消未完成 child，并返回 completed/partial 结果。

## 14. 默认限制

```text
max_depth = 1
max_tasks_per_call = 4
max_parallel = 3
max_subagents_per_parent_turn = 6
max_batches_per_parent_turn = 1
max_task_chars = 4000
max_expected_output_chars = 1000
max_result_chars_per_task = 12000
max_batch_result_chars = 32000
```

并发和 Profile 资源限制由 `subagents.yml` 配置，但必须受代码硬上限约束，不能配置为无限。

### 14.1 延迟预算

- 自动委派默认每个父 Turn 最多一个 batch，避免连续 fan-out 拉长响应。
- explorer 使用 fast model role 和更小的 context/tool schema。
- 每个 child 有 Profile timeout，batch 有总 deadline。
- deadline 到达后返回 completed/partial 结果，不无限等待最慢 child。
- 简单任务不为“并行”额外创建 child。
- 不为调用判断增加额外 preflight LLM 请求。
- activity 记录 `delegation_reason`、`queue_ms`、`child_duration_ms` 和 `batch_wall_ms`，用真实数据评估收益。

## 15. Activity、Audit 和日志

新增 Runtime Activity：

```text
subagent.batch_started/completed/partial/failed/cancelled
subagent.task_started/completed/failed/timed_out/cancelled
subagent.worktree_created/released
```

Security Audit 只记录安全相关事实：权限拒绝、Hook block、确认、越权 Profile、workspace/worktree 边界失败和高风险 Tool。

日志关联：

```text
request_id
root_session_id/root_turn_id
parent_session_id/parent_turn_id
batch_id
task_id
subagent_id
child_session_id/child_turn_id
profile
workspace_mode
```

不记录 task/output 全文、secret、Memory 内容或未脱敏绝对路径。

## 16. 实施顺序

1. Subagent/Profile/Coordinator 协议和配置 Loader。
2. ContextualTool 执行缝隙和 FilteredToolProvider。
3. child ContextBuilder 与 `prompts/subagent.md`。
4. AgentLoop child override 和独立实例工厂。
5. RuntimeEvent 父子/batch/task 关联。
6. actor-scoped child Session root。
7. bounded parallel Coordinator、结果顺序和 partial failure。
8. workspace shared-readonly/shared-exclusive 调度。
9. worktree isolation 和生命周期回收。
10. Skill preload/allow rules。
11. MCP 显式 Profile allowlist 和 per-server concurrency。
12. `delegate_tasks` Tool。
13. Web/CLI/前端接入。
14. cancellation、confirmation、activity/audit/log。
15. 正常、异常、并发、竞争、隔离和 E2E 测试。
16. ruff、pytest、前端检查和活文档同步。

## 17. 验收标准

Part 13 关闭必须满足：

1. 主 Agent 可一次提交至少 3 个独立任务并真实并行运行。
2. 子代理通过同一 AgentLoop 实现执行，独立实例之间没有共享 Turn 状态。
3. batch 等待 fan-in 后返回顺序稳定的 completed/partial 结果。
4. Skill、`exec`、MCP 可由 Profile 受控开放，而不是硬编码全禁。
5. child 有效能力严格等于父可见能力与 Profile 的安全交集。
6. 可写并行任务使用 worktree；无法隔离时进入 shared-exclusive，绝不共享并行写。
7. actor、RBAC、确认、Hook、workspace guard、timeout、脱敏、SSRF 和 artifact 边界不能降低。
8. child 独立 Session 不污染普通会话列表。
9. 父子/batch/task RuntimeEvent、activity 和日志可完整关联。
10. 单 child 失败、超时或取消不丢失其它已完成结果。
11. Web 和 CLI 使用同一 Profile/权限/并发语义。
12. 全量测试通过并将活文档更新为真实实现状态。
13. 简单问答、单文件读取和单命令用例默认不调用 Subagent。
14. 自动委派不增加额外 preflight LLM 调用，并受单 Turn batch 数和总 deadline 限制。
15. `/help` 只列顶层 `/subagent`；裸命令按 `/model` 风格显示状态并在 `Tip:` 中提示 `auto/off/once`。CLI、Web 和支持命令的外部渠道保持同一 Session 语义。

## 18. 不属于 Part 13 第一阶段

- 父 Agent先回复、child 跨 Turn 后台继续的 Job 系统。
- depth 大于 1 的递归 Agent tree。
- Agent 之间自由 SendMessage 或长期团队协作。
- 自动 merge/commit/push child worktree。
- 子代理自行改变 Profile、权限、模型或 workspace。
- Profile 可配置的 MCP per-server semaphore；当前仅复用既有 MCP Runtime 连接与 server degraded 状态。
- 无上限并发、无上限 token 或无上限 Session。
- 独立 SkillExecutor、`skill.*` 和 ProgressSink。
- 远程 worker、跨进程队列和生产分布式调度。

这些方向需要基于 Part 13 的真实并发和安全数据另行设计。
