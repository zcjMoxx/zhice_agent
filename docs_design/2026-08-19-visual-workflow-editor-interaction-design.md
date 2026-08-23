# 智策工作流成熟编辑器交互移植设计

> 说明：响应式快照导致的新建空白页与画布平移修复见 `2026-08-21-workflow-editor-blank-canvas-fix.md`；本文正文保留 2026-08-19 的交互移植方案。

> 日期：2026-08-19
>
> 状态：已实现
>
> 关联：`2026-08-10-visual-workflow-scheduler-design.md`、`zhice-agent-part20-visual-workflow-scheduler-design.md`

## 背景

首版 Vue Flow 页面已经接通 WorkflowDefinitionV1、真实 MCP Tool Catalog 和运行 API，但编辑器交互仍偏工程演示：节点库与工作流列表混在一起、非 MCP 节点主要依赖 JSON、条件分支不可见，也没有撤销、自动布局、拖线补节点和连线插入节点。

本次以桌面只读参考源码 `ai-agent-station-front-master` 和 `ai-agent-station-study-3-19-agent-admin-controller` 为实现输入。前者的 FlowGram 编辑器提供 node registry、history、snap、node panel、drag-line-end、line add button、auto layout 和 variable engine 等成熟模式；后者提供画布配置解析、节点/边持久化和策略执行分层。参考项目不直接进入运行依赖，也不复制其中的静态 MCP 数据、React 框架或 Java Agent 执行模型。

## 目标与边界

- 保持 Vue Flow，不引入 React/FlowGram 双前端运行时。
- 保持 Workflow Runtime 独立于 AgentLoop 和聊天 Session。
- 节点仍限于 WorkflowDefinitionV1 审核集合，不开放脚本、Shell、文件系统或任意 HTTP 节点。
- MCP 目录继续来自当前登录用户实际 ToolProvider 与 Query/Action allowlist 的交集；发布和运行仍由后端重新校验权限、Schema hash 和连接所有权。

## 已实现设计

- 工作流首页与编辑器分离，首页采用紧凑品牌导航、空白流程入口、模板和最近工作流。
- 编辑器左侧只保留节点注册目录，顶部提供明确的保存返回、对话、删除、保存、发布、启停和运行操作。
- 画布支持滚轮平移、拖拽平移、触控缩放、网格吸附、MiniMap、Controls、撤销/重做、自动 DAG 布局和键盘删除/复制/粘贴。
- 从节点端口拖到空白处会弹出下一节点目录；每条连线中点可插入新节点，并原子改写为两条边。
- 条件节点提供 `true`/`false` 两个可见端口，序列化为 `condition_branch`，与后端 DAG 校验一致。
- LLM Transform、模板、条件、官方通知、个人邮件和 MCP 均有专用表单；上游可达节点输出通过变量选择器插入 `${nodes.<id>.output}`。
- MCP 表单按实时 Tool Schema 渲染，Query 可执行真实测试；Schema 缺失或变化时阻止发布和运行。
- 运行历史可折叠，选择一次运行后展示 owner-scoped 节点执行时间线和错误码。

## 变更文件

- `web/frontend/src/pages/WorkflowPage.vue`
- `web/frontend/src/styles/workflow.css`
- `web/frontend/src/utils/workflow-editor.ts`
- `web/frontend/src/utils/workflow-editor.test.ts`
- `web/frontend/src/api/client.ts`
- `web/frontend/src/api/types.ts`
- `web/frontend/src/stores/workflows.ts`
- `docs_design/zhice-agent-part20-visual-workflow-scheduler-design.md`
- `docs_design/README.md`

## 测试与验收

- 工具函数覆盖拓扑布局、不修改源数据、连线插入并保留条件分支、传递上游变量和单触发器约束。
- 前端必须通过 lint、typecheck、Vitest 和生产 build。
- Python 工作流测试必须继续覆盖 DAG、Executor、真实 Tool Catalog 边界和 Node-RED 安全子集。
- 浏览器验收检查工作流首页、返回/删除、初始缩放、上下左右平移、节点专用配置、拖线添加、连线插入、撤销/重做、自动布局、真实 MCP 状态与运行时间线。
