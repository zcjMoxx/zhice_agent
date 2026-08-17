# 智策 Agent 第十二部分详细设计文档：生命周期事件与 Hook 扩展点

> 状态：已实现并关闭；RuntimeEvent、渠道/前端状态、真实 Hook Runtime 与测试均已进入当前代码基线
>
> 设计记录：`docs_design/2026-07-20-hook-runtime-boundary-design.md`
>
> 承接文档：`docs_design/zhice-agent-part11-mcp-design.md`

## 1. 背景

ZhiCe-Agent 当前 Web 主聊天通道已经使用 `WebSocket /ws`。AgentLoop 通过 `on_event` 实时发出 `text_delta` 和工具确认事件，MCP Runtime 也能通过同一回调转发 Elicitation；WebSocket 层再把内部事件映射为 `channel_text`、`tool_confirmation_required` 和 `mcp_elicitation_requested`。

Part 12 落地前，前端在用户发送问题后主要只显示三个点的等待动画；当前 RuntimeEvent 已把上下文构建、等待 LLM、执行工具和整理回答映射为真实运行状态。该事件边界不依赖具体 Skill，Part 18 的 `skill.*` 事件也通过同一传输链进入前端，但由独立 SkillExecutor 产生。

Part 12 同时补齐统一的 Agent 生命周期 Event 和最小真实可运行 Hook Runtime：现有 WS、SSE、CLI 与前端消费 RuntimeEvent；配置化 `pre_tooluse` Hook 可以增加业务阻断或修改参数，`post_tooluse` Hook 可以为最终 Tool Event 增加受限展示。Hook 不替代核心安全，也不直接发送 WebSocket。Part 18 已在独立边界实现 SkillExecutor、`skill.*` 与 ProgressSink；它们不属于 Part 12 Hook 职责。

## 2. 核心判断

### 2.1 Event、WebSocket 和 Hook 的关系

```text
AgentLoop / Tool / Future SkillRuntime
  -> RuntimeEvent：描述发生了什么
  -> channel adapter：按渠道能力转换
  -> WebSocket / SSE / CLI：负责传输或展示
```

Hook 位于事件产生前后的扩展位置：

```text
core lifecycle
  -> optional Hook enrichment
  -> validated RuntimeEvent
  -> existing channel transport
```

- WebSocket 是传输通道。
- RuntimeEvent 是统一生命周期消息。
- Hook 是可选扩展逻辑，不能直接持有 WebSocket 或决定路由身份。

### 2.2 不展示模型思维链

LLM 不提供可信的“思考到第几步”。Part 12 只展示运行时可验证状态：

- 正在整理上下文。
- 正在请求模型。
- 模型返回了工具调用。
- 正在执行某个 Tool。
- 正在等待用户确认。
- 正在根据工具结果继续生成。
- Turn 已完成、停止或失败。

不展示 chain-of-thought，不生成虚假百分比，不把等待时间包装成伪进度。

### 2.3 核心安全不能外置

以下能力继续留在内核和具体 Tool：

- RBAC、危险确认和 actor/session 边界。
- workspace guard、跨用户目录隔离。
- 命令危险模式、timeout 和输出上限。
- Secret 脱敏、SSRF、MCP ArtifactGateway。
- Tool schema 和结果结构校验。

Hook 只能增加业务限制或展示信息，不能减少核心限制、跳过确认或把失败改成成功。

## 3. 目标

1. 建立 transport-neutral `RuntimeEvent` 协议。
2. AgentLoop 在 context、LLM、Tool 和 Turn 的真实生命周期节点发出标准 Event。
3. 复用当前 `on_event -> WebRuntime -> /ws` 链路，不新建第二条实时通道。
4. 前端用确定性状态替换单一三个点等待动画。
5. 同一 Turn 内通过 `sequence` 保证事件排序，多次 LLM/Tool 循环可以重复出现。
6. CLI、WebSocket 和 SSE 使用同一事件语义，按渠道能力选择展示方式。
7. 实现配置化、可注册和真实可执行的 `pre_tooluse` / `post_tooluse` Hook Runtime。
8. 固定 Hook 的执行顺序、无 shell 子进程、workspace path guard、最小环境、timeout、输出限制、JSON 结构校验和异常策略。
9. `pre_tooluse` 只能继续、增加阻断或修改参数；修改后的参数重新经过 Tool schema、RBAC、危险确认和具体 Tool 安全检查。
10. `post_tooluse` 只能补充受限 `display/ui_metadata`，不能修改 ToolResult 的真实成功/失败、输出或 metadata。

