# ZhiCe-Agent Owner 注册开放控制设计

> 日期：2026-08-10
>
> 状态：已实现并部署；普通注册当前保持关闭

> 实施验收：2026-08-10 已完成后端强制校验、Owner 管理开关、前端入口联动、审计与持久化；本地全量测试及云端真实部署验收通过。云端 `models.json` 同步使用宿主机权威副本和容器只读挂载，具体私有地址与配置不写入仓库文档。

## 1. 背景

当前普通用户自助注册长期开放，注册者虽然只能获得 `viewer`，但系统尚未提供验证码、邀请、邮箱验证、频率限制或反滥用能力。主站进入公网后，仅依赖低权限角色不足以阻止批量账号创建和资源消耗。Owner 需要一个立即生效、跨重启保留且同时约束前后端的注册总开关。

## 2. 目标

- Owner 可在管理后台“账号管理”中查看和修改“允许新用户注册”。
- 默认关闭普通自助注册，Owner 明确开启后才开放。
- 关闭时普通登录页和 QQ 绑定认证页都不展示注册入口。
- 后端 `POST /api/auth/register` 独立强制校验，不能通过直接调用接口绕过前端。
- 管理员通过账号管理手工创建用户不受该开关影响。
- 策略跨 Gateway、容器与服务器重启保留，并记录安全审计。

## 3. 范围边界

本次不实现验证码、邀请制、注册审批、邮箱/手机验证、账号配额、IP 限流或自动封禁，也不改变普通注册固定获得 `viewer` 的边界。Owner 初始化入口继续独立受 setup credential 保护，不受普通注册开关控制。

## 4. 模块设计

### 4.1 持久状态

`auth.sqlite3` 新增单行 `auth_settings`：

```text
id=1
registration_enabled=0|1
updated_at=<UTC ISO timestamp>
updated_by_user_id=<Owner user id or empty>
```

schema 初始化幂等插入默认 `0`。旧数据库升级后同样默认关闭，Owner 可主动开启。读取异常 fail closed。

### 4.2 API

```text
GET   /api/auth/registration-policy        anonymous safe projection
GET   /api/admin/auth/registration-policy Owner only
PATCH /api/admin/auth/registration-policy Owner only
POST  /api/auth/register                   policy enforcement before creation
```

公开投影只返回 `registration_enabled`，不返回更新时间或操作者。Owner 修改成功写入 `auth.registration_policy_updated` 审计；关闭状态下的注册尝试返回 `403 AUTH_REGISTRATION_DISABLED` 并记录拒绝审计。

### 4.3 前端

`AuthLayout` 初始化时读取公开策略，默认按关闭渲染，只有明确得到 `true` 后展示桌面和移动端注册切换。策略读取失败继续只显示登录。若注册模式期间策略刷新为关闭，立即退回登录模式。

管理后台账号页向 Owner 展示策略卡与 checkbox；保存期间禁用控件，成功后同步匿名 Auth store 状态。非 Owner 即使拥有用户管理权限也看不到且不能调用策略接口。

## 5. 数据流

```text
Owner toggles policy
  -> PATCH owner-only API
  -> auth_settings transaction
  -> audit allow
  -> UI state refresh

Anonymous page
  -> GET public policy
  -> enabled: show login/register
  -> disabled/error: login only

Direct register POST
  -> server reads auth_settings
  -> disabled: 403 + audit deny
  -> enabled: existing viewer registration flow
```

## 6. 变更文件

- `agent/auth/schema.py`、`agent/auth/store.py`
- `agent/protocols/errors.py`
- `agent/app/gateway.py`
- `agent/app/api/schemas.py`、`agent/app/api/routes.py`
- `web/frontend/src/api/`、`stores/`、`layouts/AuthLayout.vue`、`layouts/AdminLayout.vue`、样式
- `tests/unit_test/auth/`、`tests/unit_test/app/`、前端测试及各自 `test_case.md`
- Part 9/16 活文档、总体设计、README 和交叉引用

## 7. 测试方案

- Store：新库和旧库迁移默认关闭、开关持久化、非法直接值受 schema 约束。
- API：公开读取、默认拒绝、开启后注册、关闭后既有登录不受影响、Owner 修改、Admin/匿名拒绝、审计内容。
- 前端：默认隐藏、服务端开启后展示、QQ binding 同样隐藏、Owner 控件加载与保存、非 Owner 不展示。
- Ruff、全量 pytest、前端 lint/typecheck/test/build。
- 真实部署：默认/当前策略读取、关闭时接口 403 与页面无注册入口、Owner 登录后切换、重启后保持。

## 8. 验收标准

1. 未明确开启时，任何匿名请求都不能创建账号。
2. 前端隐藏与后端拒绝使用同一持久真值，不存在只隐藏按钮的伪安全。
3. 只有 Owner 能修改策略；普通 Admin 的用户创建能力不等于注册策略管理权。
4. 关闭注册不影响已有用户登录或管理员手工创建用户。
5. 策略变更和关闭状态下的注册尝试可审计，且不记录密码。
