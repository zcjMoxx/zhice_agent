# 智策 Agent（ZhiCe-Agent）总体设计文档

> 目标：设计一个适合学习、开发、逐步实现的轻量 Agent 项目。
>
> 文档类型：当前活文档。本文档始终以最新代码和当前阶段口径为准。

---

## 1. 这份文档要解决什么问题

很多 Agent 系统会同时包含多端接入、用户体系、工具调用、技能扩展、会话管理、长期记忆、前端交互和部署治理。完整平台能力很强，但如果一开始全部纳入，会带来两个问题：

1. **太重**：大量能力在第一阶段用不上，却会增加理解和开发成本。
2. **太耦合**：核心 AgentLoop 容易混入渠道、界面、部署和业务细节，后续演进会变慢。

所以 ZhiCe-Agent 的设计方式是：

> 先搭一个小而清楚的 Agent 内核，再围绕内核逐步扩展。

这份文档是 ZhiCe-Agent 的总体设计蓝图。它既给后续实现用，也适合作为学习路线。后面可以一章一章实现，每一步都知道自己在做什么、为什么这么做。

---

## 2. 核心设计思想

### 2.1 最重要的核心：Agent Loop

Agent 项目最核心的不是 Web，也不是数据库，也不是部署，而是 Agent Loop。

它的基本流程是：

```text
用户输入
  -> 构建上下文
  -> 调用 LLM
  -> LLM 决定是否调用工具
  -> 执行工具
  -> 把工具结果放回上下文
  -> 再调用 LLM
  -> 直到得到最终回答
  -> 保存会话
```

换句话说，Agent Loop 负责让 LLM “边想边做事”。

这个循环是所有能力的根：

- 想读文件？LLM 调 `read_file` 工具。
- 想搜索项目？LLM 调 `grep` 工具。
- 想运行脚本？LLM 调 `exec` 工具。
- 想使用业务能力？LLM 先加载 Skill，再按 Skill 说明执行脚本。
- 想拆任务？未来可以让 LLM 调 `spawn_agent`。

所以智策 Agent 项目第一阶段必须先把 Agent Loop 做出来。

### 2.2 第二个核心：工具系统

工具系统的作用是把 Python 能力暴露给 LLM。

成熟 Agent 系统中的工具通常包括：

- 文件读取、写入、编辑。
- 目录列表。
- grep/glob 搜索。
- shell 执行。
- Skill 加载。
- MCP 工具。
- 记忆工具。
- 子代理工具。
- 消息发送工具。
- 前端 UI 渲染工具。

智策 Agent 当前第一阶段已经落地的最小工具集是：

```text
read_file     读取文件
list_dir      查看目录
grep          搜索文本
exec          执行安全命令
load_skills   读取完整 Skill 说明
sync_skills   同步已配置 Skill source
memory_read   按需检索当前 actor 的长期 Memory
memory_write  执行用户通过对话明确授权的 Memory 修改
```

`write_file`、外部 API 和 Subagent 仍属于后续扩展；Skill 正文加载与同步、受控 Memory 读写和 MCP Tool 已经进入当前轻量工具主线。

工具系统最值得学习的设计是：

```text
Tool 类
  -> 定义 name / description / parameters
  -> 实现 execute(args)

ToolRegistry
  -> 注册工具
  -> 给 LLM 返回工具 schema
  -> 根据 LLM 的 tool_call 执行对应工具
```

这样 Agent Loop 不需要知道每个工具怎么实现，它只需要把 LLM 的工具调用交给 `ToolRegistry`。

### 2.3 第三个核心：Skill 体系

这是 Agent 项目里很有价值的思想之一。

很多 Agent 项目会把业务逻辑直接写进 Agent Loop，比如：

```python
if user_asks_weather:
    call_weather_api()
elif user_asks_report:
    generate_report()
```

这样做短期很快，长期会让 Agent Loop 变成一锅粥。

更好的做法是：

> Agent Loop 不写业务功能。业务功能做成 Skill。

一个 Skill 的结构是：

```text
extends/{source_name}/skills/{skill_name}/
+-- SKILL.md
+-- scripts/
    +-- run.py
```

当前落地代码中，`${ZHICE_AGENT_SKILL_REPO}` 表示本地技能仓库根目录；运行时会按 `${ZHICE_AGENT_WORKSPACE}/config/skill_sources.yml` 同步到 `${ZHICE_AGENT_WORKSPACE}/extends/{source_name}/`，`SkillLoader` 固定从各 source 的 `skills/` 目录扫描。Skill 的运行时身份是 `source/name`，其中 `name` 来自 Skill 外层目录名。

`SKILL.md` 是给 LLM 看的说明书，里面写：

- 这个 Skill 什么时候用。
- 参数有哪些。
- 怎么执行脚本。
- 输入 JSON 示例。
- 输出 JSON 格式。
- 错误码含义。
- 失败后该不该重试。

脚本负责真正执行能力。

LLM 的使用流程是：

```text
1. 看到 skill 摘要
2. 判断 skill 可能有用
3. 调 load_skills 读取完整 SKILL.md
4. 按 SKILL.md 里的命令调用 exec
5. exec 执行 scripts/*.py --params '{JSON}'
6. 脚本返回 JSON
7. LLM 根据结果回答用户
```

智策 Agent 项目应该完整保留这个思想。

但是第一阶段不要引入复杂的多层扩展目录，比如用户私有层、开发暂存层、团队扩展层、公共扩展层等覆盖关系。

智策 Agent 项目当前阶段只保留一层 source 仓库，不做多层覆盖链：

```text
skill_repo/skills/{skill_name}/SKILL.md
skill_repo/skills/{skill_name}/scripts/*.py

同步后：
${ZHICE_AGENT_WORKSPACE}/extends/{source_name}/skills/{skill_name}/SKILL.md
${ZHICE_AGENT_WORKSPACE}/extends/{source_name}/skills/{skill_name}/scripts/*.py
```

### 2.4 第四个核心：协议边界

成熟系统通常会设置协议层，它的思想是：

> 各模块之间尽量通过接口沟通，而不是直接依赖具体实现。

智策 Agent 项目不需要那么多协议，但应该保留最小的几个：

```text
LLMProvider      调 LLM 的接口
ToolProvider     工具注册与执行接口
SkillProvider    技能发现与加载接口
SessionStore     会话存储接口
```

这样做的好处是：

- 一开始可以用 OpenAI，后面换 LiteLLM。
- 一开始可以用 JSONL 存会话，后面换 SQLite。
- 一开始 Skill 只读本地目录，后面可以读远程仓库。
- Agent Loop 不需要跟所有具体实现纠缠。

### 2.5 第五个核心：Prompt 文件化

这里采用一个重要规范：

> 所有 LLM 会看到的 prompt，都放在 Markdown 文件里。

智策 Agent 项目也应该坚持这个原则。

推荐：

```text
prompts/
+-- identity.md
+-- tool_use_policy.md
+-- skills_intro.md
+-- summarize_session.md
```

不要在 Python 里到处写长 prompt。

原因：

- Prompt 是 Agent 的“操作系统说明书”。
- Prompt 应该能被人直接阅读。
- Prompt 的演进应该走版本管理。
- 后续调 Agent 行为时，改 Markdown 比翻 Python 字符串舒服。

### 2.6 第六个核心：Session 是上下文的一部分

Session 不只是日志。

Session 会影响下一轮对话，因为历史消息会被重新放回 LLM 上下文。

智策 Agent 项目第一版可以用 JSONL。当前普通 CLI 未指定 `--session` 时默认使用当天会话，例如：

```text
contexts/sessions/chat-YYYYMMDD.jsonl
```

显式传入 `--session chat-YYYYMMDD` 或其它名称时，仍然可以恢复指定会话。

每一行是一条消息：

```json
{"role":"user","content":"hello","timestamp":1781000000.0}
```

优点：

- 简单。
- 可读。
- 容易调试。
- 不需要数据库。

等后面需要会话搜索、会话标题、工具步骤表，再升级 SQLite。

当前上下文治理已经进入 turn-based 阶段：`ContextBuilder` 先按最近 user turn 形成候选，再做本地相关性选择，并继续保持 tool-call block 合法，避免简单问候被很久以前的任务牵引。Part 10 已增加按需长期 Memory 和显式 session 摘要，但不把完整 Memory 固定注入每轮上下文；后续更复杂的自动压缩仍需单独设计。

---

## 3. 智策 Agent（ZhiCe-Agent）项目的总体设计

### 3.1 当前实现目标

当前代码库已经落地的是一个轻量、可运行、可逐步演进的本地 Agent 内核，核心能力是：

