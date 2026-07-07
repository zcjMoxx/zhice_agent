# ZhiCe-Agent Turn 运行单元与上下文治理未来设计记录

> 日期：2026-07-04
> 状态：未来设计记录，当前不代表已经实现。

> 说明：当前第七部分开发施工图已经收敛到 `docs_design/zhice-agent-part7-turn-context-design.md`，并通过 `docs_design/2026-07-06-context-relevance-selection-design.md` 补充了本地相关性选择。旧 JSONL、metadata fallback 和 legacy grouping 只保留为本历史记录中的旧方案背景，不再是当前实现目标。

## 1. 背景

当前 ZhiCe-Agent 已经有三个和 `turn` 相关但尚未统一的事实：

1. `AgentLoop.run_turn(session_id, user_text)` 天然表达“一次用户输入到一次最终输出”的运行边界。
2. Web runtime 已经有 `ActiveTurn(turn_id, token)` 和 `cancel_session(session_id)`，但这是内存态取消能力，未写入 Session 历史。
3. 总体设计已经把 Session 定义为上下文的一部分，并在后续上下文治理中明确提出：`ContextBuilder` 不应长期只按最近消息数裁剪，而应改成更接近参考项目的“按最近 N 轮 user turn”裁剪，并处理历史 tool 调用块。

这说明 `turn` 后续不只是 Web stop 的临时 id，而应该成为 Session 内可持久化、可查询、可用于上下文加载的运行单元。

本记录只做未来设计，不在当前阶段直接实现代码。它承接 `docs_design/2026-07-01-websocket-primary-chat-design.md` 中的 WebSocket、流式、`/stop` 和 active turn 方向，但把重点从“前端通道体验”下沉到 Agent core 与 Session 边界。

## 2. 当前问题

### 2.1 turn 没有持久边界

当前 `Message` 只有 `role`、`content`、`tool_calls`、`tool_call_id` 和 `metadata`。`SessionStore` 只提供 `load`、`append`、`clear`、`rename`、`delete`、`list_sessions`，没有 turn 级 API。

这导致 JSONL 中只能看到连续消息，无法稳定回答：

- 哪些消息属于同一轮用户请求。
- 哪一轮被停止、出错或正常完成。
- 某次工具调用结果归属哪一个用户 turn。
- Web 的 `turn_id` 和历史消息里的内容如何对应。

### 2.2 上下文裁剪仍以 message 为主

当前 `ContextBuilder` 先取 `history[-max_history_messages:]`，再做 OpenAI tool-call block 兼容转换。它已经会跳过孤立 tool 消息，也会尽量只保留完整的 assistant tool_calls + tool result block，但裁剪入口仍是消息数量。

问题是：一次用户 turn 可能包含 user、assistant(tool_calls)、tool、assistant(final) 等多条消息。按消息数裁剪时，仍可能把一个语义 turn 切开，或者让最近一次短问答挤掉更重要的同轮工具结果。

### 2.3 Web active turn 和 AgentLoop turn_id 不统一

WebSocket 路由当前会先生成一个 `turn_id` 发 `accepted`，随后 `WebRuntime.run_chat_events()` 又生成自己的 `turn_id`。未来如果不统一，前端可能看到 accepted/delta/done 的 turn id 不一致，停止、去重、历史恢复都会变脆。

### 2.4 CLI 现在不具备运行中 `/stop`

CLI 当前是同步输入：`input()` 收到一条文本后调用 `AgentLoop.run_turn()`，运行中没有第二条输入可以进入。因此 CLI help 不应宣称已有 `/stop`。未来 turn 模型可以为 CLI 中断能力打基础，但这不是当前实现事实，也不是本设计第一阶段的验收项。

## 3. 参考项目调查结论

参考项目 `C:\Users\84953\Desktop\sthg_nanobot_agent` 已经把 turn 当成跨运行时、Session、Web 展示和后处理的关键字段。可借鉴点如下。

### 3.1 可借鉴

