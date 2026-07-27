# ZhiCe-Agent Web 产品体验与 Vue 前端工程设计记录

> 日期：2026-07-27
>
> 状态：已按方案落地
>
> 归属：Part 16 Web 产品体验与 Vue 前端工程
>
> 当前活文档：`docs_design/zhice-agent-part16-web-product-design.md`

> 实施说明：当前代码已采用本文方案；Vue source 位于 `web/frontend`，build 输出到 `agent/web/static`，旧 `web/static` 原生应用已删除。当前事实和后续维护以 Part 16 活文档为准。

## 1. 背景

ZhiCe-Agent 当前 Web 已经承载登录、注册、Owner 初始化、聊天、Session、模型选择、RuntimeEvent、Tool confirmation、QQ/微信绑定、账号设置、用户管理、角色权限和安全审计。现有实现仍集中在：

```text
web/static/index.html
web/static/styles.css
web/static/app.js
web/static/runtime-event-state.js
```

其中 `app.js` 已超过 70 KB。API 请求、WebSocket、全局状态、DOM 查询、渲染和交互事件继续集中在一个脚本中，新增设置中心、管理监控和响应式交互时，修改成本与回归风险都会快速上升。

当前路线原计划先做运行可靠性和生产部署，再做 Web 产品化。讨论后确认先把原 Part 17 前移为 Part 16：当前本地优先系统已经具备完整业务闭环，用户每天直接接触的是 Web；先形成稳定的前端组件、状态与管理界面，也能给后续 Part 17 系统诊断和生产部署提供真实展示面。

这次前移不表示把 Provider retry、系统级诊断引擎、容器和公网部署同时塞入前端阶段。Part 16 只消费当前已有 API、RuntimeEvent、Activity、Audit 和 health 真值，并补齐必要的 app/API read model；运行可靠性和生产部署顺延为 Part 17。

## 2. 目标

1. 使用 Vue 3、Vite 和 TypeScript 替代持续膨胀的原生静态脚本。
2. 保持现有 FastAPI 同源 Gateway、REST API、`WebSocket /ws`、Session JSONL 和权限 key 兼容。
3. 建立统一的“曜石”视觉系统，支持浅色、暗色和跟随系统。
4. 重构登录/注册、聊天、Session、账号菜单、设置中心和管理后台信息架构。
5. 让管理员明确区分系统监控、Runtime Activity 和 Security Audit。
6. 把前端构建产物纳入 Python 安装包，使正式运行不依赖 Node.js。
7. 通过前端单元测试、组件测试、Gateway 测试和少量真实入口浏览器烟测保持迁移可验证。

## 3. 非目标

- 不改变 AgentLoop、LLMProvider、ToolProvider、SessionStore 或 Channel 协议。
- 不在 Part 16 实现 Provider retry/cooldown、系统级根因诊断、事故聚合或 `diagnose_system_activity` Tool。
- 不在 Part 16 提供 Docker、反向代理、KMS、Secret Manager、水平扩展或多实例共享队列；这些属于 Part 17。
- 不改变现有 RBAC key、用户基础能力与额外特权的语义。
- 不把 Element Plus、Ant Design Vue 等大型 UI 框架引入第一版。
- 不在首次 Vue 切换中同时实现 Session 自动标题、完整归档/导出或物理用户删除；这些需要后续独立设计和数据生命周期验收。
- 不长期维护原生前端和 Vue 前端两套产品入口。

## 4. 产品信息架构

### 4.1 公共入口

```text
/
├─ 未登录：登录 / 注册滑动页
└─ 已登录：聊天应用壳

/_setup
└─ Owner 初始化页

/admin
└─ 具备对应权限的管理后台
```

路由保持兼容。FastAPI 继续为 `/`、`/admin`、`/_setup` 返回同一个 Vue `index.html`，Vue Router 再根据 URL、Owner 状态、登录态和权限决定页面。

### 4.2 登录与注册