```text
一个本地智策 Agent（CLI + Web）：
能启动，
能加载 workspace 配置，
能读取 Markdown Prompt，
能保存和恢复会话，
能完成无工具聊天和工具调用循环，
能把只读工具与安全 exec 暴露给 LLM，
能通过 OpenAI-compatible 或 LiteLLM Provider 调用模型，
能按 endpoint priority 做轻量 failover，
能用 /model 查看和切换本进程内首选模型，
能通过 `zcagent gateway` 启动本地 FastAPI gateway，
能提供 REST/SSE 兼容 API、WebSocket 主聊天通道和静态 Web UI，
能在 Web 端查看、重命名、删除会话并进行模型选择，
能用稳定 `turn_id` / `turn_index` 串起一轮用户请求、WebSocket 事件和 JSONL 会话消息，
能按最近 user turn 候选和本地相关性选择历史上下文，
能通过分层 Gateway / Agent 日志和 workspace `trace.log` 观察 turn、LLM、tool 和 session 保存轨迹，
能通过本地 SQLite 用户、角色、特权和可撤销 cookie 登录态保护 Web API / WebSocket，
能按内部 user_id 隔离用户上下文、session index 和 session 模型偏好，
能让登录用户直接使用本人资源与安全工具，并在跨用户、管理、审计和危险操作前进行特权检查，
能让 CLI 与 Owner 共用 workspace Memory、普通用户使用私有 Memory，
能通过 memory_read 按需检索，通过用户对话授权后的 memory_write 修改长期 Memory，
能用 `/memory` 展示长期 Memory，
能通过 `zcagent init` 生成运行时文件。
```

这就是当前代码真正实现出来的阶段闭环。它已经不是“只聊天”的版本，而是包含本地工具、命令验证、endpoint 管理、模型切换、本地 Web 使用面、turn 上下文治理和运行日志的轻量 Agent 内核。

### 3.2 未来扩展方向

Part 10 Memory、Part 11 MCP 和 Part 12 生命周期事件/Hook Runtime 已经完成；后续先完成当前代码尚不存在的能力，再进入分领域优化：

1. Part 13 Subagent：复用同一个 AgentLoop 执行受限子任务并把摘要交回父 Agent。
2. Part 14 外部渠道：接入 IM / 协作平台，并统一身份、命令、session 和 turn 语义。
3. Part 15 生产部署与发布：补齐容器、反向代理、Secret 注入、健康检查和发布产物。
4. Part 16 Agent 运行可靠性与上下文优化。
5. Part 17 Web、会话与用户治理优化。
6. Part 18 Skill Runtime、CLI 与本地运维优化：独立承接 SkillExecutor、`skill.*` 与 ProgressSink。

Part 13～15 继续补齐新能力；Part 16～18 再优化 Part 1～15 已经形成的运行链路。第十部分当前实现见 `docs_design/zhice-agent-part10-memory-design.md`；Part 11 当前实现见 `docs_design/zhice-agent-part11-mcp-design.md`，本次边界取舍见 `docs_design/2026-07-17-mcp-tool-runtime-boundary-design.md`；Part 12 当前实现见 `docs_design/zhice-agent-part12-hooks-design.md` 和 `docs_design/2026-07-20-hook-runtime-boundary-design.md`；排序背景见 `docs_design/2026-07-06-next-stage-sequencing-design.md`。

### 3.3 推荐目录结构

```text
zhice_agent/
+-- pyproject.toml
+-- README.md
+-- .env.example
+-- config/
|   +-- llm_endpoints.example.json
|   +-- skill_sources.example.yml
+-- prompts/
|   +-- identity.md
|   +-- tool_use_policy.md
|   +-- skills_intro.md
|   +-- summarize_session.md
+-- agent/
|   +-- __init__.py
|   +-- cli.py
|   +-- message.py
|   +-- config.py
|   +-- prompt_loader.py
|   +-- app/
|   |   +-- auth.py
|   |   +-- gateway.py
|   |   +-- runtime.py
|   |   +-- api/
|   |       +-- routes.py
|   |       +-- schemas.py
|   |       +-- ws.py
|   +-- core/
|   |   +-- loop.py
|   |   +-- context.py
|   |   +-- context_relevance.py
|   |   +-- turns.py
|   +-- protocols/
|   |   +-- auth.py
|   |   +-- llm.py
|   |   +-- tool.py
|   |   +-- skill.py
|   |   +-- session.py
|   +-- llm/
|   |   +-- openai_provider.py
|   |   +-- litellm_provider.py
|   |   +-- failover_provider.py
|   |   +-- selection.py
|   +-- auth/
|   |   +-- store.py
|   |   +-- schema.py
|   |   +-- passwords.py
|   |   +-- tokens.py
|   |   +-- audit.py
|   |   +-- session_access.py
|   |   +-- user_context.py
|   |   +-- tool_policy.py
|   |   +-- confirmation.py
|   |   +-- diagnostics.py
|   +-- tools/
|   |   +-- base.py
|   |   +-- registry.py
|   |   +-- readonly.py
|   |   +-- exec.py
|   |   +-- shell_policy.py
|   |   +-- scoped.py
|   |   +-- diagnostics.py
|   +-- skills/
|   |   +-- loader.py
|   |   +-- markdown.py
|   |   +-- sync.py
|   +-- session/
|       +-- jsonl_store.py
|       +-- model_preferences.py
+-- skill_repo/
|   +-- skills/
|       +-- README.md
|       +-- {skill_name}/
|           +-- SKILL.md
|           +-- scripts/
+-- web/
|   +-- static/
|       +-- index.html
|       +-- styles.css
|       +-- app.js
+-- tests/
    +-- unit_test/
```

这份目录结构是当前轻量形态。项目已经从 CLI-only 演进到带本地 auth/RBAC 的 Web gateway：`AgentLoop` 和 `ContextBuilder` 位于 `agent/core/`，身份/权限/用户 session 服务位于 `agent/auth/`，HTTP/WS 壳位于 `agent/app/`，静态 UI 位于 `web/static/`。当前不保留 `agent/gateway.py`、`agent/loop.py` 或 `agent/context.py` 兼容导出层。

参考大型 Agent 项目时，更应该吸收它的边界思想，而不是直接复制目录重量：

```text
app shell       -> CLI / HTTP API / Web / 渠道 / 鉴权 / 产品服务
agent core      -> AgentLoop / ContextBuilder / ToolRegistry / Provider / Session
protocols       -> LLMProvider / ToolProvider / SkillProvider / SessionStore 等稳定协议
```

当前代码里：

- `agent/core/loop.py`、`agent/core/context.py`、`agent/core/turns.py`、`agent/core/context_relevance.py`、`agent/tools/`、`agent/llm/`、`agent/session/` 属于核心与可替换能力层。
- `agent/auth/` 是 app/application 侧身份、权限、session access、confirmation 和 audit 实现；AgentLoop 只消费 `agent/protocols/auth.py` / `tool.py` 中的上下文和策略协议。
- `agent/app/gateway.py`、`agent/app/runtime.py`、`agent/app/api/*` 和 `web/static/*` 属于 app shell / Web 边界。
- `agent/cli.py` 属于入口层；gateway 实现直接位于 `agent/app/gateway.py`，不再保留顶层 re-export 文件。
- `agent/protocols/` 已经承担协议层职责，应该保持只放接口和数据结构。

后续如果继续演进 OAuth/SSO、远程部署、多渠道或完整前端工程，可以在现有边界上扩展，但仍保持分层方向：

```text
agent/
+-- app/
|   +-- gateway.py
|   +-- api/
|       +-- routes.py
|       +-- ws.py
+-- core/
|   +-- loop.py
|   +-- context.py
|   +-- turns.py
|   +-- context_relevance.py
+-- protocols/
+-- tools/
+-- llm/
+-- session/
+-- config.py
```

迁移原则：

- 不为了“看起来像平台”继续拆出空目录。
- 依赖方向固定为 `app -> core -> protocols`，`protocols` 禁止 import 具体实现。
- `core` 不 import `app`，AgentLoop 不知道 CLI、HTTP、Web、鉴权或渠道。
- 先在设计文档里写清迁移范围，再做文件移动，避免纯路径重构打断当前里程碑学习。

---

## 4. 核心运行流程

### 4.1 一轮对话发生了什么

用户输入：

```text
hello
```

当前 Agent 内部流程是：

```text
1. CLI 收到用户输入
2. AgentLoop 加载 session 历史
3. ContextBuilder 构造 system prompt + history + 当前用户消息
4. LLMProvider 调用模型一次
5. 如果 LLM 返回 tool_calls，AgentLoop 串行执行工具并把 tool 消息回填
6. 重复调用 LLM，直到得到无工具调用的最终 assistant 回复，或达到工具轮数上限
7. 保存 user / assistant(tool_calls) / tool / assistant(final) 等本轮消息
8. 如果 LLM 或保存失败，也把错误作为 assistant 消息保留
9. CLI 打印最终文本
```

### 4.2 Mermaid 流程图