- Session 消息模型包含 `turn_id` 字段。参考项目的 `SessionMessage` / `SessionPort.Message` 都把 `turn_id` 放在消息结构上，而不只放在临时事件里。
- AgentLoop 为每次处理生成 `turn_id`，并把它传入工具上下文、Session 追加、Web progress、tool step、最终状态等路径。
- active task registry 以 `session_key -> list[asyncio.Task]` 跟踪运行中任务，`/stop` 时按 session 取消主任务、子任务和 registry 状态。
- Web 前端 `sendStop()` 通过同一 WebSocket 发送 `content: "/stop"`，后端在 AgentLoop 入口前拦截，不透传给 LLM。
- Web 历史序列化会使用 `turn_id` 生成稳定 message id，例如 user/assistant 按同一 turn 分组，assistant 分片也能按 turn 合并。
- 历史上下文加载已有 `keep_recent` / `max_rounds` 思路：从最近 user turn 起点切片，并避免以孤立 tool result 开头。
- 参考项目预留了 turn lifecycle 协议，例如 `TurnStartEvent`、`ToolResultEvent`、`ChildTurnEvent`、`TurnEndEvent`，便于后续 memory、review、compaction 等能力挂在 turn 结束点。

### 3.2 不直接照搬

参考项目还包含多用户认证、多渠道、跨进程 registry、子代理、审批、memory review、ContextCompactor、followup merge、内部 session 类型等复杂系统。ZhiCe-Agent 第一阶段仍应保持轻量：

- 不引入跨进程 SQLite registry。
- 不引入多用户 ownership 和鉴权模型。
- 不引入子代理和 `parent_turn_id` 的完整体系，只预留字段。
- 不把 turn lifecycle hooks 一开始接成 memory/evolution 系统。
- 不为了 turn 先迁移数据库，继续保留 JSONL SessionStore。

我们要吸收的是边界思想，不是目录重量。

## 4. 设计目标

1. 把 `turn` 定义为 Session 内的一次用户请求生命周期。
2. 新消息持久化时带稳定 `turn_id`，同一轮 user/assistant/tool 消息共享同一个 `turn_id`。
3. 上下文加载支持“最近 N 轮 user turn”，而不是只按最近 N 条 message。
4. 历史 tool-call block 在 turn 裁剪后仍保持 OpenAI-compatible，不出现孤立 tool result。
5. Web active turn、WebSocket event、Session 持久消息使用同一个 `turn_id`。
6. `/stop` 的 Web 取消语义可以落到对应 turn 状态；CLI 未来也复用同一套 turn cancellation 模型，但当前不声明为已实现命令。
7. 兼容已有无 `turn_id` 的 JSONL Session，不要求迁移重写。

## 5. 非目标

- 本设计不把 CLI 运行中 `/stop` 当作当前已实现能力；但会给出未来实现方案和落地前置条件。
- 本设计不改变 `/sessions delete`、`/sessions rename` 的现有会话管理语义。
- 本设计不引入数据库、跨进程锁、多用户鉴权或子代理系统。
- 本设计不做长期记忆、自动压缩、review memory 的完整实现，只预留 turn-end 边界。
- 本设计不把 Web slash command 扩展成完整 CLI 终端。

## 6. 概念定义

### 6.1 Session

Session 是长期会话容器，保存历史消息和会话级元数据。它仍由 `SessionStore` 管理，对应当前 JSONL 文件与 sidecar metadata。

### 6.2 Turn

Turn 是 Session 内一次用户请求的完整生命周期：

```text
user input accepted
  -> context built
  -> LLM called
  -> optional tool calls
  -> final assistant / stopped / error
  -> messages persisted
  -> optional turn-end hooks
```

一个 turn 至少包含一条 user message，通常还会包含一条或多条 assistant/tool message。

### 6.3 Active Turn

Active Turn 是运行中的 turn。它用于取消、状态反馈和流式事件路由。Active Turn 可以只存在内存里，但它的 `turn_id` 必须和最终持久化消息一致。

### 6.4 Legacy Turn

Legacy Turn 指旧 JSONL 中没有 `turn_id` 的历史片段。读取时按 user message 边界临时推导，不重写旧文件。

## 7. 数据模型

### 7.1 Message 扩展

未来在 `agent/message.py` 增加可选字段：

