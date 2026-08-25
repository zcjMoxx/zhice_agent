# 智策 Agent 第十三部分详细设计文档：并行 Subagent 编排

> 状态：已实现并关闭；有界并行 Coordinator、Profile 能力交集、独立 child Runtime、workspace 隔离、`/subagent` Session 语义与测试均已进入当前代码基线
>
> 日期设计记录：`docs_design/2026-07-21-subagent-runtime-boundary-design.md`
>
> 启动分级与诊断闭环：`docs_design/2026-07-21-startup-capability-and-subagent-diagnostics-design.md`
>
> 承接文档：`docs_design/zhice-agent-part12-hooks-design.md`

## 1. 目标

Part 13 增加由主 Agent 统一编排的并行 Subagent：主 Agent 一次拆分多个独立子任务，子代理在隔离上下文中并行执行，完成后把结构化结果返回主 Agent，由主 Agent交叉检查、归纳并面向用户回答。

核心模式采用成熟项目常见的 manager/supervisor + agents-as-tools：

```text
用户
  -> 主 Agent
       -> delegate_tasks
            ├─ explorer child
            ├─ developer child
            └─ operator child
       <- structured results
  -> 主 Agent 归纳最终答案
```

Part 13 不是把子代理限制成只能读文件。Skill、`exec` 和 MCP 均可通过能力 Profile 受控开放，但 child 永远不能获得父 actor 当前没有的能力。

## 2. 当前基线

代码已经具备：

- 通用 `AgentLoop.run_turn()`；
- LLM/Session/Context/Tool/workspace override；
- per-turn actor-scoped ToolProvider；
- RBAC、危险确认、Hook 和 Tool guard；
- Memory、SkillLoader、MCP Runtime；
- RuntimeEvent、Runtime Activity、Security Audit；
- Web/CLI 当前 Turn 与取消链路。

Part 13 当前已经实现：

- `delegate_tasks` 批量 Tool 与有界 `ThreadPoolExecutor` fan-out/fan-in；
- fail-closed `config.yml` `subagents` Profile Loader、父 Tool/Skill/MCP 能力交集和 schema/dispatch 双重过滤；
- context-aware Tool dispatch，普通旧 Tool 保持两参数兼容；
- 每个 task 独立 AgentLoop、LLM provider facade、child Session、CancellationToken 和 RuntimeEvent scope；
- shared-readonly、进程级 shared-exclusive lane 与 Git worktree lease；
- completed/partial/failed/cancelled 结果、单 child timeout、父取消广播和稳定输入顺序；
- CLI/Web `/subagent auto|off|once` Session sidecar、原子 one-shot 消费和前端 child task 状态；
- transport-neutral capability status、结构化终端/trace warning、Web health，以及 Subagent/MCP/后台 Memory extraction 的局部启动降级；
- child terminal 安全 trace 和从父 `delegate_tasks` Turn 沿 root IDs 下钻的自助诊断；
- Runtime Activity、Security Audit、Hook/RBAC/确认链复用和专项测试。

Part 13 本身仍不包含跨 Turn 后台 Job、depth > 1、自动 merge/commit/push 或远程 worker。独立 SkillExecutor 已由 Part 18 实现；child 只有同时命中父 actor source 可见性、Profile Skill allowlist 和 `run_skill` Tool 权限时才能使用。

## 3. 设计原则

### 3.1 主 Agent 始终拥有最终答案

child 只处理子任务并返回结果，不接管用户会话。主 Agent负责：

- 选择是否委派；
- 拆分 task；
- 选择 Profile；
- 判断哪些任务可以并行；
- 收集结果；
- 处理冲突、失败和遗漏；
- 给用户最终回答。

### 3.2 复用同一 AgentLoop 实现

每个并行 child 使用独立 AgentLoop 实例，但全部来自同一个 AgentLoop 类和协议。禁止复制 LLM/tool loop 或新写 SubagentLoop。

### 3.3 能力取交集，不一刀切

```text
child effective capability
  = parent turn visible capability
  ∩ selected profile allow rules
  - profile deny rules
  - kernel depth/safety deny rules
```

Profile 只能收窄或选择父能力，不能提升角色、绕过确认或创建新 credential。

### 3.4 并行写必须隔离

只读任务可以共享 workspace 并行读取。包含 `exec` 或其它潜在写能力的并行任务必须进入独立 git worktree；不能隔离时进入 shared-exclusive 串行 lane。

### 3.5 失败允许部分成功

一个 child 失败、超时或取消，不应抹掉其它 child 已完成结果。batch 返回 `completed/partial/failed/cancelled` 和每个任务的独立状态。

