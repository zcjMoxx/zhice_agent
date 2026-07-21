# 2026-07-20 生命周期事件与 Hook Runtime 边界设计

> 状态：已实现并关闭；最终验收修订精确 matcher、进程树回收与完整 JSON Schema 校验
>
> 说明：2026-07-21 在不改变本设计核心安全边界的前提下，为单个 Hook 增加显式角色豁免作用域；当前语义见 `docs_design/2026-07-21-hook-role-scope-design.md` 与 Part 12 活文档。
>
> 当前活文档：`docs_design/zhice-agent-part12-hooks-design.md`
>
> 承接：`docs_design/zhice-agent-part11-mcp-design.md`、`docs_design/zhice-agent-part8-gateway-agent-logging-design.md`、`docs_design/zhice-agent-part7-turn-context-design.md`

## 1. 背景与同日方案调整

Part 12 初稿把 Hooks 主要定义为 `pre_tool/post_tool` 外部脚本。第一次边界收敛又改成“先完成 RuntimeEvent，第一批只保留空 `RuntimeEventEnricher`，外部 Runner 后续再做”。继续核对真实代码和完成边界后，确认空协议会形成死抽象，也不能验证 Hook 是否真的不会绕过 RBAC、危险确认和 Tool 核心安全。

因此同日、代码尚未落地时直接修订同一份日期记录，不新建重复文档。最终范围固定为：

1. transport-neutral RuntimeEvent 与 turn-scoped sequence。
2. AgentLoop 的 turn/context/LLM/tool 生命周期打点。
3. 现有 WS/SSE/CLI 转发与 Web 前端真实状态。
4. 最小但真实可运行的 `pre_tooluse` / `post_tooluse` Hook Runtime。
5. Hook 配置、加载、无 shell Runner、timeout、输出限制、结构校验、异常策略和 Tool 参数重校验。
6. 完整测试通过后关闭 Part 12。

SkillExecutor、`skill.*` 和 ProgressSink 不属于 Part 12。当前 Skill 没有独立 Executor，禁止从 `exec.command` 推断 Skill；这些能力由未来 Skill Runtime / Part 18 独立设计和验收。

### 1.1 最终验收修订

独立审查发现三个实现与文档承诺之间的窄缺口，本次直接修订当前同日记录和现有实现，不重开 Part 12：

1. `tools` matcher 只允许精确 Tool name，或列表唯一值 `"*"`；`read_*`、`foo*bar` 等部分通配符配置启动失败，避免“配置成功但永不匹配”。
2. Hook 子进程进入独立进程树：Windows 使用带 `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE` 的 Job Object，POSIX 使用独立 session/process group；timeout、输出超限、stdin 失败和正常结束都会关闭整棵树，不只终止直接子进程。
3. Tool 参数校验改用声明的运行时依赖 `jsonschema>=4.23.0,<5`，按 schema `$schema` 选择 Validator，支持本地 `$ref/$defs` 和标准组合约束。所有外部/远端 `$ref` 在校验前拒绝，SchemaError、引用无法解析和 ValidationError 均 fail closed，并继续返回兼容的 `ToolResult(code=INVALID_PARAM)`。

## 2. 问题

1. `on_event` 是自由 dict，只有 `text_delta` 和少量交互事件。
2. WebSocket 层硬编码少数 event type，不能表达完整 AgentLoop 周期。
3. 前端在首个文本 delta 之前无法判断系统处于 context、LLM 还是 Tool 阶段。
4. Activity/Audit/trace 是持久诊断数据，不适合作为低延迟前端状态协议。
5. 只有空 Enricher 不能证明 Hook 配置、进程边界、失败策略和参数重校验可用。
6. Hook 如果接在错误位置，可能让修改后的参数绕过 schema、RBAC、确认或具体 Tool guard。

## 3. 目标

1. 建立有版本、可校验、不进入 Session 的 RuntimeEvent。
2. 覆盖 turn/context/LLM/tool 的 started/completed/failed/stopped/waiting_confirmation 真实生命周期。
3. 复用 `on_event -> WebRuntime -> WS/SSE/CLI`，保持 `text_delta`、`channel_status`、工具确认和 MCP Elicitation 兼容。
4. 前端用“整理上下文、请求模型、执行工具、根据工具结果生成回答”等真实状态替换三个点等待。
5. 实现显式配置的本地 Python Hook Registry 和 Runner。
6. 支持 pre Hook continue/block/modify 和 post Hook continue/enrich。
7. 固定 `shell=False`、workspace path guard、最小环境、输入/输出限制、短 timeout 和严格 JSON schema。
8. 修改后的 Tool 参数重新经过核心 schema、RBAC、危险确认和具体 Tool 安全检查。
9. post Hook 只能增强 Event 展示，不能修改 ToolResult 的成功/失败事实。

## 4. 非目标

