# Web Logo 与用户头像风格统一设计

## 背景

Gateway favicon 已采用深色圆角底、白色 Z 折线、青色强调点的 SVG 标识，但登录页和侧边栏仍使用普通蓝底字母 `Z`，用户入口则是普通蓝底用户名首字母。三处视觉语言不一致，用户希望所有品牌 Logo 和用户左侧头像统一为 favicon 的风格。

## 目标

1. 登录页和侧边栏品牌 Logo 使用与 favicon 同源的矢量造型。
2. 用户头像继续显示用户名或显示名首字母，便于区分账号。
3. 用户头像采用同款深色圆角底、白色字形和青色强调点。
4. 折叠侧边栏、登录页和用户入口保持清晰，不改变现有布局交互。
5. 使用 HTML/CSS/SVG 原生实现，不新增位图资源或网络请求。

## 范围边界

- 不修改 favicon 本身的现有图形。
- 不引入头像上传、随机颜色或用户图片存储。
- 不改变用户显示名和首字母计算逻辑。
- 不调整其它按钮、表单和页面配色体系。

## 模块设计

- `index.html`：两处 `.brand-mark` 改为与 favicon 相同 viewBox 和图形元素的内联 SVG。
- `styles.css`：增加统一 logo 色彩变量；品牌 SVG 自适应容器；用户 `.avatar` 使用深色渐变、内高光、青色角标。
- 静态 CSS query version 更新，避免浏览器继续使用旧样式。

## 变更文件

- `web/static/index.html`
- `web/static/styles.css`
- `tests/unit_test/app/test_gateway.py`
- `tests/unit_test/app/test_case.md`

## 测试方案

| 用例 | 预期 |
|---|---|
| 品牌入口 | 登录页和侧边栏各包含一份 favicon 同源 SVG |
| 用户头像 | 保留 `userAvatar` 首字母节点并启用 logo avatar 样式 |
| 样式合同 | 深色 logo 背景、青色强调色、头像角标样式存在 |
| 缓存版本 | CSS query version 更新 |
| 浏览器视觉 | 登录页和登录后用户入口尺寸、对齐和清晰度正常 |

## 验收标准

1. 页面两处 ZhiCe-Agent Logo 与 favicon 风格一致。
2. 用户头像与 Logo 属于同一视觉系统，同时仍能显示账号首字母。
3. 侧边栏折叠和常规状态均无布局溢出。
4. 浏览器视觉检查、Ruff、JS 语法和全量 pytest 通过。
