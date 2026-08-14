# 旅行接待动作提交与持续交接设计

## 背景

接待 Agent 已能理解旅行条件和无关问题，但两个用户表达仍可能只得到文字回复：用户说“确认 / 开始执行”时，接待阶段没有可调用的启动工具；用户在看到主聊天引导后继续问“怎么回主聊天”时，前端会在发送下一条消息前清空交接卡。结果是模型说了“可以开始”或“可以返回”，真实状态却没有变化。

## 目标

- 用户明确文字确认时，必须提交服务端动作并在同一次 WebSocket 请求中进入正式规划。
- 页面按钮确认继续复用现有 REST 确认链路。
- 无关问题产生的主聊天交接卡在用户继续追问操作方式时保持可用。
- 模型只负责识别意图和选择 Tool；阶段状态、必填校验和所有权继续由服务端决定。

## 范围边界

- 不把关键词判断硬编码进 AgentLoop。
- 不让接待 Agent 获得地图、天气、票务、网页、Skill、exec 或通用聊天工具。
- 不自动替用户发送主聊天问题；跳转后只预填，仍由用户确认发送。
- 不改变正式规划的来源门槛、候选审核与 finalizer 终态。

## 模块设计

### 接待确认 Tool

新增 `confirm_and_start_travel_planning`。它不接收模型生成的草稿参数，只读取 Session 中已经由 `update_travel_draft` 校验并保存的草稿，再调用现有 `WebRuntime.confirm_travel_planning`。该回调统一执行 actor ownership、完整字段校验和 `travel_phase=planning` 原子更新，避免 Tool 与 REST 形成两套规则。

成功后 Tool 发出内部事件 `travel.planning_confirmed`。当前 WebSocket worker 在接待 Turn 结束后读取到 planning 阶段，复用现有 bounded continuation 立即启动正式规划 Turn。

### 前端阶段投影

travel store 收到 `travel.planning_confirmed` 后：

- `intakeBusy=false`、`generating=true`、`phase=planning`；
- 初始化“旅行条件已确认”的进度项；
- 保存恢复用 Session id 并启动状态恢复轮询；
- 后续正式规划 RuntimeEvent 按原链路展示。

### 主聊天交接保留

发送新的接待消息时不再无条件清空 `handoffQuestion`。`update_travel_draft` 事件新增 `changed_fields`：只有用户实际新增或修正旅行字段时，交接状态才随旅行话题恢复而清理；空 patch 的操作追问不会让按钮消失。

## 数据流

```text
用户输入“开始执行”
  -> intake Agent 选择 confirm_and_start_travel_planning
  -> 服务端校验 Session 草稿与 actor ownership
  -> 原子写 travel_phase=planning
  -> emit travel.planning_confirmed
  -> 前端显示“正在规划”
  -> WebSocket bounded continuation
  -> planning Agent 查询来源、生成候选并完成计划
```

```text
无关问题
  -> offer_main_chat_handoff
  -> 前端展示“携带问题返回主聊天”
  -> 用户追问“怎么回主聊天”
  -> handoff 卡保持
  -> 点击后 sessionStorage 携带原问题并跳转主聊天预填
```

## 变更文件

- `agent/applications/travel/tools.py`
- `agent/applications/travel/service.py`
- `agent/app/runtime.py`
- `prompts/travel_intake.md`
- `web/frontend/src/api/types.ts`
- `web/frontend/src/stores/travel.ts`
- 对应后端、Prompt、Store 测试与生产静态资源

## 测试方案

- Tool：完整草稿确认成功、事件发出、回调复用；不完整和非 intake 阶段拒绝。
- Runtime：intake 只暴露三个受限 Tool，planning 能力仍需确认后才开放。
- Prompt：明确文字确认必须调用真实启动 Tool。
- Store：确认事件切换到 planning/generating；后续接待消息不清空交接卡；旅行字段真实变化才清理卡片。
- 回归执行前端 Vitest、ESLint、TypeScript、生产构建及相关 Python 单测。

## 验收标准

- 条件齐全后输入“确认”或“开始执行”，页面立即进入规划进度，不再回复“还可以继续补充”。
- 无关问题之后输入“怎么回主聊天”，仍能看到并点击“携带问题返回主聊天”。
- 点击交接按钮后进入主聊天新草稿，输入框预填原无关问题且不自动发送。
- 接待阶段依旧无法访问正式旅行来源或通用 Agent 工具。
