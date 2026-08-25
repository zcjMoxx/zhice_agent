# 手机注册页单屏适配与 XHS 本地回环配置设计

> 说明：本记录落地后的手机认证卡片仍贴近顶部，且备案链接独立悬在卡片下方；当前布局由《2026-08-25 手机认证页垂直居中与一体化备案页脚设计》继续修正，本文正文保留当时方案原貌。

## 背景

手机浏览器的可用 CSS 视口会被地址栏和底部工具栏压缩。当前注册页在窄屏仍使用自动顶边距，并保留较高的品牌区、表单间距和页脚外边距；当可用高度约为 700px 时，认证主容器会产生纵向滚动，顶部也可能被挤出视口。与此同时，XHS 的本地 HTTP host allowlist 留空虽然运行时允许 loopback，但配置含义不直观，还依赖可选空占位语义。

## 目标

- 手机注册页在常见 390×700 及以上视口中完整落在单屏内，不产生页面纵向滚动或顶部裁切。
- 保留输入框、密码显示按钮、提交按钮和模式切换按钮的触控面积。
- 极短手机视口优先移除装饰性品牌区，不裁切注册表单。
- XHS 本地与公开示例显式使用 `127.0.0.1`，云端 private 继续使用容器 DNS 主机名。
- XHS YAML 恢复普通必填环境变量占位，不再为该字段依赖空值默认语法。

## 范围边界

- 只调整认证页手机端布局，不改变注册校验、认证 API 或桌面布局。
- 公安备案链接继续展示，不覆盖表单操作区。
- 不改变 XHS loopback 的运行时安全判断，也不把云端容器地址改成本地地址。
- 不提交、不推送，不重启用户当前运行的本地服务。

## 模块设计

### 手机注册布局

- 在 `max-width: 460px` 下禁止认证页整体纵向滚动，并约束卡片和页脚留在动态视口内。
- 注册态取消自动顶边距，品牌区只保留产品标识，并进一步压缩表单 padding、字段间距和提示气泡占用。
- 在 `max-height: 650px` 的短屏下隐藏注册态品牌区，以完整保留表单操作和备案链接。
- 保持输入框至少 40px、提交与模式切换按钮至少 44px 的触控高度。

### XHS 回环配置

- 本地 runtime `.env` 和公共 `.env.example` 使用 `XHS_READONLY_HTTP_HOST_ALLOWLIST=127.0.0.1`。
- 部署 private 保留 `zhice-xhs-readonly`，避免破坏容器间 HTTP 调用。
- 三份 YAML 都使用 `${XHS_READONLY_HTTP_HOST_ALLOWLIST}`；不同环境通过各自 `.env` 提供具体值。

## 数据流

```text
本地 .env (127.0.0.1) ─┐
private .env (容器 DNS) ├─> config.yml 环境展开 ─> XHS MCP HTTP host 校验
example (127.0.0.1) ────┘

手机动态视口 ─> 响应式高度/宽度规则 ─> 单屏注册表单 + 文档流备案页脚
```

## 变更文件

- `web/frontend/src/styles/app.css`
- `web/frontend/src/styles/responsive.test.ts`
- `config/.env.example`
- `config/config.example.yml`
- `tests/unit_test/config/test_config.py`
- `tests/unit_test/config/test_case.md`
- `${ZHICE_AGENT_WORKSPACE}/config/.env`
- `${ZHICE_AGENT_WORKSPACE}/config/config.yml`
- `deploy/private/.env`
- `deploy/private/config.yml`

## 测试方案

- 更新静态响应式合同，锁定认证页无整页滚动、短屏隐藏品牌区和触控高度。
- 更新配置合同，允许且要求 XHS allowlist 示例使用安全的非 Secret 回环默认值。
- 运行前端 lint、typecheck、全量测试和生产 build。
- 运行 Ruff、后端全量 pytest 和 `gateway --check`。
- 使用真实浏览器分别在 390×700、390×844 和窄桌面视口进入注册态，检查 `scrollHeight === clientHeight`、元素边界和页面截图。

## 验收标准

- 390×700 注册页主容器不可上下滚动，账号、两组密码、提交、返回登录和备案链接均在视口内。
- 390×844 注册页不裁切顶部，不出现无意义纵向滚动。
- XHS 本地检查解析出全部五个 MCP；云端 private 的容器 DNS 未被覆盖。
- 全量后端与前端检查通过，工作区保留既有未提交改动。