### 3.6 默认直接执行，委派必须有收益

Subagent 不是每个请求的默认步骤。主 Agent先判断自己能否低成本直接完成；只有委派存在明确收益时才调用。

允许自动委派：

- 至少两个互不依赖的分支可以并行。
- 单个分支会产生大量临时上下文，适合隔离后只返回结论。
- 需要专门的 developer/operator Profile、Skill、MCP 或 worktree。
- 需要独立复核、对抗审查或多角度结论。
- 用户明确要求并行代理或子代理。

默认不委派：

- 简单问答、翻译、改写或概念解释。
- 读取一个文件、找一个函数或运行一个明确命令。
- 主 Agent已经掌握所需上下文。
- 所有步骤存在严格顺序依赖。
- 预计直接完成比创建 child 更快。
- 用户要求快速、简短或低延迟。

调用判断不额外请求一次 LLM。规则通过主 Agent Prompt、Tool description、结构校验和回归 eval 约束，AgentLoop 不硬编码业务意图。

### 3.7 自动触发与用户主动触发

Part 13 有三种触发来源：

```text
自动触发：主 Agent在 subagent_mode=auto 时按收益决定
明确请求：用户在普通消息中明确要求并行/子代理调查
确定性触发：用户执行 /subagent once，强制下一条普通消息使用一次 Subagent
```

用户主动控制仍只暴露一个顶层主入口，具体子命令由裸命令输出的 Tip 提示：

```text
/subagent
```

主动触发只改变“是否使用 Subagent”，不改变“Subagent 能做什么”。Tool、Skill、MCP、`exec`、workspace 和确认仍由 actor 与 Profile 决定。

## 4. 为什么改为并行

代码检查、测试检查、文档检查、外部资料检索等任务通常互不依赖。顺序执行会把总耗时近似累加：

```text
sequential ≈ A + B + C
parallel   ≈ max(A, B, C) + orchestration overhead
```

OpenAI Agents SDK 建议对独立 Agent 使用 `asyncio.gather` 等并行编排；LangChain 明确支持主 Agent 在同一 Turn 调用多个 subagent；Claude Code 提供 parallel research 和后台 Agent。

因此 Part 13 第一阶段就应包含有界并行，而不是把并行推迟成不确定的后续优化。

## 5. 并行模型

### 5.1 同一 Turn 内 fan-out/fan-in

```text
父 Turn
  -> fan-out: 提交 N 个 child task
  -> N 个 child 并行运行
  -> fan-in: 等待所有 child 终态或 batch timeout
  -> delegate_tasks 返回结构化结果
  -> 父 Agent继续 LLM 归纳
```

父 Turn 会等待结果，但不是顺序等待。用户可以通过 RuntimeEvent 看到每个 child 的实时状态。

### 5.2 不是后台 Job

当前不允许父 Agent先结束 Turn、child 继续跨 Turn 后台运行。真正后台任务需要 job id、状态查询、通知、跨进程恢复和清理策略，归入后续独立设计。

### 5.3 Worker Pool

同步内核使用有界 Worker Pool：

```text
default max_parallel = 3
hard max_parallel = 8
max_tasks_per_call = 4
max_batches_per_parent_turn = 1
```

每个 child 分配独立运行对象，不能共享同一个 AgentLoop 实例或可变 LLM preference。

## 6. delegate_tasks Tool

### 6.1 Schema

```json
{
  "type": "object",
  "properties": {
    "reason": {
      "type": "string",
      "enum": [
        "parallel_independent",
        "context_isolation",
        "specialist_capability",
        "independent_verification",
        "explicit_user_request"
      ]
    },
    "tasks": {
      "type": "array",
      "minItems": 1,
      "maxItems": 4,
      "items": {
        "type": "object",
        "properties": {
          "id": {"type": "string", "minLength": 1, "maxLength": 64},
          "task": {"type": "string", "minLength": 1, "maxLength": 4000},
          "profile": {"type": "string", "minLength": 1, "maxLength": 64},
          "expected_output": {"type": "string", "maxLength": 1000}
        },
        "required": ["id", "task", "profile"],
        "additionalProperties": false
      }
    }
  },
  "required": ["reason", "tasks"],
  "additionalProperties": false
}
```

### 6.2 为什么用批量 Tool

当前 AgentLoop 会顺序 dispatch 同一 LLM response 中的多个普通 Tool call。使用一个批量 `delegate_tasks` 可以把并发、超时、结果顺序、取消和 partial failure 收口在 Coordinator，不必把所有 Tool 调用都改成并行。

