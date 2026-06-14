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
- `LiteLLMProvider` 可以通过 LiteLLM Proxy 接入 Anthropic、Gemini、DeepSeek 等模型商。
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

可以通过 `--endpoint` 选择要使用的 endpoint。

工作目录下 `config/llm_endpoints.json` 的 `api_key` 目前支持两种写法：

1. 直接写本地 key
2. 写环境变量占位符，例如 `${ZHICE_LLM_OPENAI_API_KEY}`

如果使用占位符，ZhiCe-Agent 会从当前进程环境中解析。由于项目 `config/.env` 的加载不会覆盖已有环境变量，所以实际优先级是：

1. 当前 shell 或系统环境变量
2. 项目 `config/.env`

工作目录 JSON 示例：

```json
{
  "default": {
    "protocol": "openai",
    "base_url": "https://api.openai.com/v1",
    "api_key": "${ZHICE_LLM_OPENAI_API_KEY}",
    "model": "gpt-5"
  }
}
```

通过 LiteLLM Proxy 接其他模型商时，可以新增 endpoint：

```json
{
  "claude": {
    "protocol": "litellm",
    "base_url": "http://127.0.0.1:4000/v1",
    "api_key": "${ZHICE_LLM_LITELLM_API_KEY}",
    "model": "anthropic/claude-sonnet-4",
    "max_tokens": 4096,
    "temperature": 0.7
  }
}
```

启动时选择：

```bash
zcagent --endpoint claude
```

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
