# ZhiCe-Agent Part 20：可视化工作流、调度与用户连接

> 文档类型：当前活文档
>
> 当前状态：WorkflowDefinitionV1、SQLite 真值、不可变发布版本、DAG 校验与执行器、APScheduler 重建、用户连接、邮件 Provider、本人 QQ 主动通知 Provider、工作流/连接 REST API、RuntimeEvent 和 Vue Flow 产品页已进入当前代码基线。天气、地点、网页、12306 和小红书只读查询已通过当前普通账号的真实页面测试；个人邮箱已收敛为 SMTP 授权码；QQ 官方 C2C 主动文本接口已用真实绑定账号验证接受。发布与立即运行会先保存当前画布，并在必要时生成和发布下一不可变版本，界面明确区分未保存、待发布和已发布状态。
>
> 日期设计记录：`docs_design/2026-08-10-visual-workflow-scheduler-design.md`、`docs_design/2026-08-21-workflow-overview-detail-navigation-design.md`、`docs_design/2026-08-21-workflow-user-facing-productization-design.md`、`docs_design/2026-08-21-workflow-friendly-tool-input-design.md`、`docs_design/2026-08-22-workflow-product-completion-design.md`、`docs_design/2026-08-22-workflow-smtp-only-email-design.md`、`docs_design/2026-08-22-workflow-readable-output-editor-usability-design.md`、`docs_design/2026-08-22-workflow-qq-active-notification-design.md`

## 1. 当前定位

Part 20 是独立于聊天 AgentLoop 的后台自动化运行面：

```text
/workflows workflow overview
  -> /workflows/:workflowId Vue Flow editor
  -> owner-scoped Workflow REST API
  -> WorkflowRuntime
       -> WorkflowStore (state/workflows.sqlite3)
       -> WorkflowScheduler (APScheduler MemoryJobStore)
       -> WorkflowExecutor (stable DAG layers)
       -> fixed node handlers / current providers
```

工作流定义、发布版本、计划和运行历史不进入 Session。调度器只负责“何时触发”，Gateway 启动时从 SQLite 重建 active schedule；同一 workspace 只允许一个 scheduler。

## 2. 已落地边界

- `WorkflowDefinitionV1` 固定支持 schedule trigger、MCP Query/Action、LLM Transform、Template、Condition、官方通知、个人邮件和本人 QQ 通知节点。
- 发布前校验节点/边上限、端口、孤立动作、条件分支和有向无环；已发布版本不可覆盖。
- MCP Query 与 Action 使用独立 allowlist；每次发布和运行重新经过当前 actor、权限、Tool schema 与连接 ownership。
- LLM Transform 仅调用 `LLMProvider` 转换上游数据，不开放 Tool，不创建聊天 Session。
- APScheduler 使用固定 `workflow:{workflow_id}`、MemoryJobStore、`max_instances=1`、coalesce 和 misfire grace；支持 date、interval、cron 与 IANA timezone。
- 个人 SMTP 授权码只经用户连接 Store 和 Provider 使用，以 AES-GCM 加密；API 和运行摘要不返回授权码。
- 本人 QQ 通知只解析工作流所有者当前生效的绑定，普通界面和工作流 JSON 不包含 `openid`、机器人账号或凭据；发布与运行都会复查绑定、C2C 能力和在线状态。
- Vue 页面提供与旅游规划统一应用壳的工作流总览、独立 `/workflows/:workflowId` 详情路由、节点注册目录、可显式拖动/滚轮/方向按钮平移的连线画布、MiniMap、Controls、专用属性表单、变量选择器、拖线补节点、连线插入节点、撤销/重做、自动布局、键盘操作、动作确认、保存/发布/启停/立即运行和节点级运行时间线。普通界面统一使用中文产品文案，不显示 Tool name、schema key、状态枚举或 run/node ID；原始值只在协议和显式高级配置中保留。

## 3. 安全和管理边界

- 普通登录用户可使用本人基础工作流；跨用户读取、修改、执行和连接引用均拒绝。
- viewer、developer、admin 的内置基线包含个人工作流、调度、自我通知和本人邮箱发送；外部写操作、社交发布和跨用户管理不下放。
- 管理员可管理异常状态，但不能借管理权限使用他人的个人连接或读取 token。
- Action、个人邮件、官方通知和本人 QQ 通知的 timeout/transport error 不自动重放；Provider 接受只记录 `accepted`，不宣称 `delivered`。
- 不提供任意 Python、JavaScript、Shell、exec、循环、子工作流、完整 Agent 或多副本 scheduler。

