# ZhiCe-Agent

ZhiCe-Agent 是一个轻量本地 Agent 内核项目。当前代码能力已经完成到第十四部分 QQ 与微信 ClawBot 外部渠道接入。主线能力包括：

- workspace 本地运行配置与 `zcagent init`
- Markdown prompt 加载
- JSONL 会话持久化
- OpenAI-compatible 与 LiteLLM Provider
- 多 endpoint priority failover
- `/model` 查看、列表、切换和 reset
- 多轮 tool calling
- Turn-scoped `discover_tools` 按需发现与动态 Tool schema 激活；模型不再首轮接收全部业务 Tool
- 受限 `exec`、`read_file`、`list_dir`、`grep`
- Skill source 同步、SkillLoader、`load_skills` 和 `sync_skills`
- CLI、本地 Web gateway、会话 API、WebSocket 主聊天通道，以及支持安全 Markdown 与 KaTeX 公式的静态 Web UI
- `turn_id` / `turn_index` 持久化、WebSocket turn 对齐、最近 3 个 Turn 与旧相关 Turn 混合选择，以及 endpoint token 预算
- Gateway / Agent 分层运行日志、Web/QQ/微信渠道启动结果、终端时间戳格式和 workspace `logs/YYYY-MM-DD/trace.log`
- SQLite 本地用户、角色、特权权限、可撤销登录态、唯一永久 Owner、Owner 管理权委派、普通用户自助注册和个人设置
- 用户上下文目录、session owner/index、session 级模型偏好和 call-scoped provider
- 登录用户基础能力、跨用户/管理/审计特权、高风险 `exec` 明确确认、独立 Runtime Activity/Security Audit 和当前 Session 自助诊断
- CLI/Owner 共用 workspace Memory、普通用户私有 Memory、明确 list/search 的 `memory_read` 与对话授权 `memory_write`
- 对话式 Memory 授权、Session 空闲高可信提取、一次性通知和 Memory 安全过滤
- workspace 共享 MCP Runtime、stdio / Streamable HTTP / SSE、自动 Tool 发现、OAuth 刷新、Elicitation 和 actor-scoped artifact 导入
- 有界并行 `delegate_tasks`、独立 child AgentLoop/Session/RuntimeEvent、能力 Profile 和 shared-readonly/worktree/shared-exclusive workspace 隔离
- `/subagent` 的 `auto/off/once` Session 语义、Web child task 状态和可选能力结构化启动告警
- 中性 Channel 协议、外部身份绑定、持久 conversation route/event receipt，以及 QQ 私聊/群聊 `@` WebSocket adapter
- 微信 `channel_accounts` 所有权、本人扫码 API/UI、stdio NDJSON Node sidecar、基于腾讯 `2.4.6` 审计来源的 direct-text Transport 和多用户隔离

当前仍保持轻量边界：用户系统只面向本地开发，不等于生产级公网鉴权；项目还没有 OAuth/SSO、组织/租户、多 workspace 隔离、远程部署、跨 Turn 后台 Agent Job、depth > 1、自动 worktree merge 或市集。MCP Runtime 直接读取常见 `mcpServers` 配置，通过 `tools/list` 自动暴露有效 Tool，并支持 stdio、Streamable HTTP、旧 SSE、直接/env credential、OAuth token refresh、Elicitation 和 `/mcp`。配置、credential、Catalog、连接和 stdio 进程由 workspace 共享；artifact 按当前 actor 写入本人目录。stdio 已强制使用专用临时 cwd、最小环境、无 shell 和 Job Object 回收，但 Windows OS 级读取隔离仍是后续硬化项。Part 12 已完成 turn/context/LLM/tool RuntimeEvent、WebSocket/SSE/CLI 与前端真实状态，以及显式配置、无 shell、受限执行的 pre/post Tool Hook Runtime；Hook 只能增加业务阻断、修改后重新走核心校验或补充安全 display/ui_metadata，不能降低 RBAC、危险确认、workspace guard、timeout、脱敏或 SSRF。Part 13 在此基线上增加同步 Provider 下的有界线程并行、父能力交集、独立 child 运行态和 workspace lease。SkillExecutor、`skill.*` 和 ProgressSink 属于未来 Skill Runtime / Part 18。Web 侧继续使用同端口 `WebSocket /ws` 作为主聊天通道，REST/SSE 保留为兼容接口。