## 4. 非目标

- 不展示 LLM 隐藏推理或思维链。
- 不提供虚假完成百分比或预计剩余时间。
- 不让 Hook 直接调用 WebSocket、SSE response 或前端 API。
- 不把瞬态进度 Event 写入 Session JSONL。
- 不用 Hook 替代 RBAC、危险确认、workspace guard 或 Tool 自身安全检查。
- 不实现递归脚本扫描、热加载、在线安装、远端 Hook 或 Hook 市集；只加载 `${ZHICE_AGENT_WORKSPACE}/config/config.yml` 的 `hooks` 分区显式注册的本地 Python 脚本。
- 不通过解析 `exec.command` 猜测 Skill 名作为长期协议。
- Part 12 本身不实现 SkillExecutor、`skill.*`、ProgressSink、Subagent 或跨进程事件总线；其中正式 Skill Runtime 已由 Part 18 独立实现。

## 5. 当前代码基线

当前已实现链路：

```text
AgentLoop
  -> turn-scoped RuntimeEventEmitter
  -> on_event(RuntimeEvent / text_delta / interaction)
  -> WebRuntime.run_chat_events
  -> WebSocket runtime_event / SSE runtime / CLI Spinner
  -> web/static/runtime-event-state.js + app.js
```

当前 WS 事件包括：

```text
connected
hello
session_created
pong
channel_text
runtime_event
channel_status: accepted/done/stopped/error
tool_confirmation_required
mcp_elicitation_requested
mcp_elicitation_response
```

当前实现事实：

- RuntimeEvent 使用协议版本、稳定 type/status 和 turn-scoped sequence。
- AgentLoop 已覆盖 context/LLM/tool/turn 的正常、失败、停止和确认等待路径。
- WS/SSE/CLI 复用同一语义；旧交互事件保持独立兼容。
- 前端按 `turn_id + sequence` 更新安全短状态，不显示思维链或虚假百分比。
- `${ZHICE_AGENT_WORKSPACE}/config/config.yml` 的 `hooks` 分区显式注册真实 pre/post Hook；无配置时不创建 Hook 子进程。
- Activity/Audit/trace 继续承担持久诊断，RuntimeEvent 不写入 Session。

## 6. RuntimeEvent 协议

### 6.1 数据结构

```python
@dataclass(frozen=True)
class RuntimeEvent:
    protocol_version: int
    event_id: str
    type: str
    status: str
    timestamp: str
    sequence: int
    session_id: str
    turn_id: str
    request_id: str = ""
    tool_call_id: str = ""
    tool_call_record_id: str = ""
    parent_event_id: str = ""
    display: dict[str, Any] = field(default_factory=dict)
    ui_metadata: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)
```

字段原则：

- `type` 表达生命周期事实。
- `status` 只允许稳定状态值，如 `started/completed/failed/stopped/waiting`。
- `sequence` 在一个 Turn 内从 1 递增，用于并发队列和前端去乱序。
- `display` 是经过校验、允许直接展示的短信息。
- `ui_metadata` 是经过类型白名单、大小限制和脱敏校验的可选前端结构数据；核心 Event 默认留空。
- `metadata` 是有界结构化数据，不默认展示，不含 Secret、完整 prompt 或完整 Tool 参数。
- actor/user 身份不直接进入浏览器 Event；需要诊断时由 Activity/Audit 关联。

### 6.2 第一版 Event 类型

```text
turn.started
context.started
context.completed
context.failed
llm.started
llm.completed
llm.failed
tool.started
tool.completed
tool.failed
tool.waiting_confirmation
turn.completed
turn.stopped
turn.failed
```

现有交互事件继续保留：

```text
tool_confirmation_required
mcp_elicitation_requested
```

它们是需要用户响应的 channel interaction，不与只读 RuntimeEvent 混成同一种前端动作。

### 6.3 Part 12 之外的类型