```python
@dataclass
class Message:
    role: Role
    content: str
    name: str | None = None
    tool_call_id: str | None = None
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    turn_id: str | None = None
    turn_index: int | None = None
    parent_turn_id: str | None = None
```

说明：

- `turn_id` 是稳定 id，例如 `turn-<uuid>`。
- `turn_index` 是 session 内递增序号，用于排序、调试和未来压缩。
- `parent_turn_id` 先预留，后续如果引入子任务、后台任务或外部工具长流程，再用于关联父 turn。
- 旧代码仍可只构造前几个字段，因为新增字段都有默认值。

### 7.2 JSONL 记录格式

新记录建议将 turn 字段放在顶层，同时在读取时兼容旧 metadata 写法：

```json
{
  "role": "assistant",
  "content": "done",
  "timestamp": 1783080000.0,
  "turn_id": "turn-6f2c...",
  "turn_index": 12,
  "parent_turn_id": null,
  "name": null,
  "tool_call_id": null,
  "tool_calls": [],
  "metadata": {
    "finish_reason": "stop"
  }
}
```

读取优先级：

1. 顶层 `turn_id`、`turn_index`、`parent_turn_id`。
2. `metadata.turn_id`、`metadata.turn_index`、`metadata.parent_turn_id`。
3. 如果都不存在，则由 legacy grouping 推导。

### 7.3 TurnState

在 `agent/protocols/session.py` 增加轻量数据结构：

```python
TurnStatus = Literal["running", "completed", "stopped", "error"]

@dataclass
class TurnState:
    session_id: str
    turn_id: str
    turn_index: int
    status: TurnStatus
    created_at: float
    updated_at: float
    message_count: int = 0
    model: str = ""
    stop_reason: str = ""
    error_type: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)
```

第一阶段不一定要单独持久化 `TurnState` 文件，可以从消息扫描中派生。后续如果 session 很大，再增加轻量 turn index sidecar。

### 7.4 TurnGroup

`ContextBuilder` 不一定直接依赖 `TurnState`，可以使用更轻的运行期分组：

```python
@dataclass
class TurnGroup:
    turn_id: str
    turn_index: int | None
    messages: list[Message]
    legacy: bool = False
```

`TurnGroup` 是上下文裁剪的输入单位。

## 8. SessionStore 未来协议

为了不一次性扩大协议，可以分阶段推进。

### 8.1 Phase A：派生能力

先增加纯读取/派生能力，不要求新存储结构：

```python
class SessionStore(Protocol):
    def load(self, session_id: str) -> SessionState: ...
    def append(self, session_id: str, messages: list[Message]) -> None: ...
    def load_turns(self, session_id: str) -> list[TurnGroup]: ...
    def load_recent_turns(self, session_id: str, max_turns: int) -> list[TurnGroup]: ...
```

`JsonlSessionStore.load_turns()` 可以先通过 `load()` 得到 messages 后分组。这样保持实现简单，也不改变 JSONL 作为真值的原则。

### 8.2 Phase B：写入辅助

当 AgentLoop 开始稳定写入 turn 后，再增加写入辅助：

```python
class SessionStore(Protocol):
    def next_turn_index(self, session_id: str) -> int: ...
    def append_turn(self, session_id: str, turn_id: str, turn_index: int, messages: list[Message]) -> None: ...
```

`append_turn()` 内部只负责给没有 turn 字段的消息补齐，不改变 append-only 语义。

### 8.3 Phase C：显式 lifecycle

如果后续需要“运行中可见”“崩溃恢复”“turn-end hook”：

```python
class SessionStore(Protocol):
    def begin_turn(self, session_id: str, turn_id: str, user_message: Message) -> TurnState: ...
    def append_turn_messages(self, session_id: str, turn_id: str, messages: list[Message]) -> None: ...
    def finish_turn(self, session_id: str, turn_id: str, status: TurnStatus, metadata: dict[str, Any]) -> TurnState: ...
```

这一步会让 user message 在 LLM 完成前先落盘。它更利于 Web 刷新恢复，但也需要处理 running turn 崩溃后的补偿，所以不放在第一阶段。

