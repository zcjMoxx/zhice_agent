# 智策 Agent 第十部分详细设计文档：Memory 与受控长期记忆

> 关联规范：`AGENTS.md`
>
> 文档类型：阶段活文档。本文档始终按当前代码和当前阶段口径维护。
>
> 承接文档：`docs_design/zhice-agent-part9-user-auth-permission-design.md`
>
> 设计依据：`docs_design/2026-07-16-background-memory-extraction-and-trace-convergence-design.md`、`docs_design/2026-07-16-conversational-memory-consent-design.md`、`docs_design/2026-07-15-memory-boundary-design.md`、`docs_design/zhice-agent-part7-turn-context-design.md`、`docs_design/zhice-agent-part9-user-auth-permission-design.md`
>
> 当前状态：第十部分已经完成代码落地并进入当前基线。现已实现“CLI/Owner 共用 workspace Memory、普通用户使用私有 Memory”的 Markdown 存储、按需读取、用户明确授权写入、Session 空闲后的高可信长期信息提取、一次性写入通知、权限审计与受控检索；没有引入 Memory 确认弹窗、候选状态机、逐 Turn 隐藏 review、Session Summary 半成品或向量数据库。

---

## 1. 背景

ZhiCe-Agent 当前已经具备稳定的短期上下文链路：

- `JsonlSessionStore` 以 JSONL 保存聊天消息真值。
- `Message` 使用 `turn_id`、`turn_index` 和 `parent_turn_id` 标记运行单元。
- `ContextBuilder` 默认优先保留最近 3 个 Turn，并从更早候选中选择最多 3 个相关 Turn；CLI/Web 还统一受 60 message 与 endpoint ContextBudget 约束。
- Part 9 已建立用户、session、turn、tool call 和 audit 的身份与权限边界。
- CLI 本地操作者与 Owner Web 是同一个 workspace operator 的两个入口，共用全局 `contexts/sessions/`、`sessions_meta/` 和 workspace；普通登录用户通过内部 `user_id` 解析自己的用户上下文。

这些能力解决了“当前会话里应该带哪些历史”，但还没有解决跨会话长期信息：

```text
用户在 session A 说明偏好或长期约束
  -> session B 默认看不到
  -> ContextBuilder 只能从 session B 的历史中选择
```

如果直接把所有历史无限注入上下文，会带来明显问题：

- 上下文持续膨胀，成本和延迟不可控。
- 很久以前的临时信息可能错误影响当前任务。
- 多用户场景下，全局 Memory 文件可能泄露其它用户信息。
- 模型静默提炼并直接写入容易把推测、错误结论或敏感数据固化成长期事实。

第十部分因此不做“把所有聊天静默变成记忆”，而是增加一套轻量、可检查、可删除、按用户隔离的受控长期记忆能力。用户明确要求时直接写入；普通聊天不负责主动识别，Session 空闲后由独立 Extractor 只把有两到三条用户 Turn 证据的高可信长期信息自动写入，并在下次对话通知用户。

---

## 2. 目标

1. 让 CLI 本地操作者与 Owner Web 共用 workspace 级长期 Memory，并让普通登录用户使用彼此隔离的私有 Memory。
2. 以人可读 Markdown 作为长期记忆真值，不引入向量数据库或新的运行时服务。
3. 增加 `MemoryStore` 协议，使 Agent core 和 Tool 不依赖具体 Markdown 实现。
4. 增加 `memory_read` 和 `memory_write` 工具，复用现有 ToolRegistry、ToolExecutionPolicy 和 audit 链路。
5. 用户明确要求记住、修改或忘记时直接执行；重复长期风格或约束由 Session 空闲 Extractor 在严格证据校验后自动写入。
6. 支持按类别和关键词有界读取，并按 `category + 原内容` 增加、修改和删除。
7. `/memory` 展示长期 Memory，不提供手动提取或未闭环的 Session Summary 命令。
8. Memory 读取和写入按 actor 权限判断，并且 audit/trace 不记录完整 Memory 内容。
9. 保持 AgentLoop 的通用循环边界，不在其中硬编码用户偏好、项目类型或业务记忆规则。

---

## 3. 范围边界

### 3.1 本阶段包含