- 桌面端使用左右双面板滑动：品牌引导区和表单区在登录/注册之间换位。
- 浅色曜石使用明亮毛玻璃、石墨灰结构和克制冷色柔光。
- 暗色曜石使用深灰层级，但保持表单、文字和焦点对比度，不把所有表面做成纯黑。
- 移动端不做左右面板横移，改为同一卡片内淡入切换。
- 尊重 `prefers-reduced-motion`；关闭动画时直接切换内容。
- 登录、注册和 Owner 初始化复用视觉组件，但不混淆真实业务入口和安全条件。

### 4.3 聊天与 Session

- 保留当前聊天主结构，不重新设计 Agent 交互语义。
- Session 侧栏缩窄，不再显示消息条数。
- Session 行只保留标题、必要来源标识和悬停/选中时出现的三点按钮。
- 三点菜单第一版只提供“重命名”和“删除”；删除继续要求二次确认。
- 保留侧栏收起、模型选择、Runtime 状态、Tool confirmation、MCP elicitation、停止 Turn 和只读外部渠道 Session。
- 消息阅读区保持明亮、克制；毛玻璃只用于侧栏、顶部、菜单、弹窗和输入区。

### 4.4 账号菜单与设置中心

头像使用单个 initials 文本节点，不再由两个绝对定位字母拼接。英文名称取首词和末词首字母；中文名称使用稳定的一至两个字符规则。

账号菜单：

```text
当前账号
├─ 个性化
├─ 个人资料
├─ 设置
├─ 管理后台（按权限显示）
└─ 退出登录
```

设置中心采用左侧栏目、右侧详情：

1. 常规：语言、界面密度、启动页面。
2. 个性化：跟随系统/浅色/暗色、曜石主题、聊天内容宽度。
3. 个人资料：用户名、显示名称、头像预览。
4. 账号与安全：修改密码和安全说明。
5. 渠道连接：QQ、微信状态、绑定、重连和解绑。

首版主题偏好使用浏览器本地、按用户身份隔离的 key；未登录时使用公共 pre-auth key。跨设备同步不在本次范围。

### 4.5 管理后台

管理后台从 `Users / Roles / Audit` 调整为：

```text
概览
用户管理
角色与权限
系统监控
安全审计
```

栏目按当前 actor 权限独立显示，不能因为可以进入 `/admin` 就默认获得所有管理能力。

#### 角色与权限

- 内置角色显示为：系统所有者、管理员、开发者、普通用户、审计员。
- 默认展示中文能力名称、说明和能力域，不直接堆叠内部 key。
- 内部 permission key 保持不变，在“技术详情”中按需展开。
- 普通用户和开发者即使没有额外特权，仍拥有聊天、本人 Session、Memory、安全 Tool 等基础能力；界面必须明确说明。
- Owner 权限固定、只读。
- 前端必须为所有内置 permission key 提供中文映射测试；未知 key 回退到技术名称，不静默丢失。

#### 系统监控

Part 16 只展示已有真值：Gateway health、可选 capability 状态、QQ/微信连接、MCP/Subagent/Memory/Context 状态，以及当前 Activity 表可证明的近期异常摘要。

它不在浏览器中猜测事故原因，不把安全审计事件当成运行日志，也不伪造 Part 17 尚未实现的系统级诊断结果。

#### 安全审计

Audit 改名为“安全审计”，只展示：

- 登录成功/失败与账号状态变更。
- 用户、角色和权限变更。
- 权限拒绝。
- 高风险 Tool 请求、确认和结果。
- 其它明确的安全或管理事件。

提供事件类型、操作者、结果和时间范围筛选、分页、详情与导出；普通 turn/tool 成功仍属于 Runtime Activity，不回流 Security Audit。

## 5. 前端工程架构

### 5.1 技术基线

```text
Vue 3
Vite
TypeScript
Vue Router
Pinia
Vitest
Vue Test Utils
Lucide Vue
CSS Design Tokens
```

第一版使用自有组件与 CSS variables，不引入大型组件库。

### 5.2 源码与构建产物

```text
web/
  frontend/
    package.json
    vite.config.ts
    tsconfig.json
    src/
      main.ts
      App.vue
      router/
      stores/
      api/
      websocket/
      runtime-events/
      layouts/
      pages/
      components/
      styles/
      assets/

agent/
  web/
    static/              # Vite build output，随 Python wheel 发布
```