## 9. Legacy JSONL 分组规则

已有 session 文件没有 `turn_id`，不能强制迁移。读取时按以下规则分组：

1. 如果消息有 `turn_id`，按 `turn_id` 聚合，保持原顺序。
2. 如果消息没有 `turn_id`：
   - 遇到 `role == "user"` 开启一个新的 legacy turn。
   - 之后连续的 assistant/tool/system 消息归入该 legacy turn，直到下一条 user。
   - 文件开头没有 user 的孤立消息归入 `legacy-orphan-0`，默认不参与最近 N 轮 user turn 裁剪。
3. legacy turn 的派生 id 使用稳定格式，例如 `legacy-<session_id>-<ordinal>`，只在内存中使用，不写回文件。
4. 如果一个 assistant 有 `tool_calls`，但后续 tool result 不完整，仍交给 `ContextBuilder` 的 tool block 兼容逻辑过滤。

## 10. AgentLoop 设计

### 10.1 turn_id 来源

`turn_id` 必须只生成一次。建议由 app shell 或 AgentLoop 明确分工：

方案 A：AgentLoop 生成

- WebSocket 发送 message 后等待 runtime 返回 accepted。
- `WebRuntime.start_turn()` 调 `AgentLoop.run_turn(..., on_turn_started=...)`。
- accepted 事件里的 `turn_id` 来自 AgentLoop。

方案 B：app shell 生成

- WebSocket 收到 message 后生成 `turn_id` 并立即发 accepted。
- `AgentLoop.run_turn(..., turn_id=turn_id)` 使用传入 id。

推荐方案 B。原因是 Web 可以在进入同步/线程执行前立刻反馈 accepted，也能把 stop frame 对准同一个 id。CLI 则可以不传，让 AgentLoop 内部生成。

### 10.2 run_turn 签名

未来扩展为：

```python
def run_turn(
    self,
    session_id: str,
    user_text: str,
    *,
    turn_id: str | None = None,
    on_event: TurnEventCallback | None = None,
    cancellation_token: CancellationToken | None = None,
) -> str:
    ...
```

AgentLoop 内部：

1. 解析或生成 `turn_id`。
2. 获取 `turn_index`。
3. 构造 user message，并写入 `turn_id`、`turn_index`。
4. ContextBuilder 用历史 turns 构建上下文。
5. assistant/tool/error/stopped 消息全部带同一个 `turn_id`。
6. append 时以一个 batch 写入，保持当前最小实现稳定。

### 10.3 停止语义

Web `/stop` 当前可以通过 `CancellationToken` 取消当前 active turn。未来应补齐：

- `cancel_session(session_id)` 继续存在，取消该 session 当前 active turn。
- `cancel_turn(session_id, turn_id)` 后续可选，支持更精确的外部控制。
- 被取消的 turn 持久化一条 assistant stopped marker，状态为 `stopped`。
- stopped marker 必须带 `turn_id`，避免前端把停止状态挂到错误消息上。

CLI 当前不加 `/stop` 到 help，也不把 `/stop` 透传给 LLM。未来如果要做 CLI 运行中 `/stop`，需要先引入并发 reader 或把 `Ctrl+C` 映射为 active turn cancellation；本设计在第 13 节给出未来方案。

## 11. ContextBuilder 设计

### 11.1 参数变化

当前：

```python
ContextBuilder(max_history_messages=30)
```

未来：

```python
ContextBuilder(
    max_history_turns=8,
    max_history_messages=60,
    max_message_chars=8000,
)
```

`max_history_messages` 保留为兜底硬上限，避免异常 turn 太大。

### 11.2 加载流程

```text
SessionStore.load_turns(session_id)
  -> group by turn_id or legacy user boundary
  -> select recent N user turns
  -> flatten selected turns to messages
  -> enforce max_history_messages hard cap at legal turn boundary
  -> _history_to_llm_dicts()
  -> append current user message
```

关键点：

- 先按 turn 选，再转 LLM messages。
- 选择最近 N 个 user turn，不包含 orphan legacy group。
- 如果超出 message 硬上限，从更旧的 turn 开始整体丢弃。
- 单个 turn 内如果 tool-call block 不完整，继续由 `_history_to_llm_dicts()` 丢弃不安全片段。

