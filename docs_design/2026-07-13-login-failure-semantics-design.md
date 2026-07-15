# 登录失败语义调整设计

## 背景

当前 `AuthService.login` 在数据库没有任何用户时返回 `AUTH_SETUP_REQUIRED`，并把“用户名不存在”和“密码错误”统一为 `AUTH_INVALID_CREDENTIALS`。当前产品明确要求登录页面给出可区分的本地账号诊断，不再使用防枚举的统一失败提示。

## 目标

1. 登录接口不再通过 `AUTH_SETUP_REQUIRED` 暴露或处理空用户库状态。
2. 用户名不存在与密码不正确时对外统一返回 `AUTH_INVALID_CREDENTIALS`。
3. 审计日志分别记录 `AUTH_USER_NOT_FOUND` 或 `AUTH_INVALID_PASSWORD`。
4. 禁用账号返回 `AUTH_ACCOUNT_DISABLED`，不伪装为密码错误。

## 范围边界

仅改变本地登录 API 的失败契约和前端显示；不改变 Owner bootstrap、注册、密码 hash 或 token 会话机制。该选择会允许外部调用方枚举本地用户名，适用于当前明确限定的本地开发部署，不应直接复用于公网身份系统。

## 数据流

```text
POST /api/auth/login
  -> lookup username
  -> absent: audit AUTH_USER_NOT_FOUND + public AUTH_INVALID_CREDENTIALS (401)
  -> disabled: AUTH_ACCOUNT_DISABLED (403)
  -> verify password through store.login
  -> mismatch: audit AUTH_INVALID_PASSWORD + public AUTH_INVALID_CREDENTIALS (401)
  -> success: create auth session
```

## 变更文件

- `agent/protocols/errors.py`：新增稳定错误码。
- `agent/app/auth.py`：按用户状态和密码结果返回明确错误。
- `tests/unit_test/app/test_auth_routes.py`：覆盖空库、用户不存在、密码错误、禁用账号。
- `docs_design/zhice-agent-part9-user-auth-permission-design.md`：同步当前登录错误契约。

## 验收标准

- 任意空用户库登录不再返回 `AUTH_SETUP_REQUIRED`。
- 用户不存在和密码错误共享公开错误码，但保留各自审计原因；禁用账号仍有明确错误。