## 4. 运行与验证

Python 默认测试覆盖定义、Store、执行、连接加密/ownership、Provider 结果和调度重启恢复。前端执行 `lint`、`typecheck`、Vitest 与生产构建。真实外部 smoke 需要：

- 个人邮箱类型、邮箱地址、授权码及测试收件箱；QQ/163/126 的 SMTP 安全参数自动配置，其他或企业邮箱才展示服务器设置，发件地址由邮箱账号自动生成；
- Owner 审核进入 Action allowlist 的测试 MCP Tool 与明确允许产生的外部副作用。
## 编辑器与真实工具目录

当前编辑器采用 Vue Flow，并吸收本地 FlowGram 参考实现中的节点注册表、schema 表单、history、snap、node panel、drag-line-end、line add button、auto layout、变量引用和运行态反馈模式；没有直接引入 React/FlowGram 包。工作流首页与编辑器分离，支持保存返回、删除、模板创建、画布双向平移、受控缩放、拖线补节点、连线插节点、撤销/重做、自动布局、键盘复制粘贴和条件 true/false 可见端口。Java 参考工程中的画布解析/持久化/策略分层只作为结构对照，Python 侧仍以 WorkflowDefinitionV1、SQLite Store 和独立 DAG Executor 为唯一运行实现。

MCP 节点的选项不来自静态示例 JSON。`GET /api/workflow-tools` 每次按当前 Actor 重新交集计算实际注册 Tool 和 Workflow Query/Action allowlist，并返回当前参数 schema 与 hash。发布和运行均要求 schema hash 一致。只读 Query 节点可通过 `POST /api/workflow-tools/test` 走当前 Actor、UserScopedToolProvider 和 allowlist 执行一次真实测试；Action 节点不提供免确认测试。

新增信息节点不会自动绑定目录第一项，用户必须明确选择天气、车票、地点、网页或小红书等任务。服务来源仅在选中后作为弱提示，中文字段表单集中计算未选能力、schema 变化、必填参数缺失和动作确认等阻塞项；发布与立即运行按钮和“可以发布”状态使用同一前端就绪结果，后端仍执行权威校验。外部写操作仅在当前 actor 的真实 action catalog 与 allowlist 交集非空时出现。

默认表单进一步采用任务输入而不是 MCP 协议输入：天气预报只填写地点和可选天数，由隐藏的受限 geocode helper 在每次运行时解析坐标并生成动态日期；历史天气填写地点与日期；小红书搜索使用中文排序/类型选项，详情使用完整笔记链接。适配结果最终仍必须通过目标 MCP schema。Open-Meteo 只读 HTTP 客户端不继承终端代理，避免本机代理导致 TLS 中断。

查询预览最多等待 30 秒，结构化来源错误会映射为鉴权失效、超时、限流或暂不可用的中文提示。智能处理和发送结果节点以画布唯一直接前驱为数据来源，前端不再要求普通用户选择内部变量，执行器也会以真实前驱输出覆盖遗留引用。运行完成后前端重新读取 SQLite 真值，运行历史右侧只显示步骤名称、中文状态和时间；数据库内部的 `safe_*` 字段和错误码不进入普通产品界面。`template`、官方通知和个人邮件统一使用“附加说明 + 前序结果”的正文组合规则；外部邮件与通知在调用 Provider 前通过共享 Markdown-to-plain renderer 转为结构清楚的纯文本，避免邮箱客户端直接展示 Markdown 标记。

工作流模板采用可复制的完整蓝图，而不是创建前向导：点击模板会直接创建并打开包含触发、获取、处理和结果出口的全部普通节点与连线。当前用户已绑定 QQ 且渠道在线时，模板默认使用“发送到我的 QQ”；否则保留个人邮箱出口，用户仍可在画布切换为保留结果、通知邮箱或个人邮箱。待配置节点显示明确状态，顶部问题清单可定位到节点；模板生成后可以与空白工作流一样移动、删除、换类型、重连和继续扩展。天气模板的智能处理预置“生成生活建议”，可编辑带伞、穿衣、出行、通勤方式和体感偏好。