Vite production `base` 使用 `/static/`。`agent.app.gateway._default_static_dir()` 改为定位包内 `agent/web/static`；测试继续可以通过 `create_app(..., static_dir=...)` 注入替代目录。

仓库提交构建产物，原因是安装后的 `zcagent gateway` 不应要求 Node.js。CI 重新运行 `npm ci && npm run build` 并校验工作区没有生成差异。Python wheel 显式包含 `agent/web/static`。

### 5.3 状态划分

```text
authStore       登录、注册、当前用户和权限
sessionStore    Session 列表、打开、新建、重命名、删除
chatStore       消息、当前 Turn、确认、停止和只读状态
modelStore      endpoint/model 列表和 Session 偏好
channelStore    QQ/微信状态与绑定流程
adminStore      用户、角色、监控和安全审计
uiStore         主题、侧栏、弹层和账号菜单
```

WebSocket 连接和 RuntimeEvent reducer 是独立模块。Vue 组件只消费 typed store action/state，不直接解析原始 frame。

### 5.4 页面与组件

```text
AuthLayout
├─ LoginPage
├─ RegisterPanel
└─ OwnerSetupPage

AppShell
├─ SessionSidebar
│  ├─ SessionItem
│  ├─ SessionActionMenu
│  └─ AccountMenu
├─ ChatPage
│  ├─ ChatHeader
│  ├─ MessageList
│  ├─ RuntimeStatus
│  ├─ ConfirmationDialog
│  └─ MessageComposer
└─ SettingsCenter

AdminLayout
├─ OverviewPage
├─ UsersPage
├─ RolesPage
├─ MonitorPage
└─ AuditPage
```

## 6. 数据流与兼容边界

### 6.1 启动与登录

```text
Vue Router
  -> authStore.fetchCurrentUser()
  -> GET /api/auth/me
  -> unauthenticated: AuthLayout
  -> authenticated: route permission guard
```

401/403 后继续刷新当前登录态和权限，不根据错误 message 推断行为。

### 6.2 聊天

```text
MessageComposer
  -> chatStore.send()
  -> WebSocket /ws
  -> typed frame decoder
  -> RuntimeEvent reducer
  -> chat/session stores
  -> Vue components
```

现有 WebSocket frame、`turn_id`、`channel_text`、`channel_status`、Tool confirmation 和 MCP elicitation 语义保持兼容。当前 `runtime-event-state.js` 的纯 reducer 迁移为 TypeScript 模块，并保留相同输入输出测试。

### 6.3 管理后台

Roles 继续使用稳定 permission key 读写；中文名称属于前端展示映射。系统监控通过 app 层聚合现有 health/Activity 数据，不让 Vue 读取 SQLite 或 trace 文件。

安全审计 API 可以增加向后兼容的筛选、cursor 分页和导出参数；已有 `GET /api/audit/events?limit=100` 继续有效。

## 7. 变更文件规划

### 7.1 新增

- `web/frontend/**`
- `agent/web/__init__.py`
- `agent/web/static/**`
- `docs_design/zhice-agent-part16-web-product-design.md`
- 前端测试与必要的 app API read model 测试。

### 7.2 修改

- `agent/app/gateway.py`：包内静态资源定位和 SPA 入口。
- `agent/app/api/routes.py`、`agent/app/api/schemas.py`：必要的监控摘要、审计筛选/分页等 app 层接口。
- `pyproject.toml`：静态资源 wheel 包含规则。
- `README.md`、`docs_design/README.md`、`docs_design/zhice-agent-overall-design.md`：Part 16/17 路线和构建说明。
- `tests/unit_test/app/test_case.md` 与相关 Gateway/API 测试。

### 7.3 删除（最终切换时）

- `web/static/index.html`
- `web/static/app.js`
- `web/static/styles.css`
- `web/static/runtime-event-state.js`

迁移中可暂时保留这些文件做行为对照，但最终入口只能有一套。

## 8. 实施顺序