- `MemoryContext`、`MemoryEntry`、`MemoryStore` 等协议数据结构。
- workspace operator（CLI + Owner）与普通数据库用户的 Memory 路径解析。
- 只包含分类和内容的极简 Markdown Memory、原子写入与容量限制。
- `memory_read` 工具。
- `memory_write` 工具，支持 add、replace、delete。
- `memory.read.own`、`memory.write.own` 权限。
- 用户明确要求或自然语言同意时，`memory_write` 经过权限和安全校验后直接执行。
- Session 空闲后独立提取 `profile/preferences/constraints`，要求至少三个用户 Turn 和两到三条原文证据。
- 高可信结果自动写入并在下一次对话显示一次通知；中低可信结果直接丢弃。
- Memory Tool 的 RBAC、错误码、trace 和 audit 安全字段。
- `prompts/memory_policy.md`，约束什么时候可以读写 Memory。
- `/memory` 展示长期 Memory。
- 用户自己的长期 Memory 文件与本地关键词检索。
- CLI、Web/WS 共享命令语义和 Fake LLM/单元测试。

### 3.2 本阶段不包含

- 不在每个 turn 结束时追加隐藏的 LLM review。
- 不把无重复证据、中低可信或助手推测的内容当作用户事实。
- 不做 embedding、vector database、全文检索服务或 graph memory。
- 不做跨用户 Memory 管理权限；Admin 和 Owner 默认也不能通过管理 API读取其它用户 Memory。
- 不把 Memory 内容写进 SQLite auth/audit 表。
- 不把 Memory 文件放进 `contexts/shared/readonly`。
- 不修改 Session JSONL 的真值地位，也不把摘要替换成真实聊天记录。
- 不做后台摘要队列、跨进程任务系统或自动 compaction；Gateway 进程内只使用一个轻量调度线程和默认两个提取 Worker。
- 不做独立的 Memory Web 管理页面、Memory 确认弹窗或候选编辑 API。
- 不让 Skill 脚本直接 import `agent.memory`；Skill 继续通过 Tool 或文件/API 边界使用能力。

### 3.3 后续增强

后续可以继续评估：

- 在后续上下文优化 Part 单独设计 `/compact`、上下文替换、续接与失败回退；endpoint token 预算已经进入当前基线。
- 为已保存条目引入过期时间、数值置信度、更完整的来源引用和冲突提示。
- 在 Part 16 评估自动 recall 和 ContextBuilder augmenter，并复用当前 ContextBudget。
- 数据量明显增长后再评估 SQLite FTS 或向量检索。

这些增强不能改变当前原则：Memory 必须可见、可删、可追踪、按用户隔离。

---

## 4. 当前代码边界

### 4.1 Session 继续是聊天真值

当前 `SessionStore` 负责：

```text
load
append
clear
rename
delete
list_sessions
```

第十部分不向 `SessionStore` 添加长期记忆职责。JSONL 继续保存 user、assistant、tool 和 turn 字段；Memory Store 独立保存跨会话信息。

```text
SessionStore
  -> 当前会话真实消息

MemoryStore
  -> 用户明确保存的长期信息
```

### 4.2 ContextBuilder 继续治理短期历史

`ContextBuilder` 当前负责：

```text
system prompts
recent relevant turns
current user message
```

Part 10 不把完整 `MEMORY.md` 自动注入 system prompt。模型通过 `memory_read` 按需读取，避免每轮固定增加隐私暴露和上下文成本。

### 4.3 Tool 与权限边界

Memory Tool 继续使用现有路径：

```text
AgentLoop
  -> ToolExecutionContext
  -> ToolExecutionPolicy
  -> Memory Tool
  -> MemoryStore
  -> ToolResult
  -> audit/trace
```

AgentLoop 只识别普通 ToolProvider，不 import MarkdownMemoryStore，也不判断哪些内容值得长期保存。

当前 ToolProvider 的 `execute(name, args)` 不接收 ToolExecutionContext。Memory 不再保存 Session/Turn 来源，因此 Tool 只需绑定已经授权的 MemoryStore：

```text
app/CLI 解析当前 actor 的 MemoryContext
  -> 构造绑定 MemoryStore 的 MemoryReadTool/MemoryWriteTool
  -> 作为 tools_override 传入 AgentLoop.run_turn(..., turn_id=...)
```

- WebRuntime 已经按 turn 构造 `UserScopedToolProvider`，只需传入当前 actor 的 MemoryStore。
- CLI 需要在每次普通输入前生成 turn_id，并为该 turn 构造工具集合；slash command 不创建 Memory Tool turn。
- 用户授权类型仍由模型根据当前对话判断；Memory 文件不保存来源字段。

---

## 5. Memory 作用域与目录

### 5.1 Workspace operator：CLI 与 Owner

Owner 是 CLI 本地操作者在 Web 端的登录身份，两者本质上是同一个 workspace operator，共用全局 Memory：

```text
${ZHICE_AGENT_WORKSPACE}/contexts/memory/
  MEMORY.md
```

