# ZhiCe-Agent 按需 Tool 发现与动态 Capability Selection 设计

> 说明：本文正文保留 2026-07-21 当时“原 Part 16”的路线表述。当前路线已调整为 Part 15 完整 Session 上下文工程、Part 16 运行可靠性与生产部署；Capability Selection 作为已实现基线，不再占未来设计章节。

> 日期：2026-07-21
>
> 状态：已实现并进入当前代码基线
>
> 路线调整：提前落地原 Part 16 的 Capability Selection 子能力；Part 16 其它 Provider retry、全系统诊断和运行优化不随本次前移

## 1. 背景

当前 AgentLoop 在 Turn 开始时调用一次 `ToolProvider.definitions()`，随后把 actor 当前可见的全部 Tool schema 与 system prompt 一起发送给 LLM。Web 实测中，Subagent 因缺 Prompt unavailable 后，模型看不到 `delegate_tasks` 的真实能力状态，反而用 `exec` 冒充子代理完成计算。即使能力全部可用，一次性注入 read/exec/Memory/Skill/MCP/Subagent 全量 schema 也会增加 token、稀释指令并诱发近似工具替代。

## 2. 目标

1. 首次 LLM 调用只暴露一个通用 `discover_tools` bootstrap Tool，不发送全部业务 Tool schema。
2. 模型根据当前用户目标调用 `discover_tools` 查询工具；系统只从 actor、RBAC、Profile、Hook 前置边界已经允许的 Provider 中返回候选。
3. 候选 Tool 在当前 Turn 内激活；下一次 LLM 调用只收到 `discover_tools + activated schemas`。
4. 未发现/未激活 Tool 即使被模型编造也不能执行。
5. CLI、Web、Subagent child 使用同一机制；AgentLoop 保持通用，只在每次 LLM 调用前重新读取 Provider definitions。
6. 明确请求 unavailable Subagent 时发现并调用 capability facade，返回真实 cause，禁止用 `exec` 冒充。

## 3. 非目标

- 不前移 Part 16 的 LLM retry/cooldown、系统级诊断、MCP catalog reload 等其它能力。
- 不使用第二次隐藏 LLM 做意图分类。
- 不在 AgentLoop 中硬编码“文件问题选 read_file”等业务判断。
- 不向模型披露 actor 无权使用或 child Profile 已过滤掉的 Tool 名称。

## 4. 模块设计

新增 `DiscoverableToolProvider` 包装现有 ToolProvider：

```text
base provider（已做 actor/Profile 过滤）
  -> build safe catalog(name + bounded description)
  -> initial definitions = discover_tools only
  -> discover_tools(query/names/max_results)
  -> activate matched names for this Turn
  -> next definitions = discover_tools + activated schemas
  -> dispatch only discover_tools or activated names
```

`discover_tools` 支持：

- `query`：自然语言目标；本地确定性词法匹配。
- `names`：模型在后续发现中可精确请求已知名称。
- `max_results`：默认 5，硬上限 8。

返回稳定 JSON：`status`、`query`、`activated`、`available_count`、`hint`。描述有界，不返回参数 schema；schema 只在下一次 provider definitions 中出现。

## 5. AgentLoop 边界

AgentLoop 不负责搜索或选择 Tool，只把原来 Turn 开始时缓存一次 definitions 改为每次 LLM 调用前读取：

```text
while turn active:
  tool_definitions = tools.definitions()
  llm.chat(messages, tools=tool_definitions)
  dispatch selected tool
```

这样任何符合 ToolProvider 协议的动态 Provider 都能更新 schema，AgentLoop 不依赖具体 `discover_tools` 实现。

## 6. 安全与并发

- catalog 来源只能是包装时的 base `definitions()`，因此权限过滤先于发现。
- dispatch 对未激活名称返回 `TOOL_NOT_ACTIVATED`，不透传到 base。
- Provider 是 Turn-scoped；激活状态不跨 Session/Turn 泄漏。
- 激活集合使用锁保护；同一 assistant message 中并列的发现和业务调用仍按顺序执行，但业务 Tool 必须在下一次 LLM 调用看到 schema 后再调用。
- `discover_tools` 自身不执行外部副作用，不需要确认；实际 Tool 继续走 RBAC、确认、Hook、workspace guard 和审计。

## 7. 接入点

- CLI/Web 在最终 actor-scoped、Subagent-aware Provider 外层包装 discovery。
- child 在 Profile 能力交集完成后包装 discovery。
- `/tools` 仍可作为人类运维命令查看完整可见 Tool；它不代表 LLM 首轮 schema。
- `/subagent off` 不把 delegate facade 放入 base catalog；unavailable + auto 时 catalog 可发现 facade。

## 8. 变更文件

```text
agent/tools/discovery.py
agent/tools/__init__.py
agent/core/loop.py
agent/app/runtime.py
agent/cli.py
agent/subagents/factory.py
prompts/tool_use_policy.md
tests/unit_test/tools/*
tests/unit_test/agent_loop/*
tests/unit_test/app/*
tests/unit_test/cli/*
tests/unit_test/subagents/*
docs_design/README.md
docs_design/zhice-agent-overall-design.md
docs_design/zhice-agent-part13-subagent-design.md
README.md
```

## 9. 测试与验收

1. 首次 LLM 调用只收到 `discover_tools`。
2. discovery 后第二次调用只增加匹配到的 schema。
3. 未激活 Tool 返回 `TOOL_NOT_ACTIVATED`。
4. actor/child 不可见 Tool 不出现在 catalog。
5. 多次 discovery 累积但不重复激活，结果和 schema 有界。
6. 明确 Subagent 请求在 unavailable 时发现 facade，不能执行 `exec` 替代。
7. 简单无工具回答可不调用 discovery。
8. CLI、Web、child、MCP、Skill、Memory、Hook/确认回归通过。
9. Ruff、全量 pytest、前端 Node 检查和 diff 检查通过。

## 10. 同轮上下文连续性调整

本次实测同时确认 Session JSONL 一直保存完整历史，但 LLM Context 并不会无条件加载全部 Session。当前默认改为从最近 50 个 user Turn 中做本地相关性候选选择，最终仍最多注入 5 个相关 Turn，并受 60 条消息硬上限约束；因此扩大的是本地检索窗口，不是 LLM token 窗口。中文“为什么没调用、什么原因”等依赖前文的短追问继续强制保留紧邻 Turn。

## 11. 实际落地

- 新增 `agent/tools/discovery.py`，提供 Turn-scoped `DiscoverableToolProvider`、本地有界搜索、精确名称激活和 `TOOL_NOT_ACTIVATED` dispatch guard。
- AgentLoop 改为每次 LLM 调用前读取 definitions，未引入任何具体 Tool 或业务意图判断。
- CLI、Web 和 Subagent child 均在最终 actor/Profile Provider 外层接入 discovery；人类 `/tools` 运维命令仍查看完整可见 Registry。
- `tool_use_policy.md`、`subagent_once.md`、`subagent_orchestration.md` 已同步先发现再执行语义；unavailable Subagent facade 也只能通过 discovery 激活，不再诱导 `exec` 替代。
- Context 默认候选从 30 调整为 50，最终相关 Turn 和消息硬上限保持 5/60。
- 最终验证：`python -m ruff check .` 通过；`python -m pytest --basetemp .tmp/pytest_tool_discovery_final` 为 `581 passed, 1 skipped`（66.57 秒）；两个前端 JavaScript `node --check` 通过；`git diff --check` 通过。
