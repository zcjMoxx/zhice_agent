# 小红书登录状态收敛设计

> 说明：本记录补充 `2026-08-14-xhs-owner-mcp-admin-login-design.md`。旧方案以登录助手
> 进程退出作为完成信号，并假设 ToolResult 只有一个 JSON 文档；当前实现改为 Cookie 内容更新
> 驱动的同步闭环，并兼容 MCP SDK 同时返回 structured content 与 text content 的真实形态。

## 背景

Owner 扫码成功后 `cookies.json` 已更新，本地 sidecar 也完成重启，但管理卡片仍显示“不可用”。
真实链路探针确认：`check_login_status` 返回两个连续 JSON 文档，分别来自 MCP
`structuredContent` 和兼容 text content，两个文档均为 `status=success, code=OK`。管理 API
使用一次 `json.loads()` 解析整个字符串，因 trailing document 失败并得到空对象列表，最终把
已登录误判为不可用。

原扫码闭环还有两个时序缺口：

- 前端只等待登录助手进程退出；Cookie 已更新但窗口未退出时不会继续。
- 助手退出后前端立即复检，可能早于 sidecar 使用新 Cookie 完成重载。

## 目标

- 正确解析 MCP SDK 产生的一个或多个连续 JSON 文档。
- Cookie 内容稳定更新即视为扫码凭据已落盘，不再依赖窗口主动退出。
- 在登录流程结束前完成 sidecar 重载，之后才允许前端复检。
- 保存最近一次安全登录状态，使管理页面刷新后不退回错误的“不可用”。
- Gateway 重启后的首次管理页访问自动进行一次登录检查。
- 不读取、返回或记录 Cookie 具体值。

## 范围边界

- 仍只允许 Owner 使用登录管理 API。
- 不把 Cookie、二维码、路径、PID、上游原始响应暴露给前端或审计。
- Cookie 变化以内容哈希判断；哈希仅在进程内使用，不写日志和 API。
- xhs-readonly 到本地 sidecar 默认直连，不继承终端代理。
- 不替用户启动或重启 Gateway；交付后由用户自行启动新版代码。

## 模块设计

### MCP 结果解析

管理 API 使用有界 `JSONDecoder.raw_decode` 逐段读取连续 JSON 文档，再按
`data/output/result/text/structuredContent/structured_content/content` 做有界遍历。任一安全对象
出现 `success + OK` 即判定已登录；认证错误码仍优先判定为需要登录。

### Cookie 驱动的登录协调

`LocalXhsSidecarSupervisor.start_login()` 记录启动前 Cookie 内容签名并启动协调线程：

1. 轮询登录助手进程与 Cookie 文件。
2. Cookie 出现不同且连续稳定的有效 JSON 内容后，关闭登录助手。
3. 重启 Gateway 自有 sidecar，使其加载新 Cookie。
4. sidecar ready 后将登录流程从 `login_pending` 转为 `unknown/recheck pending`。
5. 前端看到流程结束后调用登录检查，并保存 `authenticated/auth_required` 状态。

若助手先退出且 Cookie 未变化，也进入复检；已有有效 Cookie 仍可得到 authenticated。

### 状态保持

Supervisor 仅保存安全字段 `state/code/message`。GET status 返回最近一次状态；开始登录、Cookie
外部更新或手动重启时重置为待复检。Gateway 新进程初始为 unknown，Owner 管理页自动触发一次
检查，并在检查期间展示“检查中”。

### 代理隔离

只读适配器访问 XHS upstream 时默认 `trust_env=False`。如未来远程 upstream 需要代理，必须通过
专门、受审查的 XHS 配置显式引入，不能被 Tavily 或终端代理变量连带影响。

## 数据流

```text
扫码助手
  -> cookies.json 内容稳定更新
  -> login coordinator
     -> 关闭扫码助手
     -> 重启 owned sidecar
     -> sidecar ready
     -> status = unknown / recheck pending
  -> 前端自动 check-login
  -> MCP structuredContent + text content
  -> 连续 JSON 有界解析
  -> authenticated
  -> 管理卡片持久显示“已登录”
```

## 变更文件

- `agent/applications/travel/xhs_sidecar.py`
- `agent/app/api/routes.py`
- `integrations/xhs_readonly_mcp/server.py`
- `web/frontend/src/layouts/AdminLayout.vue`
- `tests/unit_test/travel/test_xhs_sidecar.py`
- `tests/unit_test/app/test_auth_routes.py`
- `tests/unit_test/travel/test_mcp_adapters.py`
- `web/frontend/src/layouts/AdminLayout.test.ts`
- `tests/unit_test/app/test_case.md`
- `tests/unit_test/travel/test_case.md`

## 测试方案

- 连续两个 JSON 文档均为 success/OK 时必须判定 authenticated。
- text content 嵌套 JSON 与 structured content 重复时保持幂等。
- Cookie 内容未变但 mtime 改变时不触发重载。
- Cookie 内容稳定变化且登录窗口仍运行时自动结束窗口并重载 sidecar。
- sidecar 重载完成前 `login_in_progress` 保持 true。
- 页面首次加载 unknown 自动检查；刷新后读取最近 authenticated 状态。
- xhs-readonly 本地 upstream 在错误代理环境中仍直连。
- 运行相关单测、完整 Ruff、前后端测试、TypeScript 和生产构建。

## 验收标准

- 当前真实 Cookie 的登录检查显示“已登录”，不再显示“不可用”。
- 扫码成功并写入 Cookie 后无需手动关闭窗口、手动重启或再次点击检查。
- Cookie 更新与登录复检之间不存在使用旧 sidecar 的竞态。
- 页面刷新不会丢失同一 Gateway 进程内的最近登录状态。
- 不新增任何 Cookie 或登录原始内容泄漏。
