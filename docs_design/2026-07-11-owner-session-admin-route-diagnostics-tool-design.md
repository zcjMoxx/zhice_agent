# Owner 日常会话、独立管理路由与按需诊断工具设计

## 背景

第九部分已实现用户权限、用户会话目录、管理弹窗和近期诊断。实际使用需要收紧三个口径：Owner 虽有全局会话权限，但仍是正常聊天用户，日常列表不应混入他人记录；用户管理应有独立页面和路由；诊断应在出错后由 Agent 按需调用工具，而不是常驻菜单。

## 目标

- `GET /api/sessions` 对所有角色默认只返回当前用户自己的会话。
- 保留 `session.manage.any`，但只用于显式管理动作。
- Owner 的 Web 会话 JSONL 和模型 metadata 复用 CLI 的 `contexts/sessions` 与 `contexts/sessions_meta`；Owner 的普通文件目录仍独立。
- 账号切换时清空前端用户级会话状态。
- 管理功能迁移到独立 `/admin` 路由和全页布局。
- 移除 Recent diagnostics 菜单、弹窗和 REST 入口，保留 `diagnose_my_recent_activity` 工具。
- 诊断工具只读取当前用户的 bounded trace/audit 安全摘要，交给 LLM 判断原因。

## 非目标

- 本次不新增浏览全部用户会话的管理页面。
- 本次不允许 Owner 在聊天侧栏直接接管其他用户会话。
- 本次不暴露 raw trace、完整 prompt、完整 tool args、cookie、token 或其他用户事件。
- 本次不引入前端框架或客户端路由依赖。

## 模块设计

### 日常会话可见性

`SessionAccessService.list_sessions(actor)` 固定按 `actor.user_id` 查询 `session_index`。`session.manage.any` 仍可用于显式 `resolve_session` 管理动作，但不能隐式改变聊天侧栏。

### Owner 会话物理路径

```text
Owner:
  files         -> contexts/users/{owner_user_id}/files
  sessions      -> contexts/sessions
  sessions_meta -> contexts/sessions_meta

普通用户:
  files         -> contexts/users/{user_id}/files
  sessions      -> contexts/users/{user_id}/sessions
  sessions_meta -> contexts/users/{user_id}/sessions_meta
```

SQLite `session_index.owner_user_id` 仍是归属真相。CLI 目录中未进入索引的旧会话不会自动出现在 Web 侧栏；显式导入到 Owner 时只补索引，不复制同一路径文件。

兼容已有数据：如果某个 Owner session 已被索引、全局目录尚无对应文件，而旧 `contexts/users/{owner_id}/sessions*` 中存在文件，则继续从旧路径读取；新建 Owner session 一律写入全局 CLI 路径。

### 账号切换

退出、登录过期和新账号进入应用前统一清空 `sessions`、`activeSessionId`、`messages`、模型状态、active turn、confirmation 和旧 WebSocket。

### 管理路由

Gateway 增加 `GET /admin`，返回静态应用入口。前端按 pathname 切换独立管理页：聊天页 Administration 导航到 `/admin`；管理页只展示 Users / Roles / Audit 和返回聊天入口；API 权限校验保持不变。

### 按需诊断

移除 `POST /api/diagnostics/my-recent-activity` 和对应 UI。`diagnose_my_recent_activity` 继续由用户级 ToolProvider 注入：

```text
用户询问失败原因
  -> LLM 调用 diagnose_my_recent_activity
  -> 工具按当前 actor 或其 session 读取近期 trace
  -> 合并当前用户 audit events
  -> 只返回 allowlist 安全字段和失败候选
  -> LLM 解释事实、推断、置信度和下一步
```

trace 按分钟范围只扫描涉及的日期目录，每个文件只读尾部有限行，总返回量有上限；事件必须属于当前用户；不返回可能含敏感参数的 `args_preview`。

## 变更文件

- `agent/auth/session_access.py`
- `agent/auth/user_context.py`
- `agent/auth/diagnostics.py`
- `agent/app/runtime.py`
- `agent/app/api/routes.py`
- `agent/app/api/schemas.py`
- `agent/app/gateway.py`
- `web/static/index.html`
- `web/static/app.js`
- `web/static/styles.css`
- `tests/unit_test/auth/*`
- `tests/unit_test/app/*`
- `docs_design/zhice-agent-part9-user-auth-permission-design.md`
- `docs_design/README.md`

## 测试方案

| 场景 | 预期 |
|---|---|
| Owner 与普通用户各有会话 | 两者日常列表都只列自己的会话 |
| Owner 显式解析他人会话 | 既有全局管理权限仍有效 |
| Owner 新建 Web 会话 | JSONL 写入 `contexts/sessions` |
| 普通用户新建 Web 会话 | JSONL 写入自己的用户目录 |
| Owner 导入已存在 CLI 会话 | 不自复制文件，只补 session index |
| 同浏览器切换账号 | 前账号 active session/messages 不残留 |
| 访问 `/admin` | 返回管理入口并展示全页管理视图 |
| 用户请求诊断 | 工具只返回自己的 trace/audit 安全摘要 |
| Owner 请求自己的诊断 | 不因全局权限读取其他用户事件 |

## 验收标准

1. Owner 登录聊天页默认看不到 `user001` 的聊天记录。
2. Owner Web 会话文件落在 CLI session 目录，普通用户仍物理隔离。
3. 切换账号后页面不会显示上一账号当前会话。
4. Administration 打开 `/admin` 独立页面，不再弹出聊天页 dialog。
5. 用户菜单不再出现 Recent diagnostics。
6. Agent 仍可调用诊断工具并基于当前用户 trace/audit 回答失败原因。