```mermaid
flowchart TD
    A["zcagent"] --> B["bootstrap config/.env"]
    B --> C{"subcommand"}
    C -->|"init"| D["init runtime files in workspace"]
    C -->|"gateway"| E["load workspace and start FastAPI gateway"]
    C -->|"none"| F["start chat CLI"]

    F --> G["load_config"]
    G --> H["ensure runtime dirs"]
    H --> I["PromptLoader + JsonlSessionStore + ContextBuilder"]
    I --> J["load_llm_endpoints + EndpointFailoverProvider"]
    J --> T["create_default_tool_registry"]
    T --> K["AgentLoop.run_turn(session_id, text)"]
    K --> L{"assistant has tool_calls?"}
    L -->|"yes"| N["ToolRegistry.execute"]
    N --> O["append tool result to messages"]
    O --> K
    L -->|"no"| P["append session messages"]
    P --> M["print assistant text or error message"]
```

当前实现已经包含工具调用、多轮 tool loop、Skill source 同步、SkillLoader、`load_skills`、`sync_skills`、受控 Memory、MCP Tool、显式 session 摘要、FastAPI gateway、WebSocket 主聊天通道、静态 Web UI、RuntimeEvent 和受限 pre/post Tool Hook Runtime；Subagent 仍是后续实现。

---

## 5. 数据结构设计

下面第 5 节开始同时包含当前代码结构和长期路线图。当前已实现 CLI、配置、Prompt、Session、无工具聊天、工具调用、安全 exec、LiteLLM、endpoint failover、`/model`、FastAPI gateway、REST/SSE 兼容接口、WebSocket 主聊天通道、静态 Web UI、Skill source 同步、SkillLoader、`load_skills`、`sync_skills`、Memory、MCP、显式 session 摘要、RuntimeEvent 和受限 Tool Hook Runtime；Subagent 仍待设计与实现。

### 5.1 Message

消息是 Agent 的最基本数据。

```python
from dataclasses import dataclass, field
from typing import Any, Literal

Role = Literal["system", "user", "assistant", "tool"]

@dataclass
class Message:
    role: Role
    content: str
    name: str | None = None
    tool_call_id: str | None = None
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
```

角色含义：

```text
system      给 LLM 的系统指令
user        用户输入
assistant   LLM 输出
tool        工具执行结果
```

### 5.2 ToolResult

```python
@dataclass
class ToolResult:
    output: str
    is_error: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)
```

工具永远返回统一结构。

这样 AgentLoop 不需要关心每个工具内部细节。

### 5.3 ToolDefinition

当前代码没有单独的 `ToolDefinition` dataclass，而是让每个 `Tool` 直接声明 `name`、`description`、`parameters`，由 `ToolRegistry.definitions()` 生成 OpenAI-compatible `tools` schema。

```python
class Tool(Protocol):
    name: str
    description: str
    parameters: dict[str, Any]

    def execute(self, args: dict[str, Any]) -> ToolResult:
        ...
```

当前第一阶段刻意保持工具协议轻量：

- `name`、`description`、`parameters` 是 function calling 风格模型需要理解的核心信息。
- `ToolRegistry.definitions()` 返回 `list[dict[str, Any]]`，也就是 OpenAI-compatible schema。
- 如果后续要支持更多模型供应商差异，再把中性 `ToolDefinition` 抽出来，让 provider adapter 负责格式转换。
- `exclusive`、`category`、`read_only` 等元信息用于后续并发、确认流或 Skill 分类，当前代码暂不引入。

当前阶段使用 OpenAI-compatible dict 作为共同格式，因为 `OpenAIProvider` 和 `LiteLLMProvider` 都能消费这类 `tools` schema。等接入不兼容该 schema 的模型时，应把转换逻辑收口到 LLM provider 或小型 adapter 中。

### 5.4 SkillInfo

```python
@dataclass
class SkillInfo:
    source: str
    name: str
    qualified_name: str
    description: str
    root: Path
    skill_file: Path
    scripts_dir: Path
    summary: str
    metadata: dict[str, Any] = field(default_factory=dict)
```

这个结构用于向 LLM 展示 Skill 摘要，并保留 `source/name` 限定名，避免跨 source 同名 Skill 调用歧义。

### 5.5 SessionState

```python
@dataclass
class SessionState:
    session_id: str
    messages: list[Message]
    metadata: dict[str, Any] = field(default_factory=dict)
```

---

## 6. 协议接口设计

### 6.1 LLMProvider

```python
class LLMProvider(Protocol):
    def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
    ) -> LLMResponse:
        ...
```

返回统一格式：

```python
@dataclass
class LLMResponse:
    content: str
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
```

这样 OpenAI、LiteLLM、OpenRouter、本地模型都可以被包装成同一种接口。

当前接口是同步的。后续如果 Web API、并发工具或 MCP 需要异步能力，应先新增设计文档，再统一评估是否把 `LLMProvider`、`ToolProvider` 和 `AgentLoop` 改为 async。

LLMProvider 内部负责把工具 schema 传给目标模型需要的请求格式：

```text
ToolRegistry.definitions()
  -> OpenAI-compatible tools
  -> LiteLLM tools
  -> 其他供应商的工具调用格式
```

这样 `AgentLoop` 和 `ToolRegistry` 不需要知道当前模型到底使用 OpenAI、Anthropic、Gemini 还是本地兼容服务。

### 6.2 LLM 错误结构

LLM 错误也属于 provider 边界的一部分。`AgentLoop` 不应该靠解析 HTTP body 或字符串片段判断错误原因；当前只接收 provider 抛出的安全错误文本，保存会话并展示用户可读提示。

#### 当前已实现

```python
class LLMProviderError(RuntimeError):
    """Base error raised by LLM providers."""


class LLMConfigurationError(LLMProviderError):
    """Raised when LLM configuration is missing or invalid."""
```

`LLMConfigurationError` 用于本地配置错误，例如缺少 `api_key`、`${ENV_VAR}` 未定义、endpoint 配置字段非法等。`LLMProviderError` 用于运行时 LLM 调用失败。两者都只携带安全文本，不包含结构化错误元信息。

错误文本必须脱敏。Provider 可以保留短错误摘要，但不能把真实 API key、Authorization header、完整请求体或长 traceback 直接交给 `AgentLoop`。

#### 后续目标：结构化错误分类

后续错误分类阶段应把 `LLMProviderError` 扩展为携带结构化元信息：

```python
LLMErrorCode = Literal[
    "CONFIG_INVALID",
    "AUTH_FAILED",
    "MODEL_NOT_FOUND",
    "RATE_LIMITED",
    "NETWORK_ERROR",
    "INVALID_RESPONSE",
    "PROVIDER_HTTP_ERROR",
    "PROVIDER_ERROR",
]


class LLMProviderError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        code: LLMErrorCode = "PROVIDER_ERROR",
        status_code: int | None = None,
        retryable: bool = False,
        user_hint: str = "",
    ):
        ...
```

Provider 层负责把具体供应商错误转换为稳定错误码：

```text
401 / invalid_api_key       -> AUTH_FAILED
403 / permission denied     -> AUTH_FAILED
404 / model not found       -> MODEL_NOT_FOUND
429 / rate limit            -> RATE_LIMITED
DNS / timeout / connect     -> NETWORK_ERROR
JSON decode failed          -> INVALID_RESPONSE
其他 HTTP 4xx/5xx           -> PROVIDER_HTTP_ERROR
未知异常                    -> PROVIDER_ERROR
```

配置错误通常不是 retryable；限流、网络抖动、部分 5xx 可以标记为 `retryable=True`，供后续自动重试或提示用户稍后再试。

### 6.3 Tool

```python
class Tool(Protocol):
    name: str
    description: str
    parameters: dict[str, Any]

    def execute(self, args: dict[str, Any]) -> ToolResult:
        ...
```

### 6.4 ToolProvider

```python
class ToolProvider(Protocol):
    def definitions(self) -> list[dict[str, Any]]:
        ...

    def execute(self, name: str, args: dict[str, Any]) -> ToolResult:
        ...
```

`ToolProvider` 当前提供 OpenAI-compatible 工具定义和执行入口，这是第一阶段的务实选择。长期如果接入不兼容 OpenAI tools schema 的 provider，再把 schema 转换下沉到 provider adapter。

### 6.5 SkillProvider

```python
class SkillProvider(Protocol):
    def list_skills(self) -> list[SkillInfo]:
        ...

    def get_skill_body(self, name: str) -> str:
        ...
```

### 6.6 SessionStore

```python
@dataclass
class SessionSummary:
    session_id: str
    preview: str
    updated_at: float
    message_count: int


class SessionStore(Protocol):
    def load(self, session_id: str) -> SessionState:
        ...

    def append(self, session_id: str, messages: list[Message]) -> None:
        ...

    def clear(self, session_id: str) -> None:
        ...

    def list_sessions(self) -> list[SessionSummary]:
        ...
```

`clear` 被 `/reset` 使用，`list_sessions` 被 `/sessions` 使用。`SessionSummary` 用于渲染 CLI 会话列表。

---

## 7. AgentLoop 设计

### 7.1 AgentLoop 负责什么

AgentLoop 只负责通用循环：

1. 接收用户消息。
2. 加载历史。
3. 构造上下文。
4. 调用 LLM。
5. 执行工具。
6. 回填工具结果。
7. 重复直到最终回答。
8. 保存消息。

### 7.2 AgentLoop 不负责什么

AgentLoop 不应该负责：

