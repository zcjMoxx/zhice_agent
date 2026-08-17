# 运行配置、Prompt、部署与管理页事实收敛设计

## 背景

旅行规划已经形成专用应用、固定并发研究 Profile、小红书只读服务和携程账号观察价能力，但仓库示例、运行态 Prompt、Docker 镜像、管理页统计和部分活文档仍保留旧事实。继续让这些入口漂移会造成两类问题：用户看到的服务数量与认证方式错误；新部署或旧工作区升级后，运行行为与当前代码不一致。

## 目标

- 代码耦合 Prompt 在 Gateway 启动时从仓库受控同步，用户身份 Prompt 保持可定制。
- 配置示例只展示真实生效的旅行开关，旅行专用 Subagent Profile 由旅行应用内置。
- 模型端点配置校验默认模型与 `supported_models` 的一致性，并说明可选 `fast`、`reasoning` 角色及回退规则。
- Docker 镜像完整包含携程只读查询所需的 Python extra 与 Chromium，浏览器登录态保存到已有 state volume。
- Owner 的 MCP 与 Skills 页面把协议服务与业务账号分区展示：MCP 只统计真实 Server，外部平台账号并排展示小红书和携程。
- README、当前活设计和测试与上述运行事实一致。

## 非目标

- 不把小红书改造成 OAuth，也不尝试保存其账号密码；继续使用扫码或手机验证产生的 Cookie。
- 不把携程伪装成真实 MCP Server；它仍是内置只读 Tool，只在管理页作为可管理的数据源卡展示。
- 不修改真实运行态 Secret、Cookie、账号密码或 API Key，不执行云端发布。
- 不引入新的旅行模式、递归 Subagent 或支付、下单能力。

## Prompt 所有权

受控 Prompt 分为两组：旅行应用 Prompt 和核心协议 Prompt。两组都随代码同步，避免旧工作区继续使用已经失效的工具发现、Skill 执行、旅行意图提取和 Memory 规则。`identity.md` 等人格与用户偏好 Prompt 不进入受控清单，保留工作区覆盖能力。

本轮受控清单包含：

- `travel_intake.md`
- `travel_planning.md`
- `travel_planning_continuation.md`
- `travel_requirement_extraction.md`
- `tool_use_policy.md`
- `skills_intro.md`
- `memory_policy.md`

同步采用原子替换，并由单元测试验证只覆盖受控文件、不触碰用户 Prompt。

## 配置模板与运行态迁移

旅行应用真正生效的工作区配置仅保留 `enabled`、`max_evidence_items` 和 `max_plan_bytes`。历史字段 `default_mode`、`max_search_results`、`deep_subagent_count`、`xhs_readonly_enabled` 在本轮继续被 loader 接受但忽略，避免已有工作区升级后启动失败；示例和活文档不再宣传这些假开关，后续版本再完成移除。

`config.example.yml` 不再展开五个内置旅行 Profile。通用 Subagent 配置只保留普通聊天可用的 `explorer` 示例；旅行 Profile 在旅行 Turn 边界注入，只有显式同名配置才覆盖内置默认值。

## 模型模板与角色规则

`config/models.example.json` 是初始化模型配置的唯一来源。`zcagent init` 在运行态文件缺失时原样复制为 `${workspace}/config/models.json`；已有文件默认保留，只有显式 `--force` 才覆盖。Python 和 CLI 不再维护 endpoint、协议、地址、模型、Token 预算等第二套生成参数。

首次初始化同时创建通用工作区骨架：`contexts/sessions`、`contexts/memory`、`contexts/users`、`contexts/shared/readonly`、`state/mcp_runtime`、`extends` 与 `logs`。三份配置 Example 与全部 Prompt 均按原始字节复制，避免 Windows 换行转换产生第二种运行态内容。能力专属数据目录继续在对应能力启用时按需创建。Example 中非 Secret 的待替换值使用“请填写……”中文占位；`.env` Secret 保持空值并使用相邻中文说明，避免占位文字被加载为真实凭据。

Example 中需要用户替换的 endpoint 名、服务地址和模型名统一使用“请填写……”中文占位；`protocol`、`role`、数值类型默认和 `${ENV_VAR}` 名称属于协议结构，不伪装成中文值。初始化后由用户编辑 `models.json` 与 `.env`。

- `role: default` 是普通主模型端点。
- `role: fast` 和 `role: reasoning` 是可选标签，不是必须配置的内置模型。
- 旅行 Child 请求 `fast` 时，没有可用 `fast` 端点就继承当前主模型。
- 同一特殊角色存在多个启用端点时，按更小的 `priority` 优先，配置顺序只作为同优先级稳定顺序。
- 非空 `supported_models` 必须包含端点默认 `model`，防止 UI 可选模型与真实默认路由相互矛盾。
- Loader 继续兼容旧的 `default` 同名 endpoint，但新初始化不再由代码选择任何 endpoint 名。

