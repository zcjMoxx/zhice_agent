# Auth 测试说明

## 测试目标

- 验证 SQLite auth schema、唯一 Owner 初始化、密码校验和可撤销登录态。
- 验证 Owner 不可降级、Admin 管理边界和 `auth.admin.manage` 直接委派不传播。
- 验证角色权限聚合、外部身份映射和 token 只保存 hash。
- 验证 viewer 具备聊天、自己的会话、模型切换和低风险工具等完整正常使用权限，但不具备管理或高风险权限。
- 验证用户上下文目录、session_index，以及 Owner 复用全局 CLI session 目录。
- 验证工具权限 key 与 exec 风险分类、确认和拒绝边界。
- 验证认证、用户和 session 错误使用稳定领域码，不以裸 `FORBIDDEN`、`INVALID_REQUEST` 代替已知业务原因。

## 用例覆盖

- 空数据库初始化内置权限和角色；已有 viewer 后仍可初始化唯一 Owner。
- 新建 viewer 获得完整自身范围正常使用权限；已有数据库重新初始化时同步恢复当前内置 viewer 权限。
- Owner 不可禁用或移除；普通 Admin 不能提升/降级 Admin，被 Owner 委派后可以。
- 重复初始化、错误密码、disabled 用户、过期或撤销 token。
- 用户改密校验当前密码；成功时撤销包括当前会话在内的全部登录态，失败时不产生部分更新。
- 外部渠道身份映射到稳定内部 user_id。
- 所有角色的日常 session 列表只返回自己；`session.manage.any` 只用于显式管理动作。
- Owner 的 Web session 只复用 CLI `contexts/sessions*` 全局目录，不回退到 Owner 用户目录；普通用户继续使用独立用户目录。
- `ensure_session` 只在首次创建时返回 `created=True`，并在写权限不足时不留下半成品 session_index。
- `diagnose_my_recent_activity` 只返回当前用户自己的 bounded trace/audit 安全摘要，Owner 也不例外。
- safe exec、network/install、confirmable destructive、env dump 和 workspace 外破坏命令。
- session 缺失/冲突分别使用 `SESSION_NOT_FOUND`、`SESSION_ID_CONFLICT`；普通权限拒绝使用 `AUTH_PERMISSION_DENIED` 并携带所需权限详情。

## 关键检查点

- 数据库和 API 不返回 password hash、salt 或明文 token。
- `tool.exec.dangerous` 只进入确认流程，不能绕过基础安全策略。
- session 的物理目录和数据库 owner 必须同时匹配。