该目录同时代表 CLI 与 Owner Web 的长期 Memory，不等于任意 Web 用户的共享记忆。

Owner 的认证记录和 `session_index.owner_user_id` 继续用于 Web 登录、权限和审计，但不能据此创建或使用：

```text
contexts/users/{owner_id}/
```

`FilesystemUserContextResolver.resolve(..., use_workspace_context=True)` 已按 Part 10 口径落地：Owner context 直接指向 workspace 和 global sessions，不创建 Owner root 或 `files/`；Owner Memory 同样指向全局 `contexts/memory`。已经存在的 Owner 目录不得自动递归删除；当前代码只停止继续创建和使用，后续由显式迁移/清理操作处理。

### 5.2 普通登录用户

除 Owner 外的普通数据库用户使用自己的用户 Memory：

```text
${ZHICE_AGENT_WORKSPACE}/contexts/users/{user_id}/memory/
  MEMORY.md
```

同一个普通用户未来绑定多个渠道身份时，仍通过内部 `user_id` 共享同一个用户 Memory，不按渠道重复建目录。

### 5.3 Shared readonly

`contexts/shared/readonly` 继续表示管理员维护的公共资料，不属于用户 Memory：

```text
shared/readonly -> 公共可读资料
user memory     -> 私有长期记忆
```

Memory Tool 不接受任意路径参数，不能借由 `../`、绝对路径或 `shared/` 访问其它目录。

### 5.4 MemoryContext

新增协议数据结构：

```python
@dataclass(frozen=True)
class MemoryContext:
    scope: Literal["workspace", "user"]
    actor_user_id: str | None
    memory_dir: Path
    durable_file: Path
```

路径由 app/CLI 入口解析并授权后传入 Memory Store，Tool 不自行拼接用户目录。

---

## 6. 长期 Memory 文件格式

### 6.1 MEMORY.md 是长期记忆真值

当前使用：

```text
memory/MEMORY.md
```

文件结构：

```markdown
# ZhiCe-Agent Memory

<!-- zhice-memory:start -->

## profile

- 用户希望被称为小智。

## preferences

- 回答代码问题时先给真实代码路径。

## projects

## constraints

## decisions

<!-- zhice-memory:end -->
```

固定分类：

```text
profile
preferences
projects
constraints
decisions
```

工具只管理 `zhice-memory:start/end` 标记之间的结构；标记外的人工说明保留不动。

### 6.2 MemoryEntry

```python
@dataclass(frozen=True)
class MemoryEntry:
    category: str
    content: str
```

规则：

- `category` 只能来自固定集合。
- `content` 是单条稳定事实、偏好、项目背景、约束或决策。
- 同分类下规范化后完全相同的 content 不重复保存。
- replace/delete 使用 category 和原内容定位。
- delete 物理删除当前条目；Part 10 不维护 tombstone 历史。

### 6.3 容量限制

默认值：

```text
max_entries = 200
max_entry_chars = 1000
max_memory_file_bytes = 128 KiB
max_read_entries = 20
max_read_chars = 12000
max_query_chars = 500
```

达到上限时返回结构化错误，不静默截断写入内容。

### 6.4 原子写入与并发

- 读取使用 UTF-8。
- 写入先生成同目录临时文件，再 `replace` 原文件。
- 同一进程按 `durable_file` 使用锁，防止多个 Web turn 同时覆盖。
- 解析失败、缺少管理标记或文件超限时拒绝写入，不重建和覆盖用户文件。
- Part 10 不承诺跨进程写锁；多进程部署在 Part 16 再设计共享状态。

---

## 7. MemoryStore 协议

新增 `agent/protocols/memory.py`：

```python
class MemoryStore(Protocol):
    def search(
        self,
        query: str = "",
        *,
        category: str = "",
        offset: int = 0,
        limit: int = 8,
    ) -> list[MemoryEntry]: ...

    def count(self, query: str = "", *, category: str = "") -> int: ...

    def add(
        self,
        category: str,
        content: str,
    ) -> MemoryEntry: ...

    def replace(
        self,
        category: str,
        old_content: str,
        content: str,
    ) -> MemoryEntry: ...

    def delete(self, category: str, content: str) -> bool: ...
```

具体实现：

```text
agent/memory/markdown_store.py
  -> MarkdownMemoryStore
```

协议层不 import Markdown parser、用户服务、FastAPI 或 SQLite。

---

## 8. 受控检索

### 8.1 不使用向量检索

检索顺序：

```text
category filter
  -> normalized exact/sub-string match
  -> token/CJK bigram overlap
  -> source recency tie-break
  -> limit + max_read_chars
```

