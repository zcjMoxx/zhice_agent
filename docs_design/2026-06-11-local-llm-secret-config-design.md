# 本地 LLM 密钥配置设计

## 背景

智策 Agent 的运行时状态保存在仓库外的 workspace 中。我们希望本地开发尽量简单，同时让 LLM endpoint 配置结构尽量贴近参考项目。

之前的草案把 `api_key_env` 设计成单独字段，这会带来两个问题：

- schema 比参考项目更复杂
- 密钥模型被拆成两个概念，使用和说明都更绕

## 目标

- 让 `llm_endpoints.json` 保持简单统一，只保留 `api_key`。
- 支持 `api_key` 既可直接填写本地密钥，也可写成 `${ENV_VAR}` 占位。
- 允许 `config/.env` 提供本地默认值，但不覆盖当前 shell 环境变量。
- 保证仓库内示例配置不包含真实密钥。

## 范围边界

本设计覆盖：

- workspace endpoint 配置加载
- 环境变量占位解析
- CLI 与用户侧配置引导
- 相关测试与文档同步

本设计不覆盖：

- 密钥加密
- Secret Manager 集成
- 部署清单或平台级密钥托管

## 设计方案

workspace 中的 endpoint 配置形态如下：

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

`api_key` 支持两种写法：

1. 直接写本地值

```json
"api_key": "sk-..."
```

2. 使用环境变量占位

```json
"api_key": "${ZHICE_LLM_OPENAI_API_KEY}"
```

对应的 `config/.env` 可以是：

```env
ZHICE_AGENT_WORKSPACE=C:\Users\you\ZhiCe-Agent-Workspace
ZHICE_LLM_OPENAI_API_KEY=sk-...
```

## 解析规则

1. `zcagent` 启动时先把项目内 `config/.env` 加载到当前 Python 进程。
2. 已存在的进程环境变量保留原值，并优先于 `.env`。
3. `load_llm_endpoint()` 在读取 `${ZHICE_AGENT_WORKSPACE}/config/llm_endpoints.json` 时解析 `${ENV_VAR}`。
4. 如果引用的环境变量不存在，启动时报明确错误。

因此，占位形式的有效优先级为：

1. 当前 shell / 系统环境变量
2. 项目内 `config/.env`

如果 `api_key` 是字面量，则直接使用字面量。

## 边界约束

- `config/.env` 不写入 Windows 系统环境变量，只影响当前 `zcagent` 进程。
- `${ZHICE_AGENT_WORKSPACE}/config/llm_endpoints.json` 属于本地运行态文件，不应提交到仓库。
- 仓库中的 `config/llm_endpoints.example.json` 可以使用 `${ENV_VAR}`，但不能包含真实密钥。
- 不再保留 `api_key_env` 独立字段。

## 影响文件

- `agent/config.py`
- `agent/llm/openai_provider.py`
- `agent/loop.py`
- `agent/cli.py`
- `config/llm_endpoints.example.json`
- `README.md`
- `AGENTS.md`
- 相关单元测试

## 验证方案

- 单元测试覆盖直接 `api_key`
- 单元测试覆盖 `${ENV_VAR}` 解析
- 单元测试覆盖环境变量缺失时报错
- 单元测试覆盖 UTF-16 `.env` 加载
- `python -m ruff check .`
- `python -m pytest`