1. 建立 Vue/Vite/TypeScript、Router、Pinia、Design Tokens 和 build/wheel 链路。
2. 迁移登录、注册、Owner 初始化和路由守卫。
3. 迁移 AppShell、Session 侧栏、模型选择和主题。
4. 迁移 WebSocket、RuntimeEvent、消息、停止 Turn、confirmation 和 elicitation。
5. 迁移账号菜单、设置中心、QQ/微信绑定。
6. 迁移管理后台用户和角色。
7. 增加基础系统监控 read model，并重构安全审计筛选/分页/导出。
8. 完成前端单元/组件测试、Python Gateway/API 测试和少量浏览器烟测。
9. 切换默认静态入口、验证 Python wheel 后删除旧原生前端。
10. 同步 Part 16 活文档和总体设计当前状态。

## 9. 测试方案

### 9.1 前端单元测试

- API 错误结构、401/403 登录态刷新。
- RuntimeEvent reducer 正常、异常、乱序和终态。
- Session store 新建、重命名、删除、只读来源。
- WebSocket 重连、关闭和 active turn 清理。
- 主题选择与 browser-local identity-scoped 持久化。
- 所有内置 permission key 均有中文 UI 映射。

### 9.2 组件测试

- 登录/注册切换、移动端降级和 reduced motion。
- Session 三点菜单、外部点击/ESC 关闭、重命名和删除确认。
- 单节点 initials 头像不重叠。
- 设置栏目、主题切换和渠道操作状态。
- 管理栏目权限可见性、Owner 只读和角色保存。
- 安全审计筛选、分页和详情。

### 9.3 Python 测试

- `/`、`/admin`、`/_setup` 继续返回 SPA 入口。
- 自定义 `static_dir` 注入测试保持有效。
- wheel/包内静态目录可以被 Gateway 定位。
- 监控/审计 app API 按权限过滤且不泄露跨用户数据。
- 现有 REST/WS schema 和错误码兼容。

### 9.4 浏览器烟测

使用真实 Gateway 和构建产物覆盖少量关键链路：

1. 注册/登录 -> 新建 Session -> 发送消息 -> 停止/完成。
2. Session 三点菜单 -> 重命名 -> 删除确认。
3. 设置中心 -> 主题切换 -> 刷新后恢复。
4. Owner -> 用户/角色 -> 系统监控 -> 安全审计。
5. QQ/微信状态和绑定入口的权限与错误展示。

## 10. 验收标准

1. 正式 Web 不再依赖单体 `app.js` 和手写 DOM render。
2. 登录、注册和 Owner 初始化在桌面/移动端均可用。
3. 登录/注册桌面滑动、移动端内容切换和 reduced motion 行为正确。
4. 曜石主题支持跟随系统、浅色和暗色，关键文字与控件对比度合格。
5. Session 侧栏不显示消息数量，三点菜单提供重命名与删除。
6. 用户头像 initials 不重叠。
7. 设置中心按五个栏目展示当前真实能力，不出现无实现入口。
8. 管理后台使用中文角色与能力分组，技术 key 默认收起。
9. 普通用户基础能力与额外特权的说明不会误导管理员。
10. 系统监控只展示已有 health/Activity 真值，不伪造系统诊断。
11. 安全审计与 Runtime Activity 继续分离，并支持必要筛选、分页和导出。
12. 现有 API、WebSocket frame、Session JSONL、permission key 和渠道语义保持兼容。
13. 安装后的 Python wheel 可以直接运行 Web，不要求 Node.js。
14. Vue 构建可重复，CI 可以发现过期静态产物。
15. 前端单元/组件测试、相关 Python 测试和浏览器烟测通过；无关历史失败明确记录。

## 11. Part 17 边界

Part 17 继续承接：Provider 错误分类、retry/cooldown、系统级诊断引擎、事故聚合、完整 Turn/LLM/Tool/Context 时间线、MCP 在线 reload 强化、容器、反向代理、Secret/KMS、健康检查、备份恢复和发布产物。

Part 16 可以先提供系统监控页面和 current-state read model；Part 17 在不重写前端架构的前提下向这些页面增加更深的可靠性数据。
