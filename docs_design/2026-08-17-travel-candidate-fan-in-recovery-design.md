# 旅行候选研究 fan-in 持久化恢复设计

## 背景

三路旅行研究 Child 已经完成且父 Session 已保存成功结果后，续跑仍可能只读取进程内来源账本。网关重启或账本未回填时，系统会误判候选研究缺失，重复调用同一批 `delegate_tasks`，造成外部查询重复、规划耗时增加，并阻止 optimizer 生成候选方案。

## 目标

- 以父 Session 中完整的调用与 ToolResult 配对作为候选研究的持久化完成事实。
- 完成后恢复 Child 来源证据并直接进入 optimizer。
- 部分完成、失败或结构不完整的批次不能误判为完成。
- 不改变通用 AgentLoop、SessionStore 和 Subagent 协议边界。

## 范围边界

本次只修复旅游候选研究阶段的恢复与续跑判定，不改变最终路线、住宿和天气校验规则，不把旅游业务判断下沉到通用 AgentLoop。

小红书关键词策略仍是“目的地旅游攻略”首查、仅真实空结果才用具体景点攻略收窄一次。浏览器页面连接被关闭属于传输失败，只用原关键词有界恢复一次，不计为关键词扩展。

## 模块设计

`agent/app/runtime.py` 增加只读历史判定：找到恰好包含 `travel-transport-weather`、`travel-stay-poi`、`travel-guides` 的父级委派调用，并要求对应结果中三个任务均为 `completed` 且 code 为 `OK`。命中后，从持久化 Child Session 回放真实 Tool 结果到来源账本；工具裁剪沿用既有来源守卫，使 `delegate_tasks` 不再暴露，optimizer 路径继续可用。

## 数据流

父 Session 委派调用 → 父 Session fan-in ToolResult → 校验完整成功批次 → 读取三个 Child Session → 回放来源 ToolResult → 来源账本完成 → 运行 travel-planner optimizer。

## 变更文件

- `agent/app/runtime.py`
- `agent/applications/travel/progress.py`
- `integrations/xhs_readonly_mcp/server.py`
- `tests/unit_test/travel/test_conversation_history.py`
- `tests/unit_test/travel/test_progress.py`
- `tests/unit_test/travel/test_case.md`

## 测试方案

- 完整三任务成功批次判定为完成。
- Child 失败、缺失或 partial 不判定为完成。
- 模拟进程内账本丢失，续跑仍直接要求 optimizer，且不再要求重复委派。
- 真实浏览器新建会话，确认三路 Child 仅执行一次并自动进入候选/最终规划。

## 验收标准

- 同一旅行 Session 不再重复运行完整三路候选研究。
- 小红书通用目的地攻略搜索只执行一次，除非真实空结果触发有界补查。
- fan-in 后无需刷新即可进入候选选择或自动规划。
- 服务重启后仍能从持久化历史恢复并继续。
