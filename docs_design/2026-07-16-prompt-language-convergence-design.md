# Prompt 语言统一设计

> 说明：本文列出的 `prompts/memory_summary.md` 已随未闭环 Session Summary 能力删除；其它 Prompt 语言规则仍然适用，参考 `docs_design/2026-07-16-remove-unclosed-session-summary-design.md`。

> 状态：已确认并落地。

## 1. 背景

当前运行 Prompt 存在按子系统分裂的语言状态：`identity.md`、`tool_use_policy.md` 和 `skills_intro.md` 使用中文，Memory policy、自动提取和摘要 Prompt 使用英文。它们最终会被组合进入同一运行链路，维护者需要在中英文之间切换，也更难及时发现规则冲突。

## 2. 目标

- 所有面向维护者阅读的自然语言规则统一使用中文。
- Tool 名、参数名、JSON 字段、错误码、Markdown 固定标题等程序协议继续使用英文。
- 不同时维护中英文两套运行 Prompt，避免长期漂移。
- 不改变 Memory Tool、Extractor 和 Session Summary 的输出协议。

## 3. 规则

统一写法：

```text
自然语言解释、约束、边界 -> 中文
tool name / parameter / enum -> 英文原值
JSON key / schema / example -> 英文原值
固定解析标题 -> 英文原值
用户可见示例回答 -> 中文
```

例如：

```markdown
只返回以下 JSON，不要返回 Markdown：

{"memories": [...]}

- `category` 只允许 `profile`、`preferences`、`constraints`。
- `confidence` 只允许 `high`。
```

这属于中文规则配合英文协议标识，不属于无规则的语言混用。

## 4. 变更范围

- `prompts/memory_policy.md`
- `prompts/memory_extraction.md`
- `prompts/memory_summary.md`
- Prompt 内容断言测试与 Part 10 活文档。
- 实际 workspace 下对应 Prompt 同步更新。

## 5. 非目标

- 不翻译 tool name、字段名、category enum 或错误码。
- 不修改 `# Session Summary`、`## Goal` 等解析器依赖的固定标题。
- 不创建 `prompts_zh-CN/` 或英文/中文双份文件。
- 本次不修改 Memory ID、时间和来源存储协议；该问题需要单独确认后设计。

## 6. 验收标准

1. Memory 三个 Prompt 的自然语言规则全部使用中文。
2. JSON、Tool 和固定 Markdown 协议保持兼容。
3. Workspace Prompt 与仓库 Prompt 一致。
4. 相关测试、Ruff 和全量测试通过。
