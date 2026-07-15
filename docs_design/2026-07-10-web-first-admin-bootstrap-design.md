# Web 首管理员注册入口设计

> 说明：本文记录首管理员一次性 bootstrap 的旧方案。当前代码改为部署 Secret 保护的唯一 Owner 初始化，普通用户可在 Owner 初始化前后注册；请参考 `docs_design/2026-07-10-owner-admin-delegation-design.md` 和 Part 9 活文档。
>
> 日期：2026-07-10
>
> 状态：已实现
>
> 关联活文档：`docs_design/zhice-agent-part9-user-auth-permission-design.md`

## 1. 背景

Part 9 当前要求用户先在本地终端执行 `zcagent auth init-admin`，登录页只展示命令提示。该方式安全但首次使用割裂：用户已经打开 Web，却必须切回终端完成初始化，而且页面没有可操作入口。

本次增加 Web 首管理员注册，但不开放初始化后的匿名注册。它是 CLI `init-admin` 的同一语义入口，不是普通用户自助注册系统。

## 2. 目标

1. auth 数据库没有任何用户时，登录页显示“创建管理员账号”入口。
2. 用户可填写用户名、显示名和密码创建第一位 `admin` 角色用户。
3. 创建成功后设置现有 HttpOnly cookie 并自动进入 Web。
4. 初始化完成后立即关闭 bootstrap API；后续用户继续由管理员管理页创建。
5. 保留 `zcagent auth init-admin` 作为本地恢复和无浏览器初始化入口。

## 3. 范围边界

包含：

- 一次性 `POST /api/auth/bootstrap`。
- 未初始化登录页的注册视图。
- 首管理员创建、自动登录和安全审计。
- 重复初始化、非法输入和并发竞争的稳定错误。

不包含：

- 初始化后的公开注册。
- 邮箱/短信验证、邀请链接、找回密码。
- OAuth/SSO、组织、租户或公网部署安全方案。

## 4. 模块设计

### 4.1 AuthStore

- `initialize_first_admin()` 继续作为首用户唯一创建入口。
- 在写入 schema 前完成用户名、显示名和密码基础校验，避免非法输入留下“有表无用户”却被 Web 误判为已初始化的状态。
- 首用户创建仍授予内置 `admin` 角色。

### 4.2 AuthService

新增 `bootstrap_first_admin()`：

1. 检查当前是否已有用户。
2. 调用 AuthStore 创建首管理员。
3. 创建 auth session。
4. 记录 `auth.bootstrap_completed` 和登录成功审计。
5. 返回一次性 opaque token，由 HTTP 层写入 cookie。

### 4.3 HTTP

```text
POST /api/auth/bootstrap
```

请求字段：

```json
{
  "username": "admin",
  "display_name": "Administrator",
  "password": "用户输入的密码"
}
```

该路由和 `/api/auth/login`、`/api/health` 一样允许未登录访问，但只能在没有用户时成功。已有用户时返回 `409 AUTH_ALREADY_INITIALIZED`。

### 4.4 Web UI

- `/api/auth/me` 返回 `AUTH_SETUP_REQUIRED` 时显示注册入口和初始化说明。
- 注册表单包含用户名、显示名、密码、确认密码。
- 创建成功后复用当前 `showApp()` 流程。
- 普通 401 登录失败时不展示注册入口。

## 5. 数据流

```text
browser bootstrap form
  -> POST /api/auth/bootstrap
  -> AuthService.bootstrap_first_admin
  -> SQLiteAuthStore.initialize_first_admin
  -> create auth session
  -> Set-Cookie zcagent_session
  -> GET /api/auth/me
  -> showApp
```

## 6. 变更文件

- `agent/auth/store.py`
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
- `docs_design/zhice-agent-part9-user-auth-permission-design.md`
- `docs_design/zhice-agent-overall-design.md`

## 7. 测试方案

| 用例 | 输入/状态 | 期望 |
| --- | --- | --- |
| 首次注册 | 无用户，合法表单 | 创建 admin、设置 cookie、自动登录 |
| 重复注册 | 已有用户 | 409，不创建第二个用户 |
| 弱密码 | 少于 8 字符 | 400，不创建用户 |
| 非法用户名 | 不符合用户名规则 | 400，不创建用户 |
| 登录页状态 | `AUTH_SETUP_REQUIRED` | 只在此状态显示注册入口 |
| 初始化后登录 | 已有用户 | 只显示登录，不显示公开注册 |

## 8. 验收标准

1. 新 workspace 可以完全通过 Web 创建第一位管理员并进入聊天页。
2. CLI `init-admin` 的默认用户名、显示名和交互式密码行为保持不变。
3. 第一位用户创建后，bootstrap API 永久拒绝再次调用。
4. 初始化后的普通账号仍只能由有权限的管理员创建。
5. 密码、token 不进入日志、响应或 URL。
6. auth/API/UI 定向测试、全量 pytest、ruff 和前端语法检查通过。