Tool capability selection 已从原 Part 16 路线提前进入当前基线。每个 CLI/Web/child Turn 首次只向模型提供 `discover_tools`；模型判断需要真实能力时先查询并激活最小 Tool 集合，下一模型步才收到这些具体 schema。Catalog 在 actor RBAC 和 Subagent Profile 过滤之后生成，未激活 Tool 即使被编造也返回 `TOOL_NOT_ACTIVATED`；实际 Tool 仍经过确认、Hook、workspace guard 和审计。Session 保存完整历史；CLI 与 Web 使用同一套 ContextBuilder：最近 50 个 user Turn 中固定优先最近 3 个，再从更早历史选择最多 3 个相关 Turn，同时保留 60 条消息兜底，并在每次初始/工具结果 LLM 调用前把实际 Tool schema 一起纳入 failover-safe endpoint token 预算。child 使用新鲜独立 Session 上下文，但继承父 Turn 的同一输入预算。

## 设计文档

设计文档入口是 `docs_design/README.md`。

- 无日期文档是当前活文档，例如总体设计和 Part 文档，始终按最新代码口径维护。
- 带日期文档是当次设计记录，用于保留演进痕迹。
- 新设计落地后，再把已经成为当前准则的内容收敛进总体设计或对应 Part 文档。
- 第九部分权限设计入口是 `docs_design/zhice-agent-part9-user-auth-permission-design.md`；当前“基础能力与特权分离”记录见 `docs_design/2026-07-16-authenticated-user-baseline-capabilities-design.md`，自助诊断和 Activity/Audit 拆分见 `docs_design/2026-07-16-self-diagnostics-activity-audit-separation-design.md`。
- 第十部分 Memory 当前实现入口是 `docs_design/zhice-agent-part10-memory-design.md`，最新日期设计记录是 `docs_design/2026-07-16-background-memory-extraction-and-trace-convergence-design.md`。
- 第十一部分 MCP 当前实现入口是 `docs_design/zhice-agent-part11-mcp-design.md`，本次边界与取舍记录见 `docs_design/2026-07-17-mcp-tool-runtime-boundary-design.md`。
- 第十二部分生命周期事件与 Hook Runtime 当前实现入口是 `docs_design/zhice-agent-part12-hooks-design.md`，最终边界与取舍记录见 `docs_design/2026-07-20-hook-runtime-boundary-design.md`；RuntimeEvent、渠道/前端状态、真实 pre/post Hook Runtime 和测试均已完成，Part 12 已关闭。
- 第十三部分并行 Subagent 编排已经实现并进入当前代码基线，入口是 `docs_design/zhice-agent-part13-subagent-design.md`，边界取舍记录见 `docs_design/2026-07-21-subagent-runtime-boundary-design.md`，启动降级与诊断证据闭环见 `docs_design/2026-07-21-startup-capability-and-subagent-diagnostics-design.md`。主 Agent默认直接完成简单任务，只有委派收益明确时才通过批量 `delegate_tasks` 做有界并行 fan-out/fan-in；`/subagent` 提供 `auto/off/once` Session 语义；child 使用独立 AgentLoop、Session、RuntimeEvent scope 和 workspace lease，Skill、`exec`、MCP 通过 Profile、父能力交集、确认与 Hook 受控开放。
- 第十四部分外部渠道已实现第一版 QQ 闭环，入口是 `docs_design/zhice-agent-part14-external-channel-design.md`，初始边界见 `docs_design/2026-07-23-qq-external-channel-boundary-design.md`，跨渠道 Session、用户解绑和 QQ Markdown 收敛见 `docs_design/2026-07-23-cross-channel-session-binding-and-qq-markdown-design.md`。QQ SDK 仅位于 transport 层；未知身份在 LLM 前拒绝，群聊按触发用户隔离 Session，高风险确认转私聊或 Web。
- 第十四部分实现二微信 ClawBot 已落地，当前口径见 `docs_design/zhice-agent-part14-external-channel-design.md`，完整取舍和真实 POC 证据见 `docs_design/2026-07-24-weixin-clawbot-channel-design.md`。一个 Web 用户独立拥有一个微信 AI 账号，共享 Node Transport sidecar 接入现有 Channel Runtime，不引入第二套 AgentLoop。2026-07-24 已用真实微信验证 AI 标识、扫码、direct text 收发、context token、游标恢复和 notifyStop；双真实账号并发仍需第二名用户验收。Part 15 仍为生产部署与发布。
- 按需 Tool 发现与动态 Capability Selection 已提前落地，设计记录见 `docs_design/2026-07-21-on-demand-tool-discovery-design.md`；它是通用运行时能力，不归入 Part 13 的业务委派判断。

