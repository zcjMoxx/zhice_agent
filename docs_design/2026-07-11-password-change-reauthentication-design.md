# 修改密码后重新认证设计

## 背景

Part 9 第一版在用户修改密码后保留当前浏览器登录态，只撤销其它 auth session。实际体验中，用户已经完成高风险凭据变更，却仍停留在原应用页面，既不符合常见产品预期，也使“新密码是否真实生效”缺少一次明确验证。

## 目标

1. 修改密码成功后撤销该用户的全部 auth session，包括当前会话。
2. HTTP 响应清除当前浏览器的认证 Cookie。
3. 前端立即关闭账号设置并返回登录页，提示使用新密码重新登录。
4. 当前密码错误时保持原密码和全部登录态不变。

## 范围边界

- 不改变 Owner/Admin/Viewer 权限模型。
- 不修改 CLI 管理员重置密码的语义；CLI 重置本来就撤销全部会话。
- 不引入 refresh token 或免密续签。

## 模块设计

- `SQLiteAuthStore.change_password()` 在密码校验和密码更新成功的同一事务中撤销全部未撤销会话。
- `AuthService.change_password()` 不再传递保留会话参数，审计元数据记录 `all_sessions_revoked=true`。
- `POST /api/auth/password` 成功后清除 `zcagent_session` Cookie，返回 `reauthentication_required`。
- Web 收到成功结果后关闭 Account settings、清理本地用户态与 WebSocket，并显示登录页。

## 数据流

```text
authenticated user submits current/new password
  -> verify current password
  -> update password hash/salt
  -> revoke all auth_sessions for user
  -> clear response cookie
  -> frontend closes account dialog
  -> frontend shows login page
  -> user signs in with new password
```

## 变更文件

- `agent/auth/store.py`
- `agent/app/auth.py`
- `agent/app/api/routes.py`
- `web/static/app.js`
- `tests/unit_test/auth/test_auth_store.py`
- `tests/unit_test/app/test_auth_routes.py`
- Part 9、README 和测试说明。

## 测试方案

| 用例 | 预期 |
|---|---|
| 正确当前密码 | 更新密码并撤销当前及其它登录态 |
| API 改密成功 | 返回 `reauthentication_required`、清 Cookie、随后 `/auth/me` 为 401 |
| 使用旧密码登录 | 失败 |
| 使用新密码登录 | 成功 |
| 当前密码错误 | 密码和全部登录态保持不变 |

## 验收标准

1. 改密成功后原页面不能继续使用受保护 API。
2. 浏览器立即回到登录界面。
3. 只有新密码可以重新登录。
4. 改密失败不产生部分更新。
