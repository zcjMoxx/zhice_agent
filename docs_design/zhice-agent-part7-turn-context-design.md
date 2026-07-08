# 智策 Agent 第七部分详细设计文档：Turn 运行单元与上下文治理

> 关联规范：`AGENTS.md`
>
> 文档类型：阶段活文档。本文档始终按当前代码和当前阶段口径维护。
>
> 承接文档：`docs_design/zhice-agent-part6-web-minimum-design.md`
>
> 设计依据：`docs_design/2026-07-04-turn-runtime-and-context-design.md`、`docs_design/2026-07-06-next-stage-sequencing-design.md`
>
> 当前状态：核心 turn 持久化已按本地开发口径落地。第七部分只处理 turn 运行单元和上下文治理，不主动引入独立的用户系统、数据库、长期记忆或子代理调度；旧 JSONL、metadata fallback 和 legacy grouping 不作为本阶段兼容目标。

---

## 1. 背景

第六部分 Web 最小版已经具备：

- `AgentLoop.run_turn(session_id, user_text)`：一次用户输入到最终 assistant 输出的运行边界。
- `WebRuntime.ActiveTurn(turn_id, token)`：Web 侧内存态 active turn 和 cancellation token。
- `WebSocket /ws`：浏览器主聊天通道，已经有 accepted、text、done、stopped 等事件。
- `JsonlSessionStore`：继续以 JSONL 保存会话消息。
- `ContextBuilder`：按最近 message 数裁剪历史，并尽量保留合法 tool-call block。

第七部分启动前的问题是这些能力还没有共享同一个持久 turn 边界：

```text
Web accepted turn_id
  != WebRuntime active turn_id
  != Session JSONL messages
  != ContextBuilder history unit
```

这会影响后续三个方向：

- Web stop 和历史恢复：前端无法稳定判断哪个 done/stopped 属于哪一轮。
- 运行日志：LLM、tool、session 保存日志没有稳定关联字段。
- 用户权限和审计：未来 audit log 需要回答“哪个用户的哪一次请求触发了哪个工具调用”。

因此第七部分把 `turn` 做成当前主线能力：一次用户请求的可持久化运行单元。

---

## 2. 目标

1. 把 turn 定义为 Session 内一次用户请求的完整生命周期。
2. 新写入的 user、assistant、tool、stopped/error marker 都带同一个 `turn_id`。
3. 新写入消息带 `turn_index`，用于 session 内排序和调试。
4. `JsonlSessionStore` 只读写顶层 turn 字段，旧 metadata fallback 不再保留。
5. 没有显式 `turn_id` 的历史消息不参与 turn-based context selection。
6. `AgentLoop.run_turn()` 支持外部传入 `turn_id`，Web accepted 的 id 能传到 AgentLoop 和 Session。
7. WebSocket accepted、channel_text、done、stopped、error 使用同一个 `turn_id`。
8. `ContextBuilder` 支持先取最近 N 个 user turn 作为候选，再按当前输入做本地相关性选择。
9. 历史 tool-call block 在 turn 裁剪后仍保持 OpenAI-compatible，不出现孤立 tool result。
10. 为后续运行日志、权限审计、CLI stop、memory compaction 提供稳定字段，但不在本阶段主动实现这些独立系统。

---

## 3. 范围边界

### 3.1 本阶段自然包含

- `Message` turn 字段。
- JSONL 顶层 turn 字段读写。
- explicit turn grouping。
- `AgentLoop.run_turn(..., turn_id=...)`。
- cancellation stopped marker 归属同一 turn。
- LLM error marker 归属同一 turn。
- tool iteration limit marker 归属同一 turn。
- WebRuntime active turn id 与 AgentLoop turn id 统一。
- WebSocket event 携带一致 `turn_id`。
- `ContextBuilder(max_history_turns=...)`。
- turn 裁剪后的 tool-call block 合法性。
- 单元测试和 `test_case.md` 更新。

### 3.2 本阶段不主动引入的独立系统

这些能力可以复用 turn 字段，但不是第七部分闭环的必要实现：

- CLI 并发输入型 `/stop`。
- 通用 turn lifecycle hook 插件协议。
- 长期 memory、summary、compaction。
- 用户、登录、权限、数据库、audit log。
- 子代理调度。
- 跨进程 active turn registry。
- 结构化 trace 文件和日志面板。

如果上述能力中的局部字段或兼容处理是 turn 闭环必需的一部分，可以作为第七部分实现细节处理。例如 stopped marker 带 `turn_id` 是本阶段必需；但 CLI 并发输入 reader 不是。