- 天气业务怎么查。
- 报告业务怎么写。
- 外部协作消息怎么发。
- 审批怎么走。
- 前端怎么显示。
- 某个 Skill 的参数怎么解释。
- HTTP 状态码、供应商错误体、限流和模型不存在等 LLM provider 细节怎么分类。

这些应该放到工具、Skill、渠道层或者前端层。

### 7.3 简化伪代码

```python
class AgentLoop:
    def __init__(self, llm, sessions, context_builder, workspace, tools=None, max_tool_iterations=4):
        self.llm = llm
        self.sessions = sessions
        self.context_builder = context_builder
        self.workspace = workspace
        self.tools = tools
        self.max_tool_iterations = max_tool_iterations
        # Skill 摘要由 ContextBuilder 注入，AgentLoop 仍只处理通用 tool loop

    def run_turn(self, session_id: str, user_text: str) -> str:
        session = self.sessions.load(session_id)
        user_msg = Message(role="user", content=user_text)

        messages = self.context_builder.build(
            history=session.messages,
            user_message=user_msg,
        )

        new_messages = [user_msg]
        final_text = ""

        tool_definitions = self.tools.definitions() if self.tools else None

        for _ in range(self.max_tool_iterations):
            try:
                response = self.llm.chat(messages=messages, tools=tool_definitions)
            except LLMConfigurationError as exc:
                error_text = format_configuration_error(exc)
                new_messages.append(Message(role="assistant", content=error_text, metadata={"is_error": True}))
                final_text = error_text
                break
            except LLMProviderError as exc:
                error_text = format_provider_error(exc)
                new_messages.append(Message(role="assistant", content=error_text, metadata={
                    "is_error": True,
                    "error_type": type(exc).__name__,
                }))
                final_text = error_text
                break
            except Exception as exc:
                error_text = format_unknown_llm_error(type(exc).__name__)
                new_messages.append(Message(role="assistant", content=error_text, metadata={"is_error": True}))
                final_text = error_text
                break

            assistant_msg = Message(
                role="assistant",
                content=response.content or "",
                tool_calls=response.tool_calls or [],
            )
            messages.append(assistant_msg)
            new_messages.append(assistant_msg)

            tool_calls = assistant_msg.tool_calls
            if not tool_calls:
                final_text = assistant_msg.content
                break

            for call in tool_calls:
                result = self.tools.execute(
                    call["name"],
                    call["arguments"],
                )
                tool_msg = Message(
                    role="tool",
                    name=call["name"],
                    tool_call_id=call["id"],
                    content=result.output,
                    metadata=result.metadata,
                )
                messages.append(tool_msg)
                new_messages.append(tool_msg)

        self.sessions.append(session_id, new_messages)
        return final_text or "工具调用轮数已达到上限，未生成最终回答。"
```

这里的重点不是让 `AgentLoop` 变成错误分类中心，而是让它稳定收尾：

- 保存本轮已经产生的 `user`、`assistant(tool_calls)`、`tool`、`assistant(error)` 消息。
- 对 `LLMConfigurationError` 给出可操作配置提示。
- 对 `LLMProviderError` 使用 provider 给出的安全错误文本格式化提示。
- 对未知异常只展示错误类型，不展示原始异常正文，避免泄露 secret。
- 不在 `AgentLoop` 里解析 HTTP body、供应商错误 JSON 或模型私有字段。
- `code`、`status_code`、`retryable`、`user_hint` 等结构化错误字段是后续 Provider 错误分类阶段要补的能力。

---

## 8. ContextBuilder 设计

ContextBuilder 负责把各种上下文拼成 LLM 能理解的 messages。

输入：

- 身份 prompt。
- 工具使用策略。
- Skill 摘要。
- 历史消息。
- 当前用户消息。

输出：

```python
[
    {"role": "system", "content": "..."},
    {"role": "user", "content": "...历史用户消息..."},
    {"role": "assistant", "content": "...历史回答..."},
    {"role": "user", "content": "...当前用户输入..."}
]
```

### 8.1 system prompt 结构

推荐：

```text
# 你的身份
{identity.md}

# 工具使用规则
{tool_use_policy.md}

# Skill 使用规则
{skills_intro.md}

# 当前可用 Skill 摘要
{skill_summaries}

# 运行环境
workspace={workspace}
session_id={session_id}
```

### 8.2 历史裁剪

第一版不要做复杂压缩。

简单策略：

- 保留最近 30 条消息。
- 如果工具结果太长，截断到固定长度。

后续再做：

- token 估算。
- 完整的 Context Compaction 与续接 checkpoint。
- memory recall。

---

## 9. Prompt 文件设计

### 9.1 `prompts/identity.md`

说明 Agent 是谁。

示例：

```markdown
你是一个智策 Agent，帮助用户思考、开发、研究和自动化。

你会认真理解用户目标，必要时使用工具，而不是凭空猜测。

你不把业务流程硬编码在自己身上。当某个能力以 Skill 形式存在时，你应该先加载并阅读 Skill 说明。

你回答要清晰、具体、可执行。
```

### 9.2 `prompts/tool_use_policy.md`

说明工具使用规则。

示例：

```markdown
当工具能显著提高准确性或能让你完成实际操作时，使用工具。

工具失败后，不要用完全相同的参数反复重试。

执行可能破坏文件或仓库状态的命令前，必须确认用户确实要求这样做。

当已有工具结果足够回答时，停止继续调用工具，直接回答用户。
```

### 9.3 `prompts/skills_intro.md`

说明 Skill 是什么。

示例：

```markdown
Skill 是外部能力包，每个 Skill 有一个 SKILL.md 文件和可选 scripts 脚本。

Skill 摘要只告诉你它大概能做什么。完整 SKILL.md 会告诉你参数、示例、执行命令和返回格式。

当某个 Skill 可能有用但你还没有看到完整正文时，先调用 load_skills。

加载 Skill 后，严格按 SKILL.md 的说明执行。
```

---

## 10. 工具体系设计

### 10.1 BaseTool

```python
class BaseTool:
    name: str
    description: str
    parameters: dict[str, Any]

    def execute(self, args: dict[str, Any]) -> ToolResult:
        try:
            return self._execute(args)
        except ToolExecutionError as exc:
            return ToolResult(output=exc.message, is_error=True, metadata=exc.metadata)

    def _execute(self, args: dict[str, Any]) -> ToolResult:
        raise NotImplementedError
```

`BaseTool` 负责所有具体工具共享的参数校验、workspace 路径 guard、错误包装和输出截断。当前第一阶段不抽象 `read_only`、`exclusive` 等工具元信息，等并发工具、确认流或更复杂策略出现时再补。

### 10.2 ToolRegistry

```python
class ToolRegistry:
    def __init__(self, tools: list[Tool]):
        self._tools = {}
        for tool in tools:
            self._register(tool)

    def definitions(self) -> list[dict[str, Any]]:
        return [
            {
                "type": "function",
                "function": {
                    "name": tool.name,
                    "description": tool.description,
                    "parameters": copy.deepcopy(tool.parameters),
                },
            }
            for tool in self._tools.values()
        ]

    def execute(self, name: str, args: dict[str, Any]) -> ToolResult:
        tool = self._tools.get(name)
        if not tool:
            return ToolResult(
                output=f"Tool not found: {name}",
                is_error=True,
            )
        return tool.execute(args)
```

`ToolRegistry` 的职责边界：

- 管理工具注册、重名检查和按名称分发执行。
- 返回 OpenAI-compatible tool schema；这是当前 Provider 实现已支持的最小公共格式。
- 把未知工具、参数错误和工具异常转换为 `ToolResult`，避免异常穿透到 AgentLoop。
- 后续可以逐步增加 alias、hidden、category filter、pre/post hook 或中性 `ToolDefinition`，但这些不放入第一阶段已实现闭环。

参考项目里的 hook、UI metadata、分类动态加载、并发批处理都很完整，但对智策 Agent 当前阶段偏重。当前只吸收最小稳定边界：工具协议、注册表、workspace guard、结构化错误和输出截断。

### 10.3 MVP 工具清单

当前已实现：

```text
list_dir      列出目录
read_file     读取文件
grep          搜索文本
exec          执行安全命令
load_skills   读取完整 Skill 说明
sync_skills   同步已配置 Skill source
```

当前未实现 `write_file`。Skill 正文加载通过 `load_skills` 提供，Skill 脚本执行复用受控 `exec`。

### 10.4 exec 工具的安全规则

`exec` 最容易出问题，必须早加护栏。

第一版至少要有：

- 默认工作目录限制在 workspace。
- 禁止访问 workspace 外的路径。
- 命令超时。
- 输出截断。
- 拦截明显危险命令。

危险命令示例：

```text
rm -rf
git reset --hard
del /s
rmdir /s
format
```

---

## 11. Skill 体系设计

### 11.1 Skill 目录

```text
skill_repo/skills/example_calculator/
+-- SKILL.md
+-- scripts/
    +-- calculate.py
```

同步后运行时路径为：