Subagent 运行配置位于 `${ZHICE_AGENT_WORKSPACE}/config/subagents.yml`，仓库模板为 `config/subagents.example.yml`。缺少配置时功能默认关闭；启用后可用裸 `/subagent` 查看当前模式和 Profile，详细切换形式由输出中的 Tip 提示。能力不可用时，CLI、本地操作者和 Owner 会看到真实原因与修复建议；普通 Web 用户只会看到能力暂时不可用并联系管理员，不暴露 Prompt 文件名、内部错误码、配置路径或初始化命令。真实 cause 继续保留在终端、trace 和有权限的诊断结果中。

启动失败按能力边界分级处理：workspace/运行目录、基础 Prompt、LLM endpoint、Gateway Auth 等核心依赖继续阻断对应入口；显式 Hook 配置属于已声明安全策略，非法时保持 fail closed 并阻断启动。Skill source、MCP、Subagent 属于可选扩展，完全未配置时正常 disabled、不报警；显式配置后依赖非法或缺失时只禁用对应能力并记录结构化 WARNING。后台 Memory extraction 是系统内置能力，缺少或损坏内置 `memory_extraction.md` 时记录 WARNING 并仅关闭自动提取，基础聊天和显式 Memory 读写继续。Gateway 启动异常统一进入红色终端日志和 workspace trace；Web `/api/health` 保留通用 capability 状态供诊断工具查询，但聊天页面不常驻展示启动告警。单个 MCP server 或 child worktree 的失败仍在使用时返回精确错误，不把整个应用降级。

`diagnose_my_recent_activity` 会自动定位当前用户、当前 Session 的上一轮或最近失败，并从父 `delegate_tasks` Turn 沿 `root_session_id/root_turn_id` 下钻安全 child terminal trace。Tool 除规则摘要外还返回按时间排序、字段白名单过滤且再次脱敏的 `trace_events`，模型必须直接分析其中的 `error_message/stage/code` 和前后事件，不能只复述通用包装码。诊断专用规则位于 `prompts/diagnostics.md`，Exec 专用命令、风险和结果处理规则位于 `prompts/exec.md`；两者由主 ContextBuilder 可选加载，不与通用 `tool_use_policy.md` 混用，也不因文件缺失阻断聊天。真正的 Exec 安全边界仍由 RBAC、确认、Hook、workspace guard、危险命令拦截、timeout 和输出截断强制执行。

## 快速开始

```bash
# 进入 zhice_agent 项目所在文件夹
python -m pip install -e .
copy config\.env.example config\.env
# 编辑 config\.env，设置 ZHICE_AGENT_WORKSPACE
zcagent init
zcagent
```

输入 `/exit` 可以退出 CLI。

## 本地命令安装

`zcagent` 是在 `pyproject.toml` 里声明的 Python console command。

如果你的当前 Python 环境已经在 `PATH` 上，在项目根目录安装一次即可：

```bash
python -m pip install -e .
```

之后新开的终端里通常可以直接运行：

```bash
zcagent
zcagent gateway
```

命令会被安装到当前 Python 环境对应的 `Scripts` 目录。你这台机器当前是全局 Anaconda 环境在承接这个命令。`.venv` 仍然适合做隔离，但不是这套参考式工作流的必需条件。

## 工作区初始化

ZhiCe-Agent 会自动加载源码项目目录下的 `config/.env`。这个 `.env` 主要用于项目启动配置，例如：

```env
ZHICE_AGENT_WORKSPACE=C:\Users\you\ZhiCe-Agent-Workspace
```

执行一次 `zcagent init` 后，会在 `ZHICE_AGENT_WORKSPACE` 下生成运行时文件：

- `${ZHICE_AGENT_WORKSPACE}/config/llm_endpoints.json`
- `${ZHICE_AGENT_WORKSPACE}/config/skill_sources.yml`
- `${ZHICE_AGENT_WORKSPACE}/config/channels.yml`
- `${ZHICE_AGENT_WORKSPACE}/prompts/*.md`

`zcagent init` 可以重复执行：已有文件默认保留，缺失文件会自动补齐；确实要刷新覆盖已有模板时再加 `--force`。

