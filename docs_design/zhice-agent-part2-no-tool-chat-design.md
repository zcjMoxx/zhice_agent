# ZhiCe-Agent 第二部分详细设计文档：无工具聊天链路

> 关联总设：`docs_design/zhice-agent-overall-design.md`
>
> 文档类型：阶段活文档。本文档始终按当前代码和当前阶段口径维护。
>
> 承接文档：`docs_design/zhice-agent-part1-foundation-design.md`
>
> 开发范围：Milestone 1 无工具聊天

---

## 1. 背景

第一部分已经完成项目底座，具备这些基础能力：

- 运行路径与目录配置
- Prompt 文件加载
- 通用消息模型
- JSONL Session 持久化
- 最小 CLI 入口

第二部分的目标是在不引入工具调用、不引入 Skill 执行的前提下，打通一条完整的本地对话链路：

1. CLI 接收用户输入
2. 读取当前 Session 历史
3. 构建发给 LLM 的 messages
4. 调用 LLM Provider
5. 返回 assistant 回复
6. 将 `user` 与 `assistant` 消息写回 Session

这一部分是 ZhiCe-Agent 从“可运行项目骨架”升级为“可本地对话 Agent”的关键阶段。

---

## 2. 目标

本部分完成后，应满足：

1. `zcagent` 可以基于指定 session 进行本地对话。
2. `ContextBuilder` 能稳定拼装系统 Prompt、运行时限制说明、历史消息和当前用户输入。
3. `AgentLoop.run_turn()` 能通过 `LLMProvider` 获取 assistant 回复。
4. LLM 调用必须经过协议接口，`AgentLoop` 不直接依赖具体 SDK。
5. 成功路径下，`user` 与 `assistant` 消息都会写入 `SessionStore`。
6. 失败路径下，也会写入带错误标记的 assistant 消息，避免本轮上下文丢失。
7. 单元测试可用 Fake LLM 覆盖主链路，不依赖真实网络。
8. 本地工作目录中的 LLM 配置和 Prompt 初始化可通过 `zcagent init` 完成。

---

## 3. 范围边界

### 3.1 本部分包含

- `LLMProvider` 协议与响应结构
- `LLMEndpoint` 配置结构
- OpenAI 兼容 Provider 实现
- `ContextBuilder`
- `AgentLoop.run_turn()`
- CLI 对话主链路
- 工作目录中的 endpoint 配置读取
- `zcagent init` 生成本地运行时文件
- Fake LLM / mock HTTP 单元测试

### 3.2 本部分不包含

- ToolRegistry 与工具调度
- Skill 脚本执行
- `exec` 能力
- 文件写入类工具
- 多轮工具编排
- Web UI / WebSocket
- 长期记忆、MCP、Subagent
- 多用户、多渠道隔离

---

## 4. 模块设计

### 4.1 `agent/protocols/llm.py`

负责定义统一的 LLM 配置、返回结构和调用协议。

当前结构：

```python
from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass(frozen=True)
class LLMEndpoint:
    name: str
    protocol: str
    base_url: str
    model: str
    api_key: str
    context_window: int = 131072
    max_tokens: int = 4096
    temperature: float = 0.7


@dataclass
class LLMResponse:
    content: str
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


class LLMProvider(Protocol):
    def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
    ) -> LLMResponse:
        ...
```

设计要求：

- `LLMEndpoint` 只表达运行所需的最小配置，不混入 CLI、Session 等上下文；`context_window` 是带默认值的总窗口，`max_tokens` 是单次最大输出 token。
- endpoint 有效输入上限固定为 `context_window - max_tokens`；Failover 链取所有 enabled 候选的最小值形成 `ContextBudget.input_token_limit`。
- `LLMResponse` 保留 `tool_calls` 字段，为第三部分工具调用预留兼容面。
- `metadata` 用于容纳 `model`、`finish_reason`、`usage` 等扩展信息。
- `LLMProvider` 保持同步接口，先把链路打通；异步化不是本阶段目标。
- 错误统一使用 `LLMProviderError` / `LLMConfigurationError`，避免上层耦合底层实现细节。

### 4.2 `agent/llm/openai_provider.py`

负责把协议层消息转换为 OpenAI 兼容的 Chat Completions 请求。

职责：

- 校验 endpoint 中的 `api_key`
- 将上层 `messages` 清洗为兼容格式
- 发送 HTTP 请求
- 解析响应为 `LLMResponse`
- 将 HTTP/网络/JSON 解析错误包装为统一错误

