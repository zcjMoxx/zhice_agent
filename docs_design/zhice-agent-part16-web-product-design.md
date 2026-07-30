# ZhiCe-Agent Part 16：Web 产品体验与 Vue 前端工程

> 文档类型：当前活文档
>
> 当前状态：已实现并关闭
>
> 日期设计记录：`docs_design/2026-07-27-web-product-experience-and-vue-frontend-design.md`
>
> 布局与主题修正：`docs_design/2026-07-27-web-chat-layout-and-theme-correction-design.md`
>
> 明亮曜石配色审核：`docs_design/2026-07-27-light-obsidian-palette-alignment-design.md`
>
> 菜单、来源、本地化与系统角色修正：`docs_design/2026-07-28-web-interaction-localization-and-protected-role-ui-design.md`

> 六套主题家族与独立明暗模式：`docs_design/2026-07-28-six-theme-family-and-color-mode-design.md`

## 1. Part 16 定位

Part 16 把原路线中的 Web、会话与用户治理优化前移，建立 ZhiCe-Agent 下一阶段稳定的 Web 产品面和 Vue 工程底座。

Part 16 已用 Vue 3、Vite 和 TypeScript 取代原生 `web/static` 单体应用，同时保持现有 FastAPI、REST、WebSocket、Session、RBAC 和 Agent 内核边界不变。源码位于 `web/frontend`，production build 位于 `agent/web/static`，旧原生入口已删除。

当前实现还增加了两个 app 层只读出口：`GET /api/admin/monitor` 聚合 Gateway、Capability 与结构化 Runtime Activity 真值；Audit 列表兼容原接口并扩展筛选、游标分页和 CSV 导出。两者都不进入 AgentLoop，也不实现 Part 17 根因诊断。

## 2. 稳定边界

- AgentLoop 不感知 Vue、路由、主题或浏览器状态。
- FastAPI Gateway 继续同源提供 Web、`/api/*` 和 `/ws`。
- 现有 API error、WebSocket frame、`turn_id`、Session JSONL 和 permission key 保持兼容。
- Vue 组件不直接读取 SQLite、trace、workspace 或 Secret。
- 系统监控只消费 app 层提供的 health/Activity read model。
- Security Audit 继续只保存安全和管理事件，不重新吸收普通 Runtime Activity。
- Provider retry、系统级诊断和生产部署属于 Part 17。

## 3. 技术架构

```text
Vue 3 + Vite + TypeScript
Vue Router
Pinia
Vitest + Vue Test Utils
Lucide Vue
CSS Design Tokens
```

不引入大型 UI 组件库。源码位于 `web/frontend/`，production build 输出到随 Python wheel 发布的 `agent/web/static/`。安装后运行 `zcagent gateway` 不要求 Node.js。

前端状态按能力拆分：

```text
authStore
sessionStore
chatStore
modelStore
channelStore
adminStore
uiStore
```

WebSocket client 和 RuntimeEvent reducer 为独立 typed module，组件不能直接解析原始 frame。

## 4. 路由与页面

```text
/
├─ 未登录：登录 / 注册
└─ 已登录：聊天应用壳

/_setup
└─ Owner 初始化

/admin
└─ 管理后台
```

FastAPI 继续显式提供三个 SPA 入口。Vue Router 负责页面选择，API 和服务端权限检查仍是最终授权边界。

## 5. 视觉系统

主题由“主题家族”和“外观模式”两个独立维度组成。主题家族包括经典黑白、象牙曜石、深海蓝灰、森雾浅绿、雾紫极光和琥珀暖砂；每个家族均提供浅色与暗色 token。外观模式包括跟随系统、浅色和暗色，只决定当前家族采用哪套明暗 token。默认使用象牙曜石并跟随系统。

- 象牙曜石浅色：明亮象牙近白背景、浅灰毛玻璃、冷蓝灰石墨结构与主操作。
- 象牙曜石暗色：深灰层级、雾银结构面和高对比文字，不使用纯黑大块淹没内容。
- 跟随系统：监听系统主题，但不改变用户已选主题家族。
- 毛玻璃集中用于侧栏、顶部、菜单、弹窗和输入区；阅读正文保持清晰。
- 动画尊重 `prefers-reduced-motion`。

浅色曜石采用接近白色、只带极弱暖感的底色，配合雾灰玻璃与灰蓝石墨强调；登录表单区允许克制的浅青绿色环境光。它不使用纯黑、大面积米黄或蓝色品牌块，品牌标记、当前项、用户气泡与主操作均以石墨中性色为主。聊天 Session 侧栏标准宽度为 `280px`、紧凑宽度为 `260px`；管理后台侧栏为 `248px`，设置中心导航列为 `240px`。聊天正文保持固定标准阅读宽度，不提供额外宽度偏好。原深石墨登录面板只属于暗色曜石。

暗色曜石采用“雾银曜石”方案：炭灰页面底色、雾银石墨侧栏、半透明中灰玻璃面、柔白正文与银灰强调。它与浅色曜石保持相同的结构层级，不使用接近纯黑的主背景，也不使用蓝色焦点作为视觉中心；登录环境光、品牌面板、管理 Hero、头像渐变和主题缩略图均消费同一套暗色 token。

