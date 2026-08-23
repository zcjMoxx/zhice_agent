# 工作流 QQ 主动通知设计

## 背景

当前 QQ WebSocket 渠道已经支持账号绑定、收取消息和被动回复，并通过真实绑定账号验证了 QQ 官方接口能够接受不携带 `msg_id` 的 C2C 主动文本消息。工作流目前只有官方邮件和个人邮箱出口，QQ 用户仍需理解邮箱连接或内部渠道标识，不能直接把定时结果发给自己。

## 目标

- 新增固定工作流节点“发送到我的 QQ”，使用工作流所有者当前生效的 QQ 绑定。
- 普通用户不填写 QQ 号、`openid`、机器人账号、App ID、Secret 或任何内部标识。
- 发布和每次运行都重新确认用户绑定、机器人账号、C2C 能力与在线状态。
- 发送正文统一为结构清楚的纯文本；QQ 接口接受只表示平台已接受，不宣称用户端必达。
- 超时或返回结果不明确时不自动重试，避免重复通知。
- 保持 WorkflowRuntime 独立于 AgentLoop 和聊天 Session。

## 范围边界

- 只允许发给工作流所有者本人已绑定的 QQ，不允许任意 `openid`、群号或其他用户目标。
- 复用 `workflow.notify.self` 权限；节点保存显式发送同意时间，发布和运行均要求同意仍存在。
- 首版单条主动文本有明确长度上限，过长内容在发送前以用户可读提示截断；不拆成多条，避免部分成功和重复语义。
- QQ SDK 超时、空响应或传输状态不明统一视为“发送结果未知”，不做降级重发。
- 工作流普通界面只展示中文可操作原因；原始平台 ID、内部错误和响应只留在服务端边界，且日志只记录哈希和长度。

## 模块设计

1. `SQLiteAuthStore` 和 `ExternalIdentityService` 增加服务端专用的本人活动绑定读取，公开绑定列表仍不返回外部用户 ID。
2. `QQNotificationProvider` 持有账号到适配器的运行时注册表，负责按当前用户解析绑定、验证 C2C/在线状态并调用对应 transport。
3. `BotpyQQTransport.send_proactive_text` 在 QQ 客户端所属事件循环中调用 `post_c2c_message`；使用有限等待时间、单条纯文本、无 `msg_id`、无自动重试。
4. `WorkflowRuntime` 增加通知出口校验回调；发布 QQ 节点时校验本人绑定和当前渠道。`NodeHandlers` 在运行时再次校验权限、显式同意，并调用 QQ Provider。
5. 执行器把 QQ 节点纳入外部结果敏感节点：超时记为结果未知，尝试次数固定为一次。
6. 前端能力接口返回隐私安全的 `available/bound/code`；编辑器新增“发送到我的 QQ”，不可用时给出中文原因和“去连接”入口，不出现 QQ 号输入框。
7. 完整蓝图模板的发送出口默认选择当前账号可用的 QQ；没有 QQ 绑定时保留个人邮箱出口，模板仍是可自由编辑的普通节点和连线。

## 数据流

```text
工作流所有者发布
  -> 校验 workflow.notify.self + 显式同意
  -> 内部查询本人 active QQ binding
  -> 匹配已配置且 C2C 可用的机器人账号
  -> 保存不可变发布版本

定时或手动运行
  -> 重新解析同一所有者当前绑定
  -> 纯文本渲染与单条长度约束
  -> QQ transport 所属事件循环
  -> post_c2c_message(openid, msg_type=0, content)
  -> accepted / outcome unknown / failed
```

## 变更文件

- `agent/auth/store.py`
- `agent/channels/identity.py`
- `agent/channels/qq/notification.py`
- `agent/channels/qq/transport.py`
- `agent/channels/qq/__init__.py`
- `agent/app/runtime.py`
- `agent/workflows/schemas.py`
- `agent/workflows/catalog.py`
- `agent/workflows/node_red.py`
- `agent/workflows/nodes.py`
- `agent/workflows/executor.py`
- `agent/workflows/runtime.py`
- `web/frontend/src/api/types.ts`
- `web/frontend/src/pages/WorkflowPage.vue`
- `web/frontend/src/utils/workflow-editor.ts`
- `web/frontend/src/utils/workflow-presentation.ts`
- `web/frontend/src/utils/workflow-templates.ts`
- 对应 Python、Vue/TypeScript 测试和 Part 20 当前活文档

## 测试方案

- 单元测试绑定只按当前用户解析，公开 API 不泄露原始外部 ID。
- 单元测试主动发送调用 `post_c2c_message` 且不带 `msg_id`，超时/空响应不重试，日志和异常不包含 `openid`。
- 单元测试节点权限、显式同意、纯文本转换、无绑定、离线渠道和平台接受结果。
- 单元测试发布时再次校验当前用户的 QQ 通知能力，执行超时转换为结果未知。
- 单元测试 Node-RED 往返、节点目录、模板出口和前端中文映射。
- 运行目标 Python 测试与 Ruff；运行前端 lint、typecheck、Vitest 和生产构建。
- 浏览器使用未绑定账号验证禁用与“去连接”，使用已绑定账号验证节点可选、无内部字段；真实发送只在用户再次明确确认后进行。

## 验收标准

- 已绑定且 QQ 渠道在线的用户可以在工作流结果出口选择“发送到我的 QQ”。
- 节点详情只需编辑附加说明并确认允许发送，不要求任何 QQ 技术参数。
- 未绑定、机器人未启用、C2C 关闭或渠道离线时，发布被阻止且页面提供中文下一步。
- 定时运行始终使用工作流所有者当时仍有效的绑定；解绑后不会发送到旧目标。
- QQ 返回接受时运行记录显示发送请求已被平台接受，不表述为必达；超时不会自动再发一遍。
- 模板仍能直接生成完整可编辑画布，并依据当前账号能力提供合适的结果出口。