结构校验要求：`reason=parallel_independent` 时至少两个 task；单 task 只允许用于上下文隔离、专业能力、独立复核或用户显式触发。`reason` 不授予任何权限，只用于规则校验、日志和 eval。

### 6.3 模型不能控制

- 任意 tool list 或 denylist；
- Skill 本地路径；
- MCP credential/server inline config；
- actor/role/permission；
- workspace/worktree path；
- permission bypass；
- child model/endpoint；
- max parallel、timeout 和 depth。

模型只选择预先注册的 Profile。

## 7. Profile 配置

运行态：

```text
${ZHICE_AGENT_WORKSPACE}/config/config.yml#subagents
```

仓库模板：

```text
config/subagents.example.yml
```

Profile 字段：

```text
name / description
tools / denied_tools
allowed_skills / preload_skills
workspace_mode
max_tool_iterations
timeout_seconds
max_result_chars
allow_model_invocation
model_role
```

禁止出现：

- bypass permission；
- 自定义 actor；
- workspace 外路径；
- 明文 credential；
- 无上限并发/timeout/token；
- depth 大于代码硬上限。

### 7.1 explorer

适合代码检索、文档阅读、架构分析：

```yaml
tools: [list_dir, read_file, grep, load_skills, memory_read]
workspace_mode: shared_readonly
model_role: fast
```

### 7.2 developer

适合运行测试、复现问题、使用 Skill 脚本和准备代码修改：

```yaml
tools: [list_dir, read_file, grep, load_skills, memory_read, exec]
workspace_mode: worktree
model_role: inherit
```

### 7.3 operator

适合访问明确批准的外部系统：

```yaml
tools:
  - list_dir
  - read_file
  - grep
  - load_skills
  - memory_read
  - mcp__approved_server__*
workspace_mode: shared_exclusive
model_role: inherit
```

Profile 名称和描述只进入激活后的 `delegate_tasks` Tool description，不固定塞入主 Agent system prompt。当前所有父/child Tool 均先经过 `discover_tools` 按需激活。

`model_role` 由配置决定，模型不能通过 Tool 参数直接选 endpoint。读取和检索型 child 默认使用 `fast` role；只有确需复杂推理的 Profile 才使用 `inherit` 或受控 `reasoning` role。

## 8. ToolProvider 过滤

新增 `FilteredToolProvider`，同时过滤 schema 和 dispatch：

```text
definitions(): 只暴露 effective tools
execute():     再检查 effective tools
```

完成父能力交集与 Profile allow/deny 后，child 的 effective Provider 再由 Turn-scoped discovery 包装；首次只暴露 `discover_tools`，下一步只增加 child 已发现的最小 schema。隐藏 Tool 既不进入 catalog，也不能绕过 dispatch。

即使模型伪造隐藏 Tool call，也必须返回 `SUBAGENT_TOOL_NOT_ALLOWED`，不能透传父 provider。

Tool pattern 只支持明确规则：

- exact tool name；
- MCP server pattern，例如 `mcp__github__*`；
- 不支持任意正则；
- 不根据 Tool description 猜测副作用。

## 9. exec 设计

禁止所有 child 使用 `exec` 不合理，因为：

- 运行测试、ruff、git diff/status 需要 `exec`；
- 指令型 Skill 组合已有 Tool；显式可执行 Skill 通过 Part 18 `run_skill` 执行；
- 数据分析和诊断经常需要命令；
- 成熟 Agent 项目普遍允许按 agent profile 使用 Bash/code execution。

允许 `exec` 也不等于无条件执行。child 调用继续经过：

```text
Tool schema
-> actor RBAC
-> dangerous confirmation
-> pre Hook
-> shell policy
-> workspace guard
-> timeout/output truncation
-> post Hook
-> activity/audit
```

额外规则：

- Profile 未列出 `exec` 时 child 看不到也不能调用。
- 父 actor 无 `exec` 权限时 Profile 不能增加。
- worktree Profile 的 ExecTool root 指向 child worktree。
- shared-readonly Profile 不允许 `exec`，因为无法证明任意命令只读。
- shared-exclusive Profile 可用 `exec`，但同 workspace 不并发。
- 高风险确认显示 child/task/Profile 来源。

## 10. Skill 设计

### 10.1 Skill 可以使用

child 可以：

- 从 system prompt 看到允许 Skill 的摘要；
- 通过 `load_skills` 加载允许的 `SKILL.md`；
- 在启动时 preload 少量 Profile 指定 Skill；
- 在 Profile 同时允许 `exec` 时，按 SKILL.md 示例执行脚本。

### 10.2 两层控制

