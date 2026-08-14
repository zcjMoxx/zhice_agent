# 旅行需求 LLM 语义提取与生成前确认设计

> 说明：最初实现采用前端本地规则提取，无法可靠理解自由表达，且在旧静态资源下缺乏可见失败反馈。当前方案改为后端经 `LLMProvider` 执行严格结构化语义提取；自然语言路径进一步改为卡片内多轮问答，不再强制弹出完整表格。右上角“补充数据”只打开手工表格且不调用 LLM，见 `2026-08-13-travel-conversational-requirement-review-design.md` 与 Part 19 活文档。

## 背景

旅行输入卡此前包含一组业务默认值，点击发送会把默认值与自然语言直接拼接并立即创建旅行 Session。用户无法在外部查询和长时间规划开始前确认自然语言提取是否准确，也容易把示例值误当成真实需求。

## 目标

- 第一次提交自然语言只调用旅行需求提取 API，通过 `LLMProvider` 生成结构化草稿并打开右侧确认栏。
- 所有结构化业务字段初始为空，不使用地点、日期、人数、预算、节奏或模式默认值。
- 用户确认必填字段后，才创建旅行 Session 并启动 AgentLoop。
- “补充数据”作为输入卡右上角的次级按钮，随时打开确认栏。
- 可选字段未提供时保持空值，不伪造成用户偏好。

## 范围边界

- 需求提取是独立应用服务，必须通过 `LLMProvider`，不经过 AgentLoop、不调用 Tool/MCP/Skill、不创建 Session。
- 缺少年、完整日期等信息时不猜测；由用户在确认栏补齐。
- 不改变 Session、AgentLoop、Tool、Skill、LLMProvider 或 TravelPlanV1 边界。
- 真正生成仍由 Travel store 在收到确认后的单次 `submit` 事件启动。

## 交互与数据流

```text
自然语言
  -> 点击纸飞机 / Ctrl+Enter
  -> POST /api/travel/requirements/extract
  -> LLMProvider + travel_requirement_extraction.md
  -> 严格 JSON 白名单校验
  -> 打开右侧确认栏
  -> 用户补齐和确认
  -> emit submit(确认后的完整指令)
  -> 创建 travel Session
  -> AgentLoop / MCP / Skill / finalizer
```

输入内容发生变化后，下一次提取成功时以完整新草稿替换旧结构化结果，避免上一次目的地、日期或预算残留；提取失败时保留用户输入并显示安全错误，不打开虚假的确认结果。关闭并原样重新打开确认栏时保留用户刚刚修改的值。

## 必填与可选

生成前必填：出发地、目的地、开始日期、结束日期、同行人群、人数、旅行节奏、规划模式。精确预算、预算档位、交通、住宿、兴趣和硬约束均可空；空值在最终指令中明确为“用户未提供”或省略，不替用户作决定。

## 变更文件

- `web/frontend/src/components/travel/TravelPlanForm.vue`
- `web/frontend/src/components/travel/TravelPlanForm.test.ts`
- `web/frontend/src/styles/travel.css`
- `agent/applications/travel/requirements.py`
- `agent/app/api/travel_routes.py`
- `agent/app/api/schemas.py`
- `agent/app/runtime.py`
- `prompts/travel_requirement_extraction.md`
- `docs_design/zhice-agent-part19-intelligent-travel-planner-design.md`

## 测试与验收

- 初始结构化字段全部为空。
- 第一次提交调用 Fake LLM 提取并打开确认栏，但不触发旅行生成。
- Provider、非 JSON、未知字段、非法类型和超长输入返回安全稳定错误。
- 已识别字段正确呈现，缺失字段明确提示。
- 必填字段不完整时不能确认生成。
- 用户补齐后只触发一次 `submit`，指令包含确认值和来源边界。
- lint、typecheck、test、build 全部通过。
