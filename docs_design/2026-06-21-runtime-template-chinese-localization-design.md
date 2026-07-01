# 运行时模板中文化设计

## 背景

`zcagent init` 会生成运行态配置模板和默认 prompt。当前部分模板注释、README 和默认 Markdown 说明仍使用英文，用户初始化后需要阅读这些内容并修改为自己的真实配置，因此应统一改为中文说明。

## 目标

- 将用户会直接阅读和修改的配置模板注释、默认 prompt 文档和 Skill 说明文档改为中文。
- CLI 终端提示保持英文，以便和现有命令行输出风格一致。
- 保留 `endpoint`、`model`、`api_key`、`Skill`、`workspace` 等配置字段、命令名和项目术语，不改变运行时 schema。
- 不修改历史设计文档中的技术回顾内容。

## 范围边界

- 本次只做文案本地化，不改变配置加载、Skill 同步、LLM Provider、ContextBuilder 或 AgentLoop 行为。
- JSON/YAML 字段名、环境变量名、命令示例保持不变。

## 变更文件

- `config/llm_endpoints.example.json`
- `config/skill_sources.example.yml`
- `prompts/tool_use_policy.md`
- `prompts/skills_intro.md`
- `skill_repo/skills/README.md`
- 相关单元测试断言

## 测试方案

- 运行 CLI 初始化相关测试，确认提示文案断言更新后通过。
- 运行 ruff 检查本次触及的 Python 文件。

## 验收标准

- `zcagent init` 成功提示为中文，并提醒用户修改真实运行配置。
- 默认模板和默认 prompt 中不再保留不必要的英文说明句。
- 不影响已有配置字段和命令使用方式。