可以复用 Part 7 本地相关性选择中的通用文本归一化思想，但 Memory 模块不能 import ContextBuilder 的私有函数。需要共享时提取中性 helper。

### 8.2 列表行为

- `memory_read(mode=list)` 不依赖模型构造查询词，按固定分类和文件顺序返回有界条目。
- 支持 `offset + limit`，同时返回 `total`、`returned` 和 `has_more`。
- 指定 category 时只统计和返回该分类。
- 不把“空 query 等于列表”作为模型需要猜测的隐藏约定。

### 8.3 无匹配行为

无匹配是成功结果：

```json
{
  "status": "success",
  "entries": [],
  "message": "No matching memory entries."
}
```

模型不能把“没有记忆”改写成确定事实。

---

## 9. memory_read Tool

### 9.1 Tool 定义

```text
name: memory_read
permission: memory.read.own
```

参数：

```json
{
  "type": "object",
  "required": ["mode"],
  "properties": {
    "mode": {
      "type": "string",
      "enum": ["list", "search"]
    },
    "query": {"type": "string"},
    "category": {
      "type": "string",
      "enum": ["", "profile", "preferences", "projects", "constraints", "decisions"]
    },
    "offset": {"type": "integer", "minimum": 0, "maximum": 200},
    "limit": {"type": "integer", "minimum": 1, "maximum": 20}
  },
  "additionalProperties": false
}
```

规则：

- `mode=list` 用于“我的记忆有什么”等清单问题，不接受泛化查询词代替列表。
- `mode=search` 必须给出具体事实关键词的非空 `query`。
- Tool 只能读取构造时绑定的 MemoryStore。
- 列表返回五个固定类别、`total/returned/offset/has_more` 和有界 entry。
- ToolResult metadata 只记录 mode、匹配数量、总数和类别，不复制完整内容。

### 9.2 适用场景

- 用户询问以前明确保存的偏好、约束、项目背景或决策。
- 当前请求依赖跨 session 信息，且 Memory Tool 在本轮已提供。
- 用户要求查看自己的长期 Memory。
- 用户询问“你记得我什么”“我的 Memory 有什么”时必须使用 `mode=list`，不能不调用 Tool 就直接回答 0 条。

### 9.3 不适用场景

- 查询当前系统时间、文件、日志或进程状态。
- 当前 session 历史已经足够回答。
- 试图读取其它用户 Memory。

---

## 10. memory_write Tool

### 10.1 Tool 定义

```text
name: memory_write
permission: memory.write.own
```

参数：

```json
{
  "type": "object",
  "required": ["operation", "authorization"],
  "properties": {
    "operation": {
      "type": "string",
      "enum": ["add", "replace", "delete"]
    },
    "category": {
      "type": "string",
      "enum": ["profile", "preferences", "projects", "constraints", "decisions"]
    },
    "content": {"type": "string"},
    "old_content": {"type": "string"},
    "authorization": {
      "type": "string",
      "enum": ["user_explicit", "user_confirmed"]
    }
  },
  "additionalProperties": false
}
```

规则：

- add 需要 category + content。
- replace 需要 category + old_content + content；需要换分类时先删后加。
- delete 需要 category + content。
- `authorization=user_explicit` 表示当前用户明确要求记住、修改或忘记。
- `authorization=user_confirmed` 表示用户对上一轮助手提出的具体 Memory 询问自然语言同意，或给出了修改后的表述。
- 其它授权值、缺失授权或模型自行推断都返回 `MEMORY_USER_AUTHORIZATION_REQUIRED`，不写入。
- 通过 `memory.write.own`、authorization 和 MemorySafetyPolicy 后直接执行，不进入 tool confirmation。
- 完全重复的 add 返回已有 entry，不重复写入。
- replace/delete 找不到对应原内容时返回 `MEMORY_ENTRY_NOT_FOUND`。
- 每次成功修改写 audit，但只记录 operation、category、authorization 和内容 hash/length。

### 10.2 对话写入与后台提取原则

普通聊天模型只在以下两类情况调用 `memory_write`：

- 用户明确说“记住、保存到记忆、忘掉、修改记忆”，使用 `authorization=user_explicit`。用户请求本身就是授权，不再重复确认。
- 上一轮助手询问是否保存一项具体 Memory，当前用户通过“可以”“记住吧”等自然语言同意，或给出新的表述，使用 `authorization=user_confirmed`。

