# 旅行规划对话历史恢复与展示设计

## 背景

旅行计划已经保存 `source_session_id`，真实规划阶段的 user、assistant 与 tool 消息也始终由 SessionStore 持久化，但旅行页面打开历史计划时只读取 `TravelPlanV1`，没有恢复关联 Session。规划前的需求问答则仅存在 `TravelPlanForm` 组件内存，页面刷新或重新进入后会消失。因此用户能看到历史计划，却看不到形成该计划的对话。

## 目标

- 点击历史计划时，把它关联的需求确认对话恢复到原 `TravelPlanForm` 问答窗口。
- 新计划在用户明确确认后，把需求问答写入即将执行的 travel Session，再进入 AgentLoop。
- 只展示需求确认阶段的 user/assistant 文本；不展示 Tool、规划执行阶段回复、空 tool-call 占位、自动续跑指令或纯 JSON。
- 计划与关联 travel Session 是同一个用户对象；删除计划时同步删除该 Session 和需求问答。
- 保持 travel Session 不进入普通聊天侧栏，且不在浏览器长期保存消息正文。

## 范围边界

- SessionStore 仍是消息真值源；前端只通过当前 actor 的受控 API 读写。
- 需求提取阶段仍不提前创建 Session；只有用户确认开始规划后才创建 `channel=travel` Session。
- 历史上未写入 Session 的规划前多轮问答无法回溯生成；旧正式规划请求若含“用户需求对话”，只恢复该原始用户表达。
- 不改变 AgentLoop、LLMProvider、Tool、Skill、TravelPlanV1 或普通聊天 Session 列表边界。

## 模块设计

### 需求问答写入

`TravelPlanForm` 在确认时除规划请求外，向 Store 传递有界的需求问答。Store 创建 travel Session 后，先调用旅行专属需求对话写入 API，再通过 WebSocket 发送正式规划请求。Runtime 验证：

- Session 属于当前 actor；
- Session channel 为 `travel`；
- role 仅允许 `user` / `assistant`；
- 数量、单条长度和总长度有界；
- 重试相同内容幂等，不允许覆盖已经开始运行的 Session。

写入消息增加安全元数据 `travel_visibility=conversation` 与 `travel_phase=requirements`，并通过 `SessionAccessService.refresh_index` 同步索引。

### 历史恢复

计划列表已有 `source_session_id`。打开计划后，Travel Store 使用现有 actor-scoped Session 读取 API 加载消息，并投影为需求问答：

- 保留带需求问答元数据的消息；
- 旧计划的正式规划请求只提取其中的“用户需求对话”原文；
- 隐藏自动续跑指令、Tool role、空内容、带 tool calls 的中间 assistant 和可解析为对象/数组的纯 JSON；
- 如果存在带需求阶段元数据的消息，只使用这些消息，不混入之后的 Agent 规划回复；
- 文本做数量与长度上限，加载失败只影响输入卡历史，不影响完整计划展示。

### 页面展示

不新增独立对话卡。历史消息直接回填页面顶部原有 `TravelPlanForm`，继续使用“你 / 旅行助手”的原问答样式。历史模式隐藏发送框和“补充数据”，避免把已完成计划误作可继续编辑的草稿。

### 联动删除

TravelPlan Store 删除前读取 `source_session_id`。Runtime 完成当前 actor 的计划删除后，只在关联 Session 同属当前 actor 且 channel 为 `travel` 时调用 `SessionAccessService.delete_session`。非 travel、跨用户或已经缺失的 Session 不会被扩大删除。

## 数据流

```text
需求问答 -> 用户确认 -> 创建 channel=travel Session
                      -> 写入有界需求问答到 SessionStore
                      -> WebSocket 发送正式规划请求 -> AgentLoop / Tool / Skill
                      -> finalize_travel_plan 保存 source_session_id

点击历史计划 -> TravelPlan Store 读取计划与 source_session_id
             -> actor-scoped Session API -> 需求消息投影 -> 回填原问答窗口

删除历史计划 -> TravelPlan Store 返回 source_session_id
             -> 验证 actor + channel=travel -> 删除关联 Session/需求问答
```

## 变更文件

- `agent/app/api/schemas.py`
- `agent/app/api/travel_routes.py`
- `agent/app/runtime.py`
- `web/frontend/src/api/client.ts`
- `web/frontend/src/api/types.ts`
- `web/frontend/src/stores/travel.ts`
- `web/frontend/src/components/travel/TravelPlanForm.vue`
- `web/frontend/src/pages/TravelPlannerPage.vue`
- `web/frontend/src/styles/travel.css`
- 相关 Python/Vue 测试与 `tests/unit_test/travel/test_case.md`
- `docs_design/zhice-agent-part19-intelligent-travel-planner-design.md`

## 测试方案

- 正常：确认后先写需求问答再发送正式请求；历史计划把需求问答恢复到原输入卡；删除计划同步删除 Session。
- 异常：对话写入失败不启动规划；历史 Session 读取失败仍展示计划并给出局部提示。
- 边界：跨用户或非 travel Session 拒绝；相同写入幂等；超量/超长请求拒绝；Tool、规划执行回复、自动续跑、空消息和纯 JSON 不展示；旧正式请求提取用户原始需求；错误关联不得删除非 travel Session。

## 验收标准

- 点击任一仍保有 source Session 的历史计划，可在原需求问答窗口看到当时保存的需求交流。
- 新生成计划刷新、离开再进入或从左栏重新打开后，需求问答仍可恢复。
- 普通聊天侧栏不出现 travel Session，页面不展示 Tool/Skill 内部消息或完整 JSON 串。
- 删除计划后，其关联 travel Session 与需求问答一并删除。
- 对话恢复失败不遮挡 TravelPlanV1；前后端 lint、typecheck、测试和构建通过。
