# ZhiCe-Agent Part 16：Web 产品体验与 Vue 前端工程

> 文档类型：当前活文档
>
> 当前状态：已实现并关闭
>
> 日期设计记录：`docs_design/2026-07-27-web-product-experience-and-vue-frontend-design.md`

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

统一主题名称为“曜石”：

- 浅色曜石：明亮背景、浅灰毛玻璃、石墨灰结构与主操作。
- 暗色曜石：深灰层级和高对比文字，不使用纯黑大块淹没内容。
- 跟随系统：监听系统主题。
- 毛玻璃集中用于侧栏、顶部、菜单、弹窗和输入区；阅读正文保持清晰。
- 动画尊重 `prefers-reduced-motion`。

首版主题偏好保存在浏览器本地，并按登录用户隔离；不增加跨设备同步。

## 6. 产品结构

### 6.1 登录与注册

桌面端使用品牌区和表单区横向滑动换位；移动端使用同一卡片内容切换。Owner 初始化复用视觉组件，但保留独立 URL 和安全条件。

### 6.2 聊天与 Session

- 保留当前聊天主结构。
- 缩窄 Session 侧栏，不显示消息条数。
- Session 右端三点菜单提供重命名和删除。
- 删除继续二次确认。
- 保留模型选择、Runtime 状态、停止 Turn、Tool confirmation、MCP elicitation 和外部渠道只读 Session。

### 6.3 账号与设置

头像使用单个 initials 文本节点。账号菜单提供个性化、个人资料、设置、按权限显示的管理后台和退出登录。

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

角色和权限使用中文能力分组；内部 key 默认收起。普通用户基础能力不显示为“没有权限”。Owner 固定只读。

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
3. 登录、聊天、设置和管理后台统一使用曜石主题。
4. 明暗主题、响应式和 reduced motion 可用。
5. Session 三点菜单、头像、设置中心和管理员信息架构按本文落地。
6. 系统监控与安全审计职责明确。
7. 现有 API、WebSocket、Session、RBAC 和渠道行为没有被前端迁移改变。
8. 旧 `web/static` 原生应用已删除。

## 11. 后续 Part

Part 17 负责运行可靠性、系统级诊断、生产部署与发布；它复用 Part 16 的 Vue 页面和组件，不建立第二套管理界面。

Part 18 负责 Skill Runtime、CLI 和本地运维优化；Skill source 状态页复用 Part 16 的管理后台结构。