模型自行发现的长期偏好、频繁行为、工作风格或稳定约束不在普通 Turn 中处理。Gateway 通过统一 `MemoryExtractionScheduler` 在 Session 空闲五分钟后调用独立 `MemoryExtractionService`：调度器只有一个协调线程，默认全局两个 Worker，同一用户最多一个任务执行；至少三个用户 Turn 才运行，每项必须包含两到三条不同 Turn 的用户原文证据，只接受 `profile/preferences/constraints` 和 `confidence=high`。Extractor 使用该 Session call-scoped 模型选择携带的 failover-safe `ContextBudget`；输入超限时先移除较早来源 Turn，再对最长来源文本折半裁剪，仍超限则返回 `MEMORY_EXTRACTION_INPUT_TOO_LARGE`，不绕过 endpoint 输入限制。通过证据及安全校验后直接写入；中低可信结果丢弃，不创建候选状态。

明确请求流程：

```text
user explicitly requests Memory change
  -> LLM calls memory_write(authorization=user_explicit)
  -> ToolExecutionPolicy checks memory.write.own + authorization
  -> MemorySafetyPolicy validates content
  -> MarkdownMemoryStore mutates atomically
  -> LLM acknowledges in the normal response
```

后台提取流程：

```text
normal turns are saved
  -> session remains idle for five minutes
  -> resolve session model + failover-safe ContextBudget
  -> MemoryExtractionService fits bounded user turns within that budget
  -> validate high confidence + 2-3 exact user-turn evidence items
  -> safety + duplicate checks + atomic add
  -> show one short notification on the next conversation
```

以下情况不能写入：

- 普通闲聊中出现的临时事实。
- 无重复用户证据的模型推断、总结或偏好猜测。
- 用户拒绝、表达不清或已经转入其它话题。
- 工具结果、完整代码、完整日志、完整 prompt。
- 密码、token、API key、Cookie、身份证件或其它高风险敏感值。

### 10.3 敏感内容

增加轻量 `MemorySafetyPolicy`：

- 拒绝明显 credential/token/private-key 形态。
- 拒绝超长原文和疑似完整工具输出。
- 不把被拒绝内容写入错误日志。
- 返回稳定错误码 `MEMORY_SENSITIVE_CONTENT_REJECTED`。

该策略是最后防线，不能替代 prompt 中的明确写入规则。

---

## 11. `/memory` 命令

```text
/memory  展示当前 actor 的长期 Memory
```

主 `/help` 只展示这一行入口。CLI 和 Web/WS 使用相同语义，不提供 list、extract、session 或 summarize 子命令。

Session Summary 已删除，因为单独生成摘要文件没有接入 token 预算、上下文替换、当前 Session 续接、新 Session checkpoint、失败回退和多次压缩治理。真正的 Context Compaction 留到后续上下文优化 Part 单独设计。

---

## 12. 权限与用户隔离

新增权限：

```text
memory.read.own
memory.write.own
```

默认角色建议：

| 角色 | read own | write own |
| --- | --- | --- |
| viewer | 是 | 是 |
| developer | 是 | 是 |
| admin | 是 | 是 |
| owner | 是 | 是 |
| auditor | 是 | 否 |

规则：

- 权限判断不能硬编码角色名，以 permission key 为准。
- Part 10 不增加 `memory.read.any` 或 `memory.manage.any`。
- `session.manage.any` 不隐含跨用户 Memory 读取权限。
- Memory Tool/API 不提供跨用户读取。Owner/CLI 作为 workspace operator 仍具有现有全局文件工具范围，但那属于本地运维能力，不能被包装成普通的跨用户 Memory 查询接口。
- CLI local operator 与 Owner 都绑定同一个 workspace MemoryContext，并继续经过 ToolExecutionPolicy、敏感内容策略和 audit（如果 auth store 已初始化）。

---

## 13. Prompt 设计

所有进入 LLM messages 的长文本放入 prompt 文件。

### 13.1 prompts/memory_policy.md

Prompt 的自然语言规则统一使用中文；Tool 名、参数名、authorization enum 和 mode 等协议标识保留英文。

至少包含：

- Memory 是由用户通过正常对话控制的长期信息，不等于模型自身知识。
- 只在跨 session 信息确实相关时调用 `memory_read`。
- 用户明确要求时使用 `authorization=user_explicit` 直接写入，不重复询问。
- 用户对上一轮 Memory 询问自然语言同意或改写时，使用 `authorization=user_confirmed`。
- 普通聊天不承担自动识别长期行为，也不增加逐 Turn review 调用。
- 不保存 secret、完整日志、完整工具结果或未经用户授权的推断。
- read 无结果时承认没有匹配记忆，不编造。
- replace/delete 前优先通过 read 确认 category 和原内容。

该 prompt 加入 ContextBuilder 的系统 prompt 组合，但不直接注入 Memory 内容。

