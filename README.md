# ZhiCe-Agent

ZhiCe-Agent 是一个轻量本地 Agent 内核项目。当前代码能力已经完成到第十部分受控长期 Memory。主线能力包括：

- workspace 本地运行配置与 `zcagent init`
- Markdown prompt 加载
- JSONL 会话持久化
- OpenAI-compatible 与 LiteLLM Provider
- 多 endpoint priority failover
- `/model` 查看、列表、切换和 reset
- 多轮 tool calling
- 受限 `exec`、`read_file`、`list_dir`、`grep`
- Skill source 同步、SkillLoader、`load_skills` 和 `sync_skills`
- CLI、本地 Web gateway、会话 API、WebSocket 主聊天通道和最小静态 Web UI
- `turn_id` / `turn_index` 持久化、WebSocket turn 对齐和基于 turn 的相关历史选择
- Gateway / Agent 分层运行日志、终端时间戳格式和 workspace `logs/YYYY-MM-DD/trace.log`
- SQLite 本地用户、角色、特权权限、可撤销登录态、唯一永久 Owner、Owner 管理权委派、普通用户自助注册和个人设置
- 用户上下文目录、session owner/index、session 级模型偏好和 call-scoped provider
- 登录用户基础能力、跨用户/管理/审计特权、高风险 `exec` 明确确认、独立 Runtime Activity/Security Audit 和当前 Session 自助诊断
- CLI/Owner 共用 workspace Memory、普通用户私有 Memory、明确 list/search 的 `memory_read` 与对话授权 `memory_write`
- 对话式 Memory 授权、Session 空闲高可信提取、一次性通知、Memory 安全过滤和显式 session 摘要

当前仍保持轻量边界：用户系统只面向本地开发，不等于生产级公网鉴权；项目还没有 OAuth/SSO、组织/租户、多 workspace 隔离、远程部署、MCP、Subagent、Hook 或市集。Part 10 Memory 已进入当前代码基线，下一部分是 Part 11 MCP。Web 侧使用同端口 `WebSocket /ws` 作为主聊天通道，REST/SSE 保留为兼容接口。

## 设计文档

设计文档入口是 `docs_design/README.md`。

- 无日期文档是当前活文档，例如总体设计和 Part 文档，始终按最新代码口径维护。
- 带日期文档是当次设计记录，用于保留演进痕迹。
- 新设计落地后，再把已经成为当前准则的内容收敛进总体设计或对应 Part 文档。
- 第九部分权限设计入口是 `docs_design/zhice-agent-part9-user-auth-permission-design.md`；当前“基础能力与特权分离”记录见 `docs_design/2026-07-16-authenticated-user-baseline-capabilities-design.md`，自助诊断和 Activity/Audit 拆分见 `docs_design/2026-07-16-self-diagnostics-activity-audit-separation-design.md`。
- 第十部分 Memory 当前实现入口是 `docs_design/zhice-agent-part10-memory-design.md`，最新日期设计记录是 `docs_design/2026-07-16-background-memory-extraction-and-trace-convergence-design.md`。

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
- `${ZHICE_AGENT_WORKSPACE}/prompts/*.md`

`zcagent init` 可以重复执行：已有文件默认保留，缺失文件会自动补齐；确实要刷新覆盖已有模板时再加 `--force`。

如果启动 `zcagent` 时缺少或未正确填写 `llm_endpoints.json`，CLI 会直接报错并引导你运行 `zcagent init` 或编辑 endpoint 配置。没有可用 LLM 时聊天无法继续。

如果启动 `zcagent` 时缺少 `skill_sources.yml`，CLI 只提示 Skill 同步已跳过，并引导你运行 `zcagent init` 补齐。Skill source 是可选扩展能力，不阻断基础聊天。

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

字段口径：

- `protocol` 表示本地 Provider：`openai` 或 `litellm`。
- `provider` 对 OpenAI-compatible endpoint 保持空字符串；对 LiteLLM endpoint 写模型商前缀，例如 `anthropic`。
- `model` 和 `supported_models` 都写不带 provider 前缀的模型名。
- `api_key` 可以直接写本地 key，也可以写 `${ENV_VAR}` 占位符。
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
- `/reset`：清空当前 session 历史
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
- Owner 是 CLI 本地操作者在 Web 端的登录身份，两者共用全局 workspace、`contexts/sessions` 和 `contexts/sessions_meta`，不创建 Owner 专属的 `contexts/users/{owner_id}`；其他 Web / 外部渠道用户位于 `${ZHICE_AGENT_WORKSPACE}/contexts/users/{user_id}`。聊天侧栏始终只展示当前账号自己的已索引会话。

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

用户明确要求记忆、修改或忘记时，`memory_write` 直接执行。Web Session 空闲五分钟后，统一调度器通过默认两个全局 Worker、同一用户串行的方式调用独立 Extractor，只把至少三个用户 Turn 中具有两到三条原文证据的高可信长期信息写入 Memory，并在下一次对话显示一次简短通知。手动提取和未闭环的 Session Summary 能力均不提供；真正的 Context Compaction 留到后续上下文优化单独设计。

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