如果启动 `zcagent` 时缺少 `llm_endpoints.json`，CLI 会阻断聊天并引导运行 `zcagent init`；文件已经存在但内容非法时，CLI 会优先引导编辑现有文件，不会误导普通 `init` 可以覆盖修复。聊天至少需要一个 enabled endpoint，并配置与真实服务一致的 protocol、base URL/provider、model 和 api_key。`context_window` 缺失时默认 `131072`，`max_tokens` 缺失时使用兼容默认值；二者已有默认值，但应按实际模型限制校准。没有可用 LLM 时聊天无法继续。

Skill source、MCP 和 Subagent 都是可选扩展：未配置时作为 disabled 静默关闭，不影响基础聊天；只有显式配置后内容非法或依赖失败时才记录 WARNING。Hook 未配置时同样 disabled，但显式配置代表已声明安全策略，非法时会阻断启动。

## QQ 外部渠道

安装可选 SDK：

```bash
python -m pip install -e ".[qq]"
```

运行配置位于 `${ZHICE_AGENT_WORKSPACE}/config/channels.yml`，模板为 `config/channels.example.yml`。本地项目 `config/.env` 提供 `QQBOT_APP_ID` 和 `QQBOT_APP_SECRET`，`channels.yml` 只保留 `${QQBOT_APP_ID}` / `${QQBOT_APP_SECRET}` 引用。配置 `enabled: true` 后，`zcagent gateway` 在同一生命周期启动 QQ WebSocket adapter；QQ 不可用只局部降级，不阻断 Web/CLI。

查看状态；CLI 绑定码命令保留为管理/故障恢复入口：

```bash
zcagent channels status
zcagent channels link-code qq --user alice --account main
```

未绑定 QQ 用户会看到“绑定”按钮；裸 `/bind` 返回一次性 Web Markdown 登录链接和 URL 按钮，登录成功后自动绑定当前 Web 用户，也可以在 Web“个人设置”生成一次性绑定码后发送 `/bind <code>`。个人设置会显示当前用户自己的 QQ 绑定并允许解绑，解绑保留历史 Session。Web/CLI 可以查看本人跨渠道历史，QQ 私聊 Session 可跨端继续；QQ 群 Session 在 Web/CLI 只读，只能派生新的 Web Session。QQ 不提供跨渠道 `/sessions` 管理。QQ 私聊的普通结构化回复在安全长度内使用 Markdown；QQ 群聊和 CLI 通过共享 renderer 把 Markdown 转为可读纯文本。QQ 被动回复分块使用递增 `msg_seq`，群聊最多 5 块、单聊最多 4 块。当前 `qq-botpy 1.2.1` 未使用不稳定的原生 token stream。

## 微信 ClawBot 外部渠道

微信默认关闭。运行机器需要 Node.js 22 或更高版本；配置位于 `${ZHICE_AGENT_WORKSPACE}/config/channels.yml`：

```yaml
channels:
  weixin:
    enabled: true
    transport: sidecar_stdio
    node_path: node
    sidecar_entry: integrations/weixin_sidecar/dist/main.js
    binding_timeout_seconds: 480
    max_parallel_conversations: 8
    text_chunk_limit: 4000
```

启用后运行 `zcagent gateway`，登录 Web，在 Account settings 的 Weixin ClawBot 区域发起扫码。绑定只取当前登录用户，不接受 URL/body `user_id`；二维码响应使用 `Cache-Control: no-store`。账号凭证写入 `${ZHICE_AGENT_WORKSPACE}/config/channels/weixin/accounts/{account_key}.json`，同步游标属于 `${ZHICE_AGENT_WORKSPACE}/state/channels/weixin/{account_key}/sync.json`，均不进入仓库配置。

当前仓库已经具备本人状态/扫码/取消/解绑 API、Web UI、账号唯一约束、身份解析、receipt ACK、限流、Conversation Route、direct-text Turn、纯文本 4000 字符分块和局部降级。官方 `@tencent-weixin/openclaw-weixin@2.4.6` 的版本、integrity、MIT LICENSE、vendored 文件、补丁清单和真实 POC 结果记录在 `integrations/weixin_sidecar/vendor/upstream-manifest.json`。sidecar 只复用审计后的扫码/API/文本 Transport，不加载 OpenClaw Channel 或 Agent Runtime；不得改用个人微信自动化。

Node 测试：

```bash
cd integrations/weixin_sidecar
npm test
```

