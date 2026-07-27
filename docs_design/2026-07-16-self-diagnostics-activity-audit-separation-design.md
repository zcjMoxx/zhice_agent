# 自助诊断与 Runtime Activity / Security Audit 拆分设计

> 说明：本文“后续部分”中的旧编号已被 2026-07-27 路线调整替代。当前 Part 16 是 Web 产品体验与 Vue 前端工程，只提供已有 health/Activity 真值的监控展示面；系统级诊断引擎、事故聚合和完整时间线归入 Part 17，参考 `docs_design/zhice-agent-part16-web-product-design.md` 与 `docs_design/zhice-agent-overall-design.md`。

> 日期：2026-07-16
> 状态：已确认，进入实现

## 1. 背景

当前 `diagnose_my_recent_activity` 只接收 actor 和模型传入的时间、Session、Turn、事件类型，默认返回当前用户最近 30 分钟的 trace/audit 事件。模型通常不知道内部 `turn_id`、`request_id`，因此用户询问“刚刚为什么慢”“刚才为什么出错”时只能从一批事件中猜测目标。

当前 `AuditSink` 还同时承担两种职责：

- 写入安全审计账本 `audit_events`。
- 根据普通 turn/tool audit event 维护 `turn_runs`、`tool_call_records`。

这导致正常聊天、普通安全工具成功等运行流水也进入 audit。Runtime activity 和 security audit 需要拆分。

## 2. 当前实现目标

1. 普通聊天中的自助诊断 Tool 自动使用当前 Session，并排除正在执行的诊断 Turn。
2. 模型只需要表达 `latency`、`failure`、`trend` 等自然诊断意图，不需要知道内部关联 ID。
3. 优先从 `turn_runs`、`tool_call_records` 定位目标，再用相关 trace 补充证据，不把整段 trace 直接交给模型。
4. `turn_runs`、`tool_call_records` 改由独立 Runtime Activity sink 维护，不再依赖 AuditSink。
5. audit 只保留安全、管理、跨用户、危险执行和确认等事件；普通 turn 成功、普通工具请求/允许/成功不再写 audit。
6. 系统级诊断 Tool、Developer/Admin/Owner 监控聊天框与全系统 trace 查询只写入后续 Part 16/17/18，本次不实现。

## 3. 不删除的事件类型

### 3.1 Trace event

继续保留 turn、LLM、tool、session、gateway 技术运行轨迹。它是诊断证据源，不是安全审计。

### 3.2 Runtime activity record

继续保留：

- `turn_runs`
- `tool_call_records`

它们是自助诊断和后续监控平台的结构化查询索引。

### 3.3 Client/runtime stream event

WebSocket/SSE 的 `text_delta`、`done`、`error`、`tool_confirmation_required` 等协议事件保持不变。

## 4. Audit 保留范围

继续写入 audit：

- 登录、登出、注册、改密和账号状态变化。
- 用户、角色、特权管理。
- 认证失败、特权拒绝和跨用户访问。
- 危险工具请求、确认、拒绝和最终结果。
- Memory 持久化修改的安全摘要。
- Session 删除等安全相关持久化操作。
- audit 读取/导出和未来系统级诊断查询。

不再写入 audit：

- `chat.turn_started`、`chat.turn_done` 等普通运行流水。
- 普通安全工具的 requested/allowed/done。
- 普通成功 HTTP 请求。

普通失败、耗时和执行结果进入 Runtime Activity 与 trace。只有失败本身构成安全决策时才进入 audit。

## 5. 新协议

```python
@dataclass(frozen=True)
class RuntimeActivityEvent:
    action: str
    actor: ActorContext | None
    request_id: str = ""
    channel: str = ""
    session_id: str = ""
    turn_id: str = ""
    resource_id: str = ""
    tool_call_record_id: str = ""
    decision: str = ""
    reason_code: str = ""
    risk_category: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

class RuntimeActivitySink(Protocol):
    def record(self, event: RuntimeActivityEvent) -> None: ...
```