```text
effective_skills = parent_visible_skills ∩ profile.allowed_skills
```

`preload_skills` 只影响启动上下文，不自动扩大 allowed skills 或 Tool 权限。

### 10.3 与 Part 18 的边界

Part 13 落地时复用了 SkillLoader/`load_skills`/`exec` 链路且不伪造 Skill Event。当前 Part 18 已增加正式 Skill Runtime 和 ProgressSink；Subagent 在父 actor 可见 provider 上再取 Profile `allowed_skills` 和 Tool allow/deny 交集。

## 11. MCP 设计

MCP 可以按 Profile 开放，但默认必须显式：

```text
profile MCP pattern
∩ parent actor visible MCP tools
∩ server runtime policy
```

约束：

- 不允许 child 提交 inline server 或 credential。
- OAuth/credential 继续由 workspace Runtime 管理。
- artifact 写入当前 actor 范围。
- Elicitation 必须显示 child/task id。
- Part 13 复用现有 workspace-shared MCP Runtime 和每个 server 的连接/状态边界，不新增 Profile 可配置的 per-server semaphore；连接并发硬化留给后续 MCP Runtime 优化。
- Profile 使用宽泛 `mcp__*` 时 Loader 发出高风险配置警告；仓库默认模板不使用。
- MCP Tool 的副作用不会因为在 child 中执行而降低确认或审计要求。

## 12. workspace 并行模式

### 12.1 shared_readonly

多个 child 并行读同一 workspace。Profile 不得包含 `exec`、文件写入或共享状态修改 Tool。

### 12.2 worktree

允许代码修改或任意 `exec` 的并行 child 使用独立 git worktree：

```text
workspace/.zhice/subagents/{batch_id}/{task_id}/
```

实际路径从 workspace 派生并经过 guard。生命周期：

```text
创建 worktree
-> child 执行
-> 生成安全 diff summary
-> 保留到父 Turn 完成
-> 无改动自动清理
-> 有改动按保留策略等待父 Agent/用户处理
```

Part 13 不自动 merge、commit、push 或覆盖主 checkout。

### 12.3 shared_exclusive

非 Git workspace 或共享外部状态任务使用 actor-workspace exclusive lock。exclusive child 与同 workspace 的其它 child 不并发，但其它 workspace 仍可运行。

不能创建 worktree时必须返回清晰错误或进入显式配置的 exclusive fallback，不能静默共享并行写。

## 13. Subagent 协议

新增 `agent/protocols/subagent.py`：

```python
@dataclass(frozen=True)
class SubagentTask:
    task_id: str
    task: str
    profile_name: str
    expected_output: str = ""

@dataclass(frozen=True)
class SubagentBatchRequest:
    reason: Literal[
        "parallel_independent",
        "context_isolation",
        "specialist_capability",
        "independent_verification",
        "explicit_user_request",
    ]
    tasks: tuple[SubagentTask, ...]

@dataclass(frozen=True)
class SubagentTaskResult:
    task_id: str
    status: Literal["completed", "failed", "timed_out", "cancelled"]
    code: str
    output: str
    subagent_id: str
    child_session_id: str
    child_turn_id: str
    duration_ms: int
    truncated: bool = False

class SubagentCoordinator(Protocol):
    def run_batch(
        self,
        request: SubagentBatchRequest,
        context: ToolExecutionContext,
    ) -> tuple[SubagentTaskResult, ...]: ...
```

协议层不 import AgentLoop、WebRuntime、ThreadPoolExecutor 或 git 实现。

## 14. Context-aware Tool

`delegate_tasks` 需要可信获得父 actor/session/turn/tool event。增加向后兼容协议：

```text
ContextualTool.execute_with_context(args, context)
ContextualToolProvider.execute_with_context(name, args, context)
```

`ToolExecutionContext` 扩展：

```text
tool_started_event_id
source
root_session_id/root_turn_id
parent_session_id/parent_turn_id
subagent_id/task_id
```

普通旧 Tool 继续使用 `execute(args)`。AgentLoop 不判断 Tool 名称。

## 15. child AgentLoop 工厂

并行 child 不共享父 AgentLoop 对象。新增工厂按 task 创建：

```text
同一个 AgentLoop 类
  + call-scoped LLMProvider
  + child SessionStore
  + child ContextBuilder
  + FilteredToolProvider
  + child workspace
  + child CancellationToken
  + child RuntimeEventScope
```

Hook Runtime、activity/audit sink 等共享服务必须线程安全；不能确认线程安全的依赖通过锁或 per-child adapter 使用。

## 16. child Context

新增 `prompts/subagent.md` 和 Profile prompt。默认 child 不继承父完整历史，只获得：