## LLM 配置

仓库只提交模板 `config/llm_endpoints.example.json`。真实运行文件位于：

```text
${ZHICE_AGENT_WORKSPACE}/config/llm_endpoints.json
```

当前模板口径如下：

```json
{
  "_comment": "把此文件复制到运行工作区的 config/llm_endpoints.json 后再按实际服务修改。",
  "default": "openai_gpt5",
  "openai_gpt5": {
    "protocol": "openai",
    "provider": "",
    "base_url": "https://api.openai.com/v1",
    "api_key": "${ZHICE_LLM_OPENAI_API_KEY}",
    "model": "gpt-5.5",
    "supported_models": ["gpt-5.5", "gpt-5.4", "gpt-5.4-mini"],
    "context_window": 131072,
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
    "context_window": 200000,
    "max_tokens": 16384,
    "temperature": 0.7,
    "priority": 1,
    "enabled": true,
    "role": "default"
  }
}
```

字段口径：

- `protocol` 表示本地 Provider：`openai` 或 `litellm`。
- `provider` 对 OpenAI-compatible endpoint 保持空字符串；对 LiteLLM endpoint 写模型商前缀，例如 `anthropic`。
- `model` 和 `supported_models` 都写不带 provider 前缀的模型名。
- `api_key` 可以直接写本地 key，也可以写 `${ENV_VAR}` 占位符。
- `context_window` 是 endpoint/model 的总上下文窗口；未填写时默认 `131072`，并且必须大于 `max_tokens`。
- `max_tokens` 是单次响应允许生成的最大输出 token，不是输入上限或总窗口；它同时用于本地预留输出空间和实际 Provider 请求。
- 本地输入预算固定按 `context_window - max_tokens` 计算，不再提供第三个输入上限配置字段。
- `default` 是 endpoint 别名；不写时会按 `priority` 自动选择首选 endpoint。

如果使用 `${ENV_VAR}` 占位符，ZhiCe-Agent 会从当前进程环境中解析。项目 `config/.env` 的加载不会覆盖已有环境变量，所以优先级是：

1. 当前 shell 或系统环境变量
2. 项目 `config/.env`

`protocol="openai"` 会直接调用 `base_url` 指向的 OpenAI-compatible 模型网关。`protocol="litellm"` 会在 ZhiCe-Agent 进程内调用 `litellm.completion(...)`；`base_url` 对 LiteLLM 是可选字段，只在你要走自定义 `api_base` 时填写。

加载 `litellm_claude` 后，ZhiCe-Agent 会把模型名拼成 `anthropic/claude-opus-4.8` 交给 LiteLLM SDK。

启动时可以指定首选 endpoint：

```bash
zcagent --endpoint litellm_claude
```

## Skill 配置

仓库只提交模板 `config/skill_sources.example.yml`。真实运行文件位于：

```text
${ZHICE_AGENT_WORKSPACE}/config/skill_sources.yml
```

默认配置把官方 Skill 仓库根作为本地 source：

```yaml
name: zhice-official
local_dir: "${ZHICE_AGENT_SKILL_REPO}"
git_url: "https://example.com/skills.git"
target: "master"
```

内置 `${ZHICE_AGENT_SKILL_REPO}` 默认指向项目根目录下的 `skill_repo/`。仓库结构固定为 `skills/{skill_name}/SKILL.md`。同步后完整仓库会落到：

```text
${ZHICE_AGENT_WORKSPACE}/extends/zhice-official/
```

Agent 从 `${ZHICE_AGENT_WORKSPACE}/extends/zhice-official/skills` 发现和执行 Skill，并在模型上下文和 `/skills` 中显示 `zhice-official/{skill_name}`。`sources[].name` 用在日志、命名空间和 `/skills sync <name>`；`sources[].sync` 表示是否同步该 source。`local_dir` 存在时优先使用本地仓库，缺失时可用 `git_url` + `target` 分支兜底。

## Tool Hook 配置

Part 12 Hook Runtime 从 `${ZHICE_AGENT_WORKSPACE}/config/hooks.yml` 显式加载本地 Python Hook。配置缺失或 `hooks: []` 时禁用 Hook，不创建子进程；示例模板见 `config/hooks.example.yml`。

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