`AgentLoop` 同时消费 `RuntimeActivitySink` 和 `AuditSink`。Activity sink 只维护运行记录；AuditSink 只保存安全账本。

## 6. 自助诊断上下文

```python
@dataclass(frozen=True)
class DiagnosticContext:
    session_id: str
    current_turn_id: str
    current_request_id: str = ""
    channel: str = ""
```

`WebRuntime` 创建当前 turn 后，把该上下文注入 `UserScopedToolProvider` 和 `DiagnoseRecentActivityTool`。

## 7. Tool 参数

```json
{
  "focus": "auto",
  "target": "auto",
  "minutes": 30
}
```

允许值：

```text
focus: auto | latency | failure | trend
target: auto | previous_turn | latest_failure | recent_activity
```

不要求模型传入 `session_id`、`turn_id`、`request_id`。这些字段由后端自动解析。

## 8. 自动目标选择

```text
latency
  -> 当前 Session 上一条已完成 Turn

failure
  -> 优先上一条 Turn；若无失败，查当前 Session 最近一次失败

trend
  -> 当前 Session 最近 minutes 分钟失败记录

auto
  -> 上一条已完成 Turn；存在失败则诊断失败，否则返回耗时和阶段摘要
```

当前诊断 Turn 必须排除，避免 Tool 诊断自己正在运行的请求。

## 9. 证据与报告

优先查询：

```text
turn_runs
  -> 目标 turn、状态、开始/结束、request_id

tool_call_records
  -> 工具、耗时、错误码、timeout、安全输出尾部

相关 trace
  -> LLM 调用、Provider、Session 保存和缺失细节
```

Tool 返回：

```json
{
  "status": "diagnosed",
  "focus": "failure",
  "summary": "上一轮失败发生在 exec 工具执行阶段。",
  "failure_stage": "tool.exec",
  "cause_code": "COMMAND_TIMEOUT",
  "confirmed_facts": [],
  "probable_cause": "命令在超时时间内没有完成。",
  "confidence": "high",
  "evidence": [],
  "next_actions": [],
  "limitations": []
}
```

证据不足时返回 `insufficient_evidence`，不猜测。

## 10. LLM 耗时证据

为支持“刚刚为什么慢”，`AgentLoop` 增加 `llm.done` / `llm.error` 的 `duration_ms`、Session/Turn/request/channel 关联字段。Turn 总耗时和 Tool 耗时继续保留。

## 11. 后续部分

### Part 16

- 系统级诊断引擎。
- Runtime activity/event 进一步收敛。
- LLM/Tool/Session 完整耗时分解。
- Provider retry/failover 诊断和趋势聚合。

### Part 17

- Developer/Admin/Owner 可进入的 Web 监控与诊断平台。
- 独立诊断聊天框、事故列表、时间线和用户/组件筛选。
- `diagnostics.system.use` 特权和 `diagnose_system_activity` Tool。

### Part 18

- CLI local operator / Owner workspace 诊断入口。
- `zcagent diagnose`、配置体检、Skill source 和 endpoint 运维诊断。

## 12. 测试

- Activity sink 维护 turn/tool records，但不写 audit row。
- 普通 turn 和安全工具成功不进入 audit。
- 危险工具、确认和权限拒绝继续进入 audit。
- 自助诊断自动选择当前 Session 上一条 Turn。
- 当前诊断 Turn 被排除。
- 最近多请求时不跨 Session 误选。
- Tool timeout、普通 Tool error、Turn error 和 latency 报告。
- 普通用户不能诊断其他用户。
- 输出不包含 secret、完整参数或完整 trace。

## 13. 验收标准

1. `turn_runs`、`tool_call_records` 不再由 `record_audit()` 隐式维护。
2. Audit 表不再包含普通 turn 成功和普通安全工具成功流水。
3. 用户询问近期问题时，模型不需要内部 ID 即可获得当前 Session 的结构化诊断。
4. 全量测试、Ruff 和 diff check 通过。
