# Owner 初始化与遗留兼容清理设计

## 背景

认证主线已从“首位 Admin”调整为唯一 Owner，但 CLI、服务层、会话导入和测试中仍保留旧名称或过渡功能。当前 `init-owner` 仍要求两次输入密码，且初始化时会创建 Owner 的用户目录；这与 Owner 复用全局 CLI session 的当前语义不一致。

## 目标

1. `zcagent auth init-owner` 在无 Owner 时先安全读取并校验 `ZHICE_AGENT_SETUP_TOKEN`，再安全读取一次 Owner 密码；两者均不通过命令行参数接收。
2. Owner 初始化只创建认证记录；不创建 `contexts/users/{owner_id}`，Owner 首次使用会话时仍使用全局 `contexts/sessions` 与 `contexts/sessions_meta`。
3. 移除 `init-admin`、`initialize_first_admin`、`bootstrap_first_admin` 和 CLI session 显式导入这一阶段不再需要的兼容入口。
4. 将当前活文档、README 和测试统一为 Owner 语义；旧日期记录保持历史正文，仅补充失效说明。

## 范围边界

不迁移或删除已有 SQLite 数据、全局 CLI JSONL 或旧用户目录。普通用户的独立上下文与 Admin 角色授权模型保持不变；只删除未发布的 Python 内部兼容 API 和 CLI 子命令。

## 模块与数据流

```text
zcagent auth init-owner
  -> has_owner? (yes: reject without prompt)
  -> getpass(Setup token) -> compare ZHICE_AGENT_SETUP_TOKEN
  -> getpass(Owner password)
  -> SQLiteAuthStore.initialize_owner
  -> auth.sqlite3 users + owner role

Owner 首次 Web session
  -> SessionAccessService._resolved
  -> contexts/sessions + contexts/sessions_meta
```

## 变更文件

- `agent/cli.py`：精简 auth 子命令、单次密码读取及无用 session 导入依赖。
- `agent/auth/store.py`、`agent/app/auth.py`、`agent/app/api/*`：删除过时 first-admin 兼容命名。
- `agent/auth/session_access.py`：删除 CLI session 导入实现及复制依赖。
- `tests/unit_test/{auth,app,cli}`：先更新测试为唯一 Owner 与单次密码语义。
- `README.md`、Part 9 活文档和当日设计记录：同步当前口径。

## 测试方案

| 用例 | 预期 |
| --- | --- |
| CLI Owner 初始化 | 无 Owner 时先校验 setup token，再读取一次密码；已有 Owner 时不读取任何输入 |
| Owner session | 首次解析时使用全局 CLI session 目录 |
| 旧 CLI 子命令 | `init-admin` 与 `import-cli-session` 被 argparse 拒绝 |
| Auth/API 测试 | 所有测试通过 `initialize_owner` 建立唯一 Owner |

## 验收标准

- 全仓库运行时代码不存在 `init-admin`、`initialize_first_admin`、`bootstrap_first_admin`、`import-cli-session` 或 `import_cli_session`。
- README 和 Part 9 活文档只描述 Owner 初始化和 Owner 的全局 session 语义。
- 认证、CLI、API 定向测试及 `ruff` 通过。
