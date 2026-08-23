# 工作流编辑器空白与画布平移修复

> 说明：总览/详情独立路由、旅游规划统一 UI 与显式画布移动控制继续见 `2026-08-21-workflow-overview-detail-navigation-design.md`。

> 日期：2026-08-21
>
> 状态：已实施并完成真实浏览器验证
>
> 关联：`docs_design/2026-08-19-visual-workflow-editor-interaction-design.md`

## 背景

工作流首页点击“新建空白工作流”后，接口成功创建草稿，但编辑器渲染为空白。真实浏览器控制台显示 `DataCloneError`：页面在计算编辑快照时对 Pinia/Vue 响应式 Proxy 直接调用 `structuredClone`，异常中断了组件渲染。编辑器页面同时固定为视口高度并隐藏页面滚动，因此画布滚轮平移若未正常生效，用户无法通过页面滚动上下查看。

## 目标

- 新建或打开工作流后稳定渲染编辑器、初始触发节点和属性面板。
- 所有编辑快照在 Vue 响应式边界内安全深拷贝，不再向 `structuredClone` 传入 Proxy。
- 保持画布滚轮上下/左右平移、拖拽平移和触控缩放；普通滚轮不缩放。
- 增加可自动回归的响应式快照测试，并通过真实页面验证。

## 范围边界

- 仅修复工作流前端编辑器和相关测试、构建产物。
- 工作流数据仍为 JSON 协议；不支持函数、DOM、循环引用等非 JSON 配置。
- 不改变后端创建 API、WorkflowDefinitionV1、权限或运行时语义。

## 模块设计与数据流

在工作流编辑辅助模块提供 JSON 深拷贝函数。该函数通过 JSON 序列化先把响应式数据投影为协议允许的纯数据，再解析为独立对象。页面的编辑快照、历史栈、复制粘贴和配置复制统一走该边界。

Vue Flow 保持 `panOnScroll=true`、`panOnDrag=true`、`zoomOnScroll=false`，并显式声明自由滚轮平移模式，使纵向滚轮映射为画布 Y 轴移动，横向滚轮映射为 X 轴移动；Ctrl/Meta 与触控缩放继续由 Vue Flow 处理。

## 变更文件

- `web/frontend/src/utils/workflow-editor.ts`
- `web/frontend/src/utils/workflow-editor.test.ts`
- `web/frontend/src/pages/WorkflowPage.vue`
- `docs_design/2026-08-19-visual-workflow-editor-interaction-design.md`
- 前端生产构建生成的 `agent/web/static/` 资产

## 测试方案

- 使用 Vue `reactive` 构造响应式工作流快照，验证深拷贝成功、结果为独立纯数据且源数据不变。
- 运行前端 lint、typecheck、Vitest 和 production build。
- 重载 `http://127.0.0.1:10086/workflows`，点击新建后验证编辑器 DOM、触发节点和控制台无错误。
- 在画布区域发送纵向滚轮事件，验证 viewport 的 Y 位移发生变化。

## 验收标准

- 点击新建不再出现空白页或 `DataCloneError`。
- 初始手动触发节点可见并可编辑。
- 画布可上下平移，节点编辑、历史栈和复制粘贴不回归。
- 前端全套检查通过，生产静态资产已更新。