- 自身身份与 Profile；
- task/expected output；
- workspace/session facts；
- 允许 Tool schema；
- 允许 Skill 摘要/preload；
- Memory policy；
- 项目级长期规则。

父 Agent负责把关键上下文写入 task。默认隔离可以保持父主上下文干净，并减少无关信息。

主 Agent新增 `prompts/subagent_orchestration.md`，只描述何时委派、何时禁止委派、如何拆成独立任务和如何归纳。child 自身行为继续放在 `prompts/subagent.md`。两者不能混用：child 不负责判断父 Agent是否应该委派。

## 17. Session

```text
{actor_sessions_dir}/_subagents/{root_session_id}/{child_session_id}.jsonl
```

- 每个 child 一个 session 和 turn。
- child Message 写自己的 turn id，并记录 parent turn id。
- child transcript 不进入普通 Web/CLI 会话列表。
- 父 Session 只保存 delegate_tasks call 和结构化 batch result。
- child 全部工具轨迹不复制进父上下文。
- 诊断可通过 root/batch/task/subagent id 查询。

## 18. RuntimeEvent

继续复用：

```text
turn.*
context.*
llm.*
tool.*
```

增加：

```text
agent_id / parent_agent_id
root_session_id / root_turn_id
parent_session_id / parent_turn_id
batch_id / task_id
depth
```

每个 child sequence 独立。Web 不跨 child 比较 sequence，只按 task 分组。

batch 展示示例：

```text
并行子任务 1/3：检查实现（运行中）
并行子任务 2/3：检查测试（等待确认）
并行子任务 3/3：检查文档（已完成）
```

## 19. 结果语义

batch 状态：

```text
completed: 所有 child completed
partial:   至少一个 completed，至少一个非 completed
failed:    没有 completed，且存在 failed/timed_out
cancelled: 父 Turn 取消且没有可用完成结果
```

每个 task 独立返回：

```text
task id
status/code
bounded output/partial output
subagent id
duration
worktree change summary（如有）
```

结果按输入顺序稳定排列。父 Agent依据结果进行：

1. 去重。
2. 区分事实、建议和未确认项。
3. 比较子代理冲突。
4. 必要时直接补充检查或再次委派。
5. 生成最终用户回答。

## 20. 取消、超时和确认

- 父 CancellationToken 派生 child tokens。
- 父取消会广播给全部 child。
- 单 child timeout 不取消其它 child。
- batch timeout 取消未完成 child并返回 partial results。
- provider 阻塞调用的强制中断仍受当前实现限制。
- 一个 child 等待用户确认时，其它 child 可以继续。
- 确认 UI 必须显示 task/Profile/Tool/child。
- 子代理或主 Agent的消息不能代替用户批准。

## 21. 资源限制

默认：

| 限制 | 默认 | 硬上限 |
| --- | ---: | ---: |
| 单次 tasks | 4 | 8 |
| 并行 child | 3 | 8 |
| depth | 1 | 1 |
| 每父 Turn child 总数 | 6 | 12 |
| 每父 Turn batch 数 | 1 | 2 |
| task 字符 | 4000 | 8000 |
| 单 child 结果字符 | 12000 | 24000 |
| batch 结果字符 | 32000 | 64000 |

Profile 可在硬上限内配置 timeout 和 tool iterations，不能配置无限值。

### 21.1 延迟控制

- 默认每个父 Turn 最多自动调用一个 batch。
- explorer 使用 fast model role、最小 Prompt 和最小 Tool schema。
- child 并行不是越多越好；默认并发 3，超过 worker 数进入队列。
- batch 设总 deadline，最慢 child 超时后返回 partial results。
- 不增加额外“是否需要 Subagent”的 preflight LLM 调用。
- Runtime Activity 记录 queue、执行和 fan-in 等待时间，用真实数据判断 Subagent 是否节省墙钟时间。

## 22. 错误码

```text
SUBAGENT_INVALID_BATCH
SUBAGENT_INVALID_TASK
SUBAGENT_UNKNOWN_PROFILE
SUBAGENT_PROFILE_DISABLED
SUBAGENT_LIMIT_REACHED
SUBAGENT_DEPTH_EXCEEDED
SUBAGENT_TOOL_NOT_ALLOWED
SUBAGENT_SKILL_NOT_ALLOWED
SUBAGENT_WORKTREE_FAILED
SUBAGENT_WORKSPACE_BUSY
SUBAGENT_TIMEOUT
SUBAGENT_CANCELLED
SUBAGENT_EMPTY_RESULT
SUBAGENT_FAILED
```