---

## 4. 概念定义

### 4.1 Session

Session 是长期会话容器，保存历史消息和会话级 metadata。当前仍由 `JsonlSessionStore` 管理：

```text
${ZHICE_AGENT_WORKSPACE}/contexts/sessions/{session_id}.jsonl
${ZHICE_AGENT_WORKSPACE}/contexts/sessions_meta/{session_id}.json
```

### 4.2 Turn

Turn 是 Session 内一次用户请求的运行单元：

```text
user input accepted
  -> context built
  -> LLM called
  -> optional tool calls
  -> final assistant / stopped / error
  -> messages appended to Session
```

一个 turn 至少包含一条 user message。包含工具调用时，通常是：

```text
user
assistant(tool_calls)
tool
assistant(final)
```

### 4.3 Active Turn

Active Turn 是正在运行的 turn，只存在内存里，用于取消和 Web 状态反馈。它的 `turn_id` 必须与最终持久化消息一致。

### 4.4 Untagged History

Untagged History 是没有显式 `turn_id` 的历史消息。当前本地开发不承担旧 JSONL 迁移成本，这类消息可以被读取和展示，但不会被派生成 turn，也不会参与 turn-based context selection。

---

## 5. 数据模型

### 5.1 `Message`

修改 `agent/message.py`：

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

字段说明：

- `turn_id`：稳定 id，格式建议 `turn-<uuid hex>`。
- `turn_index`：session 内递增序号，从 1 开始。
- `parent_turn_id`：预留字段。第七部分只读写，不实现子代理或父子调度。

构造兼容性：

- 新字段都有默认值，旧测试和旧构造方式仍可工作。
- LLM message dict 不需要携带这些字段；它们是本地运行和持久化元数据。

### 5.2 JSONL record

新写入记录把 turn 字段放在顶层：

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
  "metadata": {}
}
```

读取规则：

1. 只读取顶层 `turn_id`、`turn_index`、`parent_turn_id`。
2. 不从 `metadata` 回退读取 turn 字段。
3. 都不存在时，保持 `Message.turn_id is None`，这类消息不参与 turn-based context selection。

### 5.3 `TurnGroup`

当前代码在 `agent/protocols/session.py` 放置轻量 `TurnGroup` 数据结构，在 `agent/core/turns.py` 放置分组和索引 helper。`core` 只依赖协议层数据结构，不 import 具体 JSONL store。

```python
@dataclass
class TurnGroup:
    turn_id: str
    turn_index: int | None
    messages: list[Message]
```

`TurnGroup` 只表示运行期分组，不要求单独持久化。

### 5.4 Turn 状态派生

第七部分不单独持久化 `TurnState` 表或 sidecar。状态从消息派生：

- 最后一条 assistant message metadata 有 `stopped=True`：`stopped`。
- 最后一条 assistant message metadata 有 `is_error=True`：`error`。
- 有 assistant final 且非 error/stopped：`completed`。
- 只有 user 或中间 tool block：`incomplete`。

这些状态先服务测试、日志前置和未来审计，不作为 Web API 的新稳定合约强制暴露。

---

## 6. Turn grouping 规则

当前 `agent/core/turns.py` 实现一个纯函数，把 `list[Message]` 分为 `list[TurnGroup]`：

```python
group_messages_by_turn(messages: list[Message]) -> list[TurnGroup]
```

规则：

1. 只有带 `turn_id` 的消息参与分组。
2. 相邻且 `turn_id` 相同的消息归入同一个 `TurnGroup`。
3. 不带 `turn_id` 的消息直接跳过，不派生临时 turn id。
4. 非相邻的同一 `turn_id` 不强行重排合并，按持久化文件顺序保留片段。

最近 N 个 user turn 裁剪时：

- 选择含 user message 的 turn。
- 如果显式 turn 没有 user message，不进入 recent user turns。

---

## 7. AgentLoop 设计

### 7.1 `run_turn` 签名

修改 `agent/core/loop.py`：

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

### 7.2 turn_id 来源

- WebSocket 路由先生成 `turn_id`，发 accepted，再传给 `WebRuntime.run_chat_events(..., turn_id=turn_id)`。
- `WebRuntime` 用同一个 `turn_id` 注册 active turn，并传给 `AgentLoop.run_turn(..., turn_id=turn_id)`。
- CLI 不传时，由 `AgentLoop` 内部生成。

这样 Web 可以在进入线程执行前立刻反馈 accepted，同时 stop frame 能对准同一个 active turn。

### 7.3 turn_index 来源

AgentLoop 已经会先 `sessions.load(session_id)`。第七部分可以基于已加载消息计算下一个 index：

```text
已有显式 turn_index 最大值 + 1
```

规则：

- 新 session 第一轮为 `turn_index=1`。
- 没有显式 turn_index 时，从 1 开始。
- 不为了分配 index 重写已有文件。

### 7.4 同轮消息标注

同一轮所有 pending session messages 必须共享：

```text
turn_id
turn_index
parent_turn_id
```

覆盖路径：

- user message。
- assistant response。
- assistant tool_calls。
- tool result messages。
- tool iteration limit marker。
- LLM error marker。
- cancellation stopped marker。

`_tool_result_to_message()` 可以接收 turn 字段，或创建后由统一 helper 补齐。

### 7.5 保存策略

继续保持当前最小 append 策略：

```text
run_turn 内部收集 pending_session_messages
  -> turn 结束、出错或停止时一次 append