### 13.2 prompts/memory_extraction.md

`memory_extraction.md` 是系统内置后台能力的运行 Prompt，由 `zcagent init` 随基础模板安装，不是用户可选插件配置。Gateway 默认检查该 Prompt：文件缺失时返回 `MEMORY_EXTRACTION_PROMPT_NOT_FOUND`，文件为空、不可读或编码非法时返回 `MEMORY_EXTRACTION_PROMPT_INVALID`；两者都只关闭后台自动提取并记录结构化 WARNING，显式 Memory read/write 与普通聊天不受影响。

该 prompt 只服务 `MemoryExtractionService`。自然语言规则使用中文，JSON key、category enum 和 confidence enum 保留英文；要求严格 JSON、限定 `profile/preferences/constraints`、`confidence=high`，并为每项提供两到三条不同用户 Turn 的原文证据。无合格信息时返回空列表。Prompt 与来源 Turn 一起计入 ContextBudget；内置 Prompt 本身和最小输入仍无法放入预算时，提取失败但不影响正常聊天和显式 Memory read/write。

## 14. 错误码

建议新增：

```text
MEMORY_NOT_CONFIGURED
MEMORY_INVALID_CATEGORY
MEMORY_INVALID_OPERATION
MEMORY_ENTRY_NOT_FOUND
MEMORY_DUPLICATE_ENTRY
MEMORY_FORMAT_INVALID
MEMORY_LIMIT_EXCEEDED
MEMORY_SENSITIVE_CONTENT_REJECTED
MEMORY_USER_AUTHORIZATION_REQUIRED
MEMORY_PERMISSION_DENIED
```

Tool 失败返回结构化 `ToolResult`，不能把解析异常或文件 traceback 抛给 AgentLoop。

---

## 15. 日志与审计

### 15.1 Trace

建议事件：

```text
memory.read
memory.write
```

安全字段：

```text
actor_user_id
session_id
turn_id
operation
category
authorization
query_length
match_count
content_length
content_hash
duration_seconds
status
reason_code
```

禁止字段：

- 完整 Memory 内容。
- 完整 query。
- credential 或完整用户聊天。

### 15.2 Audit

- read：记录 actor、source、match_count 和 decision，不记录内容。
- add/replace/delete：记录 operation、category、authorization、hash/length。
- 未授权写入：记录稳定 reason_code，不记录用户原话或待写内容。
- 权限拒绝和敏感内容拒绝必须有 reason_code。

---

## 16. 数据流

### 16.1 读取长期 Memory

```text
user request
  -> AgentLoop exposes memory_read schema
  -> LLM selects list/search
  -> ToolExecutionPolicy checks memory.read.own
  -> MemoryReadTool
  -> MarkdownMemoryStore.search() + count()
  -> grouped bounded ToolResult
  -> LLM produces answer
```

### 16.2 写入长期 Memory

```text
user explicitly asks, or naturally agrees to the assistant's prior Memory question
  -> LLM requests memory_write with user_explicit/user_confirmed authorization
  -> ToolExecutionPolicy checks memory.write.own + authorization
  -> MemorySafetyPolicy validates content
  -> MarkdownMemoryStore atomic mutation
  -> audit metadata without content
  -> ToolResult returns category + content
```

## 17. 模块设计

建议新增：

```text
agent/protocols/memory.py
agent/memory/__init__.py
agent/memory/markdown_store.py
agent/memory/safety.py
agent/tools/memory.py
prompts/memory_policy.md
```

建议修改：

```text
agent/config.py
agent/protocols/auth.py
agent/app/auth.py
agent/auth/user_context.py
agent/auth/session_access.py
agent/auth/schema.py
agent/auth/tool_policy.py
agent/tools/__init__.py
agent/tools/scoped.py
agent/core/context.py
agent/core/loop.py
agent/cli.py
agent/app/runtime.py
tests/unit_test/memory/
tests/unit_test/tools/
tests/unit_test/auth/
tests/unit_test/app/
tests/unit_test/cli/
```

依赖方向：

```text
cli/app -> memory service -> memory protocols
tools   -> memory protocols
memory markdown/safety implementations -> memory protocols
core/AgentLoop -> ToolProvider only
```

禁止：

- `agent/protocols/memory.py` import Markdown store、FastAPI、SQLite 或 Tool。
- `agent/memory/` import Web route。
- Memory Tool 直接查询 auth DB。
- AgentLoop 根据用户文本硬编码“这句话要记住”。

---

## 18. 测试矩阵

新增 `tests/unit_test/memory/test_case.md`，至少覆盖以下主题。

### 18.1 路径与隔离

