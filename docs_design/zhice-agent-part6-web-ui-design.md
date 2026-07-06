# 智策 Agent 第六部分前端 UI 设计

> 关联规范：`AGENTS.md`
>
> 文档类型：阶段活文档。本文档记录第六部分 Web UI 的当前方案，后续 UI 迭代应持续更新本文。
>
> 承接文档：`docs_design/zhice-agent-part6-web-minimum-design.md`
>
> 当前状态：已实现第一版静态 UI。当前方案使用 `web/static` 原生 HTML/CSS/JS，不引入 Vue、Vite、React 或完整前端工程化；浏览器主聊天通道为 `WebSocket /ws`，REST/SSE 仅保留兼容调用。

---

## 1. 背景

第六部分 Web 最小版已经验证本地 Web API、会话读取、WebSocket 聊天、停止 active turn 和 gateway 静态资源服务边界。前端第一版需要足够像一个可用的聊天产品，但不能提前扩展成完整平台。

首版视觉参考两类界面：

- 灵境 Agent：左侧品牌区、侧边栏、居中的 Agent 欢迎区。
- ChatGPT：克制的聊天主界面、左侧新建和搜索入口、底部用户入口。

本项目只吸收布局和交互结构，不复制参考产品的完整功能入口，也不加入当前后端还没有支撑的推荐问题、知识库、应用市场、项目、定时任务等能力。

---

## 2. 目标

1. 提供一个简洁的本地 Agent 聊天首屏。
2. 让用户能明确看到 ZhiCe-Agent 品牌、创建新会话、搜索历史会话和最近会话列表。
3. 让主区域聚焦聊天，不展示推荐问题和未实现能力入口。
4. 在输入栏中预留模型切换下拉栏，与现有 `/model` 能力方向一致。
5. 左下角预留用户入口，后续再接入设置、账号、本地配置或后端用户能力。
6. 提供发送前 pending 反馈、增量文本展示、停止按钮和最小 Markdown 渲染。
7. 静态版本保持可迁移，后续可以平滑替换为 `web/` 下的 Vue/Vite 工程。

---

## 3. 范围边界

本阶段 UI 包含：

- 左侧固定侧边栏。
- 左上角 logo 和折叠侧边栏按钮。
- `New chat`。
- `Search chats`。
- `Recents` 最近会话区域。
- 左下角用户入口占位。
- 中间聊天主界面。
- 初始欢迎语。
- 消息列表。
- 底部输入栏。
- 输入栏右下角模型选择下拉栏。
- 发送按钮、停止按钮、pending/typing、streaming cursor、错误状态。
- 会话重命名和删除。
- assistant Markdown 最小渲染。

本阶段 UI 不包含：

- 推荐问题卡片。
- 知识库入口。
- 技能市场入口。
- 定时任务入口。
- 文件上传、图片、表格、网页、报告等快捷按钮。
- 多页面路由。
- 登录页、账号中心和权限配置。
- 工具调用日志面板。
- 工具步骤级可视化。
- 完整 Markdown/代码高亮引擎。

---

## 4. 目录方案

第一版静态资源放在根目录 `web/static`，而不是放在 `agent/app/static`：

```text
web/
  static/
    index.html
    styles.css
    app.js
```

原因：

- `agent/` 专注后端、Agent core、API 和 gateway。
- `web/` 从一开始表达“这里是前端边界”。
- 第一版虽然不用构建工具，但后续升级 Vue/Vite 时可以在同一个 `web/` 下扩展。
- gateway 通过 `static_dir` 服务静态资源，第一版指向 `web/static`，后续可指向 `web/dist`。

后续升级后的形态：

```text
web/
  package.json
  vite.config.ts
  src/
    main.ts
    api/
    components/
    views/
  dist/
```

---

## 5. 页面结构

### 5.1 整体布局

```text
app-shell
  sidebar
    sidebar-header
      logo
      collapse-button
    sidebar-actions
      New chat
      Search chats
    recent-section
      Recents
      recent-chat-list
    user-entry
  main
    chat-empty-state
    message-list
    composer
      textarea
      model-select
      send-button
```

整体采用左右布局：