```

第七部分不改成 begin_turn / finish_turn 的实时持久化模式。这样避免引入 running turn 崩溃恢复、补偿写入和跨进程状态。

---

## 8. ContextBuilder 设计

### 8.1 参数

当前：

```python
ContextBuilder(max_history_messages=30)
```

第七部分新增：

```python
ContextBuilder(
    max_history_turns: int | None = 8,
    max_history_messages: int = 60,
    max_message_chars: int = 8000,
    ...
)
```

说明：

- `max_history_turns=None` 表示沿用 message-based 裁剪，主要用于测试或特殊调用。
- `max_history_turns=0` 表示不带历史，只带当前 user message。
- `max_history_turns>0` 表示最近 user turn 候选数量；候选还必须通过本地相关性选择。
- `max_history_messages` 保留为消息数量兜底，防止单次上下文异常膨胀。

### 8.2 构建流程

```text
ContextBuilder.build(history, user_message, workspace, session_id)
  -> group history by explicit turns
  -> select recent max_history_turns user turns as candidates
  -> score candidates against current user message locally
  -> keep only relevant turns
  -> drop oldest selected turns until message count is within max_history_messages
  -> flatten selected turn messages
  -> _history_to_llm_dicts()
  -> append current user message
```

关键点：

- 先按 turn 选，再转 LLM messages。
- 不把当前 user message 放进 history 分组。
- 本地相关性比较使用 turn 内完整文本，不只看 anchor 或关键词。
- “你好”“谢谢”等无相关信号时不带历史；“好的/ok/嗯”只有在最近 assistant 明确等待确认时才通过邻接关系加分。
- tool-call block 的合法性继续由 `_history_to_llm_dicts()` 兜底。
- 单个超大 turn 如果超过 `max_history_messages`，优先保留完整 turn，再依赖 `max_message_chars` 和 tool block 过滤；后续压缩另开设计。

### 8.3 tool block 原子性

turn 裁剪不能产生孤立 tool result。要求：

- 如果 assistant 有 `tool_calls`，只有对应 tool result 都存在时才带入该 block。
- 裁剪边界不能从 assistant tool_calls 和 tool result 中间切开。
- 旧历史里已经不完整的 tool block 继续跳过，不影响后续消息。

---

## 9. WebRuntime 与 WebSocket 设计

### 9.1 Runtime 签名

修改 `agent/app/runtime.py`：

```python
def run_chat_events(
    self,
    session_id: str,
    message: str,
    *,
    turn_id: str | None = None,
    on_event: RuntimeEventCallback | None = None,
    command_profile: str = WEB_COMMAND_PROFILE,
) -> ChatTurnResult:
    ...