- 不展示 chain-of-thought、完整 prompt、完整 Tool 参数、ToolResult、Memory 或 Secret。
- 不提供虚假百分比或预计时间。
- 不把 RuntimeEvent 写入 Session。
- 不让 Hook 直接访问 WebSocket、SSE response 或前端 API。
- 不用 Hook 替代 RBAC、确认、workspace guard、命令策略、timeout、输出截断、脱敏、SSRF 或 MCP ArtifactGateway。
- 不递归扫描 Hook 目录，不热加载，不在线安装，不执行远端 Hook，不建设 Hook 市集。
- 不实现 SkillExecutor、`skill.*`、ProgressSink、Subagent 或跨进程事件总线。

## 5. 三层职责

```text
Hook Runtime：可选业务阻断、参数修正和安全展示增强
RuntimeEvent：运行事实与安全展示数据
WS/SSE/CLI：传输或渲染
```

核心安全始终位于 AgentLoop 的 policy/confirmation 边界和具体 Tool 中。Hook 只能增加限制，不能降低限制。

## 6. RuntimeEvent

最小字段：

```text
protocol_version, event_id, type, status, timestamp, sequence,
session_id, turn_id, request_id, tool_call_id, tool_call_record_id,
parent_event_id, display, ui_metadata, metadata
```

第一版类型：

```text
turn.started/completed/failed/stopped
context.started/completed/failed
llm.started/completed/failed
tool.started/completed/failed/waiting_confirmation
```

规则：

- sequence 由每个 Turn 的 emitter 从 1 自动递增，只表示发出顺序。
- sink best-effort，失败不影响 Turn。
- display、ui_metadata、metadata 在发出前完成字段、长度、JSON 大小和敏感键校验。
- `tool.completed/failed` 只在最终 ToolResult 确定后发出。
- RuntimeEvent 不替代 Activity、Audit 或 trace。

## 7. Hook 配置与注册

唯一运行态配置入口：

```text
${ZHICE_AGENT_WORKSPACE}/config/hooks.yml
```

示例：

```yaml
version: 1
hooks:
  - name: restrict-exec
    stage: pre_tooluse
    script: extends/hooks/restrict_exec.py
    tools: [exec]
    timeout_seconds: 2
    max_output_chars: 16384
```

Loader 校验：

- `version=1`、hook name 唯一、stage 仅为 `pre_tooluse/post_tooluse`。
- script 必须是 `.py` 文件，解析后位于 workspace 内并且存在。
- tools 是精确 Tool name 的非空列表，或唯一值 `[*]`；任何部分通配符非法。
- timeout 和输出限制在核心上限内。
- 配置缺失等价于无 Hook；配置存在但非法时启动失败，避免静默绕过声明的业务限制。

Registry 保留配置顺序，同一 stage 下后一个 Hook 看到前一个 Hook 的最终参数或展示 patch。

## 8. Hook Runner

执行形态：

```text
[sys.executable, resolved_script_path]
shell=False
cwd=ZHICE_AGENT_WORKSPACE
stdin=UTF-8 JSON object
stdout=UTF-8 JSON object
```

环境只保留 Python/Windows 启动必需项、TEMP/TMP、`PYTHONUTF8=1`、`PYTHONIOENCODING=utf-8` 和 `ZHICE_AGENT_WORKSPACE`。不继承 API key、Authorization、proxy 或其它业务 Secret。

Runner 同时读取 stdout/stderr，分别施加大小上限。Windows 子进程在启动后立即加入 kill-on-close Job Object；POSIX 子进程通过 `start_new_session=True` 创建独立 process group。超限、timeout、输入失败和正常结束均关闭/终止完整进程树，必要时升级为强制 kill。stdout 必须整体解析为单个 JSON object。trace 只记录 Hook 名、stage、duration、结果和稳定错误码，不记录完整输入输出。

## 9. pre_tooluse 安全顺序

```text
parse Tool call
  -> initial Tool JSON Schema validation
  -> configured pre hooks in order
  -> final Tool JSON Schema validation
  -> ToolExecutionPolicy / RBAC
  -> explicit confirmation with final arguments
  -> concrete Tool.execute core guards
```

pre 输出：

```json
{"action":"continue"}
{"action":"modify","arguments":{"path":"docs"}}
{"action":"block","code":"HOOK_BLOCKED","message":"当前项目禁止该操作"}
```

Hook 不能修改 tool name、actor、session、turn、policy decision 或 confirmation state。pre timeout、异常、输出超限、非法 JSON/字段/action 一律 fail closed，返回结构化 ToolResult；Tool 不执行。修改后的参数使用 `jsonschema` 重新校验 Tool 公开 schema；本地 `$ref/$defs` 正常解析，外部引用、无效 schema 和无法解析引用 fail closed。

修改后的参数会重新经过 schema 和所有核心安全。Hook 无法声明“已确认”，也无法让原本需要确认的最终参数跳过确认。

## 10. post_tooluse 与 Event enrichment

```text
final ToolResult
  -> configured post hooks in order
  -> validate display/ui_metadata patch
  -> emit tool.completed or tool.failed
```

post 只允许：

