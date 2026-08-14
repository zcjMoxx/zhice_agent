# 旅行需求对话式补全与双入口确认设计

## 背景

当前旅行页把自然语言发送和右上角“补充数据”都绑定到同一个语义提取动作。自然语言即使只缺一个字段，也会强制打开完整表格；“补充数据”也会意外调用 LLM。这与用户期望的聊天式补问和纯手工录入入口不一致。

## 目标

- 自然语言发送后立即通过既有 `LLMProvider` 需求提取 API 形成草稿。
- 真正阻塞的字段缺失时，在输入卡内一次列出当前全部缺失项，鼓励用户用一条回复补齐；回复后仍有遗漏或矛盾时才继续追问，不强制打开表格。
- 补问文案使用自然对话语气，不显示“缺失 N 项”“请尽量一次回复”等系统校验式措辞。
- 手工表格的日期默认展示北京时间当天，默认态使用弱化文字；日期以中文年月日展示，不暴露浏览器的 `yyyy/mm/日` 占位。结束日期最小值为开始日期，开始日期向后修改时自动把过早的结束日期同步到新开始日期。
- 手工入口说明只描述用户操作，不解释 LLM 或内部技术边界；需求提取与规划阶段差异由产品行为体现。
- 关键信息完整后展示简洁摘要，用户可直接确认开始规划，也可打开表格补充或核对可选信息。
- 右上角“补充数据”仅打开手工表格，不调用 LLM；手工填写完成后仍需明确确认才开始规划。
- 需求提取阶段不创建旅行 Session、不调用旅行 MCP；确认后才进入现有 AgentLoop、Skill、MCP 和 finalizer 链路。

## 范围与边界

- 阻塞字段仍只有出发地、目的地、开始日期、结束日期、人数和有效日期范围。
- 人群、预算、交通、住宿、兴趣、节奏、规划模式和硬约束为可选信息。
- 多轮问答由浏览器保存本轮非敏感对话，并把有界对话记录交给同一需求提取 API；后端仍只接收不可信文本并经过严格 allowlist schema 校验。
- 最新明确修正覆盖较早表达；模型不得用默认值伪造用户事实。
- 不把对话草稿写入普通聊天 Session，也不持久化到浏览器长期存储。

## 模块设计

### 前端状态机

`idle -> extracting -> asking -> ready -> planning`

- `idle`：可发送自然语言，或点“补充数据”进入纯手填。
- `extracting`：调用 `/api/travel/requirements/extract`。
- `asking`：根据后端 `missing_fields` 一次询问全部阻塞项；用户回答后连同前文重新提取完整草稿，仍不完整时只追问剩余项。
- `ready`：展示关键字段摘要和“确认并开始规划”“补充信息”操作，不自动执行。
- `planning`：沿用现有 `travel.generate`，创建 travel Session 并进入完整规划链。

### 双入口

自然语言入口：

1. 发送即提取。
2. 缺失时在卡片内一次列出全部待确认项。
3. 完整时展示摘要。
4. 用户直接确认，或打开表格核对后确认。

手工入口：

1. 点击右上角“补充数据”。
2. 直接打开当前空白或已有草稿表格。
3. 不触发需求提取 API。
4. 补齐阻塞字段并确认后开始规划。

## 数据流

```text
自然语言/补充回答
  -> authenticated requirement extraction API
  -> TravelRequirementExtractor -> LLMProvider
  -> strict TravelRequirementDraft + missing_fields
  -> 问下一项 / 展示摘要
  -> 用户明确确认
  -> travel.generate -> travel Session -> AgentLoop -> Tool/Skill/MCP -> finalizer

补充数据按钮
  -> 仅打开表格
  -> 用户手填并确认
  -> travel.generate
```

## 变更文件

- `web/frontend/src/components/travel/TravelPlanForm.vue`
- `web/frontend/src/components/travel/TravelPlanForm.test.ts`
- `web/frontend/src/styles/travel.css`
- `prompts/travel_requirement_extraction.md`
- `tests/unit_test/travel/test_case.md`
- `docs_design/zhice-agent-part19-intelligent-travel-planner-design.md`

## 测试方案

- 正常：首轮完整需求直接进入摘要确认，不弹表格。
- 正常：只缺人数时问答一次；缺少多个字段时集中列出，用户一条回复补齐后可直接确认执行。
- 正常：纯手工入口不调用需求提取 API，补齐后可确认。
- 异常：Provider 失败显示安全错误，不伪造摘要、不执行规划。
- 边界：多轮最新修正覆盖旧草稿；无效日期仍保持阻塞；可选字段缺失不阻塞。
- 全量运行 Ruff、Pytest、前端 lint、typecheck、test、build。

## 验收标准

- 发送按钮负责 LLM 提取和集中式补问，不再自动打开表格，也不固定一个字段问一轮。
- “补充数据”按钮不产生任何 LLM/API 提取调用。
- 关键字段完整前无法执行；完整后必须由用户明确确认。
- 用户可在执行前随时打开表格核对或补充信息。
- 需求提取、Session、AgentLoop、LLMProvider、Skill、MCP、workspace 和 Secret 边界保持不变。
