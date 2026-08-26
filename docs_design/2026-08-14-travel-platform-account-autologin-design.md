# 旅行数据源账号自动登录设计

> 说明：当前代码已将携程账号管理 API 移至 `/api/admin/external-platforms/ctrip/*`，携程保持内置只读 Tool，不作为 MCP Server。旅行查询不再自动提交密码，启动只做无副作用登录态检查，服务器登录默认使用 headless Chromium；当前边界见 `docs_design/2026-08-26-ctrip-login-state-convergence-design.md` 和 Part 19 活文档。

## 背景

旅行规划需要读取携程指定日期的酒店参考价，并尽量减少小红书只读账号反复扫码。
项目没有 OTA 商业 API 签约能力，因此酒店价格采用低频、只读的本地浏览器查询。
现有小红书 sidecar 只保存 Cookie，上游登录器只支持二维码登录；携程当前网页则明确
提供账号、邮箱或手机号加密码登录。

## 目标

- Owner 可在管理后台 MCP 区保存携程账号密码并发起一次授权。
- 密码从进程环境或 Git 忽略的 runtime `config/.env` 读取，不进入 YAML、日志、审计或前端响应。
- 携程使用固定 Playwright persistent profile；正常情况下后续查询直接复用会话，失效时才用密文凭据自动登录。
- 自动登录遇到验证码、安全验证或短信确认时打开可见浏览器等待 Owner 人工完成。
- 旅行 Agent 只能调用酒店搜索与价格读取工具，不能接触凭据、通用浏览器操作、预订或付款。
- 小红书保持现有 Cookie 自动复用；管理后台明确提示平台不提供密码登录，失效后仍需扫码或手机验证。

## 范围边界

- 首期酒店平台仅支持携程，首期查询口径限定一间房、两位成人。
- 不实现预订、付款、取消、优惠券领取、App 跳转和订单查询。
- 不自动破解验证码或绕过平台风控；出现验证即转人工。
- 不把账号、密码、Cookie、profile 路径、进程号或平台原始错误返回给普通用户。
- Windows 与 Linux 都可使用 runtime `.env`；Docker、Kubernetes 和云部署可直接注入平台 Secret。
- 查询结果是账号观察价，不声明为全网最低价或所有用户可获得的价格。

## 模块设计

### 跨平台环境凭据仓库

`EnvironmentPlatformCredentialStore` 优先读取进程环境中的 `ZHICE_CTRIP_USERNAME` 与
`ZHICE_CTRIP_PASSWORD`，用于 Linux、Docker、Kubernetes 或云平台 Secret 注入；没有外部注入时，
读取 `${ZHICE_AGENT_WORKSPACE}/config/.env`。Owner 在后台保存时原子更新同一 `.env`，Linux 文件
权限设为 `0600`。API 只返回来源类型、`credential_configured` 和脱敏账号提示；外部环境 Secret
不能由后台伪装成已删除。

### 携程账号监督器

`HotelAccountSupervisor` 负责安全状态、保存/删除凭据和启动登录助手：

1. 保存凭据后启动固定 Python 模块，不在命令行传递账号密码。
2. 登录助手从进程环境或 runtime `.env` 读取，在固定 profile 目录启动有头 Playwright。
3. 已登录则直接成功；否则自动填写携程账号密码并提交。
4. 若出现验证码或安全校验，浏览器保持可见，等待 Owner 完成。
5. 助手只写安全状态文件，监督器轮询完成并更新管理后台投影。

同一 profile 只允许一个浏览器实例；管理登录和酒店查询同时使用进程内锁与操作系统
文件锁串行，覆盖 Gateway 登录助手和独立 hotel-browser MCP 之间的跨进程竞争。保存新凭据
或删除凭据前先停止仍在运行的旧登录助手，避免旧凭据继续完成登录。

### 酒店只读 MCP

`integrations.hotel_browser_mcp` 暴露：

- `check_hotel_login_status`
- `search_hotels`

搜索先复用 persistent profile；发现未登录且存在凭据时进行一次自动登录。随后通过携程酒店
公开搜索表单解析城市 ID，以指定入住/退房日期打开结果页，最多返回十个结构化候选。
返回字段包含来源、酒店名、评分、区域摘要、房型摘要、账号观察价文本、查询时间和查询条件。

### Owner 管理 API 与 UI

新增 Owner-only API：

- `GET /api/admin/mcp/hotel-browser/status`
- `PUT /api/admin/mcp/hotel-browser/credentials`
- `DELETE /api/admin/mcp/hotel-browser/credentials`
- `POST /api/admin/mcp/hotel-browser/login`

管理后台 MCP 页面增加携程账号卡片。密码输入只在提交请求体中出现，提交后立即清空；响应、
Pinia 状态和审计事件不保存密码。小红书卡片补充“不支持密码登录”的真实能力说明。

## 数据流

```text
Owner 保存携程凭据
  -> runtime config/.env 或部署 Secret
  -> 有头登录助手
  -> persistent browser profile
  -> 安全登录状态

旅行 Agent 调用 search_hotels
  -> hotel-browser MCP
  -> profile 单实例锁
  -> 已登录会话 / 一次自动登录
  -> 携程酒店搜索结果
  -> 结构化账号观察价
```

## 变更文件

- `agent/applications/travel/account_credentials.py`
- `agent/applications/travel/hotel_accounts.py`
- `integrations/hotel_browser_mcp/*`
- `agent/app/runtime.py`
- `agent/app/api/schemas.py`
- `agent/app/api/routes.py`
- `config/config.example.yml`
- `pyproject.toml`
- `web/frontend/src/api/types.ts`
- `web/frontend/src/api/client.ts`
- `web/frontend/src/stores/admin.ts`
- `web/frontend/src/layouts/AdminLayout.vue`
- 相关前后端单元测试与测试说明

## 测试方案

- 环境凭据仓库：`.env` 往返、外部环境覆盖、外部 Secret 禁止后台伪删除、删除和响应不泄漏。
- 账号监督器：不支持环境、缺少凭据、重复启动、状态文件收敛、关闭时回收子进程。
- 携程适配器：已有登录、密码登录成功、人工验证、结果卡片解析、范围校验和 profile 锁。
- API：Owner 成功，Admin 403；所有响应和审计均不含密码、账号原文和文件路径。
- 前端：保存后清空密码；状态、自动登录和小红书限制文案正确。
- 运行 Ruff、目标 Pytest、前端 TypeScript、Vitest 和正式构建。

## 验收标准

- Owner 保存一次携程账号密码后可自动建立并复用登录会话。
- Windows、Linux 或容器重启后凭据仍可从 runtime `.env` 或部署 Secret 读取，profile 仍可复用。
- 登录失效时先自动登录；需要验证码时明确转人工，不循环重试。
- 酒店工具不包含任何预订或付款能力，只返回带日期与时间戳的账号观察价。
- 小红书不展示虚假的账号密码能力，现有扫码 Cookie 闭环保持可用。