```text
${ZHICE_AGENT_WORKSPACE}/extends/zhice-official/skills/example_calculator/
+-- SKILL.md
+-- scripts/
    +-- calculate.py
```

### 11.2 SKILL.md 示例

````markdown
---
name: example_calculator
description: 用 Python 脚本执行基础四则运算。
category: default
---

# Example Calculator

当用户需要精确计算时使用这个 Skill。

## 参数

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| operation | string | 是 | add、subtract、multiply、divide 之一 |
| a | number | 是 | 第一个数字 |
| b | number | 是 | 第二个数字 |

## 执行方式

```bash
python extends/zhice-official/skills/example_calculator/scripts/calculate.py --params '{"operation":"add","a":3,"b":5}'
```

## 返回格式

```json
{
  "status": "success",
  "code": "OK",
  "data": {"result": 8},
  "message": "3 + 5 = 8",
  "error_stack": ""
}
```

## 错误码

| code | 含义 | 重试策略 |
|---|---|---|
| OK | 成功 | 继续 |
| INVALID_PARAM | 参数错误 | 修改参数后重试 |
| DIVIDE_BY_ZERO | 除数为 0 | 不要用相同参数重试 |
````

### 11.3 Skill 脚本规范

所有 Skill 脚本都应该：

- 接收 `--params` JSON 字符串。
- 最后一行输出 JSON。
- 不 import `agent.*`。
- 需要上下文时通过环境变量读取。

标准成功返回：

```json
{
  "status": "success",
  "code": "OK",
  "data": {},
  "message": "",
  "error_stack": ""
}
```

标准失败返回：

```json
{
  "status": "error",
  "code": "INVALID_PARAM",
  "data": null,
  "message": "operation is required",
  "error_stack": ""
}
```

### 11.4 SkillLoader

SkillLoader 负责：

- 扫描每个启用 source 的 `extends/{source}/skills/*/SKILL.md`。
- 解析 frontmatter。
- 返回包含 `source`、`name`、`qualified_name` 的 Skill 摘要。
- 按 `source/name` 或无歧义裸名返回完整 `SKILL.md`。

伪代码：

```python
class SkillLoader:
    def __init__(self, skill_roots: list[SkillRoot]):
        self.skill_roots = skill_roots

    def list_skills(self) -> list[SkillInfo]:
        ...

    def get_skill_body(self, name: str, source: str | None = None) -> str:
        ...
```

当前 source-aware root 只作为 `SkillSourceSync` 和 `SkillLoader` 之间的内部输入，不放入 `agent/protocols/skill.py`。对外稳定协议仍然是 `SkillInfo`、`SkillProvider` 和 `SkillError`。

后续如果多个模块都稳定依赖 source root 信息，例如 `/skills status`、Skill 索引缓存、Skill 健康检查、source 权限过滤、同步来源审计等，再把 `SkillRoot` 提升为协议层数据结构。提升前应先确认它不再只是“扫描目录”的内部细节，而是多个模块共同消费的稳定契约。

---

## 12. Session 设计

### 12.1 JSONL 存储

目录：

```text
contexts/sessions/
+-- chat-YYYYMMDD.jsonl
+-- default.jsonl
+-- project-a.jsonl
```

每行：

```json
{
  "role": "user",
  "content": "hello",
  "timestamp": 1781000000.0,
  "metadata": {}
}
```

### 12.2 为什么先用 JSONL

因为它：

- 容易实现。
- 容易阅读。
- 容易调试。
- 不需要数据库。
- 很适合单人本地项目。

### 12.3 什么时候升级 SQLite

需要这些能力时再升级：

- 会话列表分页。
- 会话搜索。
- 消息索引。
- 工具步骤单独展示。
- 文件引用。
- 多用户。

---

## 13. LLM Provider 设计

### 13.1 配置文件

`${ZHICE_AGENT_WORKSPACE}/config/llm_endpoints.json`：

```json
{
  "default": "openai_gpt5",
  "openai_gpt5": {
    "protocol": "openai",
    "provider": "",
    "base_url": "https://api.openai.com/v1",
    "api_key": "${ZHICE_LLM_OPENAI_API_KEY}",
    "model": "gpt-5.5",
    "supported_models": ["gpt-5.5", "gpt-5.4", "gpt-5.4-mini"],
    "max_tokens": 16384,
    "temperature": 0.7,
    "priority": 1,
    "enabled": true,
    "role": "default"
  },
  "litellm_claude": {
    "protocol": "litellm",
    "provider": "anthropic",
    "api_key": "${ANTHROPIC_API_KEY}",
    "model": "claude-opus-4.8",
    "supported_models": ["claude-opus-4.8", "claude-opus-4.6"],
    "max_tokens": 16384,
    "temperature": 0.7,
    "priority": 1,
    "enabled": true,
    "role": "default"
  }
}
```

`protocol="openai"` 表示 `base_url` 直接指向 OpenAI-compatible 模型网关。`protocol="litellm"` 表示在 ZhiCe-Agent 进程内调用 LiteLLM SDK；`base_url` 对 litellm 是可选字段，只在需要自定义 `api_base` 时填写。

### 13.2 为什么要包一层 Provider

不要让 AgentLoop 直接依赖某个 SDK。

好处：

- 当前可以通过 `OpenAIProvider` 直连 OpenAI-compatible endpoint。
- 当前可以通过 `LiteLLMProvider` 调用进程内 LiteLLM SDK，再接 Anthropic、Gemini、DeepSeek 等模型商。
- 后面可以换 OpenRouter。
- 后面可以接本地模型。
- 当前可以做轻量 endpoint failover：启动首选 endpoint 失败后，按 `priority` 尝试其它 enabled endpoint。
- 当前 `/model` 可以查看和切换本进程内的首选 endpoint。
- 用户系统实现后，所有入口把 `/model` 选择写入当前 session metadata。`/model reset` 清当前 session 偏好，`/new` 创建使用系统默认的新 session；不增加用户默认模型层。

AgentLoop 只认：

```python
llm.chat(messages, tools)
```

---

## 14. 配置设计

### 14.1 `.env.example`

```env
ZHICE_AGENT_WORKSPACE=C:\Users\you\ZhiCe-Agent-Workspace
ZHICE_LLM_OPENAI_API_KEY=your-api-key
```

当前 CLI 会优先加载项目目录下的 `config/.env`，也支持通过进程级 `--env-file` 指定其它 dotenv 文件。`ZHICE_AGENT_WORKSPACE` 必须显式提供；没有 workspace 时，`zcagent` 会直接提示用户初始化配置，而不是把源码目录误当作运行工作区。

运行态文件由 `zcagent init` 生成到 workspace 下：

```text
${ZHICE_AGENT_WORKSPACE}/
+-- config/
|   +-- llm_endpoints.json
|   +-- skill_sources.yml
+-- prompts/
+-- contexts/
|   +-- sessions/
+-- extends/
+-- logs/
```

### 14.2 第一版配置原则

保持简单：

- 不做多环境 overlay。
- 不做复杂部署编排配置。
- 不做复杂 Pydantic schema。
- 不把模型 key 写进代码。
- 仓库只提交 `config/llm_endpoints.example.json`，不提交真实 `llm_endpoints.json`。
- 本地运行态 `${ZHICE_AGENT_WORKSPACE}/config/llm_endpoints.json` 统一使用 `api_key` 字段，可写直接本地值，也可写 `${ENV_VAR}` 占位。
- endpoint 支持 `priority`、`enabled`、`role`、`supported_models`，并支持 keyed object 与 `"endpoints": [...]` 两种配置形态。
- 顶层 `"default": "endpoint_name"` 或 `"default": {"ref": "endpoint_name"}` 只作为别名，不是必须存在的真实 endpoint。

---

## 15. Part 12 当前基线与后续部分设计

本章记录已完成的 Part 12 当前边界和尚未实现的 Part 13～18。Part 10 Memory、Part 11 MCP 和 Part 12 RuntimeEvent/Hook Runtime 已进入当前代码基线，不再作为未来工作重复保留；其当前实现分别见对应 Part 活文档。

```text
Part 12 生命周期事件与 Hook Runtime（已完成）
  -> Part 13 Subagent
  -> Part 14 外部渠道
  -> Part 15 生产部署与发布
  -> Part 16 Agent 运行可靠性与上下文优化
  -> Part 17 Web、会话与用户治理优化
  -> Part 18 Skill Runtime、CLI 与本地运维优化
```

### 15.1 Part 12：生命周期事件与 Hook Runtime

Part 12 已按固定 Definition of Done 同批完成前端运行状态和真实 Hook 执行边界：

