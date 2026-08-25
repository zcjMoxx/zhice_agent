# 外部连接测试说明

## 测试目标

验证用户级邮件连接的凭据加密、owner 隔离、SMTP 安全参数、官方通知邮箱验证和结构化错误边界；默认单元测试不连接真实 SMTP。

## 用例覆盖

- AES-256-GCM 加解密、AAD ownership 隔离、篡改检测和非法密钥。
- SQLite connection CRUD 不返回密文，并对读取、修改和删除执行 owner 校验。
- 个人 SMTP 拒绝明文端口组合和不匹配的安全模式；真实 SMTP 只在显式集成测试执行。
- 发件显示地址和加密保存的 From 都从邮箱账号派生，用户不能提交互相冲突的值。
- Runtime 明确拒绝旧的非 SMTP Provider，不做静默兼容。
- 测试发送只报告 Provider accepted，不宣称最终送达。
- 已验证个人 SMTP 只在用户尚无通知邮箱时成为默认“我的邮箱”；已有已验证地址不被覆盖。
- 官方验证码和测试通知只发给当前用户的邮箱；个人 SMTP 仍是可选代发连接。
- 验证码请求成功返回 60 秒 `retry_after_seconds`；冷却期内返回结构化 `NOTIFICATION_EMAIL_VERIFICATION_RATE_LIMITED`，不再次调用 SMTP。

## 关键检查点

- API、日志和运行摘要均不返回授权码、密文、验证码或完整异常。
- 连接 id 不能绕过当前 actor ownership；管理员也不能借管理权限代用他人连接。
- 官方通知与个人代发是两条独立能力，缺少任一配置只局部降级。
