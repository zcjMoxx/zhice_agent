# ZhiCe-Agent Ops 与管理后台视觉一致性修正设计

> 日期：2026-08-09
>
> 状态：方案已确认，随本文落地
>
> 归属：Part 18 Ops 页面与主 Web 投影体验修正

## 1. 背景

本地 Ops 页面使用暗色背景，但没有声明暗色 `color-scheme` 或自定义 scrollbar，Chromium 因此显示白色原生轨道、箭头和滑块，与终端面板不协调。主 Web 的 Ops 操作区只有独立窗口按钮使用正式按钮 class，页面内嵌和关闭投影仍是裸按钮；浅色主题下关闭投影出现白底白字，两个入口也像来自不同产品。

管理后台还依赖浏览器对 `small`、`code` 的默认字号和字体，不同区域的小字、技术值存在字体族、字号、字重和行高差异。

## 2. 目标

1. Ops 页面、日志窗口和管理后台滚动区使用与主题匹配的细圆角 scrollbar。
2. 修复浅色主 Web 中关闭投影按钮的对比度。
3. 独立窗口与页面内嵌使用同一尺寸、圆角、字重和反馈体系，仅以主次层级区分。
4. 管理后台辅助小字统一字体、字号、字重、行高和字距。
5. 管理后台技术值统一使用明确的等宽字体栈，不再依赖浏览器默认 monospace。

## 3. 范围边界

- 只调整 Ops 页面和 `.admin-shell`，不改聊天页、登录页或全局主题语义。
- 不改变 Ops iframe、独立窗口和失败回退的数据流。
- 不改变终端日志内容、颜色语义或刷新行为。
- 不引入字体文件、前端依赖或自定义滚动组件。

## 4. 模块设计

### 4.1 Ops 页面

- `html` 声明 `color-scheme: dark`；
- Firefox 使用 `scrollbar-width` / `scrollbar-color`；
- Chromium/WebKit 使用 `::-webkit-scrollbar*`，轨道透明暗色、滑块中灰、hover 提亮；
- 页面外层与日志 `<pre>` 共用同一规则。

### 4.2 主 Web 投影区

- 两个入口都使用 `operations-action-button`；
- 独立窗口保留 primary，页面内嵌使用有边框的 secondary；
- 关闭投影使用专用暗色 header button，确保浅色/暗色主题均有可读对比度；
- 管理后台滚动区使用主题变量生成 scrollbar。

### 4.3 字体

- `.admin-shell small` 固定继承正文 sans-serif，使用统一 12px/500/1.5；
- `.admin-shell code` 使用 Cascadia Code、SFMono-Regular、Consolas 等明确等宽栈和统一字号；
- 表头、eyebrow 等具有明确语义的紧凑文本继续保留自身样式。

## 5. 变更文件

- `agent/operations/local_supervisor.py`
- `tests/unit_test/operations/test_local_supervisor.py`
- `tests/unit_test/operations/test_case.md`
- `web/frontend/src/layouts/AdminLayout.vue`
- `web/frontend/src/layouts/AdminLayout.test.ts`
- `web/frontend/src/styles/app.css`
- `README.md`
- `docs_design/zhice-agent-part18-skill-runtime-and-server-ops-design.md`
- `docs_design/README.md`

## 6. 测试方案

- 静态检查 Ops 页面包含暗色 color-scheme 与两套 scrollbar 规则。
- Vue 测试检查两个入口和关闭按钮使用稳定 class。
- 前端 lint、typecheck、Vitest、build。
- 浏览器分别验收独立 Ops、浅色管理后台内嵌、按钮对比度和 scrollbar computed style。
- Ruff、全量 pytest 与 `git diff --check`。

## 7. 验收标准

1. Ops 日志区不再出现白色原生滚动轨道。
2. 浅色 Web 的关闭投影按钮文字和背景清晰可读。
3. 独立窗口与页面内嵌按钮视觉属于同一组件体系。
4. 管理后台辅助小字和技术值在不同卡片中保持一致。
