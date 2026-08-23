# 工作流总览、详情路由与画布移动设计

> 说明：用户可见内部标识清理、工具中文化和发布就绪体验继续见 `2026-08-21-workflow-user-facing-productization-design.md`。

> 日期：2026-08-21
>
> 状态：已实施；前端已在线验证，Gateway 详情硬刷新入口待当前进程重启加载
>
> 关联：`2026-08-19-visual-workflow-editor-interaction-design.md`、`2026-08-21-workflow-editor-blank-canvas-fix.md`

## 背景

当前 `/workflows` 在同一个 `WorkflowPage` 内通过 Pinia `current` 状态切换首页和编辑器，URL 不变。总览页使用独立落地页视觉，和旅游规划的应用顶栏、左侧历史列表、右侧滚动内容区不一致；新建后也无法从 URL 看出当前进入了哪个工作流。

画布已配置 Vue Flow 平移，自动化实测左键拖动空白画布可改变 Y 轴 viewport，但界面没有抓手光标、移动提示或显式方向控制，用户很难判断应在空白网格而不是节点/面板上拖动，也没有设备交互失败时的可见替代入口。

## 目标

- `/workflows` 始终是工作流总览，不因 Pinia 当前对象切换为编辑器。
- `/workflows/:workflowId` 是单个工作流的画布、属性和运行历史详情页，支持直接刷新和浏览器前进后退。
- 总览采用与旅游规划统一的应用 UI：品牌顶栏、返回聊天与偏好设置、左侧最近工作流、右侧独立滚动内容。
- 新建、模板创建和列表打开成功后进入对应详情 URL；保存返回和删除成功后回到总览。
- 画布明确支持左/中/右键拖动、滚轮上下左右平移、Ctrl/Meta+滚轮缩放，并提供方向按钮作为可见替代操作。

## 范围边界

- 不修改 Workflow REST API、SQLite 数据、发布/执行/权限语义。
- Gateway 仅增加详情 URL 的 SPA index 入口，不增加服务端模板页面，不引入新的 UI 依赖。
- 保持当前 Vue Flow 节点、连线、历史栈、工具目录和运行时间线能力。
- 总览复用旅游规划的布局语言，但工作流领域内容与操作保持独立。

## 模块设计与数据流

路由增加命名详情项 `workflow-detail`。页面以 `route.params.workflowId` 为唯一编辑态来源：无参数渲染总览，有参数加载该 ID 并渲染编辑器。Pinia `current` 只保存详情数据，不再决定页面层级。

新建数据流为：总览按钮 → 创建草稿 API → 获得 workflow_id → 导航到详情 URL → hydrate 画布。打开列表项先加载 owner-scoped 详情，再导航。保存返回先保存草稿再清理 current 并导航总览；删除确认后调用 API并导航总览。

总览使用固定视口应用壳，右侧 `.workflow-overview-scroll` 是明确的纵向滚动容器。左栏提供新建、刷新和全部工作流列表，右侧显示欢迎卡、模板卡和工作流卡。

画布把 `panOnDrag` 显式设置为 `[0, 1, 2]`，普通滚轮保持平移、Ctrl/Meta 滚轮缩放。工具栏提供上下左右平移按钮，通过当前 viewport 增量调用 `setViewport`；空白 pane 使用 grab/grabbing 光标并显示简短操作提示。

## 变更文件

- `web/frontend/src/router/index.ts`
- `web/frontend/src/App.vue`
- `web/frontend/src/pages/WorkflowPage.vue`
- `web/frontend/src/styles/workflow.css`
- `web/frontend/src/router/index.test.ts`
- `agent/app/gateway.py`
- `tests/unit_test/app/test_gateway.py`
- `tests/unit_test/app/test_case.md`
- `docs_design/zhice-agent-part20-visual-workflow-scheduler-design.md`
- 前端构建生成的 `agent/web/static/` 资产

## 测试方案

- 路由测试覆盖总览和详情 URL 的稳定解析。
- 前端 lint、typecheck、完整 Vitest 和 production build 全部通过。
- 真实浏览器验证总览为统一应用壳，页面内容区可纵向滚动。
- 新建或打开工作流后 URL 包含 workflow_id，刷新后仍能恢复编辑器。
- 在空白画布分别验证拖动和方向按钮改变 viewport 的 X/Y，节点拖动不被破坏。

## 验收标准

- 总览与单工作流编辑器具备清晰页面层级和独立 URL。
- 总览视觉、顶部导航、侧栏历史和滚动模型与旅游规划一致。
- 用户无需猜测即可移动画布，且至少有拖动、滚轮、方向按钮三种移动方式。
- 原有创建、模板、保存、发布、运行、暂停、删除和运行历史操作保持可用。
