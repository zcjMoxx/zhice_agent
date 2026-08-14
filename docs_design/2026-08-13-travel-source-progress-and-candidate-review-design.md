# 旅行来源诊断、完整进度与候选确认设计

## 背景

旅行规划实跑暴露了三个相互关联的问题：Tavily 查询可能因原始正文过大或 `fast + country` 非法组合失败；小红书只读上游未运行时，异步 `TaskGroup` 异常被压成不透明的远程错误；前端只保留最后 12 条进度，较早的查询记录会消失。optimizer 已经比较候选，但用户在最终计划保存前看不到、也不能确认其选择。

## 目标

- 旅行频道自动收敛 Tavily 查询参数，避免已知的输出过大和参数冲突。
- 小红书适配器拆解异常组，区分未登录、上游未启动、超时、限流与一般不可用。
- 当前规划的所有用户可见进度按到达顺序保留，不再静默删除早期记录。
- optimizer 返回多个可行候选时展示受限摘要，并在用户确认后才生成和保存完整 `TravelPlanV1`。
- 候选确认状态按用户与旅行 Session 持久化，刷新页面后仍可继续。

## 范围与边界

- AgentLoop 仍只负责通用循环；Tavily 参数策略位于旅行 Hook，小红书错误映射位于只读 MCP 适配器。
- 候选确认是旅行领域 Tool、Store、API 和页面能力，不修改通用 Tool、Session 或 LLMProvider 语义。
- 候选卡只保存日期、区域、核心地点、预算、路线、强度、覆盖率、警告和分数，不保存外部原始响应或 Secret。
- 单个可行候选可直接继续；多个可行候选必须确认。不可行候选只展示稳定拒绝原因，不允许选择。
- 用户选择只约束后续完整计划生成，不执行购票、预订或支付。

## 模块设计

### 来源查询稳健性

`TravelProgressHookRuntime` 在旅行频道的 Tavily pre-tool 阶段将 `include_raw_content` 固定为 `false`，限制结果数量，并把携带 `country` 的 `fast/ultra-fast` 查询提升为 `basic`。post-tool 阶段根据结构化错误码给出可执行原因，而非统一显示“无结果”。

`xhs_readonly_mcp` 递归拆解 `BaseExceptionGroup`。连接失败映射为 `TRAVEL_SOURCE_UPSTREAM_OFFLINE`，超时映射为 `TRAVEL_SOURCE_TIMEOUT`，认证和限流继续使用既有稳定错误码。

### 候选确认

optimizer 在成功结果中增加 `feasible_candidates` 的受限摘要。`request_travel_candidate_review` 使用可信 `ToolExecutionContext` 将摘要保存到当前用户的旅行数据库，并发出 `travel.candidate_review_required`。多个候选时，`finalize_travel_plan` 必须看到已选择的候选 ID；选择不匹配时拒绝保存。

REST API 只允许当前 Session 所有者读取和更新候选选择。页面选择后继续同一个 `channel=travel` Session，Agent 根据受控续跑消息和历史 optimizer 结果生成完整计划。

### 进度记录

Pinia Store 继续按 `tool_call_id/skill_run_id` 合并同一项的 started/completed 状态，但不再执行 `slice(-12)`。展示层保持可折叠，完整计划生成后仍自动收起。

## 数据流

1. Agent 查询外部来源；旅行 Hook 收敛 Tavily 参数并生成用户侧结果摘要。
2. Agent 构造至少两个有意义的候选并执行 optimizer。
3. optimizer 返回推荐项、所有可行候选摘要及拒绝项。
4. 多个可行候选时 Agent 调用候选确认 Tool；Store 持久化，RuntimeEvent 展示卡片，本轮结束。
5. 用户选择推荐项或其它可行项；API 校验用户、Session 和候选 ID后记录选择。
6. 页面在同一 Session 发起受控续跑；finalizer 校验选择 ID，保存计划并清除待确认状态。

## 变更文件

- `integrations/xhs_readonly_mcp/server.py`
- `agent/applications/travel/progress.py`
- `agent/applications/travel/store.py`
- `agent/applications/travel/service.py`
- `agent/applications/travel/tools.py`
- `agent/protocols/runtime_event.py`
- `agent/app/runtime.py`
- `agent/app/api/schemas.py`
- `agent/app/api/travel_routes.py`
- `prompts/travel_planning.md`
- `prompts/travel_planning_continuation.md`
- `skill_repo/skills/travel-planner/SKILL.md`
- `skill_repo/skills/travel-planner/scripts/optimize.py`
- `web/frontend/src/api/{types,client}.ts`
- `web/frontend/src/stores/travel.ts`
- `web/frontend/src/components/travel/TravelProgress.vue`
- `web/frontend/src/styles/travel.css`
- 对应 Python 与前端测试及 `tests/unit_test/travel/test_case.md`

## 测试方案

- 正常：Tavily 参数被收敛；小红书成功不变；两个可行候选进入等待、选择后可 finalizer；完整进度超过 12 条仍保留。
- 异常：小红书异常组分别映射离线/超时；非法候选 ID、越权 Session、未确认 finalizer 均被拒绝；候选续跑失败可重试。
- 边界：单候选无需等待；候选摘要字段和数量受限；重复选择幂等；RuntimeEvent UI 数据不包含 URL、Token、原始响应。

## 验收标准

- 本次复现中的小红书故障明确显示上游服务未运行，而非泛化“无结果”。
- Tavily 不再发送 `include_raw_content=true`，也不再发送 `fast/ultra-fast + country`。
- 20 条以上规划进度仍从第一条开始完整可见。
- 多候选计划在最终保存前出现可选择卡片；选择会被后端校验并约束 finalizer。
- 页面刷新后可恢复待选择候选；完成计划后流程自动收起。
- Ruff、Pytest、前端 lint/typecheck/test/build 全部执行并报告结果。
