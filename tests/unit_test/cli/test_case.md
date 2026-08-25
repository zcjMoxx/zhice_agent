# CLI 单元测试用例

## 第 18 部分：本地 Ops 启动覆盖

- 普通 `zcagent gateway` 默认进入 `LocalOpsSupervisor`，child argv 固定复用 workspace、Gateway host/port 与日志参数，并携带内部 `--ops-child` 防止递归启动。
- `--check` 不启动 Ops；容器启动器注入 `ZHICE_OPS_MODE=local_docker|server_docker` 时跳过本地进程 supervisor，由外部 sidecar/systemd Ops 接管。

## 测试目标

验证 `zcagent` 命令行入口只负责参数解析、运行时依赖初始化、用户输入循环和输出展示，不直接实现 AgentLoop、LLM 或工具业务逻辑。

Part 9 还验证 `zcagent auth init-owner` 只通过安全输入读取一次密码、初始化唯一 Owner，并拒绝重复 bootstrap；旧 `init-admin` 和 session 导入命令均不再接受。

## 用例覆盖

### 用例 1: `zcagent init`

- 输入：显式 workspace、endpoint、base_url、api_key、model 等参数。
- 预期：在 workspace 下生成运行时配置和 prompts。
- 检查点：默认生成`config/.env`、`models.json`、`config.yml`和Prompts；已有文件默认保留，`--force`覆盖；`--write-env`作为deprecated兼容no-op继续可用；显式env file可提供workspace；不会生成旧分散配置文件。

### 用例 2: 默认 workspace

- 输入：没有 `ZHICE_AGENT_WORKSPACE`，也没有有效 `--workspace`。
- 预期：使用 `Path.home() / ".zhice"`，`zcagent init`直接初始化该目录。
- 检查点：显式`--workspace`仍优先，并在主配置解析前加载该workspace的`config/.env`。

### 用例 3: `zcagent gateway --check`

- 输入：配置好的 workspace 和端口。
- 预期：只做本地 gateway 配置检查，不启动服务。
- 检查点：输出 host、port、workspace 和健康检查地址；MCP 单 Server 非法导致 `MCP_CONFIG_PARTIAL` 时返回失败但不泄露原路径；公安备案配置非法时返回失败且不回显 private 文案。

### 用例 4: chat 启动 prompt 检查

- 输入：workspace 缺少运行时 prompts。
- 预期：CLI 友好提示运行 `zcagent init`，不抛 traceback。
- 检查点：错误信息包含缺失 prompt 名称。

### 用例 4.1: chat 启动 LLM 配置检查

- 输入：workspace缺少`${ZHICE_AGENT_WORKSPACE}/config/models.json`，或endpoint缺少必需字段如`api_key`。
- 预期：缺少文件时提示运行 `zcagent init`；已有文件非法时优先提示编辑现有文件，仅把 `zcagent init --force` 作为明确覆盖模板的选项。
- 检查点：这类必需配置缺失会阻断聊天启动；Skill source 缺失表示可选扩展未启用，静默使用空 SkillLoader。
- Skill source 文件已经存在但非法时只打印一次 `Skill capability unavailable`，不再同时显示“sync skipped”和“disabled”；启动同步失败但配置仍可加载时显示 degraded 并提示 `/skills sync --verbose`。

### 用例 5: 默认和显式 session

- 输入：默认启动或传入 `--session named-session`。
- 预期：默认使用当天 `chat-YYYYMMDD` session，显式参数使用指定 session。
- 检查点：普通启动输出不显示 workspace/session，聊天轮次写入当前 session 文件。

### 用例 6: 会话 slash commands

- 输入：`/help`、`/new`、`/clear`、`/sessions`、`/history`、`/prompts`、`/model`、`/memory`、`/exit` 等命令；旧 `/reset` 只提示改用 `/clear`，不进入 Agent。
- 预期：命令在 CLI 层处理，不进入 AgentLoop 普通对话路径。
- 检查点：新 session 可写入文件；reset 后 history 为空；sessions 能显示 preview；`/help` 只列顶层命令，`/skills sync` 放在 `/skills` 的 tip 中；`/model` 能紧凑显示当前模型，`/model list` 能列出 endpoint/model，`/model list endpoint` 能列出单个 endpoint 的 supported_models，且能切换、临时覆盖模型并重置当前首选 endpoint；`/subagent` 只在裸命令 Tip 中提示 `auto/off/once`，状态按 Session sidecar 隔离。
- Subagent 显式配置非法或必需 Prompt 缺失时，CLI 主聊天仍可启动，并输出包含真实 message/hint 的人类可读说明，不直接打印结构化 JSON；one-shot 只消费一次。

### 用例 7: `/tools`

- 输入：`/tools`。
- 预期：列出当前默认工具注册表。
- 检查点：输出包含 `list_dir`、`read_file`、`grep`、`exec`、`memory_read` 和 `memory_write`。

### 用例 7.1: `/memory`

- 输入：`/memory`，以及已经删除的 `/memory session`、list、extract、summarize 子命令。
- 预期：裸 `/memory` 展示当前 actor 的长期 Memory；任何附加参数只返回 `Usage: /memory`，不恢复已删除的手动提取或 Session Summary。
- 检查点：命令不进入普通聊天，也不调用提取 Provider；自动提取只由 Web Session 空闲调度器触发。

### 用例 8: Fake LLM 对话

- 输入：普通用户消息，测试中替换为 Fake LLM。
- Tool：每个 CLI Turn 使用独立 discovery Provider，首轮只暴露 `discover_tools`，发现后才激活实际 Tool schema。
- 预期：CLI 调用 AgentLoop，并通过共享 Markdown-to-plain renderer 打印 assistant 文本和 `/history` 中的 assistant 消息。
- 检查点：测试不访问真实网络或真实 LLM；终端不显示 Markdown 样式标记，Session JSONL 仍保存原始 Markdown。

## 第 7 部分：回合覆盖

- CLI 普通聊天依赖 AgentLoop 生成的回合字段。
- KeyboardInterrupt 兜底路径使用一个生成的回合写入中断消息。
- 在并发输入路径实现前，CLI 帮助仍不展示运行时 `/stop` 命令。

## 第 8 部分：日志覆盖

- `zcagent gateway` 分别解析 Agent 生命周期日志、工作区追踪日志、HTTP 访问日志和 HTTP 服务日志的独立参数。
- 已移除的旧参数 `--log-level` 和 `--access-log` 必须被拒绝，不能继续作为别名保留。

## 第 12 部分：RuntimeEvent 覆盖

- CLI 将 RuntimeEvent 的 started/waiting 安全短标题原位更新到 Spinner；`skill.progress` 优先显示脱敏 detail，internal 的 `run_skill` 包装事件不重复展示。
- completed Event、text_delta 和未知事件不额外刷状态行。
- Hook 配置从当前 workspace/config 加载；显式非法配置阻断 chat 启动并给出稳定提示。