## Docker 携程能力

运行镜像安装 `hotel-browser` extra，并安装 Playwright bundled Chromium。Linux 容器设置空的 `HOTEL_BROWSER_CHANNEL`，避免默认寻找不存在的系统 Chrome。携程浏览器 profile 继续落在 `${ZHICE_AGENT_WORKSPACE}/state/browser_profiles/ctrip`，由 `zhice-state` volume 持久化；账号密码由 `.env`、env-file 或平台 Secret 注入，不写入仓库镜像层。

## 管理页

- “MCP 服务监控”只展示并统计真实 MCP Server；当前示例运行态为 5，携程不进入 `Servers`、Catalog 或 MCP 网格。
- 小红书的连接、Catalog、调用统计和服务重启保留在 `xhs-readonly` MCP 卡；认证方式固定显示“扫码 / Cookie”，不复用通用 OAuth 文案。
- “外部平台账号”是独立 Owner 区域：小红书负责扫码、Cookie 与登录检查，携程负责账号凭据和登录。
- 两张外部账号卡同一网格、等宽等高；内部面板填满剩余高度，移动端恢复单列自然高度。
- 账号 API 使用 `/api/admin/external-platforms/{xhs|ctrip}`，安全投影返回 `platform_id`。仅小红书 MCP 重启继续位于 `/api/admin/mcp/xhs-readonly/restart`。
- 账号审计使用 `external_platform.*` 与 `external_platform_account`；小红书服务重启继续使用 `mcp.xhs.restarted` 与 `mcp_server`。
- MCP 卡认证文案按真实机制展示：高德与 Tavily 为 `API Key`，小红书为“扫码 / Cookie”，Open-Meteo 与 12306 为“无需认证”；不再把“未使用 OAuth”误写成认证方式。
- 小红书页面加载时的登录收敛检查静默执行，状态以账号卡为准；仅用户主动操作显示反馈，且反馈不吸顶。携程同样以账号卡内状态为准。
- 原“高级设置”入口直接命名为“安全审计”；运行诊断以服务端 `is_error` 为唯一分类事实，`ok=true` 的 `MCP_OK`、`OK` 等成功码不得被归为事故。

## 变更文件

- Prompt 与配置：`agent/config.py`、`agent/cli.py`、`agent/applications/travel/config.py`、`config/config.example.yml`、`config/models.example.json`
- 模型选择：`agent/config.py`、`agent/subagents/runtime.py`、`agent/app/runtime.py`
- 部署：`deploy/Dockerfile`、`deploy/docker-compose.yml`、`deploy/scripts/deploy.sh`、`deploy/README.md`
- 管理页：`web/frontend/src/layouts/AdminLayout.vue`、`web/frontend/src/styles/app.css`
- 文档：`README.md` 与相关无日期活设计文档
- 测试：配置、部署、旅行和前端对应主题目录

## 测试方案

- 配置单测覆盖受控 Prompt、模型 Example 逐字复制、已有文件保留、`--force` 覆盖、历史旅行字段兼容、模型默认值一致性和角色优先级。
- 部署静态测试覆盖 `hotel-browser` extra、Chromium 安装、Linux browser channel 和 state volume。
- API 测试覆盖外部平台账号路径、Owner 权限、无凭据回传、`platform_id` 和账号/MCP 审计边界。
- 前端组件测试覆盖 `Servers=5`、携程不进入 MCP 网格、真实认证类型、小红书静默状态收敛、两张账号卡、等高 CSS 契约，以及成功码不进入异常证据。
- 运行 Ruff、后端全量 Pytest、前端 Vitest、ESLint、TypeScript 和生产构建。
- 重启本地 Gateway 后，通过真实管理页确认 5 个 MCP Server、两张独立账号卡、小红书“扫码 / Cookie”、卡片等高和携程登录状态。

## 验收标准

- 旧工作区可直接启动，受控 Prompt 自动更新且用户 Prompt 不被覆盖。
- 示例配置不再包含五个旅行专用 Profile 或无效旅行开关。
- 错误的 `supported_models` 配置启动时给出清晰结构化配置错误。
- Docker 镜像具备携程浏览器查询所需依赖和持久登录态路径。
- Owner 管理页显示 5 个真实 MCP Server；独立账号区显示小红书与携程两张等宽等高卡片，携程不出现在 MCP 网格或计数中。
- 全量测试、前端构建和真实页面验收通过。
