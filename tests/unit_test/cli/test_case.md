# CLI 单元测试用例

## 测试目标

验证 `zcagent` 命令行入口只负责参数解析、运行时依赖初始化、用户输入循环和输出展示，不直接实现 AgentLoop、LLM 或工具业务逻辑。

## 用例覆盖

### Case 1: `zcagent init`

- 输入：显式 workspace、endpoint、base_url、api_key、model 等参数。
- 预期：在 workspace 下生成运行时配置和 prompts。
- 检查点：默认不生成 `.env`；已有文件默认保留，缺失文件会补齐；显式 env file 可提供 workspace。

### Case 2: 缺少 workspace

- 输入：没有 `ZHICE_AGENT_WORKSPACE`，也没有有效 `--workspace`。
- 预期：命令返回失败并提示如何创建 `config/.env`。
- 检查点：提示中包含 `zcagent init`、`zcagent`、`zcagent gateway` 的启动路径。

### Case 3: `zcagent gateway --check`

- 输入：配置好的 workspace 和端口。
- 预期：只做本地 gateway 配置检查，不启动服务。
- 检查点：输出 host、port、workspace 和健康检查地址。

### Case 4: chat 启动 prompt 检查

- 输入：workspace 缺少运行时 prompts。
- 预期：CLI 友好提示运行 `zcagent init`，不抛 traceback。
- 检查点：错误信息包含缺失 prompt 名称。

### Case 4.1: chat 启动 LLM 配置检查

- 输入：workspace 缺少 `${ZHICE_AGENT_WORKSPACE}/config/llm_endpoints.json`，或 endpoint 缺少必需字段如 `api_key`。
- 预期：CLI 返回失败并提示运行 `zcagent init` 或编辑 `llm_endpoints.json`。
- 检查点：这类必需配置缺失会阻断聊天启动；Skill source 缺失只打印 warning。

### Case 5: 默认和显式 session

- 输入：默认启动或传入 `--session named-session`。
- 预期：默认使用当天 `chat-YYYYMMDD` session，显式参数使用指定 session。
- 检查点：普通启动输出不显示 workspace/session，聊天轮次写入当前 session 文件。

### Case 6: 会话 slash commands

- 输入：`/help`、`/new`、`/reset`、`/sessions`、`/history`、`/prompts`、`/model`、`/model list`、`/model list endpoint`、`/model endpoint/model`、`/model reset`、`/exit`。
- 预期：命令在 CLI 层处理，不进入 AgentLoop 普通对话路径。
- 检查点：新 session 可写入文件；reset 后 history 为空；sessions 能显示 preview；`/help` 只列顶层命令，`/skills sync` 放在 `/skills` 的 tip 中；`/model` 能紧凑显示当前模型，`/model list` 能列出 endpoint/model，`/model list endpoint` 能列出单个 endpoint 的 supported_models，且能切换、临时覆盖模型并重置当前首选 endpoint。

### Case 7: `/tools`

- 输入：`/tools`。
- 预期：列出默认只读工具注册表。
- 检查点：输出包含 `list_dir`、`read_file`、`grep`、`exec`。

### Case 8: Fake LLM 对话

- 输入：普通用户消息，测试中替换为 Fake LLM。
- 预期：CLI 调用 AgentLoop 并打印 assistant 文本。
- 检查点：测试不访问真实网络或真实 LLM。

## Part 7 Turn Coverage

- CLI normal chat relies on AgentLoop-generated turn fields.
- KeyboardInterrupt fallback writes interrupted messages with one generated turn.
- CLI help still does not advertise a runtime `/stop` command before the concurrent input path exists.

## Part 8 Logging Coverage

- `zcagent gateway` parses split log flags for Agent lifecycle log, workspace trace log, HTTP access log, and HTTP server log.
- Removed legacy flags `--log-level` and `--access-log` are rejected instead of being kept as aliases.
