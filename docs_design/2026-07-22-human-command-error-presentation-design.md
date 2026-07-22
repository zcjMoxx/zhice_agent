# ZhiCe-Agent 人类命令错误与机器载荷分层设计

> 日期：2026-07-22
>
> 状态：已实现并进入当前代码基线
>
> 承接：`docs_design/zhice-agent-part13-subagent-design.md`、`docs_design/2026-07-21-startup-capability-and-subagent-diagnostics-design.md`

## 1. 背景

Subagent unavailable 时，协议层提供结构化载荷：

```json
{
  "code": "SUBAGENT_RUNTIME_UNAVAILABLE",
  "cause_code": "SUBAGENT_PROMPT_NOT_FOUND",
  "message": "...",
  "hint": "..."
}
```

该结构适合 ToolResult、诊断和 API，但 CLI/Web 的 `/subagent` 命令直接 `json.dumps()` 后返回，导致聊天界面显示原始 JSON。其它人类命令通常返回一句可读说明，这里违反了命令展示的一致性，也暴露了不必要的协议形态。

## 2. 目标

1. CLI/Web `/subagent` unavailable 返回简洁、可读的自然语言。
2. `/subagent once` 在下一条消息开始前发现 capability unavailable 时也返回同样的人类提示。
3. `delegate_tasks` unavailable facade、ToolResult、trace、health 和诊断继续保留结构化 code/cause_code。
4. 不在 AgentLoop 中增加展示判断。

## 3. 范围边界

本次修改命令和聊天展示层，不改变：

- `subagent_unavailable_payload()` 协议。
- `delegate_tasks` Tool schema 与 ToolResult。
- capability health 数据。
- 启动 WARNING 和 trace 字段。
- Subagent 的启用、Profile、权限或执行语义。

## 4. 模块设计

新增 `agent/subagents/presentation.py`，只依赖 `CapabilityStatus`，提供共享的人类提示格式：

```text
Subagent is currently unavailable: {message} {hint}
```

CLI 可在外层应用终端 warning 颜色，Web 直接作为聊天文本返回。稳定错误码保留在机器接口和日志中，不强制展示给普通用户。

## 5. 数据流

```text
/subagent command
  -> CapabilityStatus
  -> human presentation formatter
  -> readable CLI/Web text

delegate_tasks unavailable facade
  -> subagent_unavailable_payload
  -> structured ToolResult with code/cause_code/message/hint
```

## 6. 变更文件

- `agent/subagents/presentation.py`
- `agent/app/runtime.py`
- `agent/cli.py`
- `tests/unit_test/app/*`
- `tests/unit_test/cli/*`
- `tests/unit_test/subagents/*`
- `docs_design/README.md`
- `docs_design/zhice-agent-part13-subagent-design.md`

## 7. 测试方案

- Web `/subagent` unavailable 不包含 `{`、`cause_code` 或原始 JSON，包含 message 和 hint。
- CLI `/subagent` unavailable 使用相同人类文案。
- unavailable facade 的 ToolResult 仍包含 `SUBAGENT_RUNTIME_UNAVAILABLE` 和具体 `cause_code`。
- 全量 Ruff、pytest、前端语法和 diff 检查。

## 8. 验收标准

1. 浏览器输入 `/subagent` 不再返回 JSON。
2. 用户仍能直接看到真实原因和修复建议。
3. 诊断与模型调用所需结构化证据不丢失。

## 9. 实现结果

- 新增共享 `agent/subagents/presentation.py`，CLI/Web `/subagent` 和 force-once unavailable 路径统一返回人类可读 message/hint。
- 命令输出不再包含 JSON、`SUBAGENT_RUNTIME_UNAVAILABLE`、`cause_code` 或内部 payload 形态。
- `delegate_tasks` unavailable facade、health、trace 和诊断继续使用原结构化 payload，真实 code/cause_code 未丢失。
- 验证结果：`python -m ruff check .` 通过；`python -m pytest --basetemp .tmp/pytest_subagent_presentation_final` 为 `585 passed, 1 skipped`；两个前端 JavaScript 文件通过 `node --check`；`git diff --check` 通过。