```text
skill.started
skill.progress
skill.completed
skill.failed
subagent.started
subagent.progress
subagent.completed
subagent.failed
```

这些类型不属于 Part 12 原始协议白名单。Part 18 已通过明确 SkillExecutor 扩展 `skill.*`，仍禁止从命令字符串推断 Skill 事件。

## 7. Turn 状态机

普通聊天：

```text
turn.started
  -> context.started
  -> context.completed
  -> llm.started
  -> llm.completed
  -> turn.completed
```

包含工具调用：

```text
turn.started
  -> context.started
  -> context.completed
  -> llm.started
  -> llm.completed(has_tool_calls=true)
  -> tool.started
  -> tool.completed
  -> llm.started(reason=tool_result)
  -> llm.completed
  -> turn.completed
```

危险确认：

```text
tool.started
  -> tool.waiting_confirmation
  -> tool_confirmation_required
  -> approved: execute -> tool.completed
  -> denied/expired/cancelled -> tool.failed
```

一个 Turn 可以多次进入 `llm.started` 和 `tool.started`，前端不能假设线性只发生一次。

## 8. Display 设计

### 8.1 核心默认文案

核心提供短、可验证、与渠道无关的默认文案：

| Event | 默认展示 |
|---|---|
| `turn.started` | 已接收问题 |
| `context.started` | 正在整理上下文 |
| `llm.started` | 正在请求模型 |
| `tool.started` | 正在执行 `{tool_name}` |
| `tool.waiting_confirmation` | 等待操作确认 |
| `tool.completed` | `{tool_name}` 执行完成 |
| `tool.failed` | `{tool_name}` 执行失败 |
| 第二次 `llm.started` | 正在根据工具结果生成回答 |
| `turn.completed` | 已完成 |

`display` 示例：

```json
{
  "title": "正在执行 read_file",
  "detail": "",
  "icon": "tool",
  "visibility": "normal"
}
```

### 8.2 安全限制

- `title`、`detail` 有字符上限。
- `ui_metadata` 只接受已注册 `detail_type` 对应的结构，不允许任意 HTML、脚本、外部资源地址或未脱敏 ToolResult 透传。
- 不显示完整命令、完整路径、Prompt、Memory、credential 或用户隐私。
- `exec` 只显示安全摘要，如“正在执行命令”；危险确认继续使用已有 `command_preview` 安全入口。
- 失败只展示稳定错误摘要，完整证据留在 Activity/trace。

## 9. EventEmitter 设计

### 9.1 协议

```python
class RuntimeEventSink(Protocol):
    def emit(self, event: RuntimeEvent) -> None: ...
```

AgentLoop 不依赖 WebSocket。`on_event` 兼容入口内部适配为 `RuntimeEventSink`，以减少一次性改动。

### 9.2 发出规则

- Event 是 best-effort 观测能力，sink 异常不能中断 Turn。
- 每个 Event 在发出前完成 schema、长度和敏感字段校验。
- `sequence` 由 turn-scoped emitter 分配，不由 Hook 或前端提供。
- Event 只描述已经进入的真实阶段，不提前宣布成功。
- `tool.completed/failed` 必须在最终 ToolResult 确定后发出。
- stop/cancel/error 路径必须关闭仍处于 running 的前端状态。

## 10. WebSocket 与其它渠道

### 10.1 WS 信封

新增统一只读事件：

```json
{
  "event": "runtime_event",
  "session_id": "session-001",
  "turn_id": "turn-001",
  "data": {
    "protocol_version": 1,
    "event_id": "event-001",
    "type": "tool.started",
    "status": "started",
    "sequence": 5,
    "tool_call_id": "call-001",
    "display": {
      "title": "正在执行 read_file",
      "icon": "tool"
    },
    "ui_metadata": {},
    "metadata": {
      "tool_name": "read_file"
    }
  }
}
```

现有 `channel_text`、`channel_status`、确认和 MCP Elicitation 保持兼容。Part 12 不重写 WS request frame。

### 10.2 SSE

SSE 兼容接口使用：

```text
event: runtime
data: {RuntimeEvent JSON}
```

不支持新事件的旧客户端可以忽略，不影响 delta/done/error。

### 10.3 CLI

CLI 默认只原地更新当前短状态，避免刷屏：