脚本路径解析后必须位于 `ZHICE_AGENT_WORKSPACE` 内。Runner 使用当前 Python、`shell=False`、workspace cwd、最小环境、短 timeout 和有界 stdin/stdout/stderr；stdin/stdout 均为单个 UTF-8 JSON object。`pre_tooluse` 支持 `continue/block/modify`，修改后的参数会重新经过 Tool schema、RBAC、危险确认和具体 Tool guard；`post_tooluse` 支持 `continue/enrich`，只能补充受限 `display/ui_metadata`，不能修改 ToolResult 的成功失败事实。`exempt_roles` / `exempt_permissions` 是可选的单 Hook 豁免：owner 可显式按角色跳过，admin 根据已生效的角色权限或直接授权匹配权限跳过；缺省时所有身份都执行 Hook，豁免后仍经过全部核心安全检查。完整协议和错误策略见 `docs_design/zhice-agent-part12-hooks-design.md`，角色/权限作用域设计见 `docs_design/2026-07-21-hook-role-scope-design.md`。

## 命令说明

第一次完成 `zcagent init` 之后，正常聊天直接执行：

```bash
zcagent
```

未指定 `--session` 时，默认进入当天本地会话，例如 `chat-20260621`。如果要显式进入某个已有会话：

```bash
zcagent --session your-session-id
```

CLI 内可用命令：

- `/new`：新建一个 session，并切换过去
- `/clear`：清空当前 session 历史
- `/sessions`：查看已有 session 列表和简短预览
- `/history`：打印当前 session 最近消息
- `/prompts`：列出已加载的 prompt 文件
- `/tools`：列出已注册的工具
- `/skills`：列出已同步到 workspace `extends` 下的 Skill
- `/model`：查看或切换当前首选 LLM endpoint
- `/memory`：立即从当前 session 提取长期 Memory；详细子命令见执行结果中的 Tip
- `/help`：查看可用斜杠命令
- `/exit`：退出 CLI

启动本地 gateway：

```bash
zcagent auth init-owner
zcagent gateway
```

普通用户可以在 Owner 初始化前后通过 “Create account” 注册，新账号固定获得 `viewer`，不能通过请求字段自选权限。唯一 Owner 可在服务器运行 `zcagent auth init-owner` 创建；云端如需 Web 初始化，应注入随机 `ZHICE_AGENT_SETUP_TOKEN`，再访问隐藏入口 `http://127.0.0.1:10086/_setup`。Web 用户名固定为 `owner`，页面只填写一次 Owner 密码和一次 setup credential。普通登录页和账号菜单不展示该入口。

用户在 Account settings 修改密码成功后，当前及其它登录态会全部撤销，浏览器立即返回登录页，必须使用新密码重新登录。

当前静态页面、REST API 和 WebSocket 都由同一个 FastAPI Gateway 在 `10086` 同源提供。参考项目的双端口来自 aiohttp Web channel 与 FastAPI 业务 API 并存，ZhiCe-Agent 当前没有这层边界，因此暂不增加 `10186` 前端代理端口。

API 失败响应使用统一错误结构：真实 HTTP 状态码保持数字语义，body 的 `error` 包含 `status`、稳定领域 `code`、可读 `message`、关联日志的 `request_id` 和安全动态上下文 `details`。前端不解析 message，并会在 401/403 后重新获取当前登录态和权限。

默认启动时会打印简短 Agent lifecycle log，并写入 workspace trace；`turn.done` 在终端和 trace 中保留最终回答第一条非空行、最多 80 字符的 `output_preview`。`llm.call`、`llm.done` 等调用细节默认不刷终端，需要时用 `--agent-log-level debug` 或查看 trace。重复的 `llm.direct`、成功 `session.save`、`web.chat.accepted/done` 和每 Turn 模型选择事件不再写入：

Gateway 的渠道加载、就绪、停止、断连、重连与发送异常在终端统一使用 Uvicorn 风格，与带时间的 Agent/LLM/Tool 执行日志明显区分；trace 仍保存原始结构化 event 和安全字段。外部渠道及 ready 日志严格遵循 `${ZHICE_AGENT_WORKSPACE}/config/channels.yml` 中的映射顺序：

```text
INFO:     [gateway] channels enabled | channels=["web","qq","weixin"]
INFO:     [qq] channel ready | mode=shared
INFO:     [weixin] channel ready | mode=per_user accounts=20 active=18 reconnect_required=2
```