错误只返回安全摘要和可用 partial output，不返回 traceback、secret、完整 prompt 或绝对路径。

## 23. Activity、Audit、日志

Runtime Activity：

```text
subagent.batch_started/completed/partial/failed/cancelled
subagent.task_started/completed/failed/timed_out/cancelled
subagent.worktree_created/released
```

Security Audit：

- Profile/Tool/Skill 越权；
- RBAC 拒绝；
- 高风险确认；
- Hook block；
- workspace/worktree guard 失败；
- MCP credential/artifact 边界失败；
- shared-exclusive lock 异常。

日志只记录关联 ID、Profile、workspace mode、状态、duration、Tool 数量和有界摘要。

child terminal trace 额外保留安全诊断字段：`root_session_id/root_turn_id`、`parent_session_id/parent_turn_id`、`batch_id/task_id/subagent_id`、child 自身 `session_id/turn_id`、Profile、workspace mode、status、stage、code、error type、安全截断并脱敏的 error message 和 duration。`diagnose_my_recent_activity` 查询父 `delegate_tasks` Turn 时可沿 root IDs 下钻；除聚合结果外还向模型返回按时间排序、白名单过滤的 `trace_events`，由模型直接分析具体异常消息和前后因果链。

这个能力不能重建历史上从未写入的证据。旧 trace 若只有父 Tool 的通用 `SUBAGENT_FAILED`，没有 child terminal event 或具体 error message，则无法事后恢复 Prompt、LLM、workspace 或 Tool 的真实终因；诊断必须报告证据限制，不能让模型把规则生成的 probable cause 当成已确认事实。

## 24. Web 与 CLI

Web 和 CLI 都使用同一 Coordinator/Profile Loader：

```text
解析 actor/session/model/workspace
-> 构造 parent-visible ToolProvider
-> 构造 turn-scoped SubagentCoordinator
-> 注册 delegate_tasks
-> AgentLoop.run_turn
```

不按 transport 改变 Tool、Skill、MCP、exec 或并发权限。前端仅负责状态展示和已有确认交互。

### 24.1 统一主动控制命令

`/help` 只展示一个顶层入口：

```text
/subagent - show or control Subagent delegation
```

裸 `/subagent` 输出沿用 `/model` 的“紧凑状态 + Tip”风格：

```text
Current subagent mode: `auto`
Force once: `false`

Available profiles:
- `explorer` - read, search, inspect, and summarize
- `developer` - run commands and tests in an isolated worktree

Tip: use `/subagent auto` to allow automatic delegation, `/subagent off` to disable it, or `/subagent once` to use Subagent for the next message.
```

Tip 中提供详细用法，不在 `/help` 中逐项展开：

```text
/subagent auto
/subagent off
/subagent once
```

- 默认 mode 是 `auto`。
- `once` 是 one-shot flag，下一条非命令消息开始执行时原子消费。
- Turn 启动失败时不自动恢复 force-once，避免同一请求重试时重复创建 batch；用户可再次显式设置。
- `/clear` 清理 Session 消息时是否保留 mode 与现有 Session preference 规则一致；`force-once` 必须清除。
- Web 可增加“自动 / 关闭”和“下轮使用子代理”控件，最终写入同一 Session metadata。
- REST/WS 可增加向后兼容的可选 `subagent_mode` 请求字段，但不能形成区别于 Session 真值的第二套长期状态。

### 24.2 启动能力分级

当前启动边界按职责而不是按“是否可捕获异常”划分：

- 核心阻断：workspace/运行目录、基础 Prompt、LLM endpoint 和 Gateway Auth 等主流程依赖失败时，阻断对应 CLI/Gateway 入口。
- 能力局部禁用：未配置的 Skill source、MCP、Subagent 作为正常 disabled，不报警；显式配置的可选扩展依赖失败，以及系统内置 Memory extraction Prompt 缺失或非法时，只关闭对应能力并记录 warning；普通聊天、显式 Memory 读写和其它可用 Tool 继续。
- Hooks 安全策略阻断：用户显式配置的 Hook 是已声明安全策略，非法配置不得静默跳过，继续 fail closed 并阻断启动。
- 延迟使用失败：单个 MCP server、child worktree、Skill 或 Tool 在使用时返回具体 code/stage，不把整个应用标记失败。