```json
{"action":"continue"}
{"action":"enrich","display":{...},"ui_metadata":{...}}
```

未知字段、ToolResult 修改字段、event identity/status/sequence 字段均非法。多个合法 patch 按配置顺序合并，最终由 RuntimeEvent schema 再校验。

post timeout、异常、输出超限或非法输出 fail open：忽略该 Hook 的展示增强，保留真实 ToolResult 和核心默认 RuntimeEvent。成功不能变失败，失败不能变成功。

## 11. 渠道与前端

- WS 新增 `runtime_event` 信封，保留 `channel_text`、`channel_status`、`tool_confirmation_required`、MCP Elicitation。
- SSE 使用 `event: runtime`，保留 delta/done/stopped/error，并继续转发交互事件。
- CLI Spinner 只原位展示 started/waiting 的短 title，completed Event 默认不刷屏。
- 前端按 `turn_id + sequence` 去乱序；旧 sequence 不覆盖新状态；done/stopped/error 和 terminal turn Event 关闭运行状态。
- RuntimeEvent 不持久化，重连不回放。

## 12. 模块与变更文件

新增：

```text
agent/protocols/runtime_event.py
agent/protocols/hook.py
agent/core/event_emitter.py
agent/hooks/config.py
agent/hooks/loader.py
agent/hooks/runner.py
agent/hooks/runtime.py
agent/tools/schema.py
agent/process_tree.py
config/hooks.example.yml
pyproject.toml
tests/unit_test/runtime_events/
tests/unit_test/hooks/
```

修改：

```text
agent/core/loop.py
agent/app/runtime.py
agent/app/api/ws.py
agent/app/api/routes.py
agent/cli.py
agent/console.py
web/static/app.js
web/static/runtime-event-state.js
web/static/styles.css
web/static/index.html
tests/unit_test/agent_loop/
tests/unit_test/app/
tests/unit_test/cli/
README.md
docs_design/README.md
docs_design/zhice-agent-overall-design.md
docs_design/zhice-agent-part10-memory-design.md
docs_design/zhice-agent-part11-mcp-design.md
```

## 13. 测试

- RuntimeEvent schema、sequence、sink failure、敏感字段和 Event 不进入 Session。
- 普通聊天、工具循环、多 Tool、多次 LLM、LLM/Tool error、确认拒绝、stop/cancel、iteration limit。
- WS/SSE/CLI 同语义与旧事件兼容；前端旧 sequence、terminal 清理和断线处理。
- Hook config 合法/非法、workspace guard、重复名称和缺失配置。
- Tool matcher 精确名称与独立 `*` 正常，部分通配符配置启动失败。
- 真实 Python fixture 覆盖 continue、block、modify、enrich、timeout、输出超限、非法 JSON、非法字段和脚本异常。
- 派生长运行子进程的 Hook 超时后，父进程和后代进程均被回收。
- Tool schema 覆盖 `$ref/$defs`、required、type、additionalProperties、无效/外部/无法解析引用的 fail-closed 行为。
- 修改参数后 schema、RBAC、确认和具体 Tool guard 使用最终参数。
- post Hook 失败不改变 ToolResult 事实；无 Hook 不创建子进程且主链正常。

新增测试主题目录同步维护 `test_case.md`。

## 14. Definition of Done

Part 12 必须同时满足：

1. RuntimeEvent、turn-scoped sequence 和 AgentLoop 生命周期打点完成。
2. WS/SSE/CLI 和前端真实状态闭环完成，旧文本、确认、MCP Elicitation、stop、done/error 兼容。
3. 真实 Hook 协议、配置、Loader、Registry 和无 shell Runner 完成；Tool matcher 只接受精确名称或独立 `*`，部分通配符启动失败。
4. pre continue/block/modify 完成，修改参数重新经过 schema、RBAC、确认和 Tool 核心安全。
5. post continue/enrich 完成，只能增强受限 display/ui_metadata，不能篡改 ToolResult 事实。
6. timeout、输出限制、最小环境、workspace guard、JSON 校验和 fail-closed/fail-open 策略完成；Windows Job Object / POSIX process group 回收完整进程树。
7. 真实 Hook fixture 和异常/边界测试完成；Tool schema 支持本地 `$ref/$defs`，外部、无效或无法解析引用 fail closed。
8. ruff、相关测试和可行的全量 pytest 通过，或明确记录无关历史失败。
9. 当前活文档、总体设计、设计索引和 README 同步为已实现口径。

上述全部完成并通过本次最终验收后，Part 12 继续保持关闭。不得继续写“外部 Hook Runner 后续实现”。SkillExecutor、`skill.*` 和 ProgressSink 属于未来 Skill Runtime / Part 18，不影响 Part 12 关闭。

最终验证：`python -m ruff check .` 与两个前端脚本的 `node --check` 通过；全量 `python -m pytest -rs --basetemp .tmp/pytest_part12_final_review_3` 为 485 passed、1 skipped，跳过项是当前 Windows 环境不支持创建 symlink 的既有只读工具用例。