QQ 是所有用户共用的单机器人，只有真实 botpy `on_ready` 后才输出一条 ready；SDK 登录细节默认不刷终端。微信是每位 Web 用户独立绑定的 AI 插件账号，启动日志只显示隐私安全的状态聚合，不逐账号输出。正常微信收发细节只写 DEBUG trace，sidecar、重连和发送失败才显示 WARNING，且不输出 credential、外部账号标识或 context token。

终端中的实际耗时会自动显示为 `500ms`、`1.25s`、`3m20s` 或 `1h5m5s`；trace 仍保留原始 `duration_ms` 数值。

```text
${ZHICE_AGENT_WORKSPACE}/logs/YYYY-MM-DD/trace.log
```

常用日志开关：

```bash
zcagent gateway --agent-log off
zcagent gateway --agent-log-level debug
zcagent gateway --trace-log off
zcagent gateway --http-access-log off
zcagent gateway --http-server-log-level warning
```

gateway 仍只面向本地开发。已有本地用户名密码鉴权和 RBAC，但不包含生产公网安全方案、OAuth/SSO、多租户或后台服务编排。

非阻塞检查：

```bash
zcagent gateway --check
```

## 子命令补充

### `zcagent auth`

```bash
zcagent auth init-owner --username owner --display-name Owner
zcagent auth users
zcagent auth reset-password admin
```

- `init-owner` 创建唯一 Owner；默认 `--username owner --display-name Owner`，两者均可覆盖。无 Owner 时先安全读取并校验 `ZHICE_AGENT_SETUP_TOKEN`，再安全读取一次 Owner 密码；两者都不接受明文命令参数。已有 Owner 时直接失败，不读取任何输入。
- Owner 是 CLI 本地操作者在 Web 端的登录身份，两者共用全局 workspace、`contexts/sessions` 和 `contexts/sessions_meta`，不创建 Owner 专属的 `contexts/users/{owner_id}`；其他 Web / 外部渠道用户位于 `${ZHICE_AGENT_WORKSPACE}/contexts/users/{user_id}`。聊天侧栏展示当前账号自己的全部已索引会话并标明 Web、CLI、QQ 私聊或 QQ 群来源；QQ 群历史只读，避免私有 Web/CLI 上下文随后进入公开群回复。

### `/model`

`/model` 用于查看或切换当前 session 的 endpoint/model 偏好；`/model reset` 只清当前 session，`/new` 不继承旧 session 偏好：

```text
/model
/model list
/model list openai_gpt5
/model openai_gpt5
/model openai_gpt5/gpt-5.5
/model reset
```

`/model <endpoint>` 会切到该 endpoint 的默认模型。`/model <endpoint>/<model>` 会在该 endpoint 上临时使用指定 model；该 model 必须等于 endpoint 的默认 `model`，或命中 `supported_models`。`supported_models` 支持精确模型名和简单 glob，例如 `gpt-*`。

ZhiCe-Agent 不支持裸 `/model <model>` 自动猜测 endpoint。`/model` 切换只在本次 `zcagent` 进程内生效；退出后会重新使用启动参数 `--endpoint`、`default` 别名或 priority 顺序。

### `/memory`

`/memory` 展示当前账号的长期 Memory：

```text
/memory
```

用户明确要求记忆、修改或忘记时，`memory_write` 直接执行。Web Session 空闲五分钟后，统一调度器通过默认两个全局 Worker、同一用户串行的方式调用独立 Extractor，只把至少三个用户 Turn 中具有两到三条原文证据的高可信长期信息写入 Memory，并在下一次对话显示一次简短通知。后台提取调用继承当前 session 模型的 failover-safe ContextBudget，超限时先减少较早来源 Turn、再裁剪过长来源文本，不绕过 endpoint 输入上限。手动提取和未闭环的 Session Summary 能力均不提供；真正的 Context Compaction 留到后续上下文优化单独设计。

### `/skills`

`/skills` 用于列出已经同步到 workspace `extends` 下的 Skill。需要刷新配置来源时使用：

```text
/skills sync
/skills sync --verbose
/skills sync zhice-official
```

`--verbose` 会显示新增、变更、删除和未变更数量等明细。`source_name` 用于只同步某个已配置 source。

## 测试

```bash
python -m ruff check .
python -m pytest
```

如果 Windows 临时目录或 `.pytest_cache` 权限导致 warning，可以使用 repo 内 basetemp：

```bash
python -m pytest --basetemp .tmp/pytest_basetemp
```

默认单元测试使用 Fake LLM 或 mock HTTP，不会真的调用线上 LLM API。
