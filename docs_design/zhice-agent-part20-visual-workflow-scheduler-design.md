# ZhiCe-Agent Part 20：可视化工作流、调度与用户连接

> 文档类型：当前活文档。本文只描述最新代码基线；带日期设计文档保留当时的方案与实施留痕。
>
> 当前状态：Part 20 已完成。独立 Workflow Runtime、SQLite 真值、不可变发布版本、受限 DAG 执行、APScheduler 调度、用户连接、本人邮件/QQ/微信投递、REST API、RuntimeEvent、受限 Node-RED 交换格式和 Vue Flow 产品页均已进入当前代码基线。
>
> 安全边界：工作流不进入聊天 Session，不提供任意代码、Shell/exec、任意图循环、子工作流、完整 Agent 节点或多副本 scheduler；条件节点只允许对直接上游的无副作用节点执行 1 至 5 次有限重试。
>
> 可靠性口径：Open-Meteo 只读请求对瞬时传输失败、超时、429 和 5xx 做有界重试；天气模板声明节点级有限重试。调度运行历史保存真实触发类型和计划时间，通知与外部写操作仍不自动重放。实现背景见 `2026-08-31-scheduled-weather-reliability-design.md`。

## 1. 当前定位

Part 20 是独立于聊天 `AgentLoop` 的后台自动化运行面：

```text
/workflows 工作流总览
  -> /workflows/:workflowId Vue Flow 编辑器
  -> actor-scoped Workflow REST API
  -> WorkflowRuntime
       -> WorkflowStore (state/workflows.sqlite3)
       -> WorkflowScheduler (APScheduler MemoryJobStore)
       -> WorkflowExecutor (稳定 DAG 分层执行)
       -> 固定节点处理器与当前 Provider
```

工作流定义、草稿、发布版本、调度状态和运行历史不写入 Session。SQLite 是定义和运行状态真值；APScheduler 只负责“何时触发”，Gateway 启动时从 SQLite 重建 active schedule。

同一 workspace 只允许一个 scheduler。Windows 通过 PID 与进程创建时间识别陈旧锁和 PID 复用；Linux 使用内核持有的文件锁，不信任容器重建后残留的同 PID 文本。部署回滚即使遇到上一个容器被强制移除，也能由新容器安全取得锁。

## 2. 领域协议与持久化

- `WorkflowDefinitionV1` 固定支持 `schedule_trigger`、`mcp_query`、`mcp_action`、`llm_transform`、`template`、`condition`、`official_notification`、`personal_email`、`qq_notification` 和 `weixin_notification`。
- 发布前校验节点/边上限、唯一触发器、端口、孤立动作、条件 true/false 分支和有向无环；状态条件只能检查直接上游，自动重试目标只能是只读查询或纯处理节点，已发布版本不可覆盖。
- 修改已发布工作流会创建下一草稿版本；重复保存停留在同一草稿版本，发布相同内容幂等。
- “保存草稿”“发布并启用”“立即测试”语义分离。立即测试运行最新已保存草稿，不发布、不替换线上版本；发布和运行前端动作会先保存当前画布并处理版本冲突。
- Store 对运行、节点执行、输入/输出安全摘要、外部投递内容与回执进行 owner-scoped 持久化；缺失或越权统一返回稳定领域错误。
- 受限 Node-RED 导入/导出只接受智策审核过的固定节点；Function、exec、文件系统、任意 HTTP 等外部节点一律拒绝。认证、所有权、版本和执行仍由智策 Runtime 掌控。

## 3. Tool、LLM 与数据流

- `GET /api/workflow-tools` 每次按当前 actor 交集计算真实 Tool Catalog、Query/Action allowlist、参数 schema 与 hash；选项不来自静态样例。
- 发布和运行都重新检查 actor、权限、Tool 是否仍存在、allowlist、schema hash 与连接 ownership。未重新审核的 Action 不可执行。
- Query 节点可通过 `POST /api/workflow-tools/test` 走真实 `UserScopedToolProvider` 做一次只读预览；Action 节点不提供免确认测试。
- 天气地点、高德地点、12306 出发到达地和小红书笔记链接使用受限 helper 转成目标 Tool 参数；helper 仍经过 Query allowlist 和当前 actor 权限。
- Tool 结构化失败归一为鉴权、超时、限流或来源不可用；外部 Action 结果未知时不自动重放。
- `llm_transform` 只调用 `LLMProvider` 转换唯一直接前驱的不可信数据，不开放 Tool、不创建聊天 Session，并支持受限 JSON Schema 输出。
- 智能处理与发送节点始终消费画布上的唯一直接前驱；执行器覆盖遗留的陈旧 `source_ref`，避免节点插入后发送旧来源或原始 Provider JSON。
- `template`、官方通知、个人邮件、QQ 和微信使用统一的“附加说明 + 前序结果”正文组合，并在外发前经共享 Markdown-to-plain renderer 转成可读纯文本。

## 4. 调度与运行

