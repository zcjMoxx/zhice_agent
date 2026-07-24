# 2026-07-24 Session 清空命令改名设计

> 状态：已实现并同步到当前代码、测试和活文档。

## 背景

当前 CLI、Web 和 QQ 私聊使用 `/reset` 清空当前 Session 历史，但 QQ 群帮助没有展示该命令，实际命令分发又允许群聊执行 `/reset`，造成展示与行为不一致。同时，`reset` 容易与 `/model reset` 等“恢复默认值”语义混淆。

## 目标

- 将清空当前 Session 历史的顶层命令从 `/reset` 统一改名为 `/clear`。
- CLI、Web、external WebSocket、QQ 私聊和 QQ 群使用同一个命令名和同一套清空语义。
- QQ 群 `/help` 明确展示 `/clear`。
- 旧 `/reset` 不再作为兼容别名，统一返回未知命令提示。

## 范围边界

- 只修改顶层 Session 清空命令，不改变 `/model reset` 的模型偏好恢复语义。
- `/clear` 仍清空当前 Session 消息，并清理当前 Session 的 Subagent `force-once` 状态；不创建新 Session。
- `/new` 继续创建并切换到新 Session。
- `/sessions delete` 不带 Session ID 时，仍清空当前 Session，但帮助提示改为引用 `/clear`。
- 历史日期设计记录保留当时使用 `/reset` 的原始方案，不回写旧正文。

## 模块设计

### 公共 Runtime

`WebRuntime.handle_command()` 将顶层 `reset` 分支改为 `clear`。所有经过公共 Runtime 的 Web、external WebSocket、QQ 私聊和 QQ 群统一获得新语义，避免各渠道重复实现。

### CLI

CLI 输入循环与帮助列表同步将 `/reset` 改为 `/clear`，继续调用现有 Session 清空逻辑。

### QQ 群帮助

`qq_group` 命令 Profile 的帮助列表新增 `/clear`。命令执行仍进入公共 Runtime，不在 QQ Adapter 内复制清空逻辑。

## 数据流

```text
/clear
  -> channel or CLI command dispatch
  -> clear current Session messages
  -> clear Subagent force-once state
  -> keep the current session_id
  -> return Session cleared
```

## 变更文件

- `agent/app/runtime.py`
- `agent/cli.py`
- `README.md`
- 当前相关 Part 活文档与 `docs_design/README.md`
- `tests/unit_test/app/`、`tests/unit_test/cli/` 及对应 `test_case.md`

## 测试方案

- Runtime `/clear` 清空当前 Session，并保持原有清理附属状态的行为。
- QQ 群 `/help` 展示 `/clear`，执行 `/clear` 不进入 LLM。
- CLI `/clear` 清空持久化 Session。
- `/reset` 在公共 Runtime 和 CLI 中不再执行清空。
- `/model reset` 保持原行为。
- 运行 Ruff、Runtime 命令测试、CLI 测试和渠道测试。

## 验收标准

- 所有当前渠道对 Session 清空统一使用 `/clear`。
- QQ 群帮助与实际可执行命令一致。
- `/new`、`/stop`、`/model reset` 和 `/sessions delete` 的既有语义不变。
- 旧 `/reset` 不再清空 Session。
- 相关测试通过。
