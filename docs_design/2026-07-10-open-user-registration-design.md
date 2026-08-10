# 公开用户自助注册设计

> 说明：本文记录公开注册初版；当前普通注册已增加 Owner 总开关并默认关闭，前后端共同执行，见 `2026-08-10-owner-registration-control-design.md` 与 Part 9 活文档。本文正文保留当时方案。

> 说明：本文记录公开注册初版及当时“首管理员优先”的边界。当前 Web 注册只填写用户名和密码、固定获得 `viewer`，并允许在 Owner 初始化前注册；请参考 `docs_design/2026-07-10-registration-form-simplification-design.md`、`docs_design/2026-07-10-owner-admin-delegation-design.md` 和 Part 9 活文档。
>
> 日期：2026-07-10
>
> 状态：已实现
>
> 承接记录：`docs_design/2026-07-10-web-first-admin-bootstrap-design.md`
>
> 关联活文档：`docs_design/zhice-agent-part9-user-auth-permission-design.md`

## 1. 背景

首管理员 Web bootstrap 已解决空 workspace 的初始化问题，但初始化完成后登录页只允许已有账号登录，普通用户必须由管理员创建。当前产品口径调整为开放本地用户自助注册。

公开注册不能复用 bootstrap：bootstrap 创建唯一首管理员；普通注册只能创建低权限用户，不能让请求方指定角色或权限。

## 2. 目标

1. 初始化完成后，登录页持续展示普通用户注册入口。
2. 用户自定义用户名、显示名和密码。
3. 注册成功后自动创建登录态并进入系统。
4. 普通注册固定授予内置 `viewer` 角色。
5. 用户不能通过注册请求指定 `admin`、`developer`、`auditor` 或权限 key。
6. 保留首管理员 bootstrap 和管理员用户管理能力。

## 3. 范围边界

包含：

- `POST /api/auth/register` 匿名访问路径。
- 登录页普通注册表单。
- 用户名、显示名、密码校验。
- 重复用户名稳定错误。
- 注册成功自动登录和审计。

不包含：

- 注册时自选角色或权限。
- 邮箱/手机验证、验证码、邀请制、注册审批。
- 密码找回、OAuth/SSO。
- 面向公网的限流、WAF 或反滥用体系。

## 4. 安全边界

- auth 系统必须先有首管理员；没有用户时普通注册返回 `AUTH_SETUP_REQUIRED`，避免第一个账号绕过 bootstrap 成为无管理系统的普通用户。
- 请求 schema 只接受 `username`、`display_name`、`password`。
- 服务端调用 `create_user(..., role_keys=["viewer"])`，不读取客户端角色字段。
- SQLite 唯一约束继续保护重复用户名；API 对外返回 `USERNAME_ALREADY_EXISTS`。
- 注册、登录和 token 审计不记录明文密码或 token。
- 当前 gateway 仍定位为本地开发服务；公开注册不表示可以直接安全暴露到公网。

## 5. 模块设计

### 5.1 AuthService

新增 `register_user()`：

1. 检查 `store.has_users()`；未完成 bootstrap 时返回 503。
2. 创建固定 `viewer` 角色用户。
3. 创建 auth session。
4. 记录 `auth.user_registered` 和登录成功事件。
5. 返回 `AuthLogin`。

### 5.2 HTTP

```text
POST /api/auth/register
```

请求：

```json
{
  "username": "alice",
  "display_name": "Alice",
  "password": "用户自定义密码"
}
```

响应沿用 `AuthMutationResponse(status="authenticated", user=...)`，并设置现有 HttpOnly cookie。

### 5.3 Web UI

- 已初始化且未登录：显示 “Create account”。
- 未初始化：同时保留首管理员 “Create administrator account”，普通注册入口可以显示，但提交时必须提示先创建管理员；第一版 UI 直接只显示管理员入口，完成初始化后再显示普通注册。
- 普通注册表单包含用户名、显示名、密码、确认密码。
- 成功后复用 `showApp()`。

## 6. 数据流

```text
browser registration form
  -> POST /api/auth/register
  -> AuthService.register_user
  -> SQLiteAuthStore.create_user(role_keys=[viewer])
  -> create auth session
  -> Set-Cookie zcagent_session
  -> GET /api/auth/me
  -> showApp
```

## 7. 变更文件

- `agent/app/auth.py`
- `agent/app/api/schemas.py`
- `agent/app/api/routes.py`
- `agent/app/gateway.py`
- `web/static/index.html`
- `web/static/app.js`
- `web/static/styles.css`
- `tests/unit_test/app/test_auth_routes.py`
- `tests/unit_test/app/test_case.md`
- `README.md`
- `docs_design/README.md`
- `docs_design/zhice-agent-part9-user-auth-permission-design.md`
- `docs_design/zhice-agent-overall-design.md`

## 8. 测试方案

| 用例 | 状态/输入 | 期望 |
| --- | --- | --- |
| 普通注册 | 已有 admin，合法字段 | 创建 viewer、设置 cookie、自动登录 |
| 未初始化注册 | 无用户 | 503 `AUTH_SETUP_REQUIRED` |
| 重复用户名 | 用户名已存在 | 409 `USERNAME_ALREADY_EXISTS` |
| 非法字段 | 弱密码/非法用户名/空显示名 | 400，不创建用户 |
| 越权字段 | 请求附带 roles/permissions | schema 忽略或拒绝，最终只能是 viewer |
| 登录页入口 | 已初始化未登录 | 展示普通注册入口 |

## 9. 验收标准

1. 已初始化 workspace 的未登录用户可以通过 Web 自定义注册。
2. 注册用户只能获得 `viewer` 角色。
3. 注册成功后自动登录，cookie 继续使用 HttpOnly、SameSite=Lax。
4. 首管理员 bootstrap 行为保持不变。
5. API 不接受角色和权限自定义。
6. 全量 pytest、ruff、前端语法与浏览器冒烟通过。
