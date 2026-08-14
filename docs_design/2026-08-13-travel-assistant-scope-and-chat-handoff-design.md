# 智策旅行助手边界与携问返回主聊天设计

> 说明：本文的旅行专用边界与携问返回方案继续有效；纯问候不再归入旅行需求或普通无关问题，而由 `assistant_greeting` 返回固定欢迎话术，分类调用固定使用 temperature 0。当前实现见 `2026-08-14-travel-greeting-intent-and-deterministic-classification-design.md` 与 Part 19 活文档。

## 背景

旅行页当前把所有自然语言输入直接送入旅行需求提取器。用户询问“你是谁”等身份问题时，提取器按严格协议返回空旅行字段，前端据此追问出发地、目的地、日期和人数，造成“旅行助手像不会交流的小模型”的误解。

旅行页的产品定位仍应是智策 Agent 的旅游专用工作模式，不扩展为第二个通用聊天 Agent；但它需要回答身份、能力、使用方式和规划字段解释，并在遇到无关任务时明确引导用户携带原问题返回智策 Agent 主聊天。

## 目标

- 统一身份为“智策旅行助手”，自我介绍为“智策 Agent 的旅游规划助手”。
- 使用当前默认 LLM 做严格受限的意图分类和旅行需求提取，不新增或内置独立小模型。
- 只允许旅行需求、条件修改、身份、能力、使用方式和规划字段解释。
- 无关问题不进入旅行需求缺失字段追问；展示专注边界和返回主聊天入口。
- 返回主聊天时携带原问题，预填到一个新的本地聊天草稿，不自动创建 Session、不自动发送。
- 旅行页已收集的需求草稿和对话保持在当前页面状态，用户返回前可选择继续规划。

## 范围边界

- 不开放自由闲聊、通用知识问答或工具调用。
- 身份、能力和帮助回复使用审核过的固定产品话术，不直接显示模型自由文本。
- 意图协议严格白名单，未知值和额外字段拒绝。
- 跨页面问题只保存在当前浏览器 `sessionStorage`，读取一次后立即删除；不写后端 Session、日志或 URL query。
- 主聊天只预填问题，最终发送由用户确认。

## 模块设计

### 受限意图协议

`TravelRequirementDraft` 增加：

- `intent`: `travel_requirement | assistant_identity | assistant_capabilities | planner_help | unrelated`
- `intent_topic`: 空字符串或 `dates | travellers | budget | preferences | data_sources | models | workflow`

提取 Prompt 必须同时判断当前最新用户输入的意图。旅行需求与补充修改继续合并全部用户轮次并返回结构化字段；元问题或无关问题不得伪造旅行字段。

所有输入继续由当前默认 LLM 统一判断，不以关键词分支承担正确性。提取调用通过 `LLMProvider` 传入严格 JSON Schema，在模型生成阶段约束字段和枚举；后端仍补齐可安全为空的白名单字段，并拒绝缺少 intent、未知字段和非法类型。第一次格式失败时在同一 Schema 下自动纠正一次。详见 `2026-08-13-travel-requirement-structured-output-design.md`。

### 固定话术

- 身份：智策 Agent 的旅游规划助手，说明不是独立模型。
- 能力：整理条件、查询地图天气交通攻略、比较候选、生成可调整计划。
- 帮助：按 topic 解释为什么需要字段、是否可选、数据来源或模型关系。
- 无关：说明当前页面专注旅行规划，并提供“携带问题返回智策 Agent 聊天”和“继续规划旅行”。

### 携问返回

旅行表单发出 `handoffChat(question)` 事件。页面将有界问题写入一次性 `sessionStorage` key，随后路由到 `/`。`ChatPage` 挂载时读取并删除该 key，调用 `sessions.startDraft()`，把问题写入 composer 的本地 `message`；不调用 WebSocket，用户点击发送后才创建正式 Web Session。

## 数据流

1. 用户在旅行页输入自然语言。
2. 默认 LLM 按严格协议返回意图与旅行草稿。
3. `travel_requirement` 继续既有需求补全；元问题显示固定话术；`unrelated` 显示交接卡。
4. 用户点击携问返回，问题写入一次性浏览器草稿并进入主聊天。
5. 主聊天读取后立即清除，展示新对话与已预填问题，等待用户发送。

## 变更文件

- `prompts/travel_requirement_extraction.md`
- `agent/applications/travel/requirements.py`
- `agent/app/api/schemas.py`
- `agent/app/api/travel_routes.py`
- `web/frontend/src/api/{types,client}.ts`
- `web/frontend/src/components/travel/TravelPlanForm.vue`
- `web/frontend/src/pages/TravelPlannerPage.vue`
- `web/frontend/src/components/ChatPage.vue`
- `web/frontend/src/styles/travel.css`
- 对应 Python/Vue 测试与当前旅行设计文档

## 测试方案

- 提取器：旅行需求、身份、能力、字段解释、模型说明、无关任务、非法 intent/topic。
- 稳健性：所有意图继续调用 LLM 并携带严格 JSON Schema；LLM 简写 intent 可安全补齐；非 JSON 或非法字段只重试一次，连续失败返回友好错误且不放宽未知字段。
- API：响应包含严格意图；只有旅行需求计算 missing fields。
- 旅行表单：元问题不触发缺失字段追问；无关问题展示两个动作；原问题正确发出交接事件。
- 主聊天：读取一次性草稿、立即删除、不自动创建 Session、不自动发送；用户确认发送后走正常链路。
- 边界：超长交接内容截断；刷新主聊天不重复预填；旅行草稿不写入 URL。

## 验收标准

- “你是谁”回答智策旅行助手身份，不再追问五项旅行条件。
- “为什么需要人数”解释字段用途并允许继续补充旅行条件。
- 编程、写作等无关请求明确引导返回智策 Agent 主聊天。
- 点击返回后，原问题出现在主聊天输入框但没有自动发送。
- 旅行需求仍按现有确认后规划流程运行，不退化为通用聊天。
