# 登录用户基础能力与特权权限收敛设计

> 日期：2026-07-16
> 状态：已确认，进入实现
> 当前活文档：`zhice-agent-part9-user-auth-permission-design.md`

## 1. 背景

当前 Part 9 将聊天、本人 Session、模型切换、安全工具、已安装 Skill 和本人 Memory 等日常功能都定义成了 permission key。`viewer`、`developer` 等普通角色必须先获得一整套权限，才能正常使用系统。

这种设计把内部基础设施误写成了特权。可以用公司作类比：员工喝水、使用打印机是公司内部的基础能力；真正需要权限控制的是管理其他员工、读取全公司审计、执行高风险操作等少数行为。ZhiCe-Agent 的登录用户同样应默认获得正常使用能力，RBAC 只表达特殊人员额外拥有的能力。

## 2. 目标

1. 登录且状态正常的内部用户默认可以使用系统日常功能。
2. 自有数据访问由 actor 身份和 ownership 决定，不再依赖 `*.own` permission key。
3. RBAC 只保留跨用户、系统管理、审计、危险执行和全局同步等特权。
4. 危险操作仍经过静态安全拦截、特权检查、用户确认和审计；确认是安全交互，不是普通功能权限。
5. 保持 Owner 语义不变：Owner 是 CLI workspace operator 的 Web 登录身份，两者共用 workspace 级目录，不创建 Owner 用户目录。
6. 在不删除用户、角色和角色绑定的前提下，清理数据库中的旧基础权限数据。

## 3. 范围边界

### 3.1 登录用户基础能力

以下能力不再建模为 RBAC permission：

- 查看和修改自己的账号资料、修改自己的密码。
- 创建、读取、修改、删除自己的 Session。
- 发起聊天、停止自己的活动 turn、读取自己的 turn 信息。
- 查看模型列表，设置或重置自己 Session 的模型偏好。
- 使用只读工具、低风险 `exec` 和已安装 Skill。
- 读取自己的 Memory；在满足对话式用户授权和 Memory 安全规则时写入自己的 Memory。
- 对自己的 Session 执行 Memory summary。
- 查询自己的近期运行诊断。

这些能力的共同前提是：actor 已通过认证，并且资源属于 actor。未登录访问仍由认证层拒绝。

### 3.2 保留的特权权限

```text
auth.users.read
auth.users.manage
auth.admin.manage
auth.roles.read
auth.roles.manage
session.manage.any
chat.stop.any
turn.read.any
tool.exec.dangerous
skill.sync
audit.read
audit.export
```

说明：

- `session.manage.any`、`chat.stop.any`、`turn.read.any` 只处理跨用户范围。
- `tool.exec.dangerous` 只表示 actor 有资格请求高风险执行；命令仍必须通过 workspace guard、危险命令拦截和用户确认。
- `skill.sync` 是会改变全局 Skill source 状态的能力，继续作为特权。
- 用户、角色和审计接口只对具有对应特权的 actor 开放。

### 3.3 不在本次范围

- 不引入组织、部门、租户或复杂 ABAC。
- 不增加新的 Owner 私有目录。
- 不修改 Memory 的对话式授权语义。
- 不把普通功能重新包装成新的 capability key 或隐式角色权限。

## 4. Ownership 与 Permission 的区别

访问一个 Session、文件目录或 Memory 时先判断资源归属：

```text
已认证 actor
  -> 资源属于 actor：按基础能力允许
  -> 资源不属于 actor：默认隐藏/拒绝
       -> actor 具有对应 any/manage 特权时才允许
```

因此，`session.read.own` 一类 key 是重复建模：`own` 已经由 `session_index.owner_user_id == actor.user_id` 表达。删除这些 key 不会取消隔离，反而让边界更直接。

Owner 是特殊的 workspace operator 身份。CLI 的 `local_operator` 与 Web 的 `owner` 使用同一个 workspace 级 Session、files 和 Memory 路径；Owner 的数据库 user id 仅用于认证、索引和审计。

## 5. 角色收敛

