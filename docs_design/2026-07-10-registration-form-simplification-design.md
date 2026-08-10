# 登录与注册表单简化设计

> 说明：本文的表单简化仍有效；注册入口是否展示现由 Owner 持久开关决定并默认关闭，见 `2026-08-10-owner-registration-control-design.md`。本文正文保留当时方案。

> 说明：本文的表单简化仍有效；首个特权账号已从 admin 改为部署 Secret 保护的唯一 Owner，普通用户可在 Owner 初始化前后注册。当前权限口径参考 `docs_design/2026-07-10-owner-admin-delegation-design.md` 和 Part 9 活文档。

> 日期：2026-07-10
>
> 状态：已实现
>
> 承接记录：`docs_design/2026-07-10-open-user-registration-design.md`
>
> 关联活文档：`docs_design/zhice-agent-part9-user-auth-permission-design.md`

## 1. 背景

公开注册初版同时要求用户名和显示名，增加了首次注册的理解成本；登录和注册输入框还在上方重复展示字段名称，视觉层级较重。当前 UI 参考简洁账号页，改为图标和灰色 placeholder，并把显示名延后到登录后的个人设置。

## 2. 目标

1. 普通 Web 注册只填写用户名、密码和确认密码。
2. Web 首管理员注册同样不填写显示名。
3. 服务端统一令新 Web 账号 `display_name = username`。
4. CLI `init-admin --display-name` 保持不变。
5. 用户登录后仍可在 Account settings 修改显示名。
6. 登录、首管理员注册、普通注册输入框使用图标和灰色 placeholder，不在输入框上方重复字段名。
7. 登录/注册切换使用轻量文字入口，主按钮保持整行强调。

## 3. 范围边界

包含：

- `BootstrapAdminRequest` 和 `RegisterUserRequest` 移除 `display_name`。
- AuthService 从 username 派生 display name。
- Web 登录/注册表单布局与提示文字调整。
- API、静态 UI 和浏览器回归测试。

不包含：

- 修改数据库字段；`display_name` 仍是必填持久化字段。
- 删除个人设置中的显示名编辑。
- 登录记住密码、找回密码、手机号或钉钉绑定。
- 修改角色、权限或注册开放策略。

## 4. 模块设计

### 4.1 API

普通注册：

```json
{
  "username": "alice",
  "password": "用户密码"
}
```

首管理员 Web bootstrap 使用相同字段。服务端忽略客户端额外提交的 `display_name`，并调用：

```text
create user(username=username, display_name=username)
```

CLI 仍可显式传入 `--display-name`，不受 Web 简化影响。

### 4.2 UI

输入框统一结构：

```text
[user icon]  Enter username
[lock icon]  Enter password
```

注册增加确认密码，字段说明通过 placeholder 和 `aria-label` 提供。首管理员入口与普通注册共用同一视觉结构，但保持不同业务文案。

## 5. 数据流

```text
registration form(username, password)
  -> POST /api/auth/register
  -> AuthService.register_user
  -> create_user(username, display_name=username, viewer)
  -> auto login
  -> optional Account settings display-name update
```

## 6. 变更文件

- `agent/app/api/schemas.py`
- `agent/app/api/routes.py`
- `agent/app/auth.py`
- `web/static/index.html`
- `web/static/app.js`
- `web/static/styles.css`
- `tests/unit_test/app/test_auth_routes.py`
- `tests/unit_test/app/test_case.md`
- `README.md`
- `docs_design/README.md`
- `docs_design/zhice-agent-part9-user-auth-permission-design.md`
- `docs_design/zhice-agent-overall-design.md`

## 7. 测试方案

| 用例 | 输入 | 期望 |
| --- | --- | --- |
| 普通注册 | username/password | display_name 等于 username，角色 viewer |
| 伪造显示名 | 额外 display_name | 不改变派生结果 |
| Web bootstrap | username/password | admin 的 display_name 等于 username |
| 登录表单 | 页面加载 | 图标和 placeholder 可见，无外部 Username/Password 标签 |
| 注册表单 | 打开弹窗 | 只有用户名、密码、确认密码 |
| 个人设置 | 注册后修改显示名 | 仍可成功修改并展示 |

## 8. 验收标准

1. 普通注册和 Web bootstrap 不再要求 display name。
2. 新 Web 用户数据库 display_name 默认等于 username。
3. API 额外传 display_name 不会覆盖默认值。
4. 登录和注册输入框均有图标、灰色 placeholder 和无障碍名称。
5. 全量 pytest、ruff、前端语法和真实浏览器验证通过。
