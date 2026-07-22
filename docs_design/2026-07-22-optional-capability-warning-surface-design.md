# ZhiCe-Agent 可选能力启动告警出口收敛设计

> 说明：后续启用状态语义进一步收敛为“可选扩展未配置即 disabled、不报警；内置 Memory extraction 缺运行 Prompt 才报警”。当前口径见 `docs_design/2026-07-22-built-in-capability-enable-state-design.md`，本文其余正文保留当时方案原貌。

> 日期：2026-07-22
>
> 状态：已实现并进入当前代码基线
>
> 承接：`docs_design/2026-07-21-startup-capability-and-subagent-diagnostics-design.md`

## 1. 背景

2026-07-22 的本地 Web 验证发现，同一次 Gateway 启动中的可选能力缺失使用了两套输出链路：

- 缺少 `skill_sources.yml` 由 `agent/cli.py` 在 Gateway 日志初始化前直接 `print`，没有时间、级别、logger 和稳定事件字段，也不能进入同一份 trace。
- 缺少 `subagent.md` 由 Subagent startup checker 通过结构化日志输出，带时间、WARNING、事件名和错误码，并进入 trace。
- Web 又从 `/api/health` 读取 capability 状态并显示常驻 banner，导致同一运维问题同时出现在终端和聊天页面，而且页面提示不会自动消失。

可选能力不可用属于启动和运维事实，不应长期占用聊天产品界面。浏览器仍可通过命令得到与当前操作有关的精确错误，诊断工具仍需要机器可读 health 和 trace。

## 2. 目标

1. Gateway 的可选能力启动异常统一通过结构化 WARNING 输出到终端和 `trace.log`。
2. 移除静态 Web 的 capability 常驻 banner，不再主动把启动告警展示给聊天用户。
3. 保留 `/api/health.capabilities`，供诊断、自动化检查和后续管理界面使用。
4. 保留 `/subagent`、`delegate_tasks` unavailable facade 等使用期精确反馈。
5. 正常未配置且语义为 `disabled` 的能力继续不报警。

## 3. 范围边界

本次修改：

- 移除 `web/static` 中 capability banner 的 HTML、CSS 和启动请求逻辑。
- Gateway 不再在结构化日志初始化前打印 Skill source 缺失提示。
- Web runtime 将 Skill startup sync 和 Skill loader 失败收敛为单个结构化事件，避免同因重复告警。
- 更新 README、总体设计和 Part 13 当前口径。

本次不修改：

- `/api/health` 的 capability 数据结构。
- CLI 交互入口的人工可读初始化引导。
- 核心依赖的启动阻断规则。
- 单次 Tool、Skill、MCP 或 Subagent 调用的错误返回协议。

## 4. 模块设计

### 4.1 Web 展示边界

静态 Web 不再调用 `showStartupCapabilityStatus()`，也不渲染 `capabilityBanner`。Capability health 是机器接口，不再默认映射为聊天页常驻提示。

### 4.2 Gateway Skill 启动告警

`zcagent gateway` 先进入 `run_gateway()`，完成 Gateway 结构化日志 handler 配置，再由 `build_web_runtime()` 检查 Skill source。

Skill startup 分两步但只记录一次同因告警：

```text
sync_on_startup()
  -> 保存错误，不立即重复记录
skill_roots()
  -> 同一配置错误：skills.runtime_unavailable
  -> roots 可用但同步失败：skills.sync_degraded
```

缺少配置时使用稳定字段：

```text
event=skills.runtime_unavailable
code=SKILL_SOURCE_CONFIG_MISSING
config_file=skill_sources.yml
message="Skill source configuration is missing."
hint="Run zcagent init, then restart the process."
```

绝对路径、credential 和底层异常正文不进入日志字段。

### 4.3 状态与日志关系

- `disabled`：正常关闭，不记 WARNING。
- `unavailable`：能力级输入非法或缺失，记一次结构化 WARNING。
- `degraded`：部分同步或外部连接失败，但已有能力仍可用，记一次结构化 WARNING。
- 使用期错误：由命令、ToolResult、RuntimeEvent 和 trace 返回，不依赖 Web 启动 banner。

## 5. 数据流

```text
zcagent gateway
  -> configure_gateway_logging()
  -> build_web_runtime()
  -> optional capability startup checks
  -> structured WARNING
       -> red terminal line
       -> logs/YYYY-MM-DD/trace.log

browser
  -> normal chat UI
  -> no startup capability banner
  -> /subagent or actual tool use still returns precise capability error
```

## 6. 变更文件

- `agent/cli.py`
- `agent/app/runtime.py`
- `web/static/index.html`
- `web/static/app.js`
- `web/static/styles.css`
- `tests/unit_test/app/*`
- `README.md`
- `docs_design/README.md`
- `docs_design/zhice-agent-overall-design.md`
- `docs_design/zhice-agent-part13-subagent-design.md`
- `docs_design/2026-07-21-startup-capability-and-subagent-diagnostics-design.md`

## 7. 测试方案

- 静态 Web 不再包含 capability banner、相关函数或 health 启动请求。
- 缺少 `skill_sources.yml` 时 Web runtime 只记录一个结构化 WARNING，包含稳定 code，不输出绝对路径。
- Subagent、MCP、Memory extraction 的 capability health 保持可查询。
- 运行 Ruff、全量 pytest 和前端 `node --check`。

## 8. 验收标准

1. 缺少 `skill_sources.yml` 与缺少 `subagent.md` 在 Gateway 终端中都使用带时间、WARNING、logger、event、code 的统一格式。
2. 两类警告都进入同一份 trace。
3. 浏览器聊天页不显示启动 capability 常驻横幅。
4. `/api/health`、`/subagent` 和 unavailable Tool facade 仍能提供精确状态。
5. 普通聊天在可选能力缺失时继续运行。

## 9. 实现结果

- 静态 Web 已移除 capability banner、CSS 和启动 health 请求；`/api/health` 协议保持不变。
- `zcagent gateway` 不再在日志初始化前裸打印 Skill source 缺失提示。
- Web runtime 对 Skill source 缺失、非法和同步降级记录单个结构化 `agent.skills` WARNING。
- Owner workspace 实测中，缺少 `skill_sources.yml` 与缺少 `subagent.md` 使用相同的时间、级别、logger、event、code 字段格式，并同时写入当日 trace。
- 验证结果：`python -m ruff check .` 通过；`python -m pytest --basetemp .tmp/pytest_warning_surface_final` 为 `582 passed, 1 skipped`；两个前端 JavaScript 文件通过 `node --check`；`git diff --check` 通过。