- 建立 transport-neutral RuntimeEvent，覆盖 turn/context/LLM/tool 的 started/completed/failed/stopped/waiting 状态。
- 复用当前 `on_event -> WebRuntime -> WebSocket /ws`，同时对齐 SSE 和 CLI，不新建实时通道。
- 前端先用单行确定性状态展示“整理上下文、请求模型、执行工具、根据结果继续生成”。
- 不展示思维链，不制造虚假百分比，RuntimeEvent 不写入 Session。
- 显式加载 `${ZHICE_AGENT_WORKSPACE}/config/hooks.yml`，实现无 shell、最小环境、workspace path guard、timeout、输出限制和严格 JSON 校验的本地 Python Hook Runner。
- pre Hook 支持 continue/block/modify；修改参数后重新经过 Tool schema、RBAC、危险确认和具体 Tool 安全检查。
- post Hook 只补充受限业务标题、图标和 `ui_metadata`，不能篡改 ToolResult 或 Event 的成功失败事实。
- Hook 默认对所有身份生效；单 Hook 可显式配置 `exempt_roles` / `exempt_permissions`。owner 可按角色显式豁免，admin 只按实际生效权限豁免；两者均无全局自动豁免，跳过 Hook 后仍执行全部核心安全判断。
- 核心 RBAC、危险确认、workspace/用户隔离、timeout、脱敏和 SSRF 继续留在内核和具体 Tool。
- RuntimeEvent、渠道/前端状态、真实 pre/post Hook Runtime 和测试已全部完成，Part 12 已关闭。
- SkillExecutor、`skill.started/progress/completed/failed` 与 ProgressSink 归入未来 Skill Runtime / Part 18，不作为 Part 12 欠账。

当前实现依据：`docs_design/zhice-agent-part12-hooks-design.md`；设计取舍记录：`docs_design/2026-07-20-hook-runtime-boundary-design.md`。

### 15.2 Part 13：Subagent

Memory、MCP 和 Hook Runtime 已经落地，Subagent 从当前统一 AgentLoop / RuntimeEvent 基线继续实现。

关键原则：

> 子代理也复用同一个 AgentLoop，不要另写一套 Loop。

子代理本质是：

- 新开一个 session。
- 给它一个任务 prompt。
- 限制工具。
- 让它跑同一个 AgentLoop。
- 把结果摘要交回父 Agent。

### 15.3 Part 14：外部渠道

Part 14 在现有 Web/WS runtime、渠道身份映射、用户权限、session 和 turn 边界上增加真实外部渠道适配器：

- 接入一个外部 IM / 协作平台作为第一条真实渠道。
- 将渠道用户解析为内部 `user_id`，不按渠道复制权限系统。
- 复用统一 slash command、session、turn、stop 和模型偏好语义，仅按渠道能力声明裁剪表现。
- 把文本、文件、回复目标和渠道 metadata 转换成内部中性事件。
- 渠道发送失败、权限拒绝和重试写入现有 trace/audit 关联链。

渠道适配属于 app shell，不能把平台 SDK 或渠道业务分支写入 AgentLoop。

### 15.4 Part 15：生产部署与发布

Part 15 把当前本地开发运行方式收敛为可发布、可部署、可诊断的运行形态：

- Dockerfile、最小运行镜像和本地一键容器启动方式。
- `docker compose`、进程守护或云部署清单；Kubernetes 只在确有需求时增加。
- 生产环境 workspace volume、env/config mount、Secret 注入和多环境配置约定。
- HTTPS、反向代理、访问控制和公网暴露边界。
- 镜像健康检查、优雅退出、启动诊断和发布包校验。
- 明确多进程、后台任务和跨进程 active turn 的限制；需要时再引入共享状态或任务队列。
- 确认发布产物不包含本地 workspace、真实密钥、用户数据库或 session 数据。

部署层继续保持 `app -> core -> protocols`，core 不依赖容器、反向代理或平台 SDK。

### 15.5 Part 16：Agent 运行可靠性与上下文优化

Part 16 优化模型调用、工具运行、能力披露和 turn 控制，不引入领域业务 Tool：

- 为 `LLMProviderError` 增加稳定错误码、HTTP 状态、`retryable` 和安全用户提示。
- 区分鉴权失败、模型不存在、限流、网络错误、无效响应和其它 provider 错误。
- 对可重试错误增加受总超时约束的同 endpoint 有限重试、退避和 cooldown；工具执行不随模型重试而重复执行。
- trace 记录 endpoint 尝试、重试、跳过原因和最终实际模型，不记录 secret 或完整请求。
- 扩展当前自助诊断引擎，增加全系统事故聚合、LLM/Tool/Session 完整耗时分解和 Provider retry/failover 诊断。
- 继续收敛 Runtime Activity、trace 和 Security Audit 的事件边界，普通运行流水不回流安全审计账本。
- 在 actor 权限过滤后增加 Capability Selection，根据当前请求和最近相关 turn 只披露本轮需要的 Tool/Skill，不把意图判断硬编码进 AgentLoop。
- 为连续翻译、明确执行、模糊请求、权限拒绝和 Skill 相关性建立 Fake LLM 回归用例。
- CLI `/stop` 复用 active turn registry 和 cancellation token，并增加 CLI/Web 共用的 stopped/error turn 查询。
- 增强 MCP Runtime：处理 `tools/list_changed`、原子刷新 catalog、连接重建和活动调用 cancellation。
- 增强 MCP artifact 的预览、版本、保留策略和大文件流式导入。
- 评估 MCP Prompts/Resources 与 ContextBuilder、Capability Selection 的统一装配边界。

### 15.6 Part 17：Web、会话与用户治理优化

Part 17 处理当前静态 Web、会话管理和 Part 9 用户治理的产品化增强：

- 评估 Vue/Vite 或其它工程化前端，拆分聊天、会话、账号、角色和审计状态。
- 增加工具调用时间线和模型/endpoint 配置界面。
- 在现有会话列表、重命名和删除之外，增加自动标题、归档、搜索、过滤和导出。
- 增加审计筛选、分页、导出、清理归档和保留策略。
- 增加 Developer/Admin/Owner 可进入的监控与诊断平台：独立诊断聊天框、事故列表、Turn/request/tool 时间线以及用户/组件/时间范围筛选。
- 在监控与诊断平台中增加 MCP Server 健康状态、连接历史、Tool 延迟/错误统计、配置校验和安全的在线 reload。
- 增加 MCP OAuth connect/disconnect、credential 状态和 token 刷新失败处理，credential 内容始终脱敏。
- 增加 `diagnostics.system.use` 特权和系统级 `diagnose_system_activity` Tool；普通聊天中的自助诊断仍只查询当前用户当前 Session。
- 增加更完整的账号安全策略、特权模板和管理操作诊断。
- 保持现有 API、WebSocket frame、Session JSONL 和权限 key 兼容。

### 15.7 Part 18：Skill、CLI 与本地运维优化

Part 18 收敛 Skill source、初始化、本地配置和日常管理入口：

- 增加 `/skills status`、Skill 索引缓存、健康检查、source 权限过滤和来源/commit/同步时间审计。
- 增加 Skill source 状态页，并复用 Part 17 已形成的前端工程结构。
- 增加 session 归档、搜索、导出等 CLI 管理命令。
- 增加 endpoint、Skill source、prompt、workspace 权限和会话目录的完整配置体检、修复建议和初始化摘要。
- 增加 CLI local operator / Owner workspace 诊断入口和 `zcagent diagnose`，复用 Part 16 的诊断引擎。
- 增加多 profile 初始化、系统 keyring/Secret Manager 接入和不泄露明文的 Secret 状态报告。
- 如果 source root、来源、commit 和同步状态成为多个模块共享的稳定结构，再把 `SkillRoot` 提升为协议层数据结构。

---

## 16. 测试策略

### 16.1 单元测试

第一批测试：

```text
tests/unit_test/tools/test_tool_registry.py
tests/unit_test/tools/test_readonly_tools.py
tests/unit_test/tools/test_exec_tool.py
tests/unit_test/tools/test_shell_policy.py
tests/unit_test/session_store/test_session_store.py
tests/unit_test/prompt_loader/test_prompt_loader.py
tests/unit_test/llm_provider/test_openai_provider.py
tests/unit_test/llm_provider/test_failover_provider.py
tests/unit_test/agent_loop/test_agent_loop.py
tests/unit_test/agent_loop/test_agent_loop_tools.py
```

当前 LLM 错误相关单元测试至少要覆盖：

- 缺少 `api_key` 和缺少 `${ENV_VAR}` 时返回明确配置提示。
- CLI 启动时缺少或未正确填写 `llm_endpoints.json` 会阻断聊天入口，而缺少 `skill_sources.yml` 只提示 Skill 同步跳过。
- AgentLoop 遇到 provider 错误时保存 `user -> assistant(error)`，如果错误发生在工具调用之后，也保留之前的 `assistant(tool_calls)` 和 `tool` 消息。

后续 Provider 错误分类阶段再覆盖：

- HTTP 401/403 转为 `AUTH_FAILED`，并且不泄露 secret。
- HTTP 404 转为 `MODEL_NOT_FOUND`。
- HTTP 429 转为 `RATE_LIMITED`，`retryable=True`。
- 网络连接失败或超时转为 `NETWORK_ERROR`。
- Provider 返回非 JSON 转为 `INVALID_RESPONSE`。

### 16.2 Fake LLM 测试

不要一开始全靠真实 LLM。

Fake LLM 可以测试核心循环：

