# ZhiCe-Agent Web 聊天布局与主题修正设计

> 文档类型：日期设计记录
>
> 归属：Part 16 Web 产品体验与 Vue 前端工程
>
> 承接：`docs_design/2026-07-27-web-product-experience-and-vue-frontend-design.md`

## 1. 背景

Part 16 首次 Vue 落地已经完成，但真实长 Session 页面暴露出四项产品回归：历史加载会无条件跳到最后一条消息；长 Session 列表会把左下角账号入口挤出视口；限宽内容列被错误地同时当成滚动视口，形成窄滚动条、横向溢出和悬浮输入框；浅色主题也没有准确还原已经确认的明亮曜石 Demo。

当前确认的视觉基准仍是 `zhice-light-obsidian-chat.html`：近白阅读区、浅灰玻璃侧栏、石墨结构与主操作、少量冷蓝功能强调。暗色曜石继续保留，但本次不重新设计暗色主题。

## 2. 目标

- 刷新或重新打开 Session 时恢复该标签页内最后阅读位置，不强制跳到最新消息。
- 用户在接近底部或刚发送消息时继续自动跟随新内容；主动向上阅读历史后不被流式输出拉回底部。
- Session 列表只在自己的剩余空间滚动，账号入口始终固定在左下角可见。
- 主聊天滚动视口占满可用宽高，内部消息列独立限宽，页面不产生横向滚动。
- 输入区作为聊天网格的正常底部行稳定放置，不覆盖消息列表。
- 浅色主题重新对齐已确认的明亮曜石 Demo。

## 3. 范围边界

本次只修改 Vue 组件、CSS token、前端测试、构建产物和当前设计说明。不修改 REST、WebSocket frame、Session JSONL、RBAC、Channel、RuntimeEvent 或 AgentLoop 语义。

阅读位置使用 `sessionStorage`，按登录用户和 Session 隔离，只保证当前浏览器标签页刷新恢复；不写入服务端，也不成为跨设备偏好。

## 4. 模块设计

### 4.1 阅读位置与自动跟随

`ChatPage` 为当前 Session 保存 `scrollTop`。Session 历史加载完成后优先恢复已保存位置；没有保存值时才落到最新消息。

消息变化时仅在以下条件自动跟随：

1. 用户原本距离底部不超过阈值；
2. 用户刚提交了一条消息。

用户向上滚动离开底部后，流式文本和 RuntimeEvent 不再改变其阅读位置。

### 4.2 布局

```text
AppShell
├─ SessionSidebar
│  ├─ fixed header/tools
│  ├─ min-height: 0 session scroller
│  └─ fixed account area
└─ ChatPage
   ├─ header
   ├─ full-width/full-height message scroller
   │  └─ max-width message column
   ├─ optional readonly banner
   └─ in-flow composer
```

主滚动容器禁止横向溢出；Markdown 正文允许长 token 断行，代码块保留自身横向滚动。

### 4.3 明亮曜石

- 页面与阅读区使用近白背景。
- 侧栏使用更浅的灰色玻璃层，不形成整块中灰画布。
- 当前 Session、用户气泡和 hover 使用低透明石墨灰。
- 主按钮与头像使用石墨色。
- 链接、焦点和少量状态使用低饱和冷蓝。
- 标准侧栏宽度恢复为 Demo 的 `216px`。

## 5. 变更文件

- `web/frontend/src/components/ChatPage.vue`
- `web/frontend/src/components/ChatPage.test.ts`
- `web/frontend/src/components/SessionSidebar.test.ts`
- `web/frontend/src/styles/tokens.css`
- `web/frontend/src/styles/app.css`
- `web/frontend/src/test/setup.ts`
- `agent/web/static/**`
- `docs_design/zhice-agent-part16-web-product-design.md`
- `docs_design/README.md`

## 6. 测试方案

- Vitest 验证刷新加载时恢复 Session 阅读位置，而非无条件滚到底部。
- Vue Test Utils 验证账号入口仍属于侧栏稳定底部结构。
- 前端 lint、typecheck、Vitest 和 production build 全部通过。
- 真实 Gateway 浏览器烟测覆盖长 Session、刷新恢复、向上阅读时不抢滚动、左下角账号入口、无页面横向滚动、输入区贴底和明亮曜石视觉。

## 7. 验收标准

1. 长 Session 刷新后回到刷新前阅读位置。
2. 接近底部时新消息仍自动跟随；向上阅读时保持当前位置。
3. 任意 Session 数量下左下角用户入口可见且可打开。
4. 主页面 `scrollWidth` 不大于 `clientWidth`，消息滚动条位于主区右边缘。
5. 输入区完整位于视口底部，不覆盖消息正文。
6. 浅色主题与已确认 Demo 的近白、浅灰、石墨、少量冷蓝层次一致。