```text
正在整理上下文...
正在请求模型...
正在执行 read_file...
正在根据工具结果生成回答...
```

debug/trace 仍记录完整生命周期，普通终端不打印每个 completed Event。

## 11. 前端状态展示

### 11.1 第一版 UI

将三个点替换为单行状态：

```text
● 正在整理上下文
```

收到新 Event 后原位更新：

```text
● 正在请求模型
● 正在执行 read_file
● 正在根据工具结果生成回答
```

Turn 完成后状态区消失，最终 Assistant 消息保持当前行为。

### 11.2 可选展开步骤

第一版可保留内存中的最近步骤，为后续折叠面板准备数据：

```text
✓ 整理上下文
✓ 请求模型
✓ 读取文件
● 生成回答
```

当前第一版不实现复杂时间线、进度条或持久化历史。

### 11.3 前端状态归并

- 按 `turn_id + sequence` 处理。
- 旧 sequence 不覆盖新状态。
- `completed/failed/stopped` 关闭 running 状态。
- 未知 Event type 忽略但记录 debug log。
- WS 重连后不回放瞬态 Event；前端通过 Session 和 active turn 状态恢复基础界面。

## 12. Hook Runtime

### 12.1 协议与注册

Part 12 不保留空 Enricher。核心只依赖真实 `HookRuntime` 协议：

```python
class HookRuntime(Protocol):
    def run_pre_tooluse(self, request: PreToolHookRequest) -> PreToolHookResult: ...
    def run_post_tooluse(self, request: PostToolHookRequest) -> PostToolHookResult: ...
```

运行时从 `${ZHICE_AGENT_WORKSPACE}/config/config.yml` 的 `hooks` 分区按配置顺序加载 Hook。配置只允许显式本地 Python 脚本，脚本路径解析后必须位于 `ZHICE_AGENT_WORKSPACE` 内；不扫描目录，不执行远端内容。

```yaml
version: 1
hooks:
  - name: restrict-exec
    stage: pre_tooluse
    script: extends/hooks/restrict_exec.py
    tools: [exec]
    exempt_roles: [owner]
    exempt_permissions: [tool.exec.dangerous]
    timeout_seconds: 2
    max_output_chars: 16384
```

配置文件不存在时返回空 Registry，AgentLoop 正常运行。`tools` 只允许精确 Tool name，或列表唯一值 `"*"`；`read_*`、`foo*bar` 等部分通配符非法。每个 Hook 可选配置 `exempt_roles` 与 `exempt_permissions`：缺省或空列表时对所有角色生效；显式角色或有效权限仅跳过当前 Hook，不形成 RBAC allow，也不跳过危险确认和 Tool 核心安全。owner/admin 没有全局自动豁免；owner 可按 Hook 显式角色豁免，admin 根据 `ActorContext.permission_keys` 中已生效的角色权限或直接授权匹配权限豁免。配置存在但结构、stage、tool matcher、角色/权限 key、路径或限制值非法时启动失败，避免“加载成功但永不匹配”或静默绕过管理员已声明的业务 Hook。

### 12.2 Runner 边界

Runner 使用当前 Python 解释器直接启动脚本：

```text
[sys.executable, script_path]
shell=False
cwd=ZHICE_AGENT_WORKSPACE
stdin=单个 JSON request
stdout=单个 JSON result
```

固定限制：

- 环境只保留 Python/Windows 运行必需项、`PYTHONUTF8=1`、`PYTHONIOENCODING=utf-8` 和 `ZHICE_AGENT_WORKSPACE`，不继承 API key、Authorization、proxy 或其它业务 Secret。
- stdin 有输入大小上限；stdout/stderr 分别有读取上限，超过上限终止子进程并返回稳定错误码。
- 每个 Hook 有短 timeout，并托管完整进程树：Windows 使用 `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE` Job Object，POSIX 使用独立 session/process group；timeout、输出超限、输入失败以及 Hook 正常退出后均回收所有派生进程，必要时升级为强制 kill，不遗留继续运行的 Hook。
- stdout 必须是一个 UTF-8 JSON object，不接受最后一行猜测、Markdown 或自由文本。
- Hook 日志不得进入 stdout；可写受限 stderr，但 trace 只记录 Hook 名、stage、duration、状态和稳定错误码，不记录完整 Tool 参数、ToolResult 或 Secret。