```text
第一次调用：返回 read_file tool_call
工具执行：返回文件内容
第二次调用：返回最终回答
```

这样可以稳定验证：

- AgentLoop 会执行工具。
- 工具结果会回填。
- 最终回答会保存。
- LLM/provider 抛错时，AgentLoop 会保存错误 assistant 消息，而不是崩溃或丢失本轮轨迹。

### 16.3 真实 LLM 冒烟测试

可选：

```bash
RUN_REAL_LLM=1 pytest tests/test_real_llm_smoke.py
```

只测最简单场景，避免测试成本太高。

### 16.4 架构边界测试

可以写一个简单脚本检查：

- `agent/protocols` 不 import `agent/tools`。
- `agent/tools` 不 import `agent/loop`。
- `skills/*/scripts` 不 import `agent.*`。

---

## 17. 实现路线图

### Milestone 0：项目骨架（已实现）

目标：

- 项目能启动。
- 配置能加载。
- CLI 能显示提示符。

交付：

```text
pyproject.toml
.env.example
agent/config.py
agent/cli.py
agent/prompt_loader.py
prompts/identity.md
```

验收：

```bash
python -m agent.cli
```

能启动。

### Milestone 1：无工具聊天（已实现）

目标：

- 用户输入一句话。
- LLM 返回回答。
- 保存 session。

交付：

```text
LLMProvider
LLMProviderError / LLMConfigurationError
OpenAIProvider
SessionStore
AgentLoop.run_turn
```

已实现边界：

- Provider 抛出 `LLMProviderError` / `LLMConfigurationError`。
- AgentLoop 保存失败轮次为 `assistant` error marker，不让 CLI 崩溃。
- 当前错误分类仍较轻量，完整错误码、retryable 元信息和同 endpoint 重试留给后续独立设计。

### Milestone 2：工具调用（已实现）

目标：

- LLM 可以调用文件工具。

交付：

```text
Tool / ToolProvider / ToolResult
BaseTool
ToolRegistry
list_dir
read_file
grep
prompts/tool_use_policy.md
Fake LLM 测试
```

阶段取舍：

- 当前实现直接输出 OpenAI-compatible tool dict，保证工具调用链先跑通。
- 暂不引入完整 hook、UI metadata、工具分类动态加载和并发批处理。
- `read_only`、`exclusive` 和中性 `ToolDefinition` 等元信息暂缓，等并发、安全策略或多 provider schema 差异真正需要时再补。

### Milestone 3：exec 工具（已实现）

目标：

- LLM 可以执行安全命令。

交付：

```text
exec tool
workspace guard
timeout
危险命令拦截
输出截断
secret 脱敏
```

### Milestone 4：模型端点与 failover（已实现）

目标：

- 支持 OpenAI-compatible 和 LiteLLM provider。
- 支持多个 endpoint 按 priority failover。
- 支持 `/model` 查看、列表、切换和 reset。

交付：

```text
LLMEndpoint(priority/enabled/role/supported_models)
load_llm_endpoints
EndpointFailoverProvider
create_llm_provider_chain
/model slash command
```

当前取舍：

- `/model` 已按当前 session 持久化到 `sessions_meta/{session_id}.json`；CLI、Web、REST、SSE 和 WebSocket 每个 turn 都使用 call-scoped provider，不修改共享 provider 的进程级偏好。
- failover 只在 endpoint 级别顺序尝试，不做同 endpoint 重试、错误分类、circuit breaker 或 cooldown。
- `supported_models` 只做轻量白名单与 glob 校验。

### Milestone 5：Skill 加载（已实现）

目标：

- LLM 可以看到 Skill 摘要。
- LLM 可以加载完整 SKILL.md。
- LLM 可以执行 Skill 脚本。

交付：

```text
SkillSourceSync
SkillLoader
load_skills tool
sync_skills tool
config/skill_sources.example.yml
prompts/skills_intro.md
```

Prompt 文件化是贯穿所有阶段的横向规范，不单独占用一个后续里程碑。底座阶段已经建立 `PromptLoader` 和基础 identity prompt；工具与 Skill 阶段继续把工具策略、Skill 引导等长文本收敛到 `prompts/*.md`。后续新增会进入 LLM messages 的长文本，也继续按这一规范维护。

### Milestone 6：Web 最小版（已实现）

目标：

- 浏览器里能聊天。
- 入口层与 Agent core 做轻量分离，保持 `app -> core -> protocols`。
- 浏览器主聊天使用 WebSocket，REST/SSE 保留兼容调用。
- Web 端支持会话查看、重命名、删除、模型选择和停止 active turn。

交付：

```text
agent/app/gateway.py
agent/app/runtime.py
agent/app/api/routes.py
agent/app/api/ws.py
agent/app/api/schemas.py
agent/core/loop.py
agent/core/context.py
FastAPI backend
web/static 静态前端
会话列表
聊天窗口
WebSocket 流式输出
REST/SSE 兼容接口
```

说明：

- 当前代码已经删除 `agent/gateway.py`、`agent/loop.py` 和 `agent/context.py` 兼容导出层，调用方直接使用 `agent.app.gateway`、`agent.core.loop` 和 `agent.core.context`。
- Web `/stop` 当前是内存态 active turn cancellation；第七部分已让 stopped marker 和 WebSocket stopped event 复用同一个持久 `turn_id`。
- CLI `/stop` 当前未实现，不在 `/help` 中展示；未来等 CLI 侧 active turn registry、cancellation token 复用和并发输入通道稳定后再做。

### Milestone 7：Turn 运行单元与上下文治理（已实现）

目标：

- 把一次用户请求定义为可持久化、可查询、可用于上下文裁剪的 turn。
- Web accepted / done / stopped、AgentLoop 消息保存和历史恢复使用同一个 `turn_id`。
- `ContextBuilder` 支持按最近 N 个 user turn 裁剪历史。

交付：

```text
Message turn 字段
JsonlSessionStore turn 读写
AgentLoop.run_turn(turn_id=...)
WebSocket turn_id 一致性
ContextBuilder recent user turns + local relevance selection
```

设计依据：`docs_design/zhice-agent-part7-turn-context-design.md`。背景和后续扩展参考：`docs_design/2026-07-04-turn-runtime-and-context-design.md`、`docs_design/2026-07-06-context-relevance-selection-design.md`。

### Milestone 8：Gateway / Agent 运行日志优化（已落地）

目标：

- 本地 gateway 运行中能看到简短、分层、脱敏的 Agent 运行痕迹。
- 系统运行主链只把 `user_id -> session_id -> turn_id` 作为核心身份；HTTP request、WebSocket connection、LLM tool call 和存储记录 id 留在各自模块。
- JSONL trace 通过 `session_id` 和 `turn_id` 串起 LLM、tool 和 session 保存轨迹。
- 终端日志带本地日期时间，使用 username/turn_index 和突出的 Tool 名称，不铺开完整内部 ID；workspace trace 按日期写入 `logs/YYYY-MM-DD/trace.log`。

交付：

```text
GatewayLogOptions
agent/app/logging.py
logs/YYYY-MM-DD/trace.log
timestamped terminal formatter
AgentLoop lifecycle logs
AgentLoop tool dispatch logs
event-specific terminal field whitelist
WebRuntime turn logs
secret redaction and preview truncation
```

设计依据：`docs_design/zhice-agent-part8-gateway-agent-logging-design.md`。背景记录：`docs_design/2026-07-02-gateway-runtime-logging-design.md`。

### Milestone 9：用户、登录与权限执行边界设计和实现（已实现）

状态：已实现并进入当前代码基线。

目标：

- 明确登录用户基础能力、少数特权、登录态、ownership、用户上下文目录、session_index、turn、tool call 和 audit log 的关系。
- 让 `exec` 等危险工具从“一刀切拦截”演进为“权限 + 明确用户确认 + 审计”。
- 落地简单本地用户系统、权限管理界面和工具执行管控。

交付：

```text
当前活文档：docs_design/zhice-agent-part9-user-auth-permission-design.md
日期设计记录：docs_design/2026-07-08-user-auth-permission-boundary-design.md
基础能力收敛记录：docs_design/2026-07-16-authenticated-user-baseline-capabilities-design.md
模型偏好补充记录：docs_design/2026-07-10-session-model-preference-scope-design.md
SQLite schema 与 state/auth.sqlite3 auth store
登录用户基础能力、特权 key、角色和不可变 Owner 边界
唯一 Owner 初始化、普通注册和管理员管理权委派
登录、登出、个人设置和 `/admin` 用户/角色/权限管理页
用户上下文目录与 session_index
session 模型偏好与 turn-local LLM 选择
安全工具基础能力、危险命令特权/确认与审计
自动定位当前 Session 上一轮/最近失败的 `diagnose_my_recent_activity` 自助诊断
独立 Runtime Activity 索引与 Security Audit 账本
```

### Milestone 12 当前基线与 13～18 后续部分

