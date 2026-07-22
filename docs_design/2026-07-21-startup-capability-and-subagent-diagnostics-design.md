# ZhiCe-Agent 启动能力分级与 Subagent 诊断证据闭环设计

> 说明：2026-07-22 起，Web 不再常驻展示 capability banner；可选能力启动异常统一进入结构化终端 WARNING 和 trace，`/api/health` 继续保留机器可读状态。当前告警出口以 `docs_design/2026-07-22-optional-capability-warning-surface-design.md` 为准，本文其余正文保留当时方案原貌。

> 日期：2026-07-21
>
> 状态：已实现并进入当前代码基线；当前维护口径以 `docs_design/zhice-agent-part13-subagent-design.md` 为准
>
> 承接：`docs_design/zhice-agent-part13-subagent-design.md`、`docs_design/zhice-agent-part9-user-auth-permission-design.md`

## 1. 背景

Part 13 首次 Web 实测暴露出两类相关问题：

1. 运行 workspace 缺少 `prompts/subagent.md` 时，三个 child 在 context 构建阶段立即失败，但父 Tool 只得到统一的 `SUBAGENT_FAILED`。
2. `diagnose_my_recent_activity` 可以定位到父 `delegate_tasks` 失败，却只能读取顶层错误码，无法沿 batch/task/child 关联获取真实的 `PromptNotFoundError`。

同时，当前启动装配对不同能力的失败处理不一致：基础 Prompt/LLM、Hook、MCP、Subagent 配置都可能阻断启动，而 Skill 同步已经采用警告后继续。需要按能力是否属于主流程、是否承担安全策略进一步分级。

## 2. 当前代码事实

### 2.1 根因丢失链

```text
SubagentContextBuilder.load("subagent")
  -> PromptNotFoundError
  -> AgentLoop 只发瞬态 context.failed RuntimeEvent
  -> 没有 child trace / Activity turn row
  -> Coordinator 折叠为 SUBAGENT_FAILED
  -> subagent.task_failed Activity 被 SQLite sink 忽略
  -> 父 delegate_tasks 只持久化 160 字符 output_preview
  -> Diagnostics 只能报告顶层 SUBAGENT_FAILED
```

### 2.2 当前启动行为审计

| 能力 | 当前行为 | 正确分类 | 本次口径 |
| --- | --- | --- | --- |
| workspace 路径与运行目录 | 失败阻断 | 进程基础 | 保持阻断 |
| identity/tool/skill 基础 Prompt | 缺失阻断聊天 | Chat 核心 | 保持对应聊天入口阻断 |
| LLM endpoint | 无可用 endpoint 阻断聊天 | Chat 核心 | 保持对应聊天入口阻断 |
| Gateway Auth schema | 初始化失败阻断 Gateway | 安全核心 | 保持阻断 |
| Hook 显式配置 | 非法时阻断 CLI；Gateway 异常退出 | 已声明安全策略 | 不允许静默禁用；保持 fail closed，后续可设计 no-tool 降级入口 |
| Skill source/sync | 警告后继续 | 可选扩展 | 保持降级 |
| MCP 配置/runtime | 非法配置可能阻断 | 可选扩展 | 应禁用 MCP、记录 warning，主聊天继续 |
| Subagent 配置/Prompt | 非法配置阻断或运行期泛化失败 | 可选扩展 | 应禁用 Subagent、记录 warning，主聊天继续 |
| Memory 后台提取 | 运行期失败 | 可选后台能力 | 应禁用提取并 warning，不影响聊天与显式 Memory Tool |
| individual Skill/MCP server/worktree | 使用时失败 | 延迟能力 | 返回准确的能力级错误，不阻断启动 |

核心原则：

```text
应用级 fail open
能力级 fail closed
安全策略不得静默降级
```

## 3. 目标

1. 可选能力启动检查失败时，禁用对应能力并输出稳定 warning，不阻断 CLI/Gateway 主聊天。
2. `/subagent` 和 Web capability 状态能显示 unavailable 原因与修复建议。
3. 用户明确或自动使用不可用 Subagent 时，返回稳定、准确的能力错误，不创建多个伪 child failure。
4. child context/LLM/tool/workspace 失败写入安全 trace，并带 root/batch/task/subagent/child 关联字段。
5. 父 `delegate_tasks` 保留每个 child 的 `stage + code + safe message`。
6. Diagnostics 可从父 Turn 下钻 Subagent child trace，返回终端根因而不是只返回顶层包装码。
7. 找不到终端原因时不得标记 `confidence=high`。

## 4. 非目标

