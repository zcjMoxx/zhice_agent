# Auth 测试说明

## 测试目标

- 验证 `auth_settings` 在新库/旧库迁移中默认关闭注册，并在显式切换与 schema 重初始化后持久保留。

- Session index 保留来源 channel、conversation type 和跨端续写模式。
- QQ 群 Session 对 Web/CLI 只读，并可安全派生为新的 Web Session。
- 当前用户只能查询和解绑自己的 external identity，解绑保留历史。

- 验证 SQLite auth schema、唯一 Owner 初始化、密码校验和可撤销登录态。
- 验证 Owner 不可降级、Admin 管理边界和 `auth.admin.manage` 直接委派不传播。
- 验证角色权限聚合、外部身份映射和 token 只保存 hash。
- 验证登录用户默认具备聊天、自己的会话、模型切换和低风险工具等基础能力，viewer 不需要额外特权。
- 验证用户上下文目录、session_index，以及 Owner 复用全局 CLI session 目录。
- 验证安全工具基础能力与危险 exec 特权、确认和拒绝边界。
- 验证认证、用户和 session 错误使用稳定领域码，不以裸 `FORBIDDEN`、`INVALID_REQUEST` 代替已知业务原因。

## 用例覆盖

- 空数据库初始化内置特权和角色；已有 viewer 后仍可初始化唯一 Owner。
- 新建 viewer 的额外特权为空但仍可正常使用；已有数据库重新初始化时清理废弃的基础权限及关联记录。
- Owner 不可禁用或移除；普通 Admin 不能提升/降级 Admin，被 Owner 委派后可以。
- 重复初始化、错误密码、disabled 用户、过期或撤销 token。
- 用户改密校验当前密码；成功时撤销包括当前会话在内的全部登录态，失败时不产生部分更新。
- 外部渠道身份映射到稳定内部 user_id。
- 已停用非 Owner 用户可在用户名确认后永久删除；关系数据与独立用户目录同步清理，失败时恢复目录。
- 同一内部账号最多保留一条 active QQ 身份；重复绑定其它 QQ 时拒绝，解绑后才允许换绑。
- 旧数据库初始化时若存在同一账号的多条 active QQ，保留最新绑定并禁用其余记录，再建立唯一索引。
- 微信Outbox表幂等建表，pending/sent状态和attempt计数可恢复；账号解绑删除对应Outbox但保留Session历史。
- 所有角色的日常 session 列表只返回自己；`session.manage.any` 只用于显式管理动作。
- Owner 是 CLI 本地操作者的 Web 身份：Web session 复用 CLI `contexts/sessions*`，工具文件根目录直接使用 workspace，解析时不创建 `contexts/users/{owner_id}`；普通用户继续使用独立用户目录。
- `ensure_session` 只在首次创建时返回 `created=True`，本人创建和写入由认证身份与 ownership 直接允许。
- `diagnose_my_recent_activity` 自动使用当前 Session，排除当前诊断 Turn，并从上一条 Turn、最近失败或近期趋势生成结构化诊断；同时返回按时间排序、字段白名单过滤的 `trace_events` 供模型直接归因，Owner 普通聊天也不扩大到全系统。
- `diagnose_system_activity` 仅对显式拥有 `diagnostics.system.use` 的 actor 可用；跨用户查询保持时间/数量有界，事故由稳定规则聚合，trace、Tool 与时间线只返回白名单脱敏字段，绝不回传参数、命令、输出或 Secret。
- 普通用户诊断到 Subagent 内部失败时只返回暂时不可用并联系管理员，隐藏 cause/evidence/修复命令；Owner 和具备内部审计权限的管理员保留完整证据。
- 父 `delegate_tasks` Turn 可沿同 actor 的 `root_session_id/root_turn_id` 下钻 child terminal trace，优先报告 child failure code/stage 和脱敏 `error_message`；安全证据只暴露白名单字段，不能跨 actor。缺少 child 终因的通用 `SUBAGENT_FAILED` 最多为中等置信度。
- 诊断只读取权威 `logs/log-YYYY-MM-DD.jsonl`，不保留旧文件路径分支。
- Runtime Activity 独立维护 `turn_runs` / `tool_call_records`，不会写入 `audit_events`；AuditSink 也不再隐式更新运行索引。
- `turn_runs` 直接使用 `turn_id` 主键，不存在额外 `turn-run-*` id，也不保留旧表兼容结构。
- Gateway 启动恢复只把上次进程遗留的 `started` Turn 终结为 `error/GATEWAY_RESTART_INTERRUPTED`，已有终态不改写且重复恢复幂等。
- safe exec、network/install、confirmable destructive、env dump 和 workspace 外破坏命令。
- session 缺失/冲突分别使用 `SESSION_NOT_FOUND`、`SESSION_ID_CONFLICT`；跨用户访问默认隐藏，只有 `session.manage.any` 可越过 ownership 边界。

## 关键检查点

- 数据库和 API 不返回 password hash、salt 或明文 token。
- `tool.exec.dangerous` 只进入确认流程，不能绕过基础安全策略。
- Owner 的数据库 id 只用于认证、授权、session index 和 audit，不得据此派生 Owner 专属文件目录；历史残留目录只忽略，不自动删除。
- 所有登录用户都能使用自己的 Memory；Memory 写入要求对话式用户授权，但不进入 RBAC 或 tool confirmation。
- 用户明确要求或自然语言同意后的 Memory 写入直接执行，不创建 tool confirmation；没有授权类型时拒绝。
- Memory trace 和 security audit 不保存 query、候选或写入结果明文。
- session 的物理目录和数据库 owner 必须同时匹配。