实现约束：

- 只负责协议适配与传输，不负责 Prompt 拼装、Session 保存、CLI 输出。
- 使用标准库 `urllib.request`，避免在第二部分引入更重依赖。
- 对错误返回进行脱敏，不能把真实 `api_key` 回显给用户。
- `OpenAIProvider` 保留为直连 OpenAI-compatible endpoint 的默认实现。

消息清洗规则：

- 只保留 `role`、`content`、`tool_calls`、`tool_call_id`、`name`
- assistant 在存在 `tool_calls` 且 `content` 为空时，传 `None`
- 其他空内容统一转成 `"(empty)"`，避免上游接口校验失败

### 4.2.1 `agent/llm/litellm_provider.py`

负责通过进程内 LiteLLM SDK 接入 Anthropic、Gemini、DeepSeek 等非 OpenAI 模型商。

当前实现方式：

- 引入 `litellm` Python 包。
- 不在 ZhiCe-Agent 进程里直接引入各家模型 SDK。
- 通过 `litellm.completion(...)` 调用模型。
- 复用 OpenAI-compatible 的消息清洗和 tools schema，响应解析和错误脱敏在 `LiteLLMProvider` 内完成。

因此 `protocol="litellm"` 不要求用户额外启动 LiteLLM Proxy。`base_url` 是可选字段：不填时由 LiteLLM SDK 根据模型前缀选择默认模型商接口；填写时作为 `api_base` 传给 LiteLLM SDK，用于公司内部网关或自定义 OpenAI-compatible 服务。

配置示例：

```json
{
  "claude": {
    "protocol": "litellm",
    "provider": "anthropic",
    "api_key": "${ANTHROPIC_API_KEY}",
    "model": "claude-sonnet-4",
    "context_window": 200000,
    "max_tokens": 4096,
    "temperature": 0.7
  }
}
```

启动时通过 `zcagent --endpoint claude` 选择该 endpoint。配置层保留 `provider` 和未加前缀的 `model`，由 `LiteLLMProvider` 调用 SDK 时拼接为 LiteLLM 可识别的 `anthropic/claude-sonnet-4`。

### 4.3 `agent/core/context.py`

负责将 Prompt、运行时限制说明、Session 历史和当前用户输入组装为发给 LLM 的 `messages`。

当前依赖的 Prompt：

- `prompts/identity.md`
- `prompts/tool_use_policy.md`
- `prompts/skills_intro.md`

可选加载的专项 Prompt：

- `prompts/memory_policy.md`
- `prompts/diagnostics.md`
- `prompts/exec.md`

专项 Prompt 缺失不阻断主聊天；诊断工具的 Trace 读取与归因规则只放在 `diagnostics.md`，Exec 的命令、风险与结果处理规则只放在 `exec.md`，均不混入通用 `tool_use_policy.md`。安全边界仍由 Tool 运行时代码执行。

系统 Prompt 内容由以下几层拼接而成：

1. 身份说明
2. 工具使用规则
3. Skill 使用规则
4. 可选 Memory/Diagnostics/Exec 专项规则
5. 当前阶段限制说明
6. 运行时元信息：
   - `workspace=...`
   - `session_id=...`

当前设计要求：

- Session 历史按 Turn 处理：最近 50 个 user Turn 为候选，最近 3 个直接保留，更早历史最多选择 3 个相关 Turn。
- CLI 与 Web 统一保留 `max_history_messages=60` 的消息数量兜底。
- CLI 展示层使用共享 Markdown-to-plain renderer 打印最终回答和历史；Session 仍保存原始 Markdown，Web 等支持 Markdown 的客户端不受影响。
- `ContextBuilder.build(..., context_budget=...)` 根据 endpoint 输入预算裁剪最旧历史；无工具聊天虽然没有 Tool schema，仍服从同一 token budget。
- 超长消息按 `max_message_chars` 截断，并在尾部追加 `[truncated]`。
- 第三部分落地后，历史中的合法 assistant tool call / tool result 块也会进入 ContextBuilder；本文的无工具链路不单独维护另一套上下文实现。
- 当前用户消息必须是 `role="user"`，否则抛出明确错误。
- Prompt 缺失时不吞错，直接向上抛出，交给 CLI 在启动阶段提前失败。

### 4.4 `agent/core/loop.py`

负责无工具单轮对话主循环。

核心流程：

