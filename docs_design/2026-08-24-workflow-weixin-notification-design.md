# 工作流发送到我的微信设计

## 背景

ZhiCe-Agent 已支持 Owner 绑定微信账号，并通过微信 Sidecar 在已有一对一会话上下文中回复消息。工作流目前支持保留结果、我的邮箱、个人 SMTP 和我的 QQ，但没有微信结果投递节点。微信发送协议要求 `peer + context_token`；Sidecar 已将真实 token 保存在自己的私有状态中，Python 侧只持有不可逆的安全引用 `context_token_ref`。

当前本地环境已验证存在 active 微信账号、active 外部身份、近期会话上下文和成功 Outbox 记录，因此具备真实验收条件。

## 目标

- 工作流“发送结果”支持“发送到我的微信”。
- 只允许发送到当前工作流 Owner 已绑定的微信身份，禁止填写任意微信号、联系人或群。
- 发布与运行时均校验绑定、渠道、账号和会话上下文状态。
- 复用微信持久化 Outbox、分段、串行发送和恢复机制。
- 工作流执行记录保留发送内容安全摘要和微信投递回执。
- Python、数据库和前端都不暴露真实微信标识或 context token。

## 范围边界

- 仅支持当前账号自己的微信一对一通知，不支持任意联系人、群聊、附件、卡片和广播。
- 这不是微信模板消息；必须先在微信中给智策发送过消息，以取得可用会话上下文。
- 工作流节点不直接调用 Sidecar，也不读取 `context.json`；所有发送经 `WeixinNotificationProvider` 和微信 Adapter。
- Sidecar 继续独占真实 context token；AuthStore 只保存安全引用。

## 模块设计

### 安全投递上下文

AuthStore 新增 `weixin_delivery_contexts`，按 `account_key + peer` 保存最近的 `context_token_ref` 和更新时间。微信 Adapter 接收到合法的一对一消息后更新该记录，并用新引用刷新同一目标仍为 pending 的 Outbox 项。解绑时同步删除。

为兼容升级前已有记录，查询投递上下文时可从最近的微信 Outbox 安全引用回填；若两处都没有，要求用户先在微信中给智策发送一条消息。

### 微信通知 Provider

`WeixinNotificationProvider` 按内部 Owner ID 解析绑定账号，不返回平台标识。能力状态依次检查：

1. 当前用户存在绑定；
2. 绑定状态为 active；
3. 微信 Adapter 已装载且健康；
4. 存在安全会话上下文。

发送时生成与工作流运行和节点绑定的稳定投递键，调用 Adapter 主动文本发送接口。Adapter 复用现有分段、Outbox 幂等、账号发送锁和 Sidecar `message.send`。

### 工作流节点

新增固定节点类型 `weixin_notification`：

- 需要 `workflow.notify.self` 权限；
- 需要 `send_consent_at`；
- 输入来自唯一上游结果，可附加固定前缀；
- 超时属于外部操作结果未知，不由 DAG Executor 重试；
- 返回安全回执 `{status, channel, chunks}`。

### 前端

“结果怎么处理”增加“发送到我的微信”。不可用时禁用并显示具体原因：未绑定、渠道不可用、需要重新连接或缺少会话上下文。用户不填写微信号。微信节点纳入统一发送授权、图输入绑定和执行记录“发送内容摘要 / 微信投递结果”。

## 数据流

```text
微信用户发消息
  -> Sidecar 保存真实 context token
  -> Adapter 收到安全 context_token_ref
  -> AuthStore 保存最新安全引用

工作流产生结果
  -> weixin_notification
  -> WeixinNotificationProvider 解析当前 Owner 绑定
  -> Adapter 分段并写入 Outbox
  -> Sidecar 使用私有 token 发送
  -> Outbox 标记 sent
  -> 工作流执行记录保存内容摘要与安全回执
```

## 变更文件

- `agent/auth/schema.py`、`agent/auth/store.py`
- `agent/channels/weixin/adapter.py`、`notification.py`、`__init__.py`
- `agent/workflows/schemas.py`、`catalog.py`、`nodes.py`、`executor.py`、`runtime.py`、`store.py`、`node_red.py`
- `agent/app/runtime.py`
- 工作流前端类型、编辑器、展示、模板与页面
- 对应 Auth、微信、工作流和前端测试及测试说明

## 测试方案

- AuthStore：安全上下文写入、升级前 Outbox 回填、pending 引用刷新、解绑清理。
- 微信 Provider：未绑定、渠道不可用、缺少上下文、成功发送与结果未知。
- Adapter：主动发送分段、幂等 Outbox 和确认回执。
- 工作流：节点处理、权限、授权、发布复检、超时不重试和运行记录。
- 前端：选项、能力提示、授权、定义序列化、执行记录语义。
- 完整 Ruff、工作流/微信/Auth 测试、前端测试、类型检查和生产构建。

## 验收标准

- Owner 可选择“发送到我的微信”，无需填写微信号。
- 无有效上下文时发布被阻止，并明确提示先在微信中发送一条消息。
- 有效上下文下真实工作流消息到达 Owner 微信。
- 执行记录展示发送正文摘要和微信投递结果，不展示平台标识、token 或凭据。
- 服务重启后已确认发送不重复，pending 发送可按现有恢复机制处理。
