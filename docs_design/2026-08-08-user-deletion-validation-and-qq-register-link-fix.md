# 用户删除确认反馈与 QQ 注册链接修正

## 背景

永久删除弹窗在确认用户名不匹配时直接禁用提交按钮，用户点击后没有反馈。QQ 绑定认证页的移动端账号切换入口整行使用弱文本色，注册动作缺少链接视觉提示。

## 目标与范围

- 删除按钮只在请求处理中禁用；确认值为空或不匹配时提交后显示明确行内错误，并聚焦确认输入框。
- 用户继续输入时清除旧错误。
- QQ 绑定认证页将提示语与动作语拆分，仅“立即创建/返回登录”使用蓝色加粗链接样式。
- 不改变删除 API、安全边界、登录注册流程或路由。

## 变更文件

- `web/frontend/src/layouts/AdminLayout.vue`
- `web/frontend/src/layouts/AdminLayout.test.ts`
- `web/frontend/src/layouts/AuthLayout.vue`
- `web/frontend/src/layouts/AuthLayout.test.ts`
- `web/frontend/src/styles/app.css`

## 测试与验收

- 错误用户名提交后出现 `用户名不一致，请重新输入`，且不会调用删除 API。
- 改成正确用户名后错误消失并可提交。
- QQ 登录与注册两种状态均包含独立的蓝色动作文本节点。