1. 从 `SessionStore` 读取历史
2. 构造本轮 `user_msg`
3. 调用 `ContextBuilder.build(..., context_budget=...)`
4. 在调用前按本次 `ContextBudget` 再次 fit messages，然后调用 `LLMProvider.chat()`
5. 生成 `assistant_msg`
6. 追加 `[user_msg, assistant_msg]` 到 Session
7. 返回 assistant 文本

错误路径设计：

- 若 LLM 抛错，不让异常直接炸出 CLI
- 将错误格式化为用户可操作的提示文本
- 仍然写入一条 `assistant` 错误消息，并加上：
  - `metadata["is_error"] = True`
  - `metadata["error_type"] = ...`
- 如果 Session 保存失败，再将保存失败附加到最终返回文本中

本阶段重点约束：

- `AgentLoop` 只能依赖 `LLMProvider`、`SessionStore`、`ContextBuilder`
- 不直接出现 OpenAI SDK 或 HTTP 请求代码
- 不在这里实现工具调度分支

### 4.5 `agent/config.py`

第二部分在第一部分配置底座上补充了与 LLM 相关的运行时能力。

新增职责：

- 从 `${ZHICE_AGENT_WORKSPACE}/config/models.json` 读取 endpoint 与用途路由
- 解析 `api_key`
- 初始化本地运行时文件
- 在 workspace 确定后加载 `${workspace}/config/.env`

关键点：

- workspace 解析优先级固定为 `CLI --workspace > 进程 ZHICE_AGENT_WORKSPACE > Path.home() / ".zhice"`
- 默认 workspace 在 Windows 是 `C:\Users\<user>\.zhice`，在 Docker 是 `/home/zhice/.zhice`
- `${workspace}/config/.env` 只提供运行变量，不得反向定义 `ZHICE_AGENT_WORKSPACE`
- 显式 `--env-file` 可以兼容提供 workspace；项目 `config/.env` 仅作为遗留迁移 fallback
- 真实 endpoint 配置放在工作目录，不放在仓库中
- `zcagent init` 会在工作目录生成：
  - `${ZHICE_AGENT_WORKSPACE}/config/models.json`
  - `${ZHICE_AGENT_WORKSPACE}/config/config.yml` 的 `skills` 分区
  - `${ZHICE_AGENT_WORKSPACE}/prompts/*.md`
  - `${ZHICE_AGENT_WORKSPACE}/config/.env`
- `zcagent init` 可重复执行：已存在的本地文件默认保留，缺失文件会自动补齐；只有显式传 `--force` 才覆盖已有文件。

### 4.6 `agent/cli.py`

CLI 是第二部分对话链路的真实入口。

启动职责：

- 先处理全局 `--env-file` 和 `--workspace`
- 按 `--workspace > 进程 ZHICE_AGENT_WORKSPACE > 默认目录` 确定 workspace
- 普通路径加载 `${workspace}/config/.env`；仅迁移场景 fallback 到项目 `config/.env`
- 解析 `init`、`gateway`、默认 chat 子路径
- 组装 `PromptLoader`、`JsonlSessionStore`、`ContextBuilder`、`LLMProvider`、`AgentLoop`

chat 路径职责：

- 解析 `--session`、`--workspace`、`--endpoint`
- 未显式传 `--session` 时，默认进入当天本地 session：`chat-YYYYMMDD`
- 启动时只打印项目名，workspace 和 session id 保持在 gateway/check 等诊断输出中，避免普通对话窗口噪声过多。

第二部分相关命令：

- `/history`：查看当前 session 最近消息
- `/prompts`：查看当前已加载的 prompt 文件
- `/exit`：退出

说明：

- `/new`、`/clear`、`/sessions` 是后续在 CLI session 管理中补充的能力，不属于第二部分最初边界，但当前实现已经兼容。
- `gateway` 子命令在代码中已存在入口脚手架，但完整 Web 能力不属于本阶段验收内容。

---

## 5. 配置与运行时文件

### 5.1 workspace 与 env 解析

默认 workspace 是 `Path.home() / ".zhice"`，因此普通用户无需先设置环境变量即可运行 `zcagent init`。Windows 默认路径为 `C:\Users\<user>\.zhice`，Docker 默认路径为 `/home/zhice/.zhice`。

显式选择 workspace 时，优先级是 CLI `--workspace`、进程 `ZHICE_AGENT_WORKSPACE`、默认目录。普通运行态 env 位于 `${workspace}/config/.env`；它不能定义 workspace。只有显式 `--env-file` 可以在没有 `--workspace` 时兼容提供 `ZHICE_AGENT_WORKSPACE`。项目 `config/.env` 不再是主入口，只保留遗留迁移 fallback。