- 不把完整 traceback、Prompt 正文、Secret、绝对 child worktree 路径暴露给模型或浏览器。
- 不把 RuntimeEvent 全量持久化。
- 不在本次把 Hook 非法配置改成静默禁用；Hook 是显式安全策略。
- 不引入通用服务编排或后台健康检查框架。

## 5. 启动能力状态

新增 transport-neutral 状态：

```text
name
state = available | unavailable | degraded | disabled
code
message
hint
```

Subagent 启动检查：

1. `subagents.yml` 是否存在；缺失表示正常 disabled，不产生错误。
2. 显式配置是否可解析；非法时 `SUBAGENT_CONFIG_INVALID`，能力 unavailable。
3. enabled 时检查 `subagent.md`、`subagent_orchestration.md`、`subagent_once.md`；缺失时 `SUBAGENT_PROMPT_NOT_FOUND`。
4. Profile 和限制检查仍 fail closed，但只关闭 Subagent 子系统。

启动日志示例：

```text
subagent.runtime_unavailable code=SUBAGENT_PROMPT_NOT_FOUND missing=subagent.md hint="run zcagent init"
```

## 6. 使用时错误

Subagent 整体不可用时不暴露一个会产生 N 个 child failure 的 Coordinator。用户主动查看或使用时返回：

```json
{
  "code": "SUBAGENT_RUNTIME_UNAVAILABLE",
  "cause_code": "SUBAGENT_PROMPT_NOT_FOUND",
  "message": "Required Subagent runtime prompt is missing: subagent.md",
  "hint": "Run zcagent init, then restart the process."
}
```

child 已启动后的错误分类：

| stage | code 示例 |
| --- | --- |
| context | `SUBAGENT_PROMPT_NOT_FOUND`、`SUBAGENT_CONTEXT_FAILED` |
| llm | `SUBAGENT_LLM_CONFIG_FAILED`、`SUBAGENT_LLM_FAILED` |
| tool | 保留具体 Tool code |
| workspace | `SUBAGENT_WORKTREE_FAILED`、`SUBAGENT_WORKSPACE_BUSY` |
| cancelled/timeout | 现有稳定 code |
| runtime | `SUBAGENT_INTERNAL_ERROR` |

`SUBAGENT_FAILED` 只作为旧记录兼容，不再用于所有未知异常。

## 7. trace 与诊断下钻

Coordinator 为每个 child 终态写安全 trace：

```text
event=subagent.task_failed
root_session_id/root_turn_id
parent_session_id/parent_turn_id
batch_id/task_id/subagent_id
child_session_id/child_turn_id
profile/workspace_mode
stage/code/error_type/duration_ms
```

Diagnostics 查询目标父 Turn 时同时接受：

```text
session_id + turn_id 精确匹配
或 root_session_id + root_turn_id 匹配
```

当父 Tool 是 `delegate_tasks` 且存在 child terminal failure：

1. 优先使用具体 child `stage/code`。
2. 多个 child 同因失败时合并为共同根因。
3. 多种原因时返回 per-task 摘要和 partial/conflict 说明。
4. 只有具体终端 code 和对应 trace 证据同时存在时才允许 high confidence。
5. 只有父 `SUBAGENT_FAILED` 包装码时 confidence 不得为 high，并明确缺少 child terminal cause。

## 8. 变更文件

新增：

```text
agent/protocols/capability.py
agent/subagents/startup.py
```

修改：

```text
agent/subagents/coordinator.py
agent/protocols/subagent.py
agent/tools/subagent.py
agent/app/runtime.py
agent/app/gateway.py
agent/cli.py
agent/auth/diagnostics.py
tests/unit_test/subagents/*
tests/unit_test/auth/*
tests/unit_test/app/*
tests/unit_test/cli/*
README.md
docs_design/README.md
docs_design/zhice-agent-part13-subagent-design.md
docs_design/zhice-agent-overall-design.md
```

最终按实际职责收敛，不为清单创建空模块。

## 9. 测试方案

- enabled Subagent 缺 Prompt：CLI/Gateway 正常启动、warning 可见、`/subagent` 显示 unavailable。
- 非法 `subagents.yml`：只关闭 Subagent，不影响普通聊天。
- 普通聊天在 Subagent unavailable 时不增加 LLM preflight 和 child。
- `/subagent once` 后使用不可用能力返回准确 cause code，one-shot 仍只消费一次。
- child context `PromptNotFoundError` 返回 `stage=context/code=SUBAGENT_PROMPT_NOT_FOUND`。
- child trace 包含完整关联 ID，但不含 Prompt 正文、Secret 或绝对 worktree 路径。
- Diagnostics 从父 Turn 找到 child trace 并报告准确根因。
- 只有父包装码时 confidence 不得为 high。
- 既有 LLM/base Prompt/Auth/Hook 阻断语义保持不变。
- Skill 启动降级回归保持不变。