### 12.3 pre_tooluse 执行链

```text
parsed Tool call
  -> core Tool schema validation
  -> pre_tooluse hooks（配置顺序，后一个看到前一个的最终 arguments）
  -> modified arguments 再次 core Tool schema validation
  -> ToolExecutionPolicy / RBAC
  -> dangerous confirmation（使用最终 arguments）
  -> concrete Tool.execute（workspace guard / command policy / timeout / SSRF 等）
```

允许输出：

```json
{"action":"continue"}
{"action":"modify","arguments":{"path":"docs"}}
{"action":"block","code":"HOOK_BLOCKED","message":"当前项目禁止该操作"}
```

- `continue` 不改变参数。
- `modify` 只能替换当前 Tool 的 arguments object，不能改 Tool 名、actor、session、turn 或权限上下文。
- `block` 生成结构化失败 ToolResult，属于额外业务限制。
- pre Hook timeout、进程异常、输出超限、非法 JSON 或非法 action 一律 fail closed；Tool 不执行。
- pre/post Hook 在运行前按配置检查当前 `role_keys` 与 `permission_keys`；命中 `exempt_roles` 或 `exempt_permissions` 时只记录安全的 `hook.skipped` 和命中类型，并继续后续 Hook。无身份上下文不获得豁免。
- 即使 Hook 把危险参数改成表面安全值，最终参数仍重新经过 schema、RBAC、确认和具体 Tool 核心安全检查，Hook 无法声明“已确认”或跳过检查。Tool schema 由运行时依赖 `jsonschema>=4.23.0,<5` 完整校验，支持本地 `$ref/$defs`；外部引用、无效 schema 和无法解析的引用 fail closed，并保持 `INVALID_PARAM` 结构化错误码。

### 12.4 post_tooluse 与 Event enrichment

ToolResult 最终确定后，按配置顺序调用 `post_tooluse`：

```text
final ToolResult
  -> post_tooluse hooks
  -> validate display/ui_metadata patch
  -> tool.completed 或 tool.failed RuntimeEvent
```

允许输出：

```json
{
  "action": "enrich",
  "display": {"title": "资料搜索完成", "icon": "search"},
  "ui_metadata": {
    "detail_type": "search_results",
    "detail_data": {"items": []}
  }
}
```

`post_tooluse` 只接收有界输入，只能返回 `continue/enrich`。Runner 不接受 `is_error`、`output`、ToolResult metadata、event identity、sequence 或 status 修改；未知字段直接判为非法输出。多个 Hook 的 display/ui_metadata patch 按配置顺序合并，最终仍由 RuntimeEvent schema 做字段白名单、大小和 JSON 结构校验。

post Hook timeout、异常或非法输出 fail open：保留真实 ToolResult 和核心默认 Event，忽略本次展示增强并记录稳定诊断。Hook 不能把 `tool.failed` 改成 `tool.completed`，也不能让 ToolResult 失败变成功。

### 12.5 无 Hook 与真实 fixture

- 无 `hooks` 分区或空 `hooks.entries` 时不创建子进程，RuntimeEvent 与 Tool 主链保持完整。
- 测试提供 workspace 内真实 Python Hook fixture，覆盖 continue、block、modify、enrich、timeout、派生子进程树回收、非法输出和脚本异常。
- fixture 通过 stdin/stdout JSON 走真实 Runner，不直接 import Hook 脚本内部函数绕过边界。

## 13. Skill Runtime 边界

Part 12 落地时 Skill 还是 `SKILL.md + scripts` 指令包，因此 Part 12 不发 `skill.*`，不从 `exec.command` 推断 Skill，也不定义 ProgressSink。当前代码已由 Part 18 增加显式 runtime、SkillExecutor 和 `run_skill`。

SkillExecutor、`skill.started/progress/completed/failed`、真实中间进度与 ProgressSink 已按 Part 18 独立日期设计实现；Part 12 仍保持关闭。

## 14. Activity、Audit、trace 与 Session