### 5.2 工作目录配置

`${ZHICE_AGENT_WORKSPACE}/config/models.json` 描述真实调用的 chat/embedding endpoint 与用途路由。

示例：

```json
{
  "schema_version": 1,
  "routing": {"chat": "请填写端点名称/请填写模型名称"},
  "chat": {
    "请填写端点名称": {
      "protocol": "openai",
      "provider": "",
      "base_url": "请填写模型服务地址",
      "api_key": "${ZHICE_LLM_OPENAI_API_KEY}",
      "model": "请填写模型名称",
      "supported_models": ["请填写模型名称"],
      "context_window": 131072,
      "max_tokens": 4096,
      "temperature": 0.7,
      "role": "default"
    }
  }
}
```

`zcagent init` 原样复制 `config/models.example.json`，不在代码中生成第二份模型配置。`api_key` 可以直接写在本地工作区，也可以像示例一样使用环境变量占位符；仓库模板只使用固定环境变量名，不包含真实 key。

`context_window` 缺失时默认 `131072`；`max_tokens` 缺失时沿用当前默认输出上限，并且只表示单次最大输出 token。输入预算固定按二者差值计算，不再提供第三个输入预算配置字段。

对应环境变量优先来自当前 shell / 系统环境，其次来自 `${workspace}/config/.env`；显式 `--env-file` 和项目 `config/.env` fallback 只承担兼容场景。

### 5.3 `zcagent init`

第一次初始化时，用户执行：

```bash
zcagent init
```

默认行为：

- 未显式指定时在 `Path.home() / ".zhice"` 初始化
- 默认从公开 `config/.env.example` 生成 `${ZHICE_AGENT_WORKSPACE}/config/.env`
- 在 `${ZHICE_AGENT_WORKSPACE}/config/models.json` 创建 endpoint 与路由配置
- 在 `${ZHICE_AGENT_WORKSPACE}/config/config.yml` 的 `skills` 分区创建 Skill source 配置
- 复制默认 prompts
- 默认保留已有用户文件，只补齐缺失文件

完成提示按当前运行边界区分：聊天前必须校验至少一个 enabled LLM endpoint 的真实 service address/provider、model 和 api_key；`context_window` 与 `max_tokens` 已有默认值，只需按模型限制校准；Skill source、MCP、Subagent、Hook 等扩展能力只在启用时配置，未配置不属于错误。

可选行为：

- `--force`：覆盖已存在本地文件
- `--workspace`：显式选择初始化目录，优先于进程环境和默认目录
- `--write-env`：保留为兼容参数；普通 init 已默认生成 env，因此该参数不再改变文件结果

---

## 6. 数据流

### 6.1 正常对话流程

```mermaid
flowchart TD
    A["用户输入"] --> B["CLI 接收输入"]
    B --> C["SessionStore.load(session_id)"]
    C --> D["ContextBuilder.build(..., ContextBudget)"]
    D --> E["fit messages within endpoint input budget"]
    E --> F["LLMProvider.chat(...)"]
    F --> G["AgentLoop 生成 assistant 消息"]
    G --> H["SessionStore.append(user, assistant)"]
    H --> I["CLI 打印回复"]
```

### 6.2 错误对话流程

```mermaid
flowchart TD
    A["LLMProvider 抛错"] --> B["AgentLoop 格式化错误"]
    B --> C["构造 assistant 错误消息"]
    C --> D["SessionStore.append(user, assistant_error)"]
    D --> E["CLI 输出可操作错误提示"]
```

### 6.3 时序图

```mermaid
sequenceDiagram
    participant User as 用户
    participant CLI as CLI
    participant Agent as AgentLoop
    participant Store as SessionStore
    participant Builder as ContextBuilder
    participant LLM as LLMProvider

    User->>CLI: 输入文本
    CLI->>Agent: 调用 run_turn
    Agent->>Store: 读取 session 历史
    Store-->>Agent: history
    Agent->>Builder: 构建上下文 messages
    Builder-->>Agent: messages
    Agent->>LLM: 调用 chat
    LLM-->>Agent: 返回 LLMResponse
    Agent->>Store: 追加 user 与 assistant
    Agent-->>CLI: assistant_text
    CLI-->>User: 打印回复
```

---

## 7. 变更文件

第二部分实际涉及：