| 用例 | 输入/场景 | 期望 |
| --- | --- | --- |
| local operator | CLI context | 使用 `contexts/memory` |
| ordinary user | user id | 使用 `contexts/users/{id}/memory` |
| owner user | Owner Web | 与 CLI 共用 `contexts/memory`，不创建 `contexts/users/{owner_id}` |
| stale owner dir | 历史目录已存在 | 不读取、不写入、不自动删除 |
| invalid user id | 非法 id | 拒绝路径解析 |
| cross user | user A 尝试 user B path | 无路径参数入口，无法访问 |

### 18.2 MarkdownMemoryStore

| 用例 | 输入/场景 | 期望 |
| --- | --- | --- |
| initialize | 文件不存在 | 创建固定模板 |
| add | 合法 category/content | 返回稳定 id 并写入 |
| duplicate | 规范化后相同内容 | 返回已有 entry，不重复 |
| replace | category + old_content | 更新为新内容 |
| delete | category + content | 删除并返回成功 |
| missing content | replace/delete 未找到原内容 | 结构化错误 |
| malformed | 管理标记或 entry 结构损坏 | 读取报错，写入不覆盖 |
| preserve manual | 标记外有人工文本 | 写入后保持不变 |
| atomic failure | 临时写失败 | 原文件不变 |
| limit | 条数、字符或文件超限 | 拒绝写入 |

### 18.3 检索

| 用例 | 输入/场景 | 期望 |
| --- | --- | --- |
| exact | query 命中完整文本 | 优先返回 |
| CJK | 中文短语 | 使用本地 token/bigram 匹配 |
| category | 只查 preferences | 不返回其它分类 |
| empty query | 无 query | 有界返回最近条目 |
| no match | 无相关条目 | success + empty entries |
| output cap | 多个长条目 | 不超过 limit/max chars |

### 18.4 Tool 与权限

| 用例 | 输入/场景 | 期望 |
| --- | --- | --- |
| read allowed | viewer + memory.read.own | 允许 |
| read denied | 无 permission | AUTH_PERMISSION_DENIED |
| explicit write | viewer + user_explicit | 直接执行，不请求 confirmation |
| confirmed write | viewer + user_confirmed | 直接执行，不请求 confirmation |
| write denied | auditor | 拒绝 |
| missing authorization | 缺少 authorization | 拒绝，不写入 |
| inferred write | assistant_inferred 或其它值 | MEMORY_USER_AUTHORIZATION_REQUIRED |
| conversational proposal | 模型识别到长期信息 | 先回答，再自然语言询问，不调用写工具 |
| user refusal | 用户说不用/拒绝 | 不调用写工具 |
| user rewrite | 用户给出修改后的表述 | 以 user_confirmed 写入新表述 |
| secret | token/private key | 拒绝且日志不含原文 |
| invalid args | 缺字段/错误 operation | ToolResult error，不抛异常 |
| audit | write success | 只有 id/category/hash/length |

### 18.5 回归

- 不配置 Memory 时，现有 AgentLoop/ToolRegistry 测试继续通过。
- Session JSONL 格式不变化。
- Part 7 混合 Turn 选择和 endpoint ContextBudget 不被 Memory 绕过。
- Part 9 session owner、模型偏好、RBAC、危险确认和 audit 测试继续通过。
- 默认测试不访问真实 LLM 或网络。

---

## 19. 落地顺序

Part 10 已按以下依赖顺序完成：

1. 新增 `tests/unit_test/memory/test_case.md` 和 MemoryStore 失败测试。
2. 新增 `agent/protocols/memory.py` 和 `MarkdownMemoryStore`。
3. 增加 `AppConfig.local_memory_dir`、普通用户 `UserContext.memory_dir`，沿用已经修正的 Owner workspace context，不创建 `contexts/users/{owner_id}`。
4. 实现 `memory_read`、`memory_write`、MemorySafetyPolicy 和 authorization 参数校验。
5. 增加 Memory permissions、默认角色授权、CLI local operator 权限和 ToolExecutionPolicy 映射。
6. 把 Memory Tool 接入 CLI 默认工具和 Web 用户作用域 ToolProvider；用户授权写入直接执行，不复用 confirmation broker。
7. 新增 `prompts/memory_policy.md` 并接入 ContextBuilder prompt 组合，约束明确请求与对话式 Memory 修改，不增加 turn-end review 调用。
8. 补齐 trace/audit、安全错误码和 CLI/Web 命令测试。
9. 更新 README、总体设计、Part 7/9 前向引用和 Part 10 活文档状态。
10. 运行针对性测试、ruff 和全量 pytest。
11. 增加 `prompts/memory_extraction.md`、`MemoryExtractionService`、五分钟空闲调度、提取检查点和下一次对话通知。
12. 用 `MemoryExtractionScheduler` 替换 per-Session Timer，增加全局两个 Worker、同用户串行、任务合并、取消、队列上限和 30/120 秒有限重试。
13. 删除未形成上下文消费闭环的 Session Summary 半成品，把 Context Compaction 留到后续上下文优化。