- `viewer`：标准内部用户，无额外特权。
- `developer`：当前没有必须额外授予的系统级特权，因此可与 `viewer` 同为空特权集合；保留角色名只为兼容现有用户和 UI。
- `auditor`：保留 `audit.read`，并按现有审计需求保留 `turn.read.any`。
- `admin`：保留用户读取/管理、角色读取和必要的跨用户管理能力；`auth.admin.manage` 与 `auth.roles.manage` 仍不自动授予普通 Admin。
- `owner`：拥有全部特权。
- `local_operator`：与 Owner 表示同一个 workspace operator，拥有全部特权，但不对应额外数据库用户或目录。

## 6. 模块修改

### 6.1 `agent/auth/schema.py`

- `PERMISSIONS` 只保留特权 key。
- 重建内置角色的特权集合。
- `viewer`、`developer` 的内置特权集合为空。

### 6.2 `agent/auth/store.py`

`initialize_schema()` 在写入当前 schema 后清理不再属于 `PERMISSIONS` 的旧 permission rows。依赖外键级联同步删除 `role_permissions` 和 `user_permissions` 残留，不删除用户、角色或 `user_roles`。

### 6.3 `agent/auth/session_access.py`

- 已认证用户可自动创建、读写、删除自己的 Session。
- 访问其他用户 Session 时只检查 `session.manage.any`。
- 不存在或无权访问仍统一表现为 `SESSION_NOT_FOUND`，避免资源枚举。

### 6.4 `agent/app/api/routes.py` 与 `agent/app/runtime.py`

- `_actor()` 改为“认证必需、特权可选”。
- 账号自身、Session、聊天和模型路由只解析 actor，不检查基础 permission。
- runtime 删除 `chat.run`、`model.view`、`model.switch`、`chat.stop.own`、`memory.summarize.own` 检查。
- 跨用户停止仍检查 `chat.stop.any`。

### 6.5 `agent/auth/tool_policy.py`

- 只读工具、安全 `exec`、已安装 Skill、本人诊断、Memory read 默认允许已认证用户使用。
- `memory_write` 不再检查 RBAC，但仍严格要求 `user_explicit` 或 `user_confirmed` 的对话授权，并继续经过 MemorySafetyPolicy。
- `sync_skills` 检查 `skill.sync`。
- 高风险 `exec` 检查 `tool.exec.dangerous`，随后进入确认；永久禁止项仍直接拒绝。

## 7. API 与 UI 兼容

- `/api/auth/me` 继续返回 `permissions`，但该字段只表示额外特权，不再表示完整功能清单。
- 角色权限页只展示可配置特权。
- 普通用户即使 `permission_keys=[]` 也能正常聊天和管理自己的资源。
- 保留现有角色和用户绑定，避免数据库迁移导致账号变化。

## 8. 测试方案

至少覆盖：

1. `viewer` 和 `developer` 的 `permission_keys` 为空，但可执行全部自有基础能力。
2. 普通用户可创建、读取、改名、清空和删除自己的 Session。
3. 普通用户不能读取或修改其他用户 Session；具备 `session.manage.any` 时可以。
4. 普通用户可聊天、切换自己的 Session 模型、使用只读工具、安全 `exec`、已安装 Skill 和本人 Memory。
5. Memory write 缺少自然语言授权时仍拒绝。
6. 普通用户请求危险 `exec` 时因缺少 `tool.exec.dangerous` 被拒绝；有特权时进入确认。
7. `skill.sync`、审计、用户/角色管理仍需要对应特权。
8. schema 重初始化后旧基础权限从 `permissions`、`role_permissions`、`user_permissions` 清理。
9. Owner 与 CLI 继续共用 workspace 目录，且不创建 `contexts/users/{owner_id}`。

## 9. 验收标准

1. 代码中不再用基础 permission key 控制日常功能。
2. `PERMISSIONS` 只包含本设计列出的特权。
3. ownership 隔离和跨用户特权测试全部通过。
4. 危险命令的静态拒绝、特权检查、确认和审计链路不退化。
5. Part 9、总体设计、设计索引和 README 与新口径一致。
6. `python -m ruff check .`、相关单元测试和全量测试通过。
