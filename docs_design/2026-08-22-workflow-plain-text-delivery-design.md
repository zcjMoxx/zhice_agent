# 工作流消息纯文本投递设计

## 背景

工作流中的智能处理可能返回 Markdown。个人 SMTP 邮箱按 `text/plain` 投递时不会渲染 Markdown，导致 `**34.6℃**`、`*雷雨*` 等标记直接展示给用户。不同邮箱对 HTML 和 Markdown 的支持也不一致，不能把可读性依赖交给收件客户端。

## 目标

- 工作流邮件和平台通知统一输出排版清楚的纯文本。
- 智能处理默认不生成 Markdown 标记，但投递出口仍做确定性兜底转换。
- 保留段落、项目符号、链接文字和关键数值，不把正文粗暴压成一行。
- 不改变 SMTP 连接、权限、收件人、发送确认或运行时数据流。

## 范围边界

- 仅规范发送到外部文本渠道的正文；工作流内部结果和 Web 展示不被修改。
- 不发送 HTML 邮件，不增加富文本编辑器或第三方 Markdown 渲染依赖。
- 不在天气查询节点中硬编码穿衣、带伞等业务规则；建议由智能处理节点按用户配置生成。

## 模块设计与数据流

1. 智能处理的默认指令明确要求普通中文纯文本，不使用 Markdown、JSON、内部字段或代码形式。
2. 官方通知和个人邮件在真正调用 Provider 前，统一通过现有 `markdown_to_plain_text` 转换正文。
3. 纯文本本身保持幂等；标题、强调、列表和链接转换为邮箱可直接阅读的文字结构。
4. SMTP Provider 继续使用 `text/plain; charset=utf-8`，避免不同邮箱客户端产生不一致渲染。

## 变更文件

- `agent/workflows/nodes.py`
- `tests/unit_test/workflows/test_workflows.py`
- `tests/unit_test/workflows/test_case.md`
- `web/frontend/src/pages/WorkflowPage.vue`
- `docs_design/zhice-agent-part20-visual-workflow-scheduler-design.md`

## 测试方案

- 官方通知和个人邮件输入包含粗体、斜体和列表 Markdown，断言 Provider 收到的是保留结构的纯文本。
- 普通纯文本输入保持不变。
- 运行工作流单元测试、Ruff、前端 Lint、类型检查、Vitest 和生产构建。

## 验收标准

- 邮件正文不再出现 `**`、`*文本*`、标题井号等 Markdown 标记。
- 日期、温度、降水概率、建议和段落结构完整保留。
- 不触发真实邮件或其他外部消息。
