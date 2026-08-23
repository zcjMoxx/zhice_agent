# 工作流个人邮箱 SMTP-only 收敛设计

> 说明：当前界面已进一步改为服务商预设和邮箱账号自动作为发件地址，旧文中的全量 SMTP 字段表单不再适用；请参考 `2026-08-22-workflow-friendly-email-connection-design.md` 和 Part 20 活文档。

> 状态：已实施并完成全量测试与真实浏览器验收

## 背景

工作流个人邮箱原先同时实现 Gmail OAuth、Microsoft OAuth 和个人 SMTP。当前产品明确不考虑 Gmail 与 Microsoft，继续保留未启用入口、OAuth 回调、Token 刷新和两套发送 Provider 只会增加配置、维护和用户理解成本。本次将个人邮箱完整收敛为 SMTP 授权码模式。

## 目标

- 用户界面只出现一个“连接邮箱”入口，不出现 Gmail、Microsoft 或 OAuth。
- 每个用户分别填写自己的 SMTP 服务、邮箱账号、授权码和发件地址。
- 授权码仍由服务端主钥匙 AES-256-GCM 加密，并按用户和连接隔离。
- 工作流只保存连接 ID，发布和运行继续校验连接所有权和状态。
- 删除不再使用的 OAuth 路由、状态表逻辑、Provider、前端回调页、配置和测试。

## 范围边界

- 保留个人 SMTP 和平台官方 SMTP 两条独立链路。
- 不影响 MCP Runtime 自身的 OAuth refresh；本次只删除工作流个人邮箱 OAuth。
- 不迁移或伪装既有 Google/Microsoft 连接；若数据库中存在旧类型记录，读取时返回不支持，用户可删除。
- 不内置邮箱服务商密码，不接受网页登录密码，只使用用户从服务商生成的 SMTP 授权码。

## 模块设计

### 连接运行时

`ConnectionRuntime` 只负责 SMTP 连接的创建、列举、删除、发布校验、测试发送和工作流发送。连接类型固定为 `smtp_personal`，其他类型 fail closed。

### 存储

保留通用加密连接表和审计表，移除 OAuth state 表及创建、消费、Token 刷新方法。主钥匙仍由 `ZHICE_AGENT_CREDENTIAL_ENCRYPTION_KEY` 提供。

### API 与前端

API 只保留连接列表、创建 SMTP、删除连接和测试发送。设置页只展示一个 SMTP 卡片和表单；删除 OAuth 回调路由和页面。

### 配置

删除 Google/Microsoft Client ID、Client Secret 和 redirect URI 示例。个人 SMTP 无需管理员配置邮箱账号，用户在页面内分别填写；管理员只需设置连接加密主钥匙。

## 数据流

```text
用户填写 SMTP 信息与授权码
  -> POST /api/connections/email/smtp
  -> TLS 连通性验证
  -> AES-GCM 加密写入本人连接库
  -> 工作流 personal_email 节点保存 connection_id
  -> 运行时校验本人连接并通过 SMTP 发送
```

## 变更文件

- `agent/connections/`：删除 OAuth 协议、状态和刷新逻辑。
- `agent/integrations/email/`：删除 Gmail 与 Microsoft Provider。
- `agent/app/runtime.py`、`agent/app/api/connection_routes.py`：只组装和暴露 SMTP。
- `web/frontend/src/components/SettingsCenter.vue`：单一 SMTP 产品入口。
- `web/frontend/src/api/`、`web/frontend/src/router/`、回调页面：删除 OAuth API 和路由。
- `config/`、`README.md`、Part 20 活文档及测试：同步当前口径。

## 测试方案

- 后端：密钥、加密隔离、SMTP 安全端口、连接所有权、创建、测试发送、删除和不支持旧 Provider。
- 前端：设置页不包含 Gmail/Microsoft，只显示 SMTP 连接、表单和测试发送。
- 回归：Ruff、Pytest、ESLint、Vue typecheck、Vitest、production build。
- 浏览器：重启后以普通账号打开“连接与账号”，确认只剩 SMTP，且按钮和表单可操作。

## 验收标准

- 源码和产品配置中不存在工作流 Gmail/Microsoft OAuth 实现。
- 页面不出现 Gmail、Microsoft、“系统未启用”或 OAuth 回调入口。
- 个人 SMTP 仍按用户隔离并加密保存。
- 全量测试和生产构建通过。
- 下一步只向用户索取一个实际邮箱的 SMTP 授权码并完成真实收件验收。