- `agent/protocols/llm.py`
- `agent/llm/__init__.py`
- `agent/llm/openai_provider.py`
- `agent/core/context.py`
- `agent/core/loop.py`
- `agent/config.py`
- `agent/cli.py`
- `tests/unit_test/context_builder/test_context_builder.py`
- `tests/unit_test/agent_loop/test_agent_loop.py`
- `tests/unit_test/llm_provider/test_openai_provider.py`
- `tests/unit_test/cli/test_cli_init.py`

说明：

- `agent/session/jsonl_store.py`、`agent/prompt_loader.py` 虽然主体来自第一部分，但第二部分依赖它们作为正式运行链路的一部分。
- 后续 session 命令扩展另有独立设计文档：`docs_design/2026-06-12-cli-session-commands-design.md`

---

## 8. 测试方案

### 8.1 `ContextBuilder`

验证：

- 能拼出 system prompt 与当前用户消息
- 预算允许时保留完整 Session 历史；超长历史使用 Part 15 的结构化 compaction、recent raw Turn 与混合召回
- CLI/Web/外部渠道统一服从 endpoint ContextBudget
- 能截断超长历史消息
- 合法 Tool 块进入统一 ContextBuilder 后仍保持 call/result 完整
- 缺少必需 Prompt 时能抛出清晰错误

### 8.2 `AgentLoop`

验证：

- 成功路径下返回 assistant 文本
- 成功路径下会写入 `user` 与 `assistant`
- 历史消息会原样传给 `ContextBuilder`
- LLM 抛错时会记录错误 assistant 消息
- 缺少 API key 时能给出可操作提示
- 缺少环境变量占位符时能明确指出变量名
- Provider 请求失败时能指向 `models.json`
- Session 保存失败时不会覆盖原本的 LLM 返回

### 8.3 `OpenAIProvider` / `LiteLLMProvider`

验证：

- 请求体能正确映射到 `chat/completions`
- `tool_calls` 与空内容处理符合兼容要求
- 可从本地工作目录 JSON 提供 API key
- HTTP 错误不会泄漏 secret
- `protocol="openai"` 创建 `OpenAIProvider`
- `protocol="litellm"` 创建 `LiteLLMProvider`
- `LiteLLMProvider` 会调用 `litellm.completion(...)`

### 8.4 CLI

验证：

- `zcagent init` 能生成运行时文件
- `zcagent gateway --check` 可用
- 未设置 workspace 时使用 `Path.home() / ".zhice"`
- `--workspace`、进程 `ZHICE_AGENT_WORKSPACE` 和默认目录严格按优先级解析
- 普通 `zcagent init` 默认生成 `config/.env`；已有文件保留，`--force` 覆盖，`--write-env` 与普通 init 结果一致
- `${workspace}/config/.env` 不能反向改变 workspace，显式 `--env-file` 可以兼容提供 workspace
- 缺少启动 prompts 时能引导用户执行 `zcagent init`
- 缺少或未正确填写 `${ZHICE_AGENT_WORKSPACE}/config/models.json` 时，`zcagent` 聊天入口直接失败并提示配置；因为 LLM 是聊天运行必需能力
- `${ZHICE_AGENT_WORKSPACE}/config/config.yml` 缺少 `skills` 分区时静默跳过 Skill 同步并视为 disabled；只有显式配置后非法或同步失败才记录 warning

提交前建议运行：

```bash
python -m ruff check .
python -m pytest
```

---

## 9. 验收标准

1. `zcagent` 能完成一次真实的无工具本地对话调用。
2. `AgentLoop` 主链路只依赖协议与基础组件，不直接耦合具体 Provider 实现细节。
3. 成功与失败路径都能稳定写入 Session。
4. `ContextBuilder` 能稳定装配 Prompt、混合 Turn 历史和运行时信息，并在无工具调用中同样服从 endpoint ContextBudget。
5. LLM endpoint 配置完全来自工作目录，不要求把真实 key 放进仓库。
6. 第二部分为第三部分工具调用预留兼容接口，但不提前引入额外复杂度。

---

## 10. 后续衔接

第二部分完成后，第三部分可以在此基础上继续扩展：

- 将 `LLMResponse.tool_calls` 真正接入 AgentLoop
- 引入 `ToolProvider` / `ToolRegistry`
- 将 `tool` 消息纳入 ContextBuilder
- 在 Session 中保存完整的 `user -> assistant(tool_calls) -> tool -> assistant` 轨迹

也就是说，第二部分的目标不是“一次性做成全功能 Agent”，而是把最关键的无工具聊天骨架先立稳。