- 调度支持 date、interval、cron 和 IANA timezone；普通界面用“单次 / 每天 / 每周 / 每月 / 间隔”表单生成协议值。
- APScheduler 使用固定 `workflow:{workflow_id}`、MemoryJobStore、`max_instances=1`、coalesce 和 misfire grace；启动、暂停、恢复与 Gateway 重启都从 SQLite 权威状态收敛。
- DAG 按稳定拓扑层执行；条件节点可比较输出或检查直接上游状态。受控上游失败可按 1 至 5 次上限重试，恢复后走 true 分支，耗尽后走 false 分支；每次尝试、取消信号和节点失败均写入结构化运行状态。
- 外部写操作、个人邮件和本人 QQ/微信通知不因 timeout 或 transport error 自动重试；未知外部结果保持 outcome unknown。
- 运行详情展示节点时间线、实际发送内容的有界安全副本和 Provider 回执。长内容可展开和复制，但凭据、内部 `safe_*` 字段、run/node ID 与原始异常不进入普通界面。

## 5. 用户连接与通知

- 普通登录用户只管理本人的工作流和连接；管理员不能借跨用户管理权限使用他人的个人连接或读取 token。
- “我的邮箱”是已验证的本人通知地址。官方 SMTP 发送 8 位验证码，挑战只保存加盐摘要、10 分钟过期、单次消费；连续请求有 60 秒冷却，API 用 `429 NOTIFICATION_EMAIL_VERIFICATION_RATE_LIMITED` 和 `retry_after_seconds` 驱动前端倒计时。
- 官方通知节点只向当前 owner 已验证的“我的邮箱”发送；缺少官方 SMTP 或未验证地址时保持明确不可用。
- 个人邮箱只支持用户自己的 SMTP 授权码，以 AES-GCM 加密保存。QQ/163/126 自动配置安全参数，其他或企业邮箱显式填写服务器；工作流只保存 connection id，不保存授权码。
- 本人 QQ 通知只解析当前 owner 生效的绑定并复查 C2C 能力和在线状态；工作流 JSON 不包含 openid、机器人账号或凭据。
- 本人微信通知只解析 owner 当前生效的绑定、Adapter 状态和最近一对一入站上下文；发送复用稳定 delivery id、持久化 Outbox 和恢复机制，不接受微信号、联系人或群。
- 工作流页在窗口重新获得焦点、页面重新可见或连接设置关闭后刷新 capability，避免用户刚完成绑定仍看到陈旧不可用状态；微信状态统一映射为用户可读中文。

## 6. Vue Flow 产品交互

- 总览与 `/workflows/:workflowId` 详情路由共用旅行应用壳；编辑器提供节点目录、MiniMap、Controls、专用表单、变量选择、拖线连接、连线插入、撤销/重做、自动布局、复制粘贴和节点运行时间线。
- 桌面端支持拖动、滚轮、`Alt + 滚轮` 横向移动和方向按钮；移动端支持点击节点后再点目标节点连线、空白右键/双击取消连接、紧凑问题入口和视口内连线插入/删除动作。
- 节点详情由节点点击或编辑操作打开；不保留脱离画布的“节点详情”图标。开始平移或点空白画布会关闭详情气泡，但不会误取消正常连接模式。
- 手工保存成功后按钮持续显示“已保存到工作流”确认；画布重新进入且 store 仍持有同一工作流时立即恢复节点，不等待 id 再次变化。
- 顶部状态统一区分未保存、待发布、已发布和配置问题；移动端用紧凑状态控件打开同一权威问题列表。
- 模板直接创建可编辑的完整普通节点蓝图。安全默认出口固定为“保留结果”，不会因当前 QQ 可用而静默改成外发；用户必须显式选择官方邮箱、个人邮箱、QQ 或微信并满足连接/同意条件。
- 普通界面使用中文任务字段，不展示 Tool name、schema key、状态枚举或内部 ID；高级协议值只在显式高级配置中保留。

## 7. 权限与非目标

- viewer、developer、admin 的基础权限均可管理本人工作流、调度和自我通知；跨用户读取、修改、执行和连接引用拒绝。
- MCP Action、个人邮件和本人 QQ/微信需要显式同意时间；发布与每次运行都复查当前授权条件。
- 不支持任意 Python、JavaScript、Shell、exec、任意图循环、子工作流、分布式队列、完整 Agent 节点、任意收件平台身份或多 Gateway scheduler。有限重试不会覆盖 MCP Action、邮件、QQ/微信通知等有副作用节点。
- 真实外部副作用验收必须使用明确测试账号、收件箱和 allowlist；“Provider accepted”不等于最终送达。

## 8. 验证基线

默认 Python 测试覆盖 schema、Store、草稿/发布/试运行、DAG、状态条件有限重试、调度锁恢复、Tool 输入适配、连接加密与 ownership、通知重检、外部 outcome unknown、Node-RED 限制和稳定 API 错误。前端覆盖 store 冲突恢复、模板默认值、画布连接、状态重试配置、移动交互、保存反馈、capability 刷新和运行详情，并执行 lint、typecheck、Vitest 与 production build。

真实外部 smoke 单列执行：只读来源需要对应运行态服务和登录态；官方通知需要已配置官方 SMTP 与真实验证码收件箱；个人邮件需要用户 SMTP 授权码；QQ/微信需要 owner 当前有效绑定与在线上下文；Action 需要 Owner 审核后的真实 allowlist 和明确允许的副作用。
