# ZhiCe-Agent

ZhiCe-Agent 是一个按阶段逐步搭建的轻量本地 Agent 项目。

当前已经具备的基础能力：

- 项目配置加载
- Markdown Prompt 加载
- 通用消息模型
- JSONL 会话持久化
- 最小可运行 CLI 入口

第二阶段已经补上无工具聊天链路：

- `ContextBuilder` 会基于 prompts、历史消息和当前输入组装 LLM messages。
- `AgentLoop.run_turn` 会调用 `LLMProvider`，写入 `user` 与 `assistant` 消息，并返回回复文本。
- `OpenAIProvider` 是一个 OpenAI 兼容接口的 `LLMProvider` 实现。
- `LiteLLMProvider` 可以通过进程内 LiteLLM SDK 接入 Anthropic、Gemini、DeepSeek 等模型商。
- 本地 endpoint 配置放在运行工作目录，不放在源码仓库里。

## 快速开始

```bash
python -m pip install -e .
copy config\.env.example config\.env
# 编辑 config\.env，设置 ZHICE_AGENT_WORKSPACE
zcagent init
zcagent
```

输入 `/exit` 可以退出 CLI。

## 本地命令安装

和参考项目一样，`zcagent` 是在 `pyproject.toml` 里声明的 Python console command。

如果你的当前 Python 环境已经在 `PATH` 上，那么在项目根目录安装一次即可：

```bash
python -m pip install -e .
```

之后新开的终端里，通常就可以直接运行，不需要每次手动激活 `.venv`：

```bash
zcagent
zcagent gateway
```

这是因为命令会被安装到当前 Python 环境对应的 `Scripts` 目录。你这台机器当前就是全局 Anaconda 环境在承接这个命令。`.venv` 仍然适合做隔离，但不是这套参考式工作流的必需条件。

## LLM 配置

ZhiCe-Agent 会自动加载源码项目目录下的 `config/.env`。

这个 `.env` 主要用于项目启动配置，例如 `ZHICE_AGENT_WORKSPACE`。

最少需要先配置：

```env
ZHICE_AGENT_WORKSPACE=C:\Users\you\ZhiCe-Agent-Workspace
```

然后执行一次 `zcagent init`，它会在 `ZHICE_AGENT_WORKSPACE` 下生成运行时文件，包括：

- `config/llm_endpoints.json`
- prompts 目录下的默认 prompt 文件

如果这些本地文件已经存在，默认不会覆盖；确实要刷新时再加 `--force`。

可以通过 `--endpoint` 选择启动时首选的 endpoint；不传时会先看 `default` 别名，
没有 `default` 时按 `priority` 从小到大自动选择。LLM 调用失败时也会按
`priority` 尝试其它 `enabled=true` 的 endpoint。

工作目录下 `config/llm_endpoints.json` 的 `api_key` 目前支持两种写法：

1. 直接写本地 key
2. 写环境变量占位符，例如 `${ZHICE_LLM_OPENAI_API_KEY}`

如果使用占位符，ZhiCe-Agent 会从当前进程环境中解析。由于项目 `config/.env` 的加载不会覆盖已有环境变量，所以实际优先级是：

1. 当前 shell 或系统环境变量
2. 项目 `config/.env`

工作目录 JSON 示例：

```json
{
  "default": "openai_gpt5",
  "openai_gpt5": {
    "protocol": "openai",
    "provider": "",
    "base_url": "https://api.openai.com/v1",
    "api_key": "${ZHICE_LLM_OPENAI_API_KEY}",
    "model": "gpt-5",
    "supported_models": ["gpt-5", "gpt-5.1", "gpt-*"],
    "priority": 1,
    "enabled": true
  },
  "backup": {
    "protocol": "openai",
    "provider": "",
    "base_url": "https://backup.example.com/v1",
    "api_key": "${ZHICE_LLM_BACKUP_API_KEY}",
    "model": "backup-model",
    "supported_models": ["backup-model"],
    "priority": 2,
    "enabled": true
  }
}
```

`default` 可以只是别名，也可以完全不写；不写时会按 `priority` 自动选首选 endpoint。

通过 LiteLLM SDK 接其他模型商时，可以新增 endpoint：

```json
{
  "claude": {
    "protocol": "litellm",
    "provider": "anthropic",
    "api_key": "${ZHICE_LLM_LITELLM_API_KEY}",
    "model": "claude-sonnet-4",
    "supported_models": ["claude-sonnet-4", "claude-*"],
    "max_tokens": 4096,
    "temperature": 0.7
  }
}
```

启动时选择：

```bash
zcagent --endpoint claude
```