### 11.3 与未来压缩的关系

turn 是未来压缩的最小摘要单位。后续可在 `TurnState.metadata` 或 session metadata 中保存：

- `summary`
- `tool_summary`
- `token_count`
- `compacted_at`
- `compaction_version`

第一阶段不做自动压缩，只把 turn 边界和分组能力打牢。

## 12. Web / WS 设计

### 12.1 事件一致性

同一个 Web turn 的所有事件必须带同一个 `turn_id`：

```json
{"event":"channel_status","data":{"type":"accepted","turn_id":"turn-..."}, "session_id":"chat-20260701"}
{"event":"channel_text","data":"...", "session_id":"chat-20260701", "turn_id":"turn-..."}
{"event":"channel_status","data":{"type":"done","turn_id":"turn-..."}, "session_id":"chat-20260701"}
```

如果保留当前 `channel_text` 的简单 data 形态，也应至少在外层加 `turn_id`，让前端能过滤 stop 后迟到的 chunk。

### 12.2 Runtime active registry

当前：

```python
_active_turns: dict[str, ActiveTurn]
```

未来先保持每 session 一个 active turn：

```python
_active_turns: dict[str, ActiveTurn]  # session_id -> active turn
```

`ActiveTurn` 增加：

```python
@dataclass
class ActiveTurn:
    turn_id: str
    token: CancellationToken
    started_at: float
```

如果以后允许同一 session 并发多个 turn，再升级为：

```python
_active_turns: dict[tuple[str, str], ActiveTurn]
```

### 12.3 Web 历史展示

Web 历史 API 可以继续返回 message 列表，但每条 message 带 `turn_id`。前端可以：

- 用 `user:<turn_id>` 作为用户消息稳定 key。
- 用 `assistant:<turn_id>` 或 `assistant:<turn_id>:<phase>` 作为助手消息稳定 key。
- stop 后只抑制同一个 turn 的迟到 chunk，不影响后续新 turn。

## 13. CLI 设计边界与 `/stop` 未来方案

### 13.1 当前边界

当前 CLI 仍是同步输入模型：

- `/new` 创建并切换新 session。
- `/reset` 清空当前 session。
- `/sessions` 展示本地 session。
- `/sessions rename` 和 `/sessions delete (<id>)` 属于会话管理。
- `/stop` 不在 CLI help 中展示，也不是当前可用命令。

turn 模型落地后，CLI 可以自然获得：

- history 打印时可按 turn 展示。
- `/history` 后续可选择展示最近 N turns。
- `Ctrl+C` 后续如果要保存 stopped marker，可以复用同一 turn 状态模型。

### 13.2 为什么 CLI `/stop` 不能只加一个 slash command

当前 CLI 的基本形态是：

```text
input() waits for user text
  -> AgentLoop.run_turn(...)
  -> print final answer
  -> input() waits again
```

当 `AgentLoop.run_turn()` 正在执行 LLM、工具或流式输出时，主线程已经不在 `input()` 上等待下一条命令。用户此时即使想输入 `/stop`，CLI 也没有独立通道接收这条命令。

所以 CLI `/stop` 不是简单加一行命令分支。它至少需要：

- active turn registry：CLI 也要知道当前 session 正在跑哪个 `turn_id`。
- cancellation token：`/stop` 能触发当前 turn 的 token。
- 并发输入通道：运行中仍能接收 `/stop`。
- 输出协调：停止时 spinner/streaming 输出要收束，不能和新 prompt 混在一起。
- Session 持久化：停止后写入同一 `turn_id` 的 stopped marker。

### 13.3 未来 CLI `/stop` 目标体验

未来实现后的交互目标：

```text
> 帮我跑一个很长的任务
... assistant is working ...
> /stop
stopped: turn-...
>
```

行为约定：

