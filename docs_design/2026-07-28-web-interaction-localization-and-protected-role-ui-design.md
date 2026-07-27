# ZhiCe-Agent Web 交互、双语与系统角色保护修正设计

> 文档类型：日期设计记录
>
> 归属：Part 16 Web 产品体验与 Vue 前端工程
>
> 承接：`docs_design/2026-07-27-light-obsidian-palette-alignment-design.md`

## 1. 背景

Part 16 实际使用暴露出四组问题：账号菜单只能再次点击触发器关闭；Session 侧栏把所有非 Web channel 都显示成只读并泄露内部 `CLI_LEGACY`；语言设置没有英文且缺少全局语言/主题快捷入口；Owner 与 Admin 的角色层级、编辑边界和固定标签表达不清晰。

## 2. 目标

- 账号菜单和 Session 三点菜单点击外部自动收起，Escape 继续有效。
- Session 来源按 `continuation_mode` 展示，内部兼容值不直接暴露。
- 提供简体中文和 English 两种界面语言，偏好按登录用户保存在浏览器。
- 在聊天页和登录页提供紧凑、美观的语言与明暗主题快捷按钮。
- Owner 始终固定只读；Admin 权限只允许 Owner 修改，普通 Admin 不能修改 Admin 角色本身。
- 角色列表按 Owner、Admin、Developer、Auditor、Viewer 的权限层级稳定排序。
- 聊天页模型选择器不展示内部 endpoint 名称。
- 启动页面只保留“聊天”和“新会话”：前者打开最近更新的 Session，后者进入不落库的空白草稿界面。

## 3. 范围边界

- 不修改 Session ownership、conversation route、QQ 群隔离或 AgentLoop。
- `QQ + group` 继续使用后端返回的 `fork_only`，在 Web 中只读并允许派生 Web 私聊副本。
- QQ 私聊、微信私聊、CLI 与 Web 继续使用后端返回的 `writable`。
- `cli_legacy` 仍可作为数据库兼容值存在，UI 统一展示为 `CLI`。
- 语言偏好只在浏览器本地保存，不增加服务端用户字段或数据库迁移。
- Owner 权限保护仍以后端 `AuthStoreError` 为最终边界；Admin 角色更新由 API 根据 ActorContext 强制要求 Owner 身份。

## 4. 模块设计

### 4.1 弹层收起

`SessionSidebar` 在 document 级监听 `pointerdown`。点击账号区域或当前 Session 操作区域内部时保持对应弹层，点击其它位置关闭。组件卸载时移除监听，避免重复注册。

### 4.2 Session 来源展示

来源标签由 `channel + conversation_type + continuation_mode` 共同生成：

- `cli` / `cli_legacy`：`CLI`；
- `qq + group + fork_only`：`QQ群 · 只读来源`；
- QQ 私聊：`QQ · 可继续`；
- 微信私聊：`微信 · 可继续`；
- 其它来源：使用安全渠道名称，并由 `continuation_mode` 决定只读或可继续。

聊天只读 Banner 仍只依赖 `continuation_mode !== writable`，不根据 channel 猜测。

### 4.3 语言与主题快捷入口

`uiStore` 新增 `language`，支持 `zh-CN` 和 `en`，与 theme/density 一样按用户隔离持久化。应用语言同步写入 `document.documentElement.lang`。

复用 `QuickPreferences` 组件提供：

- 语言按钮：显示语言图标与简短当前/目标提示，点击在中英之间切换；
- 主题按钮：根据实际 resolved theme 显示太阳或月亮，点击在浅色/暗色间切换；
- hover/focus tooltip、键盘焦点和移动端紧凑布局。

设置中心语言下拉框与快捷按钮消费同一状态，不建立第二份偏好。

### 4.4 角色层级与保护

Owner 角色详情头部使用独立锁定说明块，不再复用会被 flex 拉伸的普通 pill。Owner checkbox 全部 disabled，`togglePermission` 在调用 API 前再次检查并立即返回。Owner 查看 Admin 时可修改其权限；非 Owner 查看 Admin 时显示“仅系统所有者可修改”并禁用控件。后端 store 只永久保护 Owner，角色 API 对 Admin 更新额外校验 `actor.role_keys` 包含 `owner`。

角色列表不采用数据库按 key 的字母序，前端统一按 `owner → admin → developer → auditor → viewer` 排序。模型选择器只显示模型名称，endpoint 继续保留在 API 与 store 中用于模型路由，但不作为普通聊天页 UI 文案暴露。

### 4.5 启动页面与空会话生命周期

`startPage=chat` 在登录后打开 Session API 返回的第一条，即最近更新的 Session；没有历史 Session 时保持空白聊天界面，并在首次发送时按现有行为创建 Session。`startPage=new` 只调用 Session store 的 `startDraft()` 清空 active Session 和消息，在前端展示空白草稿，不调用 WebSocket 或 Session API。用户发送第一条真实消息时，`chat.send()` 才按现有链路创建 Session 并发送内容。侧栏“新对话”使用同一草稿行为，因此刷新、反复点击新对话或退出登录都不会产生需要清理的空 Session。旧 `startPage=last` 本地偏好迁移为 `chat`，不再维护 `lastSession` 浏览器键。

## 5. 变更文件

- `web/frontend/src/i18n.ts`
- `web/frontend/src/stores/ui.ts`
- `web/frontend/src/components/QuickPreferences.vue`
- `web/frontend/src/components/SessionSidebar.vue`
- `web/frontend/src/components/ChatPage.vue`
- `web/frontend/src/components/SettingsCenter.vue`
- `web/frontend/src/layouts/AuthLayout.vue`
- `web/frontend/src/layouts/AdminLayout.vue`
- `web/frontend/src/admin/permissions.ts`
- 相关组件测试与 `web/frontend/src/styles/app.css`
- `agent/web/static/**`
- 当前 Part 16 文档与索引

## 6. 测试方案

- 账号菜单点击触发器打开，点击外部关闭；点击菜单内部不误关。
- CLI legacy、QQ 群、QQ 私聊和微信 Session 来源标签分别验证。
- 语言设置和快捷按钮切换后更新 store、`html lang` 与本地存储。
- 主题快捷按钮切换 resolved theme。
- Owner checkbox disabled；Owner 可修改 Admin；非 Owner 修改 Admin 被前后端同时拒绝。
- “聊天”打开最近 Session；“新会话”进入前端草稿，首次发送时才创建 Session。
- 前端 lint、typecheck、Vitest、production build 与真实浏览器烟测。

## 7. 验收标准

1. 所有临时菜单都能点击外部自动关闭。
2. UI 不出现 `LEGACY`，只有 QQ 群显示只读来源。
3. 中英切换和明暗切换均有快捷按钮，设置页与快捷入口状态一致。
4. Owner 固定状态不拉伸且不可勾选；Owner 可修改 Admin 权限，非 Owner 不可修改。
5. 角色列表按权限层级排序，聊天页不显示 endpoint 名称。
6. 现有 Session、聊天与渠道语义不变。
