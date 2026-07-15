# 工具迭代上限校准设计

## 背景

当前 ZhiCe-Agent 默认 `max_tool_iterations=4`。Owner 已可使用全局 workspace 排查，但一次真实 trace 定位通常需要列目录、定位日志、读取目标 turn、必要时调用诊断工具和给出结论；4 次模型工具决策过早截断正常排查。

本机 `sthg_nanobot_agent` 的主 Agent 默认 80、子 Agent 默认 25，并在更高上限之外配合专项循环守卫。ZhiCe-Agent 仍处于轻量阶段，尚无同等细粒度循环识别能力，因此不直接采用 80。

## 目标

- 将默认工具迭代上限从 4 调整为 25。
- 保留每轮工具调用计数、现有 exec 安全策略、确认流、输出截断和上限后的无工具总结。
- 显式记录：上限按 LLM 的工具决策轮数计算，不按同一轮内的 tool call 数量计算。

## 范围边界

- 不引入 Nanobot 的搜索专项循环守卫或其配置体系。
- 不改变测试中传入的自定义 `max_tool_iterations` 行为。
- 不改变普通用户与 Owner 的工具权限范围。

## 变更文件

- `agent/core/loop.py`
- `tests/unit_test/agent_loop/test_agent_loop_tools.py`
- `tests/unit_test/agent_loop/test_case.md`
- `docs_design/zhice-agent-part3-tool-calling-design.md`

## 测试方案

| 场景 | 预期 |
| --- | --- |
| 默认构造 AgentLoop | 上限为 25 |
| 显式传入较小上限 | 仍按传入值触发测试中的循环保护 |
| 第 26 次工具决策 | 不执行新工具，进入无工具总结/fallback |

## 验收标准

1. 默认 AgentLoop 可完成最多 25 轮工具决策。
2. 第 26 次工具决策仍不会执行。
3. 上限不会绕过已有的权限、确认和 workspace guard。
