# Owner 与管理员委派设计

> 说明：Owner 唯一性和委派边界继续有效；普通自助注册现由 Owner 独占的持久开关控制并默认关闭，见 `2026-08-10-owner-registration-control-design.md`。本文正文保留当时方案。

## 背景

Part 9 第一版把 `admin` 作为最高角色，并允许拥有通用用户管理权限的管理员直接替换用户角色。该模型不能表达唯一系统拥有者，也无法阻止管理员互相降级、修改内置角色后间接提权。普通注册在首管理员创建前也被阻塞，不适合先积累普通用户、再完成云端治理初始化的部署方式。

本文替代一次性“首 admin”模型。旧日期记录保留当时方案，当前实现和活文档以本文为准。

## 目标

1. 初始化入口创建唯一、永久、不可降级的 `owner` 用户。
2. 普通用户在 Owner 尚未创建时仍可注册，固定获得 `viewer`。
3. 普通 `admin` 负责用户和系统日常运营，但默认不能任命管理员。
4. Owner 可以向指定 Admin 直接委派 `auth.admin.manage`；委派不会随管理员任命传播。
5. 拥有该委派的 Admin 可以提升或降级普通 Admin，因此不维护管理员上下级树。
6. Owner、Owner 角色、内置角色核心权限和管理员管理委派都不能被普通管理 API 绕过。

## 范围边界

- 本次不建设通用审批流；管理员推荐第一版只保留后续扩展点。
- 不维护 A -> B -> C 的组织树或授权祖先关系。
- 不允许创建第二个 Owner，也不允许通过普通角色数组授予 Owner。
- CLI 是基础设施恢复入口，但不是数据库里的第二个 Owner。
- Web Owner 初始化必须校验部署 Secret `ZHICE_AGENT_SETUP_TOKEN`；未配置时关闭 Web 初始化，只保留服务器 CLI。
- Owner 初始化页面只通过隐藏入口 `/_setup` 提供；普通登录页、注册页和账号菜单不展示入口。路径隐藏只是额外防护，真正的安全边界仍是部署 Secret 和 Owner 唯一性校验。
- Web 初始化用户名固定为 `owner`，不接受客户端自定义；页面只输入一次 Owner 密码和一次 setup credential，不设置确认密码框。

## 模块设计

### 内置角色与权限

- `owner`：拥有全部权限，唯一且不可变。
- `admin`：拥有日常用户管理、审计、诊断和运行管理权限，但不包含 `auth.admin.manage` 和 `auth.roles.manage`。
- `viewer` / `developer` / `auditor`：保持各自使用边界。
- `auth.admin.manage`：仅由 Owner 直接委派给已是 Admin 的用户。

直接委派存入 `user_permissions`，不修改 `admin` 角色权限，因此 A 提升 B 后，B 不会自动获得继续任命管理员的能力。

### Owner 不变量

- `owner` 角色最多且必须只授予一个用户。
- Owner 不能被禁用、删除、降级或通过通用用户更新移除角色。
- `owner`、`admin` 内置角色权限不能通过普通角色编辑接口修改。
- Owner 可以修改自己的资料和密码；CLI 可重置同一 Owner 的密码。

### 用户管理策略

- 普通 Admin 可以创建、更新、禁用非 Admin/Owner 用户，并分配非特权角色。
- 普通 Admin 不能创建 Admin、修改现有 Admin，也不能操作 Owner。
- 拥有 `auth.admin.manage` 的 Admin 可以提升和降级普通 Admin，但不能管理 Owner，也不能授予该委派。
- Owner 可以管理普通 Admin，并为 Admin 开关 `auth.admin.manage`。

## 数据流

```text
CLI/Web owner bootstrap
  -> GET /_setup（仅 Owner 不存在且 setup token 已配置时返回页面）
  -> initialize schema
  -> verify deployment setup token for Web
  -> ensure no owner exists
  -> create owner role user
  -> normal login

public register
  -> initialize schema if needed
  -> create viewer
  -> owner existence is not a prerequisite

owner delegates admin management
  -> verify target has admin
  -> insert user_permissions(auth.admin.manage)
  -> actor permission aggregation includes direct grants
```

## 变更文件

- `agent/auth/schema.py`：Owner 角色、管理员委派权限和直接权限表。
- `agent/auth/store.py`：Owner 初始化、不变量和直接委派存储。
- `agent/app/auth.py`：注册、Owner bootstrap 和用户管理策略。
- `agent/app/api/routes.py`、`schemas.py`：Owner 初始化与委派 API。
- `agent/cli.py`：`init-owner` 和 Owner 密码恢复语义。
- `web/static/*`：Owner 初始化文案和管理员委派控件。
- Part 9、总体设计、README、测试说明同步当前口径。

## 测试方案

| 用例 | 预期 |
|---|---|
| 空库注册普通用户 | 创建 viewer，不要求 Owner |
| 已有 viewer 后初始化 Owner | 成功创建唯一 Owner |
| Web setup token 缺失或错误 | 禁止初始化 Owner |
| 普通首页 | 不显示 Owner 初始化入口 |
| `/_setup` | setup 可用时显示；Owner 已存在或未配置 Secret 时返回 404 |
| bootstrap 伪造 username | 服务端忽略并固定创建 `owner` |
| 重复初始化 Owner | 拒绝 |
| 请求伪造 owner/admin 角色 | 普通注册仍为 viewer |
| Admin 创建或修改 Admin | 无委派时拒绝 |
| Owner 委派 `auth.admin.manage` | 指定 Admin 获得能力 |
| 被委派 Admin 提升/降级 Admin | 成功，委派不传播给新 Admin |
| Admin 禁用或改动 Owner | 拒绝 |
| 通用角色接口修改 owner/admin | 拒绝 |

## 验收标准

1. Owner 是唯一永久最高用户，普通 HTTP 请求无法取消其身份。
2. 无 Owner 时仍可注册和登录 viewer。
3. Admin 与 Viewer 的运营能力存在明确差异。
4. 任命管理员的能力只来自 Owner 直接委派，不随任命传播。
5. 所有安全相关变更写入审计，测试覆盖允许与拒绝路径。

## 端口调查结论

参考项目 `sthg_nanobot_agent` 的 `18791` 与 `18091` 不是简单的“前端端口 + 后端端口”：`18791` 是 aiohttp Web channel，负责静态页面、WebSocket 和轻量 API；`18091` 是同一 Gateway 内的 FastAPI 业务 API，部分路径还要由 18791 反向代理。该拆分来自参考项目长期演进形成的两套服务边界。

ZhiCe-Agent 当前由一个 FastAPI Gateway 同时承载静态页面、REST 和 WebSocket，没有第二套独立业务 API。现阶段额外增加 `10186` 前端代理会引入 Cookie、CORS、WebSocket 代理和部署配置，但没有对应的模块收益，因此继续使用单端口 `10086`。未来如果前端升级为独立 Vite/Node 工程，开发期可再引入单独的 dev server，生产部署仍优先同源。