CLI 中可以用 `/model` 查看当前模型，也可以切换当前进程的首选 endpoint：

```text
/model
/model list
/model list cpa_gpt55
/model cpa_gpt55
/model cpa_gpt55/gpt-5.5
/model reset
```

当前 `/model` 切换只在本次 `zcagent` 进程内生效。退出后重新启动时，会重新使用启动参数
`--endpoint` 指定的首选 endpoint；如果未指定，则回到 `default`。后续需要补充 session 级模型
持久化，让同一会话重启后继续使用上次 `/model` 选择的模型。

`/model <endpoint>` 会切到该 endpoint 的默认模型。`/model <endpoint>/<model>`
会切到指定 endpoint，并在该 endpoint 上临时使用指定 model；该 model 必须等于 endpoint
的默认 `model`，或命中 endpoint 的 `supported_models`。`supported_models` 支持精确模型名
和简单 glob，例如 `gpt-*`。没有配置 `supported_models` 时，只允许使用默认 `model`。
ZhiCe-Agent 不支持裸 `/model <model>` 自动猜测 endpoint。`/model list` 会显示所有可用
endpoint 及其默认 model；`/model list <endpoint>` 会显示该 endpoint 的默认模型和
`supported_models`；`/model reset` 会清除本次进程内的手动切换，恢复到配置的 `default`
别名或 priority 顺序。

### LiteLLM 与 OpenAI-compatible endpoint

ZhiCe-Agent 里的 `protocol` 表示本地选择哪个 Provider：

- `protocol="openai"`：直接调用一个 OpenAI-compatible endpoint，例如 `https://api.openai.com/v1`、OpenRouter、DeepSeek 或公司内部兼容网关。
- `protocol="litellm"`：在 ZhiCe-Agent 进程内调用 `litellm` Python SDK，由 LiteLLM 适配 Anthropic、Gemini、DeepSeek、DashScope 等模型商。

当前 `LiteLLMProvider` 不要求你本地单独启动 LiteLLM Proxy。它会调用 `litellm.completion(...)`，并把 `api_key`、模型名、tools、max_tokens、temperature 等参数交给 LiteLLM SDK。

因此，`base_url` 的含义是：

```text
openai  -> 必填，指向真实 OpenAI-compatible 模型网关
litellm -> 可选，只在你要走自定义 LiteLLM/OpenAI-compatible 网关时作为 api_base 传给 SDK
```

其它模型可以走 LiteLLM，把模型名写成 LiteLLM 能识别的格式：

```json
{
  "claude": {
    "protocol": "litellm",
    "provider": "anthropic",
    "api_key": "${ANTHROPIC_API_KEY}",
    "model": "claude-sonnet-4",
    "supported_models": ["claude-sonnet-4", "claude-*"],
    "priority": 2,
    "enabled": true
  }
}
```

加载后，ZhiCe-Agent 会把模型名拼成 `anthropic/claude-sonnet-4` 交给 LiteLLM SDK。通常不需要给 Anthropic/Gemini 这类原生模型商填写 `base_url`；除非你用的是公司内部网关或自建 OpenAI-compatible 转发层。

项目 `config/.env` 示例：

```env
ZHICE_AGENT_WORKSPACE=C:\Users\you\ZhiCe-Agent-Workspace
ZHICE_LLM_OPENAI_API_KEY=your-api-key
```

工作目录里的本地 JSON 不会被 git 提交，适合放本地运行配置。

如果 `ZHICE_AGENT_WORKSPACE` 没设置，`zcagent` 会直接退出，并提示如何创建 `config/.env`，不会把源码目录误当成工作目录。

## 命令说明

第一次完成 `zcagent init` 之后，正常聊天直接执行：

```bash
zcagent
```

默认会进入稳定的本地会话 `default`。

如果你要显式进入某个已有会话，可以传：

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
- `/model`：查看或切换当前首选 LLM endpoint
- `/model list`：查看可用 endpoint 和默认 model
- `/model list <endpoint>`：查看某个 endpoint 支持的 model
- `/model reset`：恢复到配置默认模型或 priority 顺序
- `/help`：查看可用斜杠命令
- `/exit`：退出 CLI

启动本地 gateway：

```bash
zcagent gateway
```

现阶段 gateway 还只是入口脚手架和基础状态面，不包含完整 Web UI、WebSocket、渠道接入、鉴权或后台服务编排。

如果只想做非阻塞检查：

```bash
zcagent gateway --check
```

## 测试

```bash
python -m ruff check .
python -m pytest
```

第二阶段的 provider 测试使用 mock HTTP，不会真的调用线上 LLM API。