- `/stop` 只在 CLI 命令层处理，不能作为普通 user message 写入 LLM 上下文。
- 有 active turn 时，`/stop` 取消当前 session 的 active turn。
- 没有 active turn 时，`/stop` 返回简短提示，例如 `No active turn.`。
- 停止后 Session 中追加 assistant stopped marker，带同一个 `turn_id`。
- stopped turn 后续参与 history 展示，但 ContextBuilder 可按规则选择是否带入上下文。
- `/help` 只有在 CLI 真实实现运行中 stop 后，才加入 `/stop` 一行。

### 13.4 CLI Runtime 未来结构

未来可以在 CLI 层引入轻量 `CliRuntime`，不要把 CLI 特有并发逻辑塞进 `AgentLoop`：

```text
CLI input reader
  -> command router
     -> /stop: CliRuntime.cancel_current_turn()
     -> other slash command: existing local handlers
     -> normal text: CliRuntime.start_turn()

CliRuntime.start_turn()
  -> create turn_id + CancellationToken
  -> register ActiveTurn(session_id, turn_id, token)
  -> AgentLoop.run_turn(..., turn_id=turn_id, cancellation_token=token)
  -> unregister active turn
```

`AgentLoop` 继续只负责通用循环：上下文、LLM、工具、Session 写入。CLI 输入并发、终端显示、按键/命令处理留在 CLI runtime。

### 13.5 分阶段落地

CLI `/stop` 建议排在 turn 持久化和 Web stop 对齐之后：

1. 先让 `AgentLoop.run_turn()` 支持外部传入 `turn_id` 和 cancellation token。
2. 再让 CLI 有 active turn registry，但仍保持同步输入；这一阶段可支持 `Ctrl+C` 转换为 stopped turn。
3. 最后引入并发 input reader，支持运行中输入 `/stop`。

普通文本在 active turn 运行中如何处理，第一版建议保守：

- 如果输入 `/stop`，立即取消。
- 如果输入其它文本，提示当前 turn 正在运行，先 `/stop` 或等待完成。
- 暂不做参考项目里的 followup merge，避免第一阶段 CLI 复杂化。

## 14. 变更文件预估

未来代码落地大概率涉及：

- `agent/message.py`：增加 `turn_id`、`turn_index`、`parent_turn_id` 可选字段。
- `agent/protocols/session.py`：增加 `TurnState`、`TurnGroup` 和可选 turn 读取协议。
- `agent/session/jsonl_store.py`：读写 turn 字段，派生 legacy turn groups。
- `agent/core/loop.py`：`run_turn()` 接收/生成 turn_id，并给同轮消息统一标注。
- `agent/core/context.py`：支持按 recent turns 构建上下文。
- `agent/app/runtime.py`：统一 Web active turn id，取消路径返回同一 turn id。
- `agent/app/api/ws.py`：accepted/text/done/stopped/error 事件统一携带同一 turn id。
- `agent/app/api/routes.py`：如果 HTTP/SSE 继续保留，cancel API 也应返回 turn-aware 状态。
- `agent/cli.py`：未来在 CLI `/stop` 阶段引入 active turn registry、运行中命令拦截和 stopped marker 写入。
- `tests/unit_test/session_store/`：覆盖 turn 读写和 legacy grouping。
- `tests/unit_test/context_builder/`：覆盖 recent N turns 和 tool block 原子性。
- `tests/unit_test/agent_loop/`：覆盖正常、tool、error、stopped 的 turn_id 标注。
- `tests/unit_test/app/`：覆盖 WebSocket turn_id 一致性和 stop。
- `tests/unit_test/cli/`：当前阶段确认 CLI help 不展示未实现 `/stop`；未来阶段覆盖运行中 `/stop` 取消 active turn。

## 15. 分阶段计划

### Phase 1：持久化 turn 边界

- `Message` 增加可选 turn 字段。
- `JsonlSessionStore` 写入/读取 turn 字段。
- `AgentLoop.run_turn()` 统一给 pending messages 标注 turn。
- WebSocket accepted 的 `turn_id` 传入 AgentLoop，避免双 id。

验收：

- 新 session JSONL 中同一轮 user/assistant/tool 消息有相同 `turn_id`。
- 旧 session 文件仍可读取。
- Web accepted/done 使用同一个 `turn_id`。

### Phase 2：按最近 N 个 user turn 构建上下文

