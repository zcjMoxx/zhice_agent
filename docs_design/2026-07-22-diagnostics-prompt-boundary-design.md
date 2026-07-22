# ZhiCe-Agent 诊断 Prompt 边界设计

> 日期：2026-07-22
>
> 状态：已实现并进入当前代码基线
>
> 承接：`2026-07-22-direct-trace-evidence-diagnostics-design.md`

## 1. 背景

直接 Trace 证据诊断落地时，把 `diagnose_my_recent_activity` 的触发条件、Trace 字段优先级和归因限制写进了 `prompts/tool_use_policy.md`。这些内容属于诊断工具的专用行为，不是所有 Tool 共用的通用规则，导致 Prompt 职责混杂。

## 2. 目标

1. `tool_use_policy.md` 只描述通用 Tool discovery、真实调用、安全边界和失败处理。
2. 新增 `prompts/diagnostics.md`，承载诊断触发、证据读取和回答规则。
3. 主 ContextBuilder 可选加载 diagnostics Prompt；缺失时不阻断聊天。
4. Python 只保留诊断 Tool 的短 name/description/schema，不嵌入长诊断指令。

## 3. 加载规则

```text
identity + tool_use_policy + skills_intro  -> 核心必需
memory_policy                              -> 可选
diagnostics                                -> 可选
extra_system_prompts                       -> 可选
```

`zcagent init` 会随其它 Prompt 复制 `diagnostics.md`。已有 workspace 没有该文件时，主流程继续；Tool 的短 description 仍可被 discovery 找到，但完整诊断策略要在补齐 Prompt 后生效。

## 4. 变更文件

- 新增 `prompts/diagnostics.md`。
- `prompts/tool_use_policy.md` 删除诊断专用段落。
- `agent/core/context.py` 可选加载 diagnostics Prompt。
- Prompt、ContextBuilder、CLI init 测试与测试说明同步。
- README、总体设计、设计索引同步。

## 5. 测试与验收

- system prompt 中诊断规则位于独立 `# Diagnostics Policy` 段。
- `tool_use_policy.md` 不再出现诊断 Tool 名称或 Trace 字段细节。
- workspace 缺少 `diagnostics.md` 时 ContextBuilder 仍可正常构建。
- `zcagent init` 会生成 `prompts/diagnostics.md`。
- 全量 Ruff、pytest、JavaScript 与 diff check 通过。

## 6. 验证结果

- diagnostics Prompt 的可选加载、缺失不阻断、职责隔离和 init 复制专项测试：`76 passed`。
- `python -m ruff check .` 通过。
- `python -m pytest --basetemp .tmp/pytest_diagnostics_prompt_boundary_full`：`612 passed, 1 skipped`。
- 两个前端 JavaScript 文件的 `node --check` 与 `git diff --check` 通过。