- 左侧侧边栏宽度约 260px。
- 右侧主区占满剩余空间。
- 主区内容最大宽度约 760px 到 840px，居中排布。
- 输入栏固定在主区底部附近，历史消息多时主聊天区可滚动；滚动条贴近主页面右侧，消息列仍保持居中阅读宽度。

### 5.2 左侧栏

左上角：

- 显示 ZhiCe-Agent logo 和名称。
- 右侧放折叠侧边栏按钮。
- 折叠后只保留窄栏和图标，第一版可以先只实现按钮视觉，不强制实现完整折叠动画。

主操作：

```text
New chat
Search chats
```

`New chat`：

- 点击后创建或切换到一个新的本地 session。
- 当前第一版由前端生成一个本地 session id，首次通过 WebSocket 发送消息后由 SessionStore 落盘。

`Search chats`：

- 第一版可先展示搜索输入状态。
- 如果后端还没有全文搜索，只在当前已加载的 recent 列表中做前端过滤。
- 不引入 SQLite 或全文索引。

`Recents`：

- 展示最近会话标题或 preview。
- 来源为 `GET /api/sessions`。
- 每一行提供重命名和删除操作。
- 删除当前会话后清空主区并回到空会话状态。
- 空状态显示简短文本，例如“暂无最近会话”。
- 不显示过长内容，单行省略。

左下角用户入口：

- 显示一个头像占位和本地用户名称占位。
- 第一版不接登录、不接权限。
- 后续可承接设置、配置、账号、用户偏好等能力。

### 5.3 主聊天区

空会话初始态：

```text
I'm ZhiCe-Agent. How can I help?
```

要求：

- 欢迎语位于主区中上位置。
- 不显示推荐问题。
- 不显示能力卡片。
- 不显示知识库、市场、文档、网页、报告、图片、表格等快捷入口。
- 页面保持安静留白，重点突出输入框。

消息态：

- 用户消息和助手消息按时间顺序纵向展示。
- 用户消息靠右或使用轻背景气泡。
- 助手消息靠左或使用正文块。
- 错误消息使用简短提示，不暴露堆栈。
- 工具调用消息第一版不展开，只在后续工具日志面板中处理。

### 5.4 输入栏

输入栏参考 ChatGPT 的紧凑浮层形态，但更贴近本项目本地工具属性：

- 多行 textarea，默认一行到三行高度。
- placeholder 简洁，例如 `Message ZhiCe-Agent`。
- 右下角放模型选择下拉栏。
- 右侧放发送按钮和停止按钮。
- 输入为空时发送按钮禁用。
- 发送中显示 pending/typing 状态，禁用重复提交，并允许点击停止按钮请求取消 active turn。
- 发送通过 `WebSocket /ws` 的 message frame；输入框中的 `/stop` 不作为普通消息透传给 LLM，而是在后端被 stop 路径拦截。
- Web UI 连接 `/ws` 后发送 `{"type":"hello","client":"web"}`；web command profile 不支持 `/history` 和 `/exit`，这两个输入由后端返回英文“不支持当前客户端”文本，不透传给 LLM。

模型选择下拉栏：

- 显示当前 endpoint 下的模型名，例如 `gpt-5`。
- 数据来自 `GET /api/models`。
- 切换时调用 `POST /api/model/preference`，只在当前 endpoint 的可用模型内切换。
- 不在 UI 内暴露 API key、base_url 或完整配置路径。
- 下拉框只显示模型名，不显示 endpoint 前缀。

### 5.5 Assistant 输出

助手消息按 Markdown 正文渲染，但保持最小安全边界：

- 支持标题、无序列表、有序列表、代码块、加粗、行内 code 和安全链接。
- 用户消息仍按纯文本展示。
- 链接只允许 `http(s)` 和 `mailto`，其它链接写法退回纯文本。
- 不引入第三方 Markdown 包，不执行 HTML。
- assistant 正在输出时显示 streaming cursor；尚未收到文本时显示 typing dots。

---

## 6. 视觉规则

整体风格：

- 本地开发工具感。
- 简洁、克制、可读。
- 不做营销页和大面积宣传式 hero。
- 不使用推荐问题卡片。

颜色：

- 背景以浅灰和白色为主。
- 品牌色可使用低饱和蓝色点缀。
- 不做重紫蓝渐变主视觉。
- 侧边栏和主内容区要有清晰层次，但避免厚重卡片堆叠。