- `JsonlSessionStore.load_turns()` 派生 turn groups。
- `ContextBuilder` 支持 `max_history_turns`。
- tool-call block 保持合法。

验收：

- 最近 N 个 user turn 被带入上下文。
- 不会以孤立 tool result 开头。
- 单个完整 tool-call block 不被拆散。

### Phase 3：turn 状态与 Web stop 对齐

- stopped/error/completed 状态写入 turn 派生信息。
- Web stop 返回并持久化 stopped marker。
- `channel_text` 支持 turn_id，前端可过滤迟到 chunk。

验收：

- `/stop` 后 session 中能看到 stopped marker 归属对应 turn。
- stop 后迟到 delta 不污染后续 turn。
- 没有 active turn 时 stop 幂等返回 `cancelled=0`。

### Phase 4：turn-end 扩展点

- 增加轻量 `TurnLifecycleProvider` 协议，但默认空实现。
- `on_turn_end` 只接收 provider-neutral 字段，不 import 具体 memory/review 实现。
- 后续 memory、summary、context compaction 另开设计记录接入。

验收：

- AgentLoop 只知道协议，不知道业务 provider。
- 默认未配置 provider 时无行为变化。

### Phase 5：CLI `/stop` 后续能力

在 turn 持久化、cancellation token 和 active turn registry 稳定后，再进入 CLI `/stop`：

- 先支持 `Ctrl+C` 保存当前 turn 为 stopped。
- 再引入 CLI 并发 reader，支持运行中 `/stop`。
- `/history --turns N` 按 turn 展示。

这些能力不属于本设计第一批落地，但属于 turn 模型明确服务的未来目标。

## 16. 测试方案

| 用例 | 输入/场景 | 期望 |
| --- | --- | --- |
| new turn write | 一轮普通 user -> assistant | 两条消息同一 `turn_id`，`turn_index` 一致 |
| tool turn write | user -> assistant tool_calls -> tool -> assistant | 四类消息同一 `turn_id` |
| error turn | LLM 抛错 | assistant error marker 带 `turn_id`，状态可派生为 `error` |
| stopped turn | Web stop cancellation | stopped marker 带 `turn_id`，状态可派生为 `stopped` |
| legacy grouping | 旧 JSONL 无 `turn_id` | 按 user 边界派生 legacy turns，不重写文件 |
| recent turns | 5 轮历史，配置 `max_history_turns=2` | 只带最近 2 个 user turn |
| tool block atomic | 裁剪边界靠近 tool_call | 不产生孤立 tool result |
| ws turn consistency | WebSocket message | accepted/text/done/stopped 使用同一 `turn_id` |
| cli help | `zcagent /help` 或交互 `/help` | 不展示 CLI `/stop` |
| future cli stop | active turn 运行中输入 `/stop` | 取消当前 turn，写入 stopped marker，不透传 LLM |
| future cli stop idle | 无 active turn 时输入 `/stop` | 返回 `No active turn.`，不写 Session user message |

## 17. 验收标准

1. 不破坏现有 JSONL session，旧文件可继续 load/list/history。
2. 新写入消息有稳定 turn 边界。
3. `ContextBuilder` 可按最近 user turn 裁剪，而不是只按 message 数。
4. 工具调用历史保持 OpenAI-compatible。
5. Web active turn、WS event 和持久化消息的 `turn_id` 一致。
6. Web `/stop` 可落到 stopped turn；CLI 当前不声明未实现的 `/stop`，未来 CLI `/stop` 复用同一 turn cancellation 语义。
7. 没有引入重型数据库、跨进程 registry、多用户鉴权或子代理依赖。

## 18. 和现有文档的关系

- `zhice-agent-overall-design.md` 保持当前活文档口径：Session 是上下文的一部分，后续上下文治理要改向最近 N 轮 user turn。
- `2026-07-01-websocket-primary-chat-design.md` 关注 WebSocket、流式和 Web stop，本设计补充它背后的 turn 持久化与上下文加载边界。
- 本文是未来设计记录。等代码落地并成为主线后，再把精简后的当前规则同步回总体设计或对应 Part 文档。
