# 携程登录态检查与账号操作收敛设计

## 背景

Owner 管理页已经将小红书与携程并列为外部平台账号，但两者的登录态管理并不对称。小红书提供独立“检查登录”，携程只有状态读取和密码登录；携程状态文件可能来自上一次运行，服务启动后不会验证持久 profile。更严重的是，携程登录助手固定启动 `headless=False` 浏览器，而服务器 Docker 没有 X Server，导致接口先返回已受理，后台随后稳定失败为 `HOTEL_BROWSER_START_FAILED`。界面同时把依赖存在显示为“浏览器已就绪”，并并列展示登录、更新和删除三个按钮，状态和动作语义都不够准确。

## 目标

- 携程提供与小红书一致的显式登录态检查，但检查只读取持久 profile，不提交账号密码。
- Gateway 启动后对已配置携程账号执行一次后台检查，不阻塞主服务启动。
- 携程密码登录按运行环境选择浏览器模式：有本地图形会话时允许可见人工验证；服务器容器默认 headless，不再依赖 X Server。
- 旅行查询只复用已认证 profile；登录态失效时返回结构化认证错误，不在旅行任务中静默重复提交密码。
- 管理页收敛为一个状态感知主动作和一个“管理账号”入口，更新与删除放入管理区。
- 所有状态、日志、审计和 API 响应继续不暴露账号原文、密码、Cookie、profile 路径或浏览器原始 stderr。

## 范围边界

- 保留现有账号密码存储、持久 profile、只读酒店查询和 Owner 权限边界。
- 不增加云端桌面、Xvfb、远程验证码输入或浏览器画面转发。
- 不改变小红书 MCP/sidecar，也不把携程改造成 MCP Server。
- 不在启动检查或旅行查询中提交密码；只有保存凭据或 Owner 明确点击登录才可执行密码登录。

## 模块设计

### 登录态检查

`HotelAccountSupervisor.check_login()` 调用适配器现有的 `check_ctrip_login()`，使用 headless Chromium 打开固定 profile 并判断会话。新增 Owner-only `POST /api/admin/external-platforms/ctrip/check-login`，结果写入安全状态文件并记录不含敏感内容的审计事件。

监督器在运行时装配完成后调用 `start_initial_check()`。仅在已保存凭据、Playwright 可用且没有其它检查/登录操作时创建 daemon 线程；主 Gateway 启动不等待携程网络。检查与凭据更新、删除、登录启动通过监督器操作锁串行，profile 仍由进程内锁与系统文件锁保护。

### 登录运行模式

登录子进程新增显式 `--headed` 参数。监督器仅在 Windows/macOS 桌面或 Linux 存在 `DISPLAY`/`WAYLAND_DISPLAY` 时传入；服务器 Docker 默认不传，因而使用 headless Chromium。headless 登录若触发验证码或安全验证，返回 `HOTEL_MANUAL_VERIFICATION_REQUIRED`，不会声称已经弹出浏览器。

安全状态投影增加 `check_in_progress`，并把 `login_mode` 区分为 `password_with_manual_verification_fallback` 与 `password_headless`。`browser_supported` 继续表示组件可用，界面文案改为“浏览器组件已安装”，不再表示实际登录或 headed 启动成功。

### 旅行查询边界

`search_ctrip_hotels()` 先复用持久 profile。发现未登录时直接返回 `HOTEL_AUTH_REQUIRED`，不加载或提交保存的密码。账号密码只由明确登录动作消费，使旅行规划保持只读、可预期且不会因失效登录在后台反复触发风控。

### 管理页交互

携程卡片只有两个顶层入口：

- 主动作：未检查或已登录时显示“检查登录”；需要登录/不可用时显示“使用已保存凭据登录”；检查或登录进行中显示对应进行态。
- “管理账号”：展开账号密码更新、取消和删除操作；未配置时默认展示首次保存表单。

启动后台检查进行时页面轮询状态直至收敛。服务器 headless 模式遇到人工验证时给出明确说明，不再提示“在弹出的浏览器中完成”。

## 数据流

```text
Gateway 启动
  -> 已配置账号：后台 check_ctrip_login(headless)
  -> 只读取 persistent profile
  -> authenticated / auth_required / unavailable

Owner 明确登录或保存凭据
  -> login helper
  -> 本地图形会话：headed，可人工验证
  -> 服务器容器：headless，无 X Server 依赖
  -> 写入安全状态

旅行酒店查询
  -> 打开 persistent profile
  -> 已登录：执行只读日期房价查询
  -> 未登录：HOTEL_AUTH_REQUIRED，不自动提交密码
```

## 变更文件

- `agent/applications/travel/hotel_accounts.py`：检查生命周期、启动检查、运行模式和并发串行。
- `integrations/hotel_browser_mcp/login.py`：显式 headed 参数。
- `integrations/hotel_browser_mcp/ctrip.py`：旅行查询不再自动密码登录。
- `agent/app/api/routes.py`、`agent/app/api/schemas.py`：携程检查 API 与状态投影。
- `agent/app/runtime.py`：运行态完成装配后启动一次后台检查。
- `web/frontend/src/api/*`、`stores/admin.ts`、`layouts/AdminLayout.vue`：API、状态与双入口交互。
- 旅行、API、前端测试与测试说明、Part 19 活文档。

## 测试方案

- 单元测试检查已有/失效 profile 的状态持久化、启动检查仅运行一次、操作互斥和无敏感信息投影。
- 适配器测试确保酒店查询未登录时不调用密码表单，headed 参数只由显式开关启用。
- API 测试覆盖 Owner-only 检查、审计动作和安全响应。
- Vue 测试覆盖状态感知主按钮、管理区折叠、检查轮询和 headless 验证文案。
- 执行相关 Python/Vue 测试、Ruff、前端 typecheck/build 和 `git diff --check`；不默认执行 MCP、XHS、容器、公网 health 或核心旅行工作流烟测。

## 验收标准

- 云端点击“使用已保存凭据登录”不再因缺少 X Server 返回 `HOTEL_BROWSER_START_FAILED`。
- 携程卡片可以独立检查现有登录态，Gateway 重启后自动检查一次且不提交密码。
- 旅行查询不会在账号失效时暗中使用保存密码重登。
- 携程顶层按钮收敛为状态动作与账号管理两个入口，状态和错误文案与真实运行模式一致。
- API、日志、审计和前端均不泄漏凭据或浏览器内部状态。
