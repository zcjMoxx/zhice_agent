# Viewer 正常使用权限补齐设计

> 说明：本文通过给 Viewer 补齐多项 permission key 来恢复正常使用的方案已被替代。当前代码将这些日常功能定义为所有登录用户的基础能力，Viewer 无需额外特权；请参考 `docs_design/2026-07-16-authenticated-user-baseline-capabilities-design.md` 和当前 Part 9 活文档。本文其余正文保留当时方案原貌。

## 背景

Part 9 普通注册固定授予 `viewer` 角色，但当前 `viewer` 缺少 `model.switch`、`session.delete.own` 和 `tool.exec.safe`。其中模型切换会直接影响 Web 聊天：前端发送普通消息时携带当前模型，后端会校验 `model.switch`，导致已经登录的普通用户在进入 AgentLoop 前被拒绝。

`viewer` 在当前产品中承担“普通用户”而非“只读访客”的角色，因此账号自身范围内的正常聊天、会话、模型和低风险工具能力应默认可用。管理他人、读取审计、同步 Skill 和执行高风险工具仍属于管理或高风险权限。

## 目标

1. 普通用户可以查看和切换自己会话使用的模型。
2. 普通用户可以删除自己的会话。
3. 普通用户可以使用只读工具和低风险 exec。
4. 普通用户仍不能管理用户、角色、任意会话、审计或高风险工具。
5. 已有数据库在 Gateway 重启并执行 schema 初始化后自动同步权限，不要求重建账号。

## 范围边界

- 不改变 Owner、Admin、Developer 和 Auditor 的权限。
- 不授予 `tool.exec.dangerous`、`session.manage.any`、`chat.stop.any`、`turn.read.any` 等跨用户或高风险权限。
- 不改变公开注册固定授予 `viewer` 的规则。
- 不取消后端权限校验；伪造角色或权限字段仍然无效。

## 模块设计

- 在 `ROLE_PERMISSIONS["viewer"]` 中加入：
  - `session.delete.own`
  - `model.switch`
  - `tool.exec.safe`
- 继续复用 `SQLiteAuthStore.initialize_schema()` 的内置角色幂等同步：启动时删除并按代码定义重建内置角色权限映射，使已有 viewer 用户自动获得新权限。
- 回归测试同时覆盖全新数据库和已有数据库重新初始化两条路径。

## 数据流

```text
Gateway startup
  -> SQLiteAuthStore.initialize_schema()
  -> rebuild built-in viewer role permissions
  -> existing viewer user resolves ActorContext
  -> model.switch / session.delete.own / tool.exec.safe available
  -> normal Web chat can apply the selected session model
```

## 变更文件

- `agent/auth/schema.py`
- `tests/unit_test/auth/test_auth_store.py`
- `tests/unit_test/auth/test_web_runtime_auth.py`
- `tests/unit_test/auth/test_case.md`
- `tests/unit_test/app/test_auth_routes.py`
- `tests/unit_test/app/test_case.md`
- `docs_design/zhice-agent-part9-user-auth-permission-design.md`

## 测试方案

| 用例 | 预期 |
|---|---|
| 新建 viewer | 拥有全部自身范围正常使用权限 |
| viewer 权限被旧数据库状态覆盖后重新初始化 | 内置权限恢复到当前代码定义 |
| viewer 设置会话模型偏好 | 成功，且只影响自己的目标会话 |
| viewer 调用模型偏好 HTTP API | 查看、设置和重置均成功 |
| viewer 检查管理及高风险权限 | 均未授权 |

## 验收标准

1. 普通注册用户可以正常聊天并切换模型。
2. 已存在的 viewer 用户在 Gateway 重启后无需重新注册。
3. viewer 仍不具备用户管理、角色管理、审计和危险 exec 权限。
4. Ruff、相关单测和全量 pytest 通过。