| 数据 | 用途 | 是否持久化 |
|---|---|---|
| RuntimeEvent | 当前 Turn 实时状态 | 默认否 |
| Runtime Activity | 运行诊断与索引 | 是 |
| Security Audit | 权限、安全和管理操作 | 是 |
| trace | 详细技术排障 | 是 |
| Session Message | 对话上下文真值 | 是 |

RuntimeEvent 不替代 Activity/Audit。相同生命周期可以同时产生 Event 和 Activity，但 Event 使用更小、更安全的前端字段。

## 15. 模块设计

本次新增：

```text
agent/protocols/runtime_event.py
agent/protocols/hook.py
agent/core/event_emitter.py
agent/hooks/config.py
agent/hooks/loader.py
agent/hooks/runner.py
agent/hooks/runtime.py
agent/process_tree.py
agent/tools/schema.py
config/hooks.example.yml
tests/unit_test/runtime_events/
tests/unit_test/hooks/
```

本次修改：

```text
agent/core/loop.py
agent/app/runtime.py
agent/app/api/ws.py
agent/app/api/routes.py
agent/cli.py
web/static/app.js
web/static/runtime-event-state.js
web/static/styles.css
web/static/index.html
tests/unit_test/agent_loop/
tests/unit_test/app/
tests/unit_test/cli/
docs_design/README.md
docs_design/zhice-agent-overall-design.md
README.md
```

Hook Runtime 与 RuntimeEvent 同批进入 Part 12 Definition of Done；不保留外部 Runner 的开放尾巴。

## 16. 测试方案

### 16.1 RuntimeEvent

- schema、版本、状态枚举和长度限制。
- 同一 Turn sequence 单调递增。
- sink 异常不影响 Turn。
- display/metadata 不泄漏 prompt、Secret 或完整 Tool 参数。

### 16.2 AgentLoop

- 无 Tool 普通聊天的完整事件顺序。
- 单 Tool、多 Tool、多次 LLM 调用顺序。
- LLM error、Tool error、确认拒绝、stop/cancel 和 iteration limit。
- Event 不改变 Session Message 和最终回答。

### 16.3 WebSocket/SSE

- `runtime_event` 信封包含正确 session_id/turn_id。
- 现有 `channel_text`、done/error、confirmation 和 MCP Elicitation 兼容。
- 未知事件不关闭连接。
- 并发 Turn 不串 session/turn/sequence。

### 16.4 前端

- accepted 后显示首个状态。
- sequence 较旧的事件不覆盖新状态。
- done/stopped/error 清理 running 状态。
- 工具循环时状态可以多次从 LLM 切到 Tool 再回 LLM。
- WS 断开时当前状态结束并显示明确错误。

### 16.5 Hook Runtime

- 缺少配置和空配置时不启动 Hook 子进程，正常 Tool 链不变。
- 配置加载覆盖精确名称与独立 `*`、部分通配符拒绝、非法 stage、workspace 外脚本、重复名称和限制值。
- 身份作用域覆盖角色/权限缺省与空列表、显式 owner、admin 有/无有效权限、非法 key、多 Hook 独立跳过和无身份上下文。
- 真实脚本 fixture 覆盖 continue、block、modify、enrich。
- pre 修改后 schema 再校验，RBAC/确认/Tool 核心 guard 使用最终参数。
- schema 回归覆盖本地 `$ref/$defs`、required、type、additionalProperties，以及外部/无效/无法解析引用 fail closed。
- pre timeout、输出超限、非法 JSON、非法 action、脚本异常 fail closed。
- timeout 的真实派生子进程 fixture 验证父子完整进程树均被回收。
- post timeout、非法输出和异常 fail open，ToolResult 真实状态不变。
- post 只能修改 display 和允许的 `ui_metadata`，不能修改 event identity、sequence、status 或核心失败事实。

新增 `tests/unit_test/runtime_events/` 和 `tests/unit_test/hooks/` 时必须分别维护同目录 `test_case.md`。

## 17. 实施顺序