Web `/api/health` 返回通用 capability 状态；`unavailable/degraded` 不改变整体 health 的 `ok`。Capability health 供自动化检查使用，聊天前端不常驻展示启动告警；Gateway 将可选能力异常统一写入结构化终端 WARNING 和 trace。Subagent unavailable 时，CLI、本地操作者、Owner 和具备 `audit.read` 的管理员可看到真实 message/hint；普通 Web 用户的裸 `/subagent`、`once`、unavailable `delegate_tasks` facade 和自助诊断只返回“暂时不可用，请联系管理员”，不暴露 Prompt 文件名、路径、初始化命令或内部 cause code。真实 `SUBAGENT_RUNTIME_UNAVAILABLE` 与具体 cause 继续保留在终端、trace 和有权限的诊断结果中，不创建多个伪 child failure。

## 25. 实际变更文件

新增：

```text
agent/protocols/subagent.py
agent/protocols/capability.py
agent/subagents/__init__.py
agent/subagents/config.py
agent/subagents/coordinator.py
agent/subagents/context.py
agent/subagents/factory.py
agent/subagents/runtime.py
agent/subagents/workspace.py
agent/subagents/startup.py
agent/tools/subagent.py
agent/tools/filtered.py
agent/session/subagent_preferences.py
agent/session/sidecar_lock.py
agent/mcp/startup.py
agent/memory/startup.py
prompts/subagent.md
prompts/subagent_orchestration.md
prompts/subagent_once.md
config/subagents.example.yml
tests/unit_test/subagents/*
```

修改：

```text
agent/protocols/tool.py
agent/protocols/runtime_event.py
agent/core/loop.py
agent/core/context.py
agent/core/event_emitter.py
agent/tools/__init__.py
agent/app/runtime.py
agent/cli.py
agent/session/__init__.py
agent/session/model_preferences.py
agent/auth/diagnostics.py
agent/mcp/__init__.py
agent/memory/__init__.py
agent/memory/extraction.py
.gitignore
web/static/app.js
web/static/runtime-event-state.js
web/static/styles.css
tests/unit_test/runtime_events/*
tests/unit_test/hooks/test_hook_runtime.py
tests/unit_test/app/*
tests/unit_test/cli/*
tests/unit_test/session_store/*
tests/unit_test/auth/*
tests/unit_test/mcp/*
tests/unit_test/memory/*
README.md
docs_design/README.md
docs_design/zhice-agent-overall-design.md
```

不为对齐清单创建空模块，最终按实际职责收敛。

## 26. 测试设计

### 26.1 真实并行

- 三个 Fake LLM child 同时开始，墙钟时间证明不是顺序执行。
- max_parallel 生效，第四个 task 等待 worker slot。
- 结果顺序按输入而非完成顺序。
- 单 child 失败/超时后其它 child 继续。

### 26.2 调用时机与延迟

- 简单事实问答不调用 `delegate_tasks`。
- 单文件读取、单函数定位和单命令执行不调用 `delegate_tasks`。
- 两个独立检索任务允许并行委派。
- 大量日志/文件读取允许用单 child 做 context isolation。
- 需要 developer Profile 的独立验证允许委派。
- `reason=parallel_independent` 且只有一个 task 时拒绝。
- `/subagent once` 产生 `explicit_user_request`，并在下一条普通消息开始时只消费一次。
- `/subagent off` 阻止自动委派，但不阻止用户随后显式执行 `once`。
- `once` 不绕过 Profile、权限、确认和 workspace isolation。
- `/help` 只断言包含 `/subagent`，不列 `auto/off/once`；裸 `/subagent` 的 Tip 必须包含三者。
- 每父 Turn 默认只允许一个 batch。
- 不发生额外 preflight LLM 请求。
- fast Profile 和 batch deadline 生效。

### 26.3 Tool/Profile

- parent visible ∩ profile allow - deny 计算正确。
- 隐藏 Tool schema 和 dispatch 双重拒绝。
- child 不能调用 delegate_tasks。
- Profile 不能增加父 actor 没有的 `exec`/MCP/Skill。
- 配置非法 fail closed。

### 26.4 exec/Skill/MCP

- developer child 可在 worktree 运行 ruff/pytest/Skill 脚本。
- explorer child 无 exec。
- preload Skill 不扩大 Tool 权限。
- 未允许 Skill/MCP 被拒绝。
- MCP Elicitation/confirmation 带 child/task id。
- MCP exact/server-pattern 能力交集、非法宽泛 pattern 拒绝和 optional startup checker 生效。

### 26.5 workspace

- shared-readonly child 可并行。
- 两个 worktree child 可独立修改同名文件而不互相覆盖。
- worktree 创建失败不回退共享写。
- shared-exclusive 与同 workspace 其它 task 互斥。
- 不同 actor/workspace 仍可并行。

### 26.6 Session/Event