```

普通聊天：

- `turn_id` 为空时由 runtime 生成。
- `turn_id` 非空时必须复用调用方传入的 id。
- `_register_turn()` 使用该 id。
- `AgentLoop.run_turn()` 使用该 id。
- `ChatTurnResult.turn_id` 返回该 id。

slash command：

- 仍由 `handle_command()` 短路。
- 不经过 AgentLoop 的命令不写 Session，也不伪造持久 turn。
- `/stop` 或 stop frame 仍走 active turn cancellation。

### 9.2 WebSocket event

修改 `agent/app/api/ws.py`：

```text
accepted:      turn_id = generated once in _run_message_frame
channel_text:  same turn_id
done:          same turn_id
stopped:       same turn_id
error:         same turn_id
```

事件兼容策略：

- `channel_text` 的 `data` 继续保持字符串，避免前端大改。
- 在 payload 外层增加 `turn_id`：

```json
{
  "event": "channel_text",
  "session_id": "chat-20260701",
  "turn_id": "turn-abc",
  "data": "delta"
}
```

`channel_status` 保持 `data.turn_id`，也可以同时在外层带 `turn_id`。前端后续可用该字段过滤迟到 chunk。

### 9.3 Stop 对齐

Web stop 自然属于第七部分闭环：

- stop frame 调 `runtime.cancel_session(session_id)`。
- runtime 返回 active `turn_id`。
- AgentLoop 在 cancellation checkpoint 抛 `TurnCancelledError`。
- stopped marker 写入 Session，带同一个 `turn_id` 和 `metadata={"stopped": True}`。
- WebSocket stopped event 带同一个 `turn_id`。

这不等于实现 CLI 运行中 `/stop`。CLI 仍是未来独立入口问题。

---

## 10. API 与历史展示

### 10.1 Session message response

`GET /api/sessions/{session_id}` 当前返回 message list。第七部分可以在每条 message 上增加可选字段：

```json
{
  "role": "assistant",
  "content": "done",
  "turn_id": "turn-abc",
  "turn_index": 3
}
```

展示策略：

- 只返回真实持久化的 turn 字段，不派生临时 id。
- 前端当前只渲染 role/content，因此新增字段不破坏 UI。

### 10.2 HTTP/SSE 兼容接口

REST/SSE 兼容接口可以继续调用同一个 `WebRuntime.run_chat_events()`，由 runtime 生成 turn id。返回体可后续增加 turn 字段，但第七部分优先保证 WebSocket 主通道和 JSONL 持久化。

---

## 11. 变更文件

当前落地涉及：

```text
agent/message.py
agent/protocols/session.py
agent/session/jsonl_store.py
agent/core/loop.py
agent/core/context.py
agent/app/runtime.py
agent/app/api/ws.py
agent/app/api/routes.py
agent/app/api/schemas.py
agent/cli.py
tests/unit_test/session_store/test_session_store.py
tests/unit_test/session_store/test_case.md
tests/unit_test/context_builder/test_context_builder.py
tests/unit_test/context_builder/test_case.md
tests/unit_test/agent_loop/test_agent_loop.py
tests/unit_test/agent_loop/test_agent_loop_tools.py
tests/unit_test/agent_loop/test_case.md
tests/unit_test/app/test_ws_routes.py
tests/unit_test/app/test_api_routes.py
tests/unit_test/app/test_case.md
tests/unit_test/cli/test_cli_init.py
tests/unit_test/cli/test_case.md
docs_design/README.md
docs_design/zhice-agent-overall-design.md
docs_design/zhice-agent-part6-web-minimum-design.md
```

当前已新增：

```text
agent/core/turns.py
tests/unit_test/core/test_turns.py
```

`agent/core/turns.py` 承载 `new_turn_id()`、`group_messages_by_turn()`、`next_turn_index()` 和 `assign_turn()`；`TurnGroup` 保留在 `agent/protocols/session.py`，方便 core 与 SessionStore 协议共享同一轻量结构。

---

## 12. 测试方案

### 12.1 SessionStore

| 用例 | 输入/场景 | 期望 |
| --- | --- | --- |
| write turn fields | append 带 turn 字段的 Message | JSONL 顶层写出 `turn_id`、`turn_index`、`parent_turn_id` |
| read turn fields | 读取新 JSONL | Message 恢复 turn 字段 |
| no metadata fallback | turn 字段只在 metadata 中 | Message 不恢复 turn 字段 |
| untagged jsonl | 记录无 turn 字段 | `load()` 正常，Message.turn_id 为 None，不参与 turn selection |
| list sessions | 新旧记录混合 | preview、updated_at、message_count 不受影响 |

### 12.2 Turn grouping

| 用例 | 输入/场景 | 期望 |
| --- | --- | --- |
| explicit turns | 多条消息带相同 turn_id | 分为同一个 TurnGroup |
| untagged messages | user/assistant 没有 turn_id | 不派生 TurnGroup |
| mixed history | untagged + explicit 混合 | 只保留显式 turn，按文件顺序输出 |
| next index | 没有显式 turn_index | 下一轮 index = 1 |

### 12.3 AgentLoop

| 用例 | 输入/场景 | 期望 |
| --- | --- | --- |
| generated turn | CLI 风格不传 turn_id | 自动生成 turn_id，user/assistant 同一 id |
| external turn | Web 传入 turn_id | 所有 pending messages 使用传入 id |
| tool turn | user -> assistant tool_calls -> tool -> assistant | 四类消息同一 turn_id / turn_index |
| llm error | LLM 抛错 | user + assistant error marker 同一 turn |
| cancellation | token 被 cancel | stopped marker 同一 turn，metadata.stopped=True |
| iteration limit | tool 超轮数 | tool error + limit marker 同一 turn |

### 12.4 ContextBuilder

| 用例 | 输入/场景 | 期望 |
| --- | --- | --- |
| recent turns | 5 个 user turn，max_history_turns=2 | 只带最近 2 个 user turn |
| no history | max_history_turns=0 | 只带 system 和当前 user |
| message fallback | max_history_turns=None | 使用原 message-based 行为 |
| local relevance | 当前输入和候选 turn 无关 | 不带该 turn |
| follow-up relevance | 当前输入引用候选 turn 的术语/代码/错误 | 带该 turn |
| hard cap | selected turns 超过 max_history_messages | 从旧 turn 开始整体丢弃 |
| tool block complete | assistant tool_calls + tool | block 完整保留 |
| tool block incomplete | 缺 tool result | 不产生孤立 tool result |

### 12.5 Web / API

| 用例 | 输入/场景 | 期望 |
| --- | --- | --- |
| ws consistency | message frame | accepted/text/done 同一 turn_id |
| ws stop | stop frame during active turn | stopped event 和 session marker 同一 turn_id |
| ws error | runtime 抛错 | error event 使用 accepted turn_id |
| no active stop | stop when idle | cancelled=0，不写新的 session turn |
| API session read | 读取新消息 | response 可包含 turn_id / turn_index |

### 12.6 CLI

| 用例 | 输入/场景 | 期望 |
| --- | --- | --- |
| normal chat | CLI run_turn 不传 turn_id | 自动生成并保存 turn |
| help | CLI `/help` | 不展示运行中 `/stop` |
| history | 现有 history 输出 | 不因 turn 字段崩溃 |

---

## 13. 实现顺序

推荐顺序：

1. 更新测试说明 `test_case.md`，写清 turn 目标和覆盖矩阵。
2. 扩展 `Message` 和 `JsonlSessionStore` 顶层 turn 字段读写，不保留 metadata fallback。
3. 增加 turn grouping 和 next index helper。
4. 改 `AgentLoop.run_turn(..., turn_id=None)`，统一标注 pending messages。
5. 改 `ContextBuilder`，支持 `max_history_turns` 候选和本地相关性选择。
6. 改 `WebRuntime.run_chat_events(..., turn_id=None)`，统一 active turn id。
7. 改 WebSocket event，accepted/text/done/stopped/error 使用同一个 turn id。
8. 视需要给 session API response 增加可选 turn 字段。
9. 更新总设、Part 6 引用和 README/索引。
10. 运行针对性测试，再跑全量检查。

推荐验证命令：

```bash
python -m ruff check .
python -m pytest --basetemp .tmp/pytest_basetemp
```

---

## 14. 验收标准

第七部分完成时，应满足：

1. 新写入 JSONL session 使用顶层 turn 字段；旧 metadata fallback 不作为兼容目标。
2. 新写入 session messages 具备稳定 `turn_id` 和 `turn_index`。
3. 同一轮 user、assistant、tool、error、stopped marker 共享同一个 turn。
4. WebSocket accepted、channel_text、done、stopped、error 使用同一个 `turn_id`。
5. Web stop 后持久化 stopped marker 归属对应 turn。
6. `ContextBuilder` 支持最近 N 个 user turn 候选，并只注入和当前输入相关的 turn。
7. 工具调用历史保持 OpenAI-compatible，不产生孤立 tool result。
8. CLI 当前不声明未实现的运行中 `/stop`。
9. 没有把独立数据库、多用户鉴权、长期 memory、子代理或跨进程 registry 提前并入第七部分。
10. `python -m ruff check .` 和 `python -m pytest --basetemp .tmp/pytest_basetemp` 通过；如果存在无关历史失败，交付说明中明确写出。

---

## 15. 和其它文档的关系

- `docs_design/2026-07-04-turn-runtime-and-context-design.md` 是未来设计记录，保留更完整的后续方向；本文是第七部分当前实现口径。
- `docs_design/2026-07-06-next-stage-sequencing-design.md` 确定第七部分排在日志和用户权限之前。
- `docs_design/zhice-agent-part8-gateway-agent-logging-design.md` 已承接第八部分运行日志施工图；`docs_design/2026-07-02-gateway-runtime-logging-design.md` 保留为历史背景。
- 后续用户权限系统应基于本文形成的关系：`User -> Session -> Turn -> ToolCall / AuditLog`。