1. 定义 `RuntimeEvent`、状态枚举和 turn-scoped sequence emitter。
2. 在 AgentLoop 增加 turn/context/LLM/tool 生命周期打点。
3. 保留现有 `text_delta` 和交互事件兼容。
4. WebRuntime/WS 增加统一 `runtime_event` 转发。
5. SSE 与 CLI 对齐同一事件语义。
6. 前端用单行确定性状态替换三个点等待动画。
7. 定义 Hook 协议、配置与 Registry，加载 workspace 内显式 Python 脚本。
8. 实现无 shell、最小环境、完整进程树回收、timeout、输出限制和 JSON 校验 Runner。
9. 把 pre Hook 接在 schema 初验之后、RBAC/确认/Tool 核心安全之前；修改参数后重新校验。
10. 把 post Hook 接在最终 ToolResult 之后，只生成受限 Event display/ui_metadata patch。
11. 补齐 stop/error/confirmation/并发以及 Hook continue/block/modify/enrich/异常测试。
12. 更新当前活文档并完成 Definition of Done；Skill Runtime 后续由 Part 18 独立承接且现已落地。

## 18. 验收标准

1. 普通聊天无需 Tool/Skill 也能依次显示整理上下文、请求模型和完成状态。
2. Tool 调用可以显示开始、等待确认、完成或失败，并在下一次 LLM 调用时更新状态。
3. 所有状态来自真实生命周期节点，不展示思维链或虚假百分比。
4. Event 复用当前 WS，不建立第二条实时连接。
5. CLI、WS、SSE 使用相同 Event type 和状态语义。
6. sequence 保证同一 Turn 的事件顺序，并发 Turn 不串线。
7. RuntimeEvent 不写入 Session，不改变最终回答和上下文。
8. Event sink 或前端状态失败不影响 AgentLoop 主流程。
9. 现有文本流、确认、MCP Elicitation、stop 和 done/error 行为继续兼容。
10. 没有 Hook 配置时不创建子进程，所有核心状态和 Tool 正常工作。
11. workspace 内配置的真实 pre Hook 可以 continue/block/modify；block 只能增加限制，modify 后必须重新经过 schema、RBAC、危险确认和 Tool 核心安全。
12. 真实 post Hook 可以补充受限 display/ui_metadata，但不能修改 ToolResult、event identity、sequence、status 或核心失败事实。
13. Hook Runner 固定 `shell=False`、workspace cwd/path guard、最小环境、输入/输出上限、短 timeout、完整进程树回收、结构校验和稳定异常策略；Tool matcher 只接受精确名称或独立 `*`；角色/权限豁免只按单 Hook 显式配置生效。
14. pre Hook 失败 fail closed；post Hook 失败 fail open；两者均不泄漏完整参数、结果或 Secret 到 RuntimeEvent/日志。
15. 没有 SkillExecutor 时不得伪造 `skill.*`；当前 Part 18 的 SkillExecutor 与 ProgressSink 仍不是 Part 12 欠账。
16. pre Hook 最终参数使用完整 JSON Schema 校验，支持本地 `$ref/$defs`；外部、无效或无法解析引用 fail closed，且保持 `INVALID_PARAM` 兼容。
17. ruff、相关测试和全量 pytest 通过，或明确记录无关历史失败。

以上 17 项已满足，Part 12 已关闭。不得再以“外部 Hook Runner 后续实现”或“Skill Progress 后续补齐”重新打开 Part 12。

最终验证：`python -m ruff check .` 与两个前端脚本的 `node --check` 通过；加入单 Hook 显式角色/有效权限豁免后，全量 `python -m pytest -rs --basetemp .tmp/pytest_hook_permission_scope_full` 为 500 passed、1 skipped，跳过项是当前 Windows 环境不支持创建 symlink 的既有只读工具用例。

## 19. 和其它部分的关系

- Part 7 提供 `turn_id` 和 turn-based 运行单元；Part 12 的 sequence 只在 Turn 内排序。
- Part 8 提供 trace 和生命周期日志；RuntimeEvent 是前端实时视图，不替代 trace。
- Part 9 提供 actor、Activity、Audit、权限和确认；Event 只使用安全展示字段。
- Part 10 Memory 的后台提取不默认进入当前 Turn 状态，后续如需展示必须单独声明事件语义。
- Part 11 MCP 的 Elicitation 继续使用交互事件；MCP Tool 同样产生标准 tool lifecycle Event。
- Part 13 Subagent 复用 RuntimeEvent 协议，并通过 parent/child 标识扩展，不另写一套前端状态系统。