---

## 20. 验证命令

实现阶段至少运行：

```bash
python -m ruff check .
python -m pytest tests/unit_test/memory --basetemp .tmp/pytest-memory
python -m pytest tests/unit_test/tools tests/unit_test/auth tests/unit_test/context_builder --basetemp .tmp/pytest-memory-related
python -m pytest --basetemp .tmp/pytest-basetemp
```

如果全量测试因 Windows stdout、缓存目录权限或耗时限制无法一次完成，应按测试主题拆分运行并报告每组结果，不能把工具超时描述成测试失败。

---

## 21. 验收标准

第十部分完成时，应满足：

1. CLI 本地操作者和 Owner Web 共用 workspace 级 Memory；普通数据库用户拥有各自隔离的 Memory 目录。
2. Owner 不创建、不使用 `contexts/users/{owner_id}`；Owner 的 sessions、metadata、workspace 和 Memory 与 CLI 共用全局路径。
3. `MEMORY.md` 是可人工检查的长期记忆真值，格式损坏时不会被自动覆盖。
4. `memory_read` 使用明确的 `list/search` 模式；列表按固定类别整理并返回分页数量信息，搜索使用具体关键词，不需要向量数据库。
5. `memory_write` 支持 add/replace/delete，条目只包含 category 和 content。
6. 用户明确请求或对具体 Memory 修改自然语言同意时可以直接写入；普通聊天不负责主动识别，高可信重复长期信息由空闲提取器自动写入。
7. 明显 secret、credential、完整日志和完整工具结果不能写入 Memory。
8. Memory Tool 经过 `memory.read.own` / `memory.write.own` 权限判断。
9. Part 10 没有跨用户 Memory 管理权限。
10. audit/trace 不记录完整 Memory query 或 content。
11. AgentLoop 不 import MarkdownMemoryStore、auth DB 或用户业务。
12. Memory 不使用 tool confirmation、Web Memory 弹窗、候选状态机、Session Summary 或 session metadata 抑制状态。
13. 空闲提取至少要求三个用户 Turn，每项包含两到三条真实用户证据；中低可信结果直接丢弃，并且提取输入服从 session 模型的 failover-safe ContextBudget。
14. ContextBuilder 不自动注入完整 Memory 文件。
15. 未配置 Memory 时现有聊天、Tool、Skill、Web、turn、权限和日志行为不变。
16. 自动写入后下一次对话显示一次简短通知，不增加前端弹窗。
17. 有 Memory、Tool、对话授权、权限、提取和回归测试主题说明及单元测试。
18. ruff、相关测试和全量测试通过，或明确记录与本次无关的历史问题。

---

## 22. 和其它文档的关系

- `docs_design/2026-07-16-background-memory-extraction-and-trace-convergence-design.md` 记录当前空闲提取、一次性通知和日志去重设计。
- `docs_design/2026-07-16-conversational-memory-consent-design.md` 保留对话式授权和移除 Memory confirmation 的历史设计；其中依赖普通模型主动询问的部分已被空闲提取方案替代。
- `docs_design/2026-07-16-remove-unclosed-session-summary-design.md` 记录删除 Session Summary 半成品、后续单独设计 Context Compaction 的当前决定。
- `docs_design/2026-07-15-memory-boundary-design.md` 保留 Memory 作用域、Markdown 存储和摘要边界的历史设计；其中候选状态机、确认写入和 Session Summary 方案已被 2026-07-16 记录替代。
- `docs_design/zhice-agent-part7-turn-context-design.md` 提供 turn 分组和短期上下文选择；Part 10 不替代该逻辑。
- `docs_design/zhice-agent-part9-user-auth-permission-design.md` 提供 actor、用户目录、权限和 audit 边界；Part 10 必须复用内部 `user_id` 隔离 Memory。
- `docs_design/zhice-agent-overall-design.md` 维护 Part 10～18 的当前顺序；本文是 Part 10 的直接开发依据。
- Part 11 MCP、Part 12 生命周期事件/Hook 扩展点和 Part 13 Subagent 可以读取 Memory 协议，但不能绕过 Memory 权限或直接操作其它用户文件；Part 12 RuntimeEvent 不默认携带 Memory 内容。
