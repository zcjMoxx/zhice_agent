# ZhiCe-Agent Exec Prompt 边界设计

> 日期：2026-07-22
>
> 状态：已实现并进入当前代码基线
>
> 承接：`2026-07-22-diagnostics-prompt-boundary-design.md`

## 1. 背景

诊断专用规则已经从通用 `tool_use_policy.md` 拆到 `prompts/diagnostics.md`。当前 `tool_use_policy.md` 仍包含独立的 `exec 使用规则`，其中的命令选择、危险操作、shell 约束和失败处理同样属于单个 Tool 的专用行为，不属于所有 Tool 的通用策略。

## 2. 目标

1. `tool_use_policy.md` 只保留跨 Tool 通用的发现、真实调用、失败处理和基础安全原则。
2. 新增 `prompts/exec.md`，承载 Exec 的使用场景、命令约束、风险确认和结果解释规则。
3. 主 ContextBuilder 将 Exec policy 作为可选独立段加载；缺失不阻断聊天。
4. Exec 的真正安全边界继续由代码强制执行，Prompt 不能替代 guard。

## 3. 强制安全边界

无论 `exec.md` 是否存在，以下边界必须继续由运行时代码执行：

- workspace guard；
- RBAC 与危险操作确认；
- Hook；
- timeout、输出截断和危险命令拦截；
- 环境变量、路径、网络与破坏性命令限制。

## 4. 加载顺序

```text
Identity
Tool Use Policy
Skill Use Policy
Memory Policy      (optional)
Diagnostics Policy (optional)
Exec Policy         (optional)
extra prompts       (optional)
Runtime
```

`zcagent init` 会自动复制仓库中的 `exec.md`。已有 workspace 缺少该文件时再次运行普通 `zcagent init` 即可补齐，不覆盖其它已有 Prompt。

## 5. 测试

- `tool_use_policy.md` 不再包含 `exec 使用规则` 或 Exec 专用命令约束。
- system prompt 存在 `exec.md` 时追加独立 `# Exec Policy`。
- 缺少 `exec.md` 时聊天不阻断。
- `zcagent init` 生成 `prompts/exec.md`。
- 全量 Ruff、pytest、JavaScript 和 diff check 通过。

## 6. 验证结果

- Exec Prompt 的可选加载、缺失不阻断、职责隔离和 init 复制专项测试：`79 passed`。
- `python -m ruff check .` 通过。
- `python -m pytest --basetemp .tmp/pytest_exec_prompt_boundary_full`：`615 passed, 1 skipped`。
- 两个前端 JavaScript 文件的 `node --check` 与 `git diff --check` 通过。