控件：

- 侧边栏按钮高度稳定，文字不换行。
- 折叠、搜索、发送、模型选择等控件要有 hover 和 disabled 状态。
- 图标优先使用简单线性图标；静态版本可以先用文本和少量内联 SVG，后续 Vue/Vite 阶段再统一图标库。

响应式：

- 桌面优先。
- 窄屏时侧边栏可以默认折叠或转为顶部抽屉。
- 输入栏宽度使用 `max-width`，避免超宽屏上过长。

---

## 7. API 对接

首版 UI 需要这些后端能力：

```text
GET /api/sessions
GET /api/sessions/{session_id}
PATCH /api/sessions/{session_id}
DELETE /api/sessions/{session_id}
POST /api/chat
POST /api/chat/stream
GET /api/models
POST /api/model/preference
WebSocket /ws
```

Web UI 主聊天使用 `WebSocket /ws`；连接后先发送 `hello client=web` 握手。其它 UI 客户端也使用 `client=web`；脚本、CLI 或自动化客户端需要 `/history`、`/exit` 时使用 `client=external`。`POST /api/chat` 和 `POST /api/chat/stream` 保留为兼容或外部一次性调用。

模型 API 只覆盖当前 endpoint 的模型选择，不扩展为完整模型管理页。

前端 `app.js` 要把 API 调用集中在独立函数中：

```text
fetchSessions()
fetchSession(sessionId)
sendWebSocketMessage(sessionId, message, model)
stopActiveTurn()
renameSession(sessionId, title)
deleteSession(sessionId)
fetchModels()
setModelPreference(value)
```

这样以后迁移到 Vue/Vite 时，可以把这些函数迁入 `web/src/api/`，页面组件不需要重写 API 合约。

---

## 8. 状态设计

必须覆盖：

- 初始空会话。
- 正在加载会话列表。
- 会话列表为空。
- 正在发送消息。
- 等待模型首个输出。
- 正在流式输出。
- 停止 active turn。
- 会话重命名或删除失败。
- 发送失败。
- 后端配置错误。
- LLM 调用失败。
- 会话读取失败。

状态提示原则：

- 用户可见提示短而明确。
- 详细错误只进入后端日志或测试断言。
- 不在页面里展示 Python traceback、环境变量、API key 或本地敏感路径。

---

## 9. 验收标准

当前 UI 方案完成时应满足：

1. 页面左上角显示 ZhiCe-Agent logo 和侧边栏折叠按钮。
2. 侧边栏只包含 `New chat`、`Search chats`、`Recents` 和左下角用户入口占位。
3. 初始主区显示 `I'm ZhiCe-Agent. How can I help?`。
4. 不显示推荐问题、知识库、技能市场、定时任务或其它未实现入口。
5. 输入栏位于主区下方，能输入并发送消息。
6. 输入栏右下角有模型选择下拉栏或只读模型显示。
7. 会话列表来自 `GET /api/sessions`。
8. 打开会话后能展示历史消息。
9. 发送消息后能立即展示用户消息和 pending assistant 反馈。
10. 收到 WebSocket 文本增量后，assistant 气泡流式更新。
11. assistant Markdown 内容能渲染标题、列表、代码块、加粗和行内 code。
12. 会话行提供重命名和删除；删除当前会话后进入空界面。
13. 停止按钮能发起 WebSocket stop frame，不把 `/stop` 当普通消息透传。
14. 模型下拉框只显示模型名，不显示 endpoint、base_url 或 key。
15. 窄屏下不出现文字重叠或主要控件溢出。
16. 静态资源位于 `web/static`。
17. gateway 通过可替换 `static_dir` 服务前端资源。

---

## 10. 后续演进

后续可以单独设计：

- Vue/Vite 前端工程化。
- 工具调用日志面板。
- `/model` Web 控制面。
- Skill source 状态页。
- 设置页和用户入口。
- 会话标题生成、搜索和归档。
- 持久化 turn_id，让 accepted/done/stopped 与历史消息完全统一。
- 更完整的 Markdown/代码高亮渲染。

这些能力不并入当前 UI 方案，避免第六部分从“Web 最小版”膨胀成完整产品平台。等后续方案落地后，再把新的当前准则同步回本文。
