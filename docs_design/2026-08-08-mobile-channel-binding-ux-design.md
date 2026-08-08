# 移动端渠道绑定闭环设计

## 背景

QQ 私聊裸 `/bind` 已能返回网页授权链接，但当前 Web 流程仍有两个实际问题：

- 手机浏览器沿用桌面登录页和设置弹窗的压缩布局，绑定输入与操作按钮会互相挤压；
- 未登录用户打开链接后注册账号，自动绑定依赖首页和认证组件之间的一次 `authenticated` 事件，没有独立的任务页面持有 token 与结果状态。注册后的组件切换或路由清理一旦与异步授权交错，用户就只能手动进入“渠道连接”再次完成绑定。

讨论中进一步确认：为移动端建立独立的绑定任务页更清晰，但不复制整套移动端前端工程。独立页继续复用同一认证 Store、渠道 Store、API client、主题和组件。

## 目标

1. QQ 新链接统一进入移动优先的 `/bind/qq?token=<opaque-token>` 独立页面；旧 `/?channel_bind=` 链接兼容重定向。
2. 未登录用户无论登录还是新注册，认证成功即在绑定页自动消费原始 token。
3. 自动绑定成功后清除 URL token，在当前页明确展示“QQ 绑定成功”与返回 QQ 提示。
4. 自动绑定失败时保留 token 和大号重试入口，不静默丢失失败原因。
5. 手机窄屏下独立绑定页和渠道设置符合单列触控布局；“完成绑定”按钮独占一行、文字不换行且触控高度不少于 44px。

## 范围边界

- 不改变 QQ authorization token 的服务端协议、有效期、单次消费和冲突语义。
- 不实现 QQ 身份自动注册；用户仍需明确创建或登录内部账号。
- 不移除手动绑定码与失败后的手动重试能力。
- 不重做整个聊天页面，只修正认证和渠道设置这条手机绑定路径。

## 模块设计

### 独立绑定任务页

QQ Adapter 生成 `/bind/qq?token=<opaque-token>`。`QqBindingPage.vue` 持有 token、认证状态和绑定结果；`AuthLayout.vue` 通过 `qq-binding` flow 复用登录/注册能力，同时使用精简的单卡片文案和布局。认证状态变为已登录后，绑定页 watch 自动调用授权 API，不再把正确性押在子认证组件的一次 emit 上。

Gateway 必须为 `/bind/qq` 显式返回同一个 SPA `index.html`。只添加 Vue Router 路由不够：手机从 QQ 直接请求服务端子路径时，若没有该 HTML fallback，会在前端代码加载前得到 404。

`HomePage.vue` 只承担旧 `/?channel_bind=` 链接到新路由的兼容重定向，不再承载主绑定流程。

授权成功：

1. 清除绑定页 URL 中的 token；
2. 原地展示大号成功状态；
3. 提示用户关闭页面并返回 QQ，另提供进入 ZhiCe-Agent 的按钮。

授权失败：

1. 保留 URL token；
2. 展示稳定错误；
3. 在当前独立页提供全宽“重新绑定”按钮。

并发保护保证 onMounted、认证 watch 或重复 UI 事件不会重复消费同一个单次 token。

### 移动端布局

- QQ 绑定认证页使用独立单卡片 flow；在 460px 以下缩小卡片边距和表单留白，使用动态视口高度。
- 设置中心在 720px 以下采用全屏单列结构，导航保持横向滚动，详情区独立滚动。
- QQ inline bind 在桌面为“输入 + 按钮”，手机为两行单列；按钮全宽、固定最小触控高度并禁止文字换行。

## 数据流

```text
QQ /bind URL
  -> /bind/qq 捕获 token
  -> 用户登录或注册
  -> auth.authenticated = true
  -> QqBindingPage 自动 POST /api/channels/qq/authorize
     -> success: 清理 URL -> 原地展示成功 -> 返回 QQ
     -> failure: 保留 token -> 原地展示错误 -> 大按钮重试
```

## 变更文件

- `agent/channels/qq/adapter.py`
- `agent/app/gateway.py`
- `tests/unit_test/app/test_gateway.py`
- `web/frontend/src/router/index.ts`
- `web/frontend/src/pages/HomePage.vue`
- `web/frontend/src/pages/QqBindingPage.vue`
- `web/frontend/src/pages/QqBindingPage.test.ts`
- `web/frontend/src/layouts/AuthLayout.vue`
- `web/frontend/src/layouts/AuthLayout.test.ts`
- `web/frontend/src/components/SettingsCenter.vue`
- `web/frontend/src/components/SettingsCenter.test.ts`
- `web/frontend/src/styles/app.css`
- `docs_design/zhice-agent-part14-external-channel-design.md`
- `docs_design/README.md`

构建产物由 Vite 统一更新到 `agent/web/static/`。

## 测试方案

- 前端单测：未登录打开独立绑定页，随后 auth 变为已登录，只调用一次授权 API并展示成功结果。
- 前端单测：授权失败保留 URL token并在当前页打开手动重试入口；缺少 token 时给出重新获取链接提示。
- 组件单测：绑定操作使用独立移动端 action class 和“完成绑定”文案。
- 执行 `npm test`、`npm run lint`、`npm run build`。
- 执行仓库级 `python -m ruff check .` 与 `python -m pytest`。
- 用手机 viewport 对登录/注册和渠道设置做浏览器截图与按钮尺寸检查。

## 验收标准

- 新用户从 QQ 独立绑定页注册后无需再点击“个性化”或“渠道连接”即可完成绑定。
- 成功后独立页显示“QQ 绑定成功”，URL 不再携带 token，并明确提示返回 QQ。
- 手机宽度下绑定输入和按钮上下两行，按钮文字不挤压、不换行。
- 桌面绑定和已有手动绑定码流程不回归。
