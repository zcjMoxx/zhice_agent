# ZhiCe-Agent 开发规范

> 本规范用于约束 ZhiCe-Agent 的日常开发，优先服务轻量、清晰、可逐步演进的 Agent 内核。

## 1. 核心边界

- AgentLoop 只负责通用循环：加载上下文、调用 LLM、调度工具、回填工具结果、保存会话。
- 不在 AgentLoop 里硬编码业务判断。业务能力优先做成 Tool 或 Skill。
- Session 是上下文的一部分，不只是日志；所有会话消息必须通过 SessionStore 读写。
- LLM 调用必须经过 `LLMProvider` 协议接口，AgentLoop 禁止直接依赖 OpenAI、LiteLLM 或其他 SDK。

## 2. 依赖方向

保持单向依赖：

```text
cli/app -> agent core -> protocols
tools   -> protocols/message/base types
skills  -> no agent imports
```

规则：

- `agent/protocols/` 只放接口和数据结构，禁止 import 具体实现。
- `agent/loop.py` 消费能力时走 `LLMProvider`、`ToolProvider`、`SkillProvider`、`SessionStore`。
- `agent/tools/` 内的 LLM 可调用工具禁止互相 import；公共逻辑放 `base.py` 或明确的 shared helper。
- `skills/*/scripts/` 禁止 import `agent.*`，建议通过 `--params`、环境变量、文件或外部 API 通信。

## 3. Prompt 管理

- 所有会进入 LLM messages 的长文本指令必须放在 `prompts/*.md`。
- Skill 说明必须放在 Skill source 的 `skills/{name}/SKILL.md`；内置官方 source 位于 `skill_repo/skills/{name}/SKILL.md`，运行时同步到 `${ZHICE_AGENT_WORKSPACE}/extends/{source}/skills/{name}/SKILL.md`。
- Python 代码里只允许保留短 tool name、短 description、极短 fallback 文案。
- 新增 prompt 不在文件名里写 v1/v2，演进交给 git 记录。

## 4. 配置与路径

- 所有运行路径从 `ZHICE_AGENT_WORKSPACE` 派生。
- 运行态配置文件统一放在 `${ZHICE_AGENT_WORKSPACE}/config/`；仓库 `config/` 只放模板或启动用示例。
- 仓库不提交真实 Secret；仓库只提交 `config/.env.example` 和不含真实 key 的示例配置。
- 本地开发可在 `${ZHICE_AGENT_WORKSPACE}/config/llm_endpoints.json` 写入 `api_key`，因为工作目录不属于仓库。
- Docker、云部署、CI 等环境优先通过 `.env`、env-file 或平台 Secret 注入 Secret；`${ZHICE_AGENT_WORKSPACE}/config/llm_endpoints.json` 里仍统一使用 `api_key`，可写明文，也可写 `${ENV_VAR}` 占位。
- 文件、工具、exec 默认限制在 workspace 内，禁止默认访问 workspace 外路径。

## 5. Tool 规范

- Tool 必须声明 `name`、`description`、`parameters`，并返回统一 `ToolResult`。
- Tool 失败要返回结构化错误，不把异常直接抛给 AgentLoop。
- `exec` 必须有 workspace guard、超时、输出截断和危险命令拦截。
- 破坏性操作必须能从用户请求中明确确认，不能由模型自行猜测执行。

## 6. Skill 规范

Skill source 内的 Skill 最小结构：

```text
skills/{skill_name}/
  SKILL.md
  scripts/{entry}.py
```

`SKILL.md` 必须包含：

- frontmatter：`name`、`description`
- 参数表
- 完整可执行示例
- 返回格式
- 错误码和重试策略
- 边界情况和不适用场景

脚本规范（通过 `exec` 按 `SKILL.md` 示例执行）：

- 推荐通过 `--params '{JSON}'` 接收输入。
- 推荐 stdout 最后一行输出 JSON。
- 返回字段固定为 `status`、`code`、`data`、`message`、`error_stack`。
- `except Exception` 时填 `traceback.format_exc()[:1500]`。
- 禁止 import `agent.*`，禁止 Skill 之间互相 import。

## 7. 测试规范

- 新模块必须配单元测试；涉及 AgentLoop、工具、Skill、Session 的改动必须覆盖正常路径、异常路径和边界条件。
- 新增或扩展 `tests/unit_test/{topic}` 测试主题目录时，同目录必须维护 `test_case.md`，说明测试目标、用例覆盖和关键检查点；不要求每个测试文件单独配说明。
- AgentLoop 优先用 Fake LLM 做稳定测试，真实 LLM 冒烟测试用环境变量显式开启。
- E2E 测试必须走真实入口和真实调用链；不要直接 import 内部实现绕过边界。
- 提交前至少运行：

```bash
python -m ruff check .
python -m pytest
```

如果存在与本次无关的历史失败，需要在交付说明里写明。

## 8. 设计先行

设计文档分两类：

- 当前活文档：无日期文件名，例如 `docs_design/zhice-agent-overall-design.md` 和 `docs_design/zhice-agent-part*.md`，始终以最新代码和当前阶段口径为准。
- 日期设计记录：`docs_design/YYYY-MM-DD-*.md`，记录当次设计背景、权衡和变更方案；设计完成并落地后原则上不再改写方案内容。

同一日期、同一功能且代码尚未落地时，讨论中的方案调整直接更新当天同一份日期设计记录，不为每次口径变化重复创建文件。跨日期继续迭代时，按新日期创建记录；旧日期正文保留，并在标题下方补充说明指向新记录。

以下情况先写新的日期设计记录：

- 涉及 3 个及以上文件修改。
- 新增核心模块或协议接口。
- 改变 AgentLoop、Tool、Skill、Session、配置加载的边界。
- 引入新的运行时依赖或外部服务。

设计文档至少包含：背景、目标、范围边界、模块设计、数据流、变更文件、测试方案、验收标准。

代码落地后，如果该方案成为当前主线，再同步更新总体设计或对应 Part 活文档。代码落地后的新变化不要回头重写旧日期设计记录，而是在新日期设计记录的背景里说明旧方案的不足和本次改进。

如果后续设计已经改变了旧日期设计记录的方案，只在旧文档标题下方补一段 `> 说明：...`，说明当前代码采用什么、旧方案哪里不再适用、应参考哪份新文档或当前活文档；旧文档正文保持当时方案原貌。

## 9. 暂不纳入第一阶段的内容

ZhiCe-Agent 第一阶段先保持轻量，暂不纳入：

- 多用户和多渠道隔离。
- Skill 市场、审批流、自进化。
- 复杂容器编排、发布流水线、多环境 overlay。
- 重型 E2E 覆盖率硬指标。
- 多层扩展仓库和用户私有覆盖优先级。

这些以后需要时再按模块引入。
