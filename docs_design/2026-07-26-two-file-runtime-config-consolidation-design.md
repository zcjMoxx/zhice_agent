# 两文件运行配置收敛设计记录

> 日期：2026-07-26
>
> 状态：已落地；不提供迁移命令，不做运行时懒读取旧配置。

## 背景与目标

当前运行工作区按能力拆成`llm_endpoints.json`、`embedding_endpoints.json`、`context.yml`、`skill_sources.yml`、`subagents.yml`、`channels.yml`、`hooks.yml`和`mcp.json`，对模块开发清楚，但用户难以确认“哪个模型负责什么”和完整运行策略。目标是收敛成两个主配置文件，外加只承载Secret的可选`.env`。

## 最终文件

```text
config/models.json
config/config.yml
config/.env  # 可选Secret容器，不是业务策略文件
```

`models.json`统一Chat、Compaction和Embedding端点、价格与用途路由；`config.yml`统一Context、Skill、Subagent、Channel、Hook、MCP和Logging。

## models.json

```json
{
  "schema_version": 1,
  "routing": {
    "chat": "cpa_one/gpt-5.4",
    "compaction": "cpa_one/gpt-5.4",
    "embedding": "bailian/text-embedding-v4"
  },
  "chat": {
    "cpa_one": {
      "protocol": "openai",
      "base_url": "https://example/v1",
      "api_key": "${ENV_VAR}",
      "model": "gpt-5.4",
      "supported_models": ["gpt-5.4"],
      "context_window": 200000,
      "max_tokens": 16384,
      "pricing": {"input_per_million": 0, "output_per_million": 0}
    }
  },
  "embedding": {
    "bailian": {
      "protocol": "openai",
      "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
      "api_key": "${ENV_VAR}",
      "model": "text-embedding-v4",
      "supported_models": ["text-embedding-v4"],
      "dimensions": 1024,
      "batch_size": 16
    }
  }
}
```

路由同时支持`endpoint`和`endpoint/model`。只写endpoint使用端点默认model；显式model必须等于默认model或命中`supported_models`，否则启动失败。地址、Key、模型、窗口与价格只在端点定义中出现一次。

## config.yml

顶层固定为：

```yaml
schema_version: 1
context: {}
skills: {sync: {}, sources: []}
subagents: {}
channels: {}
hooks: {version: 1, entries: []}
mcp: {servers: {}}
logging: {}
```

中央加载器只负责安全解析一次YAML并返回各section；现有模块继续负责本领域严格校验。一个可选section错误只降级对应能力，不能让无关CLI/Web聊天失效；核心Context或Models错误按现有安全边界阻止启动。

## 依赖与安全

- App读取文件并注入各模块；AgentLoop不读取配置文件。
- `${ENV_VAR}`继续由统一Secret解析器展开，日志不得输出值或环境变量名。
- Session JSONL、SQLite、绑定状态和Prompt不属于配置，不移动。
- 不新增`zcagent config migrate`。本次直接转换当前工作区；`zcagent init`以后只生成两个主文件。
- 新运行时不读取旧文件；旧文件由本次施工确认新文件有效后移出活跃配置目录，不做静默兼容。

## 变更与测试

新增统一section加载器与Models schema，改造Context、Embedding、Skill、Subagent、Channel、Hook、MCP和CLI/Web装配；更新init模板、启动诊断、错误文案与`test_case.md`。覆盖endpoint/model、未知路由、Secret缺失、各section正常/异常/缺失、可选能力隔离、init真实产物、当前工作区启动、Ruff、相关回归和全量pytest。

## 验收标准

- 活跃工作区除`.env`外只有`models.json`和`config.yml`两份配置。
- 用户在`models.json.routing`一处看清Chat/Compaction/Embedding实际选择。
- `config.yml`按层级看清所有运行策略。
- 不存在迁移命令、旧文件懒读取或AgentLoop配置判断。

## 落地结果

- 新运行时只读取`config/models.json`与`config/config.yml`；旧文件即使仍存在也不会被读取。
- `routing.chat`、`routing.compaction`、`routing.embedding`均支持`endpoint`与`endpoint/model`。
- Compaction费用从实际成功LLM端点的`pricing`写入usage trace；Embedding批次从实际Embedding端点的`batch_size`读取。
- `zcagent init`只生成两个主配置文件并非覆盖式补齐Prompt；未新增迁移命令。
- 配置、Part 15、CLI/App/Channel/Skill/Subagent/Hook/MCP与全量测试通过；全量结果为`747 passed, 1 skipped`。
- 当前真实工作区路由验证为Chat=`cpa_one/gpt-5.4`、Compaction=`cpa_one/gpt-5.4`、Embedding=`default/text-embedding-v4`；真实CLI Session返回`CONFIG_OK`，进程正常退出，总耗时约7.7秒。
- 旧运行配置已移动到`config/legacy_backup_20260726/`，可恢复；`config/channels/`是微信账号运行状态目录，不属于业务配置文件，继续保留。
