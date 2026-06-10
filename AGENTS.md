# Zhice-Agent 开发规范

> 本规范用于约束 Zhice-Agent 的日常开发，优先服务轻量、清晰、可逐步演进的 Agent 内核。

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
- `skills/*/scripts/` 禁止 import `agent.*`，只能通过 `--params`、环境变量、文件或外部 API 通信。

## 3. Prompt 管理

- 所有会进入 LLM messages 的长文本指令必须放在 `prompts/*.md`。
- Skill 说明必须放在 `skills/{name}/SKILL.md`。
- Python 代码里只允许保留短 tool name、短 description、极短 fallback 文案。
- 新增 prompt 不在文件名里写 v1/v2，演进交给 git 记录。

## 4. 配置与路径

- 所有运行路径从 `ZHICE_AGENT_WORKSPACE` 派生。
- 配置文件统一放在 `config/`。
- Secret 不进仓库，只放环境变量；仓库只提交 `.env.example`。
- 文件、工具、exec 默认限制在 workspace 内，禁止默认访问 workspace 外路径。

## 5. Tool 规范

- Tool 必须声明 `name`、`description`、`parameters`，并返回统一 `ToolResult`。
- Tool 失败要返回结构化错误，不把异常直接抛给 AgentLoop。
- `exec` 必须有 workspace guard、超时、输出截断和危险命令拦截。
- 破坏性操作必须能从用户请求中明确确认，不能由模型自行猜测执行。

## 6. Skill 规范

Skill 最小结构：

```text
skills/{skill_name}/
  SKILL.md
  scripts/{entry}.py
```

`SKILL.md` 必须包含：

- frontmatter：`name`、`description`、`category`、`readonly`
- 参数表
- 完整可执行示例
- 返回格式
- 错误码和重试策略
- 边界情况和不适用场景

脚本规范：

- 通过 `--params '{JSON}'` 接收输入。
- stdout 最后一行输出 JSON。
- 返回字段固定为 `status`、`code`、`data`、`message`、`error_stack`。
- `except Exception` 时填 `traceback.format_exc()[:1500]`。
- 禁止 import `agent.*`，禁止 Skill 之间互相 import。

## 7. 测试规范

- 新模块必须配单元测试；涉及 AgentLoop、工具、Skill、Session 的改动必须覆盖正常路径、异常路径和边界条件。
- AgentLoop 优先用 Fake LLM 做稳定测试，真实 LLM 冒烟测试用环境变量显式开启。
- E2E 测试必须走真实入口和真实调用链；不要直接 import 内部实现绕过边界。
- 提交前至少运行：

```bash
python -m ruff check .
python -m pytest
```

如果存在与本次无关的历史失败，需要在交付说明里写明。

## 8. 设计先行

以下情况先写 `docs_design/YYYY-MM-DD-*.md`：

- 涉及 3 个及以上文件修改。
- 新增核心模块或协议接口。
- 改变 AgentLoop、Tool、Skill、Session、配置加载的边界。
- 引入新的运行时依赖或外部服务。

设计文档至少包含：背景、目标、范围边界、模块设计、数据流、变更文件、测试方案、验收标准。

## 9. 暂不纳入第一阶段的内容

Zhice-Agent 第一阶段先保持轻量，暂不纳入：

- 多用户和多渠道隔离。
- Skill 市场、审批流、自进化。
- 复杂容器编排、发布流水线、多环境 overlay。
- 重型 E2E 覆盖率硬指标。
- 多层扩展仓库和用户私有覆盖优先级。

这些以后需要时再按模块引入。
