# Skill 同步默认刷新设计

> 说明：这是一份历史设计记录。当前代码仍采用本文的“`/skills sync` 默认刷新、`sync_skills` 不暴露刷新开关”结论；但 `skill_sources.yml` schema 已由 `docs_design/2026-06-30-skill-source-namespace-design.md` 收敛为 `name`、`sync`、`local_dir`、`git_url`、`target`，不再保留旧 source 字段讨论。

## 背景

Skill source 同步对用户来说应该是一个直接动作：执行 `/skills sync` 后，workspace 里的 Skill 就同步到配置来源的当前状态。额外暴露“是否刷新”的命令参数会让同一个动作看起来像两个不同命令，增加理解成本。

## 目标

- `/skills sync` 默认刷新配置来源。
- `sync_skills` 工具不暴露刷新开关。
- CLI 帮助和文档只保留 `/skills sync [--verbose] [source_name]`。

## 范围边界

- 本文当时不改变 `skill_sources.yml` schema；后续 2026-06-30 设计已收敛 schema，当前以新版 `local_dir`、`git_url`、`target` 语义为准。
- 不改变 git/local source 的同步边界。
- `--verbose` 仍只控制输出明细，`source_name` 仍用于只同步某个已配置 source。

## 变更文件

- `agent/skills/sync.py`
- `agent/cli.py`
- `agent/tools/skill.py`
- CLI、Skill tool 和文档测试说明

## 测试方案

- 运行 Skill 同步、Skill tool 和 CLI 聚焦测试。
- 运行 ruff 检查触及的 Python 文件。

## 验收标准

- `/skills sync` 本身就执行刷新同步。
- `/skills` tip 只出现 `/skills sync [--verbose] [source_name]`。
- `sync_skills` tool schema 不包含刷新开关。
