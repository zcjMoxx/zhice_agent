# 小红书只读 MCP 的 Owner 管理入口设计

> 说明：当前登录收敛仍采用 Cookie 稳定更新触发 sidecar 重载；账号扫码、Cookie 与登录检查现已移至 `/api/admin/external-platforms/xhs/*`，`xhs-readonly` 的技术监控和服务重启仍属于 MCP。当前管理边界见 `docs_design/2026-08-16-runtime-config-prompt-example-convergence-design.md`，下文保留最初方案背景。

## 背景

小红书只读服务的扫码登录属于宿主机数据源凭据维护，不是旅行用户功能。把重新登录入口放在旅行页会暴露 MCP 与认证运维概念，也会让普通用户误以为可以管理系统共享账号。当前管理后台已有“MCP 与 Skills”页及逐 Server 运行卡片，因此入口应只出现在 `xhs-readonly` MCP 卡片中。

本地 Windows 已具备配套登录程序、Cookie 文件和 Gateway-owned sidecar。扫码更新 Cookie 后，supervisor 会自动重载自有 RedNote sidecar。本次需要补齐 Owner-only Web 管理入口、状态检查与受控重启，不暴露 Cookie、路径、进程号或原始工具输出。

## 目标

- 在管理后台“MCP 与 Skills”的 `xhs-readonly` 卡片展示登录状态与管理动作。
- 只有 Owner 能检查登录、打开扫码程序和重启本地 sidecar。
- 普通用户和非 Owner 管理员不看到认证管理区，也不能直接调用 API。
- 登录程序只从固定 workspace bin 目录选择，Cookie 只写固定 workspace data 目录。
- 登录完成后由 supervisor 自动重载；管理员可再次检查状态确认。
- 云端、Docker 或缺少本机登录器时明确显示“不支持本机弹窗”，不伪装启动成功。

## 范围边界

- 不把 Cookie 值、文件路径、二维码内容、进程 ID 或 MCP 原始输出返回前端。
- 不新增可分配给普通角色的宿主机进程权限，操作固定要求 `owner` 角色。
- 不从旅行页发起登录，也不让旅行 Agent 调用管理 API。
- 本次本机登录弹窗仅支持 Windows 固定登录器；服务器扫码仍走独立运维流程。
- 不自动循环调用登录检查；管理员按按钮检查，避免与正在扫码或查询的浏览器并发冲突。

## 模块设计

### Supervisor 管理能力

`LocalXhsSidecarSupervisor` 增加安全管理快照、登录器选择、`start_login` 与 `restart`：

- 登录器优先使用与当前 Windows Cookie v2 兼容的固定 `xiaohongshu-login-windows-amd64.exe`，不存在时返回不支持。
- 登录进程使用独立 Windows 控制台并纳入 `ManagedProcessTree`，避免重复启动；Gateway 关闭时一并回收。
- 快照只返回 enabled、login_supported、login_in_progress、restart_supported 和 Cookie 更新时间，不返回路径与 PID。
- restart 只重启 supervisor 自己创建的 sidecar；外部 listener 不接管。

### Owner-only API

- `GET /api/admin/mcp/xhs-readonly/status`：返回安全管理快照。
- `POST /api/admin/mcp/xhs-readonly/check-login`：通过当前 `McpRuntime` 调用只读 `check_login_status`，归一为 authenticated/auth_required/unavailable。
- `POST /api/admin/mcp/xhs-readonly/login`：启动本机扫码程序。
- `POST /api/admin/mcp/xhs-readonly/restart`：受控重启自有 sidecar。

四个接口均先解析登录 actor，再硬校验 `owner` 角色。审计只记录动作、结果和稳定状态码。

### 管理后台

`xhs-readonly` MCP 卡片在 Owner 视角增加“登录管理”区：状态、Cookie 最近更新时间，以及“检查登录”“重新登录”“重启服务”三个动作。按钮执行期间禁用重复操作，成功或失败使用现有页面状态条展示。非 Owner 不渲染该区域。

## 数据流

```text
Owner 点击重新登录
  -> Owner-only REST API
  -> supervisor 启动固定登录器
  -> 用户扫码，cookies.json 更新
  -> supervisor watcher 自动重启自有 RedNote sidecar
  -> Owner 点击检查登录
  -> McpRuntime.check_login_status
  -> 管理卡显示已登录
```

## 变更文件

- `agent/applications/travel/xhs_sidecar.py`
- `agent/app/api/schemas.py`
- `agent/app/api/routes.py`
- `web/frontend/src/api/types.ts`
- `web/frontend/src/api/client.ts`
- `web/frontend/src/stores/admin.ts`
- `web/frontend/src/layouts/AdminLayout.vue`
- `web/frontend/src/styles/app.css`
- 对应后端、前端测试与测试说明

## 测试方案

- Supervisor：登录器缺失/存在、重复启动、固定快照、仅重启自有进程、shutdown 回收。
- API：Owner 成功读取和操作；普通管理员即使有 `skill.sources.read` 也返回 403；响应不含路径、PID、Cookie 或工具原文。
- 登录检查：成功、认证失效、MCP 不可用和 Tool 缺失映射稳定状态。
- 前端：Owner 在 `xhs-readonly` 卡片看到动作并能调用对应接口；Admin 不渲染管理区；按钮状态和结果文案正确。
- 运行 Ruff、Pytest、ESLint、TypeScript、Vitest 与正式构建。

## 验收标准

- Owner 可从“MCP 与 Skills → xhs-readonly”打开扫码窗口。
- 扫码后不需要进入旅行页，也不需要手工重启 Gateway。
- 登录检查显示已登录，真实单关键词搜索仍可返回结果。
- 非 Owner 无 UI 入口且 API 拒绝。
- 前后端响应和日志均不包含 Cookie 值、宿主机路径或进程号。
