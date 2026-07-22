# ZhiCe-Agent 按角色展示可选能力错误设计

> 日期：2026-07-22
>
> 状态：已实现并进入当前代码基线
>
> 承接：`2026-07-22-human-command-error-presentation-design.md`

## 1. 背景

当前 `/subagent` 已从原始 JSON 改为自然语言，但所有身份都会看到真实 `CapabilityStatus.message/hint`。当缺少 `subagent.md` 时，普通 Web 用户会看到内部 Prompt 文件名、初始化命令和运行结构；公共 health、unavailable Tool facade 或诊断 Tool 也可能成为旁路。

CLI、本地操作者和 Owner 需要完整信息自行修复；普通用户只需要知道能力暂时不可用并联系管理员。

## 2. 目标

1. CLI、本地操作者、Owner 和具备内部审计权限的管理员保留详细 message/hint。
2. 普通用户的 `/subagent`、force-once 和 unavailable Tool 只返回通用文案。
3. 公共 health 只暴露 capability 名称和通用状态，不暴露内部 cause、文件名、路径或修复命令。
4. 普通用户通过诊断 Tool 查询 Subagent runtime/config 失败时，同样得到通用结论。
5. 终端、Trace、启动告警和 Owner 诊断继续保留真实 cause code，不能为了展示脱敏而删除证据。

## 3. 展示规则

详细身份：

```text
actor_type == local_operator
or role_keys contains owner
or permission_keys contains audit.read
```

普通用户文案：

```text
Subagent is temporarily unavailable. Please contact an administrator.
```

## 4. 出口

- CLI `/subagent` 和启动 warning：详细。
- Owner Web `/subagent`、force-once：详细。
- 普通 Web `/subagent`、force-once：通用。
- `delegate_tasks` unavailable facade：按 ToolExecutionContext.actor 分层。
- 公共 `/health`、`/api/health`：只返回通用 capability 状态。
- `diagnose_my_recent_activity`：普通用户遇到 Subagent 内部 cause 时隐藏 cause/evidence；Owner 保留完整报告。

## 5. 测试

- presentation helper 覆盖详细与通用两种模式。
- Web command 和 force-once 分别覆盖 Owner/普通用户。
- unavailable Tool 覆盖 actor-aware 输出。
- health 不包含 Prompt 文件名、路径和内部 cause code。
- 普通诊断不返回 Subagent cause code，Owner 仍返回。
- 全量 Ruff、pytest、JS 与 diff check 通过。

## 6. 实现结果

- `agent/subagents/presentation.py` 集中判断详细信息可见身份并生成人类文案。
- CLI 始终保留详细原因；Web `/subagent` 与 force-once 按当前 `ActorContext` 分层。
- unavailable `delegate_tasks` facade 对普通用户只返回通用错误，同时把真实 cause 写入内部日志与 trace。
- 公共 health 仅返回 capability 的通用状态；普通用户诊断隐藏内部 cause、证据和修复命令。
- Owner、本地操作者和具备 `audit.read` 的管理员仍可查看完整诊断原因。