- child Session 独立且不进入普通列表。
- parent/child/batch/task IDs 完整。
- 各 child sequence 独立单调。
- sink 失败不破坏 batch。
- 父 Session 只保存有界结构化结果。
- 父诊断可沿 root IDs 找到 child terminal stage/code；跨 actor trace 被拒绝。
- 多个同因 child terminal 可聚合；只有通用父包装码时不得 high confidence。

### 26.7 取消/确认

- 父取消广播全部 child。
- 等待确认 child 不阻塞其它 child。
- 拒绝一个 child Tool 后 batch 返回 partial。
- batch timeout 保留 completed result。

### 26.8 E2E

- Web 普通用户并行 explorer。
- Owner/CLI 并行 developer worktree。
- actor/workspace/Memory/artifact 不越界。
- 主 Agent收到结果后完成归纳回答。

新增测试主题目录必须维护 `test_case.md`。

## 27. 实施顺序

1. Profile/配置/协议。
2. ContextualTool 与过滤 Provider。
3. child Context/Prompt/Session。
4. AgentLoop factory 和 RuntimeEvent scope。
5. bounded Coordinator 和 partial result。
6. shared-readonly/shared-exclusive 调度。
7. worktree isolation。
8. exec/Skill Profile。
9. MCP Profile 与父能力交集，复用现有 MCP Runtime 状态边界。
10. delegate_tasks Tool。
11. Web/CLI/前端事件和确认。
12. activity/audit/log。
13. 并行、安全、竞争和 E2E 测试。
14. 全量验证和活文档同步。

## 28. Definition of Done

Part 13 关闭必须满足：

1. 一次委派至少 3 个独立 child 可真实并行执行。
2. 主 Agent等待 fan-in，获得稳定、有界、可部分成功的结果。
3. child 使用同一 AgentLoop 实现和独立实例。
4. Profile 能受控开放 Skill、`exec` 和 MCP。
5. child 能力永远不超过父 Turn 可见能力与 actor 权限。
6. 并行可写任务使用 worktree，无法隔离时明确互斥或失败。
7. RBAC、确认、Hook、guard、timeout、脱敏、SSRF 和 artifact 边界不能降低。
8. child Session/RuntimeEvent/activity/log 可完整关联且不污染普通会话。
9. 失败、超时、取消和确认不会丢失其它 child 结果。
10. Web/CLI 使用同一编排和权限语义。
11. 主 Agent能依据多个 child 结果完成去重、冲突判断和最终归纳。
12. ruff、pytest、前端检查和全量 E2E 通过。
13. 简单请求默认直接执行，Subagent 不成为每 Turn 固定前置步骤。
14. 自动委派没有额外 preflight LLM 调用，并受单 Turn batch 数、fast Profile 和总 deadline 约束。
15. CLI、Web 和支持命令的外部渠道只在主帮助中展示 `/subagent`；裸命令按 `/model` 风格用 Tip 提示 `auto/off/once`，one-shot 状态原子消费且不改变安全能力。

以上 15 项已满足，Part 13 已关闭；其 Tool 披露现已统一接入 Capability Selection。当前验证使用 `python -m ruff check .`、全量 `python -m pytest` 与 Vue 前端 test/lint/typecheck/build；具体通过数以当次命令输出为准。Windows 下遇到系统临时目录 ACL 问题时使用 repo-local basetemp。Hook 真实 fixture 的测试 timeout 只用于避免进程启动抖动，不改变运行时默认配置或 fail-closed 语义。

## 29. 后续方向

不属于第一阶段：

- 跨 Turn 后台 Job 和完成通知；
- depth > 1；
- Agent 间长期 SendMessage/团队协议；
- 自动 merge/commit/push；
- 远程 worker 和分布式队列；
- 子代理自行创建 Profile 或提升权限；
- Part 18 独立 SkillExecutor、`skill.*` 和 ProgressSink 已落地；Part 13 只负责能力交集，不拥有执行器。

第一阶段先完成并行 manager/worker 闭环及真实工具安全边界，再根据运行数据扩展。

Part 19 的旅行 Profile 不再复制到仓库全局模板，而由旅行应用在对应 Turn 内按阶段注入：候选前固定为交通天气、住宿景点、攻略避坑三个互斥 shared-readonly Profile，选择后固定为住宿价格和市内路线两个 Profile，depth 仍为 1。quick/deep 都使用相同并发结构；部分失败由父 Agent写入 unknowns 后继续 fan-in。Profile 请求可选 `fast` 角色，没有该角色端点时继承当前主模型。当前应用口径见 `docs_design/zhice-agent-part19-intelligent-travel-planner-design.md`。