主题家族与外观模式分别保存在浏览器本地，并按登录用户隔离；不增加跨设备同步。旧版 `theme` 明暗偏好在加载时迁移为新的 `colorMode`。

## 6. 产品结构

### 6.1 登录与注册

桌面端使用品牌区和表单区横向滑动换位；移动端使用同一卡片内容切换。Owner 初始化复用视觉组件，但保留独立 URL 和安全条件。

### 6.2 聊天与 Session

- 保留当前聊天主结构。
- 缩窄 Session 侧栏，不显示消息条数。
- Session 右端三点菜单提供重命名和删除。
- Session 与账号浮层点击外部自动收起。
- `cli_legacy` 只作为内部兼容来源值，界面统一显示为 `CLI`；QQ 私聊、微信、CLI 和 Web 可继续，只有 QQ 群 Session 在 Web 中显示为只读来源并允许派生。
- 删除继续二次确认。
- 保留模型选择、Runtime 状态、停止 Turn、Tool confirmation、MCP elicitation 和外部渠道只读 Session。
- 主滚动视口占满聊天区域，消息正文独立限宽，输入区作为正常底部行放置；页面本身不产生横向滚动。
- 当前标签页按用户和 Session 保存阅读位置。只有接近底部或刚发送消息时跟随新内容，刷新和向上阅读历史不会被强制拉到最新。
- Session 列表在剩余高度内独立滚动，左下角账号入口始终可见。

### 6.3 账号与设置

头像使用单个 initials 文本节点。账号菜单提供个性化、个人资料、设置、按权限显示的管理后台和退出登录。

界面语言支持简体中文与 English，并按当前身份保存在浏览器本地。登录页、聊天页和管理后台提供紧凑的语言与明暗主题快捷按钮，完整偏好仍在设置中心管理。

启动页面支持“聊天”和“新会话”：聊天打开最近更新的 Session；新会话只进入前端空白草稿，不立即创建 Session，用户发送第一条消息时才创建。刷新、反复点击新对话或退出登录都不会留下空 Session。

设置中心栏目：

1. 常规。
2. 个性化。
3. 个人资料。
4. 账号与安全。
5. 渠道连接。

### 6.4 管理后台

```text
概览
用户管理
角色与权限
系统监控
安全审计
```

角色和权限按当前界面语言显示能力分组；内部 key 默认收起。普通用户基础能力不显示为“没有权限”。角色按系统所有者、管理员、开发者、审计员、普通用户排序。Owner 固定只读；Admin 权限只能由 Owner 修改，非 Owner 在前端不可操作且服务端继续作为最终权限边界。

系统监控展示现有 Gateway、Channel、MCP、Subagent、Memory、Context 和 Activity 真值。安全审计展示登录、权限、角色和危险操作事件，并支持筛选、分页、详情与导出。

## 7. 组件边界

```text
AuthLayout
AppShell
├─ SessionSidebar
├─ ChatPage
└─ SettingsCenter

AdminLayout
├─ OverviewPage
├─ UsersPage
├─ RolesPage
├─ MonitorPage
└─ AuditPage
```

常用低层组件包括 SessionActionMenu、AccountMenu、RuntimeStatus、ConfirmationDialog、MessageComposer、ThemeSelector 和 ChannelBindingPanel。

## 8. 迁移顺序

1. Vue 工程、主题、Router、Pinia、build/wheel。
2. 登录、注册、Owner 初始化。
3. 聊天壳、Session 和模型选择。
4. WebSocket、RuntimeEvent、confirmation 和 stop。
5. 账号、设置和渠道绑定。
6. 用户、角色、系统监控和安全审计。
7. 测试、正式切换和删除旧原生前端。

迁移期间旧页面只作为行为对照；最终不保留第二套生产入口。

## 9. 测试与验收

- Vitest 覆盖 stores、API error、WebSocket 和 RuntimeEvent reducer。
- Vue Test Utils 覆盖登录切换、Session 菜单、设置、角色和 Audit 交互。
- Python 测试覆盖 SPA 路由、包内静态资源和新增 app API 权限。
- 少量真实 Gateway 浏览器烟测覆盖登录聊天、Session 菜单、主题、管理后台和渠道状态。
- `python -m ruff check .`、`python -m pytest`、前端 lint/typecheck/test/build 全部通过，或明确记录无关历史失败。

## 10. 完成定义

Part 16 完成时：

1. Vue 是唯一正式 Web 前端。
2. Python wheel 自带可运行静态资源。
3. 登录、聊天、设置和管理后台统一消费六套主题的语义 token。
4. 主题家族、跟随系统/浅色/暗色模式、响应式和 reduced motion 可用。
5. Session 三点菜单、头像、设置中心和管理员信息架构按本文落地。
6. 系统监控与安全审计职责明确。
7. 现有 API、WebSocket、Session、RBAC 和渠道行为没有被前端迁移改变。
8. 旧 `web/static` 原生应用已删除。

## 11. 后续 Part

Part 17 负责运行可靠性、系统级诊断、MCP 动态可靠性和私有镜像部署；它复用 Part 16 的 Vue 页面和组件，不建立第二套管理界面。当前设计见 `docs_design/zhice-agent-part17-reliability-diagnostics-deployment-design.md`。

Part 18 负责 Skill Runtime、CLI 和本地运维优化；Skill source 状态页复用 Part 16 的管理后台结构。