- Part 12 生命周期事件与 Hook Runtime：已完成 RuntimeEvent、现有 WS/SSE/CLI、前端运行状态和真实受限 pre/post Tool Hook Runtime，DoD 已满足并关闭。
- Part 13 Subagent：复用同一个 AgentLoop 创建受限子任务 Agent。
- Part 14 外部渠道：接入真实 IM / 协作平台适配器。
- Part 15 生产部署与发布：容器、反向代理、Secret 注入、健康检查和发布产物。
- Part 16 Agent 运行可靠性与上下文优化。
- Part 17 Web、会话与用户治理优化。
- Part 18 Skill Runtime、CLI 与本地运维优化，独立承接 SkillExecutor、`skill.*` 与 ProgressSink。

---

## 18. 应该坚持的设计原则

建议长期坚持：

- Agent Loop 的基本循环思想。
- ToolRegistry 设计。
- Skill 的 `SKILL.md + scripts` 设计。
- Prompt 文件化规范。
- Protocol/provider 边界。
- Session 作为上下文的一部分。
- 工具执行前后的安全意识。
- 子代理复用同一个 AgentLoop 的原则。

---

## 19. 不建议第一阶段纳入什么

不要一开始纳入：

- 完整平台级 AgentLoop 实现。
- 平台级应用 API 层、复杂鉴权和多租户；简单本地用户权限系统已排入 Milestone 9，但不等于一开始就做完整平台。
- 多渠道接入层。
- 工程化多页面 Web 前端。
- 外部协作平台渠道。
- 审批和通知。
- Skill 市场。
- 自动演化系统。
- 图谱化长期记忆。
- 复杂部署编排。
- 多服务运行套件。

这些能力不是不需要，而是不适合作为 ZhiCe-Agent 第一阶段。

---

## 20. MVP 验收标准

第一阶段已实现 MVP 的验收口径：

1. CLI 能启动。
2. 用户能输入消息。
3. LLM 能返回普通回答。
4. 会话能保存到 JSONL。
5. LLM 能调用 `list_dir`。
6. LLM 能调用 `read_file`。
7. LLM 能调用 `grep`。
8. LLM 能安全调用 `exec`。
9. 明显危险命令会被拒绝。
10. 工具调用有最大轮数限制。
11. `zcagent init` 能生成本地运行态文件。
12. `zcagent gateway --check` 能验证本地 gateway 配置并快速退出。
13. `${ZHICE_AGENT_WORKSPACE}/config/llm_endpoints.json` 能配置多个 endpoint，并按 priority failover。
14. `/model` 能按当前 session 查看、列出、切换和 reset endpoint/model 偏好。
15. `zcagent gateway` 能启动本地 FastAPI/Web 服务，默认地址为 `http://127.0.0.1:10086/`。
16. Web 前端能通过 `/ws` 发送消息、展示 pending/streaming 状态和 assistant Markdown。
17. Web 端能读取、重命名、删除 session；删除当前 session 后进入空界面。
18. Web 模型选择只显示当前 endpoint 下的模型名，不暴露 endpoint/base_url/api_key。
19. Fake LLM 测试通过。
20. 默认测试不访问真实 LLM 或网络。

Part 9 已实现扩展项：

1. `state/auth.sqlite3`、唯一 Owner 初始化、登录/登出、普通注册、用户上下文、session_index、渠道身份映射和 Owner CLI session 索引对账。
2. session 级模型偏好、turn-local provider、登录用户安全工具基础能力、危险确认、当前 Session 自助诊断，以及拆分后的 Runtime Activity / Security Audit。

Part 10 已实现扩展项：

1. CLI/Owner workspace Memory 与普通用户私有 Memory、极简 Markdown 内容、原子写入和本地相关性检索。
2. `memory_read` / `memory_write` 本人基础能力、对话式用户授权、模型自然语言询问、安全过滤、`/memory` 展示和隐私化 trace/audit。

Part 11 MCP 已实现并进入当前基线：支持 stdio、Streamable HTTP、SSE、自动 Tool 发现、共享 Runtime、OAuth refresh、ArtifactGateway、Elicitation 和 `/mcp`；Windows OS 级 stdio 读取隔离仍是后续安全硬化项。Part 12 已按 `docs_design/zhice-agent-part12-hooks-design.md` 完成 RuntimeEvent、渠道/前端状态和真实 pre/post Tool Hook Runtime。

---

## 21. 学习路线

### 第 1 课：Message 和 Session

学习：

- system/user/assistant/tool 四种消息。
- JSONL 会话存储。
- 历史消息如何回到上下文。

实现：

- `Message`
- `SessionStore`

### 第 2 课：LLMProvider

学习：

- chat completion。
- function calling。
- 不同模型返回格式如何归一化。

实现：

- `LLMProvider`
- `OpenAIProvider`

### 第 3 课：ToolRegistry

学习：

- 工具 schema。
- 参数 JSON。
- tool result message。

实现：

- `BaseTool`
- `ToolRegistry`
- `read_file`

### 第 4 课：AgentLoop

学习：

- LLM 推理循环。
- 工具调用。
- 工具结果回填。
- 最大迭代次数。

实现：

- `AgentLoop.run_turn`

### 第 5 课：Skill

学习：

- 能力说明书。
- Skill 发现。
- Skill 正文加载。
- 脚本执行。
- 结构化结果。

实现：

- `SkillLoader`
- `load_skills`
- `example_calculator`

### 第 6 课：安全

学习：

- workspace 边界。
- shell 命令风险。
- 输出截断。
- hook 思路。

实现：

- `exec` guard。
- 可选 hook runner。

### 第 7 课：Web

学习：

- chat API。
- REST/SSE 兼容接口和 WebSocket 主通道。
- 会话列表。

实现：

- 最小 FastAPI。
- 简单前端。
- WebSocket `/ws`。
- Web active turn cancellation。

### 第 8 课：Turn

状态：已实现，当前代码已将 turn 作为运行、持久化和上下文选择的基线。

学习：

- 一次用户请求如何成为可持久化的 turn。
- Web stop、历史恢复、上下文裁剪和日志如何共享同一个 `turn_id`。
- 没有显式 `turn_id` 的历史如何保持可读，但不参与 turn-based context selection。

开发文档：`docs_design/zhice-agent-part7-turn-context-design.md`。

实现：

- `Message` turn 字段。
- `JsonlSessionStore` turn 读写。
- `AgentLoop.run_turn(turn_id=...)`。
- recent user turns 上下文裁剪。

### 第 9 课：可观测性

学习：

- Gateway / Agent 日志如何分层。
- 日志如何截断和脱敏。
- 终端日志为什么必须带日期时间。
- workspace `trace.log` 如何按日期落盘并用于回放。
- LLM、tool、session 保存和 stop/error 如何通过 `session_id` / `turn_id` 串起来。

开发文档：`docs_design/zhice-agent-part8-gateway-agent-logging-design.md`。

实现：

- 运行日志优化。
- 日志参数分层。
- timestamped terminal logs。
- date-based workspace `trace.log`。
- AgentLoop lifecycle logs 和 AgentLoop tool dispatch logs。

### 第 10 课：用户权限

学习：

- 用户、session、turn、tool call 和 audit log 的关系。
- 危险工具如何从一刀切拦截演进到权限、确认和审计。

实现：

- 用户权限系统设计：`docs_design/zhice-agent-part9-user-auth-permission-design.md`。
- 简单登录和权限管理 UI。

---

## 22. 最终建议

我们要做的不是“复制一个缩小版大平台”，而是做一个真正能学懂、能掌控、能逐步变强的智策 Agent（ZhiCe-Agent）项目。

第一版完整目标应该非常明确（基础能力已实现，平台能力继续演进）：

```text
一个本地智策 Agent：
能聊天，
能读文件，
能搜索，
能执行安全命令，
能通过 OpenAI-compatible 或 LiteLLM Provider 调用模型，
能按 endpoint priority 做轻量 failover，
能用 /model 查看和切换首选模型，
能加载 Skill，
能按 `SKILL.md` 通过 `exec` 运行 Skill 脚本，
能保存会话，
能启动本地 Web UI，
能通过 WebSocket 流式展示助手输出，
能在 Web 端管理会话和切换当前模型，
能用 turn 串起 WebSocket、AgentLoop、Session 历史和上下文选择。
```

当前代码已经完成 Part 10 Memory、Part 11 MCP 和 Part 12 生命周期事件/Hook Runtime。后续按剩余核心 Part 的依赖顺序逐步增加：

```text
Part 12 生命周期事件与 Hook Runtime（已完成并关闭）
Part 13 Subagent
Part 14 外部渠道
Part 15 生产部署与发布
Part 16 Agent 运行可靠性与上下文优化
Part 17 Web、会话与用户治理优化
Part 18 Skill Runtime、CLI 与本地运维优化
```

新功能完成后再进入优化阶段，避免优化中的协议调整反复打断 Hooks、Subagent 和渠道接入主线。

这样做的好处是：

- 每一步都能跑。
- 每一步都能学到一个核心概念。
- 不会被平台化复杂度拖住。
- 最后得到的是一个真正可掌控的智策 Agent（ZhiCe-Agent）项目。