## 10. 验收标准

1. 缺少 Subagent Prompt 不阻断 CLI/Gateway 普通聊天。
2. 启动日志和 `/subagent` 都能显示准确原因与修复建议。
3. 使用不可用 Subagent 不产生三个模糊 child failure。
4. 已启动 child 的失败返回准确 stage/code。
5. trace 持有可供诊断的安全 child terminal evidence。
6. Diagnostics 能从父 Turn 下钻并输出 `SUBAGENT_PROMPT_NOT_FOUND`。
7. 泛化包装码不能得到虚假的 high confidence。
8. 核心启动依赖与 Hook 安全策略不被静默降级。
9. Ruff、全量 pytest 和前端检查通过。

## 11. 实际落地与设计差异

本记录已经完成代码落地，最终边界如下：

- 核心阻断保持不变：workspace/运行目录、基础 Prompt、LLM endpoint、Gateway Auth 等失败时，阻断对应入口。
- Hooks 单独归类为已声明安全策略：显式配置非法时继续 fail closed 并阻断启动，不能按普通插件静默降级。
- 可选能力局部禁用：Skill 同步、MCP、Subagent、后台 Memory extraction 失败时，禁用对应能力、写安全 warning，CLI/Gateway 普通聊天继续。
- MCP/Subagent/Memory extraction 使用统一 `CapabilityStatus`；Web health 暴露安全状态，前端以非阻断 banner 展示 `code/message/hint`，CLI 输出同类 warning。
- Subagent unavailable 时不注册可产生 child 的有效 Coordinator；`/subagent` 和 one-shot 使用返回 `SUBAGENT_RUNTIME_UNAVAILABLE` 加具体 cause，并且 one-shot 仍只消费一次。
- Coordinator 将 child context、LLM、Skill、workspace、timeout/cancel 和未知 runtime 异常映射为稳定 stage/code；terminal trace 保留安全父子关联字段，不记录 Prompt 正文、Secret、traceback 或绝对 worktree 路径。
- Diagnostics 沿 `root_session_id/root_turn_id` 从父 Turn 下钻 child terminal trace。多个 child 同因失败时聚合 `child_failure_count/common_child_failure_count`，证据最多五条；多个不同原因时优先当前共同度最高的具体终因，不伪造不存在的单一根因。
- 提案曾写“只有父包装码时 confidence=low”；实际实现采用 `medium`，因为父 Tool failure 本身是已确认事实，但缺少 child terminal cause，仍明确写入 limitation，绝不标记 high。
- 旧 trace 如果没有 child terminal evidence，无法通过新代码回溯恢复历史真实根因；只能报告父 `SUBAGENT_FAILED` 和证据缺口。
- Part 13 没有新增 Profile 可配置的 MCP per-server semaphore。MCP startup checker 只负责能力级 fail closed；单 server 连接失败继续使用既有 runtime degraded 状态，进一步连接并发控制属于后续 MCP Runtime 优化。

实际新增文件还包括 `agent/mcp/startup.py`、`agent/memory/startup.py` 及对应测试；CLI/Web 均已接入 checker，Subagent Tool 后续又统一进入 Turn-scoped discovery。当前最终验证为：全量 Ruff 通过；`python -m pytest --basetemp .tmp/pytest_tool_discovery_final` 为 `581 passed, 1 skipped`；两个前端 JavaScript 文件的 `node --check` 均通过。

## 12. Web 实测补充：不可用能力与短追问连续性

后续 Web 实测发现两个仍会误导用户的边界：

1. Subagent startup unavailable 时完全移除 `delegate_tasks`，模型无法看到真实 capability cause，可能用 `exec` 等其它 Tool 冒充用户明确要求的子代理调用。
2. Context relevance 对“为什么没调用，什么原因”这类中文短追问按二元词重叠打分，可能因低于阈值而删除紧邻 Turn，使模型只收到 system + 当前 user 两条消息。

本轮追加口径：

- Subagent 状态为 `unavailable` 时暴露无执行能力的 unavailable facade Tool；它不创建 child、不接受 Profile 权限，只返回 `SUBAGENT_RUNTIME_UNAVAILABLE + cause_code/message/hint`。用户明确要求 Subagent 时，模型不得改用其它 Tool 冒充。
- Context relevance 识别“为什么、怎么回事、刚才、上一条、没调用”等依赖前文的短追问，并至少保留紧邻 Turn；普通无关新话题仍按相关性筛选。
- trace 的 `llm.call messages` 与 `tools` 数量作为这类问题的直接诊断证据，测试覆盖中文追问和 unavailable Tool facade。
