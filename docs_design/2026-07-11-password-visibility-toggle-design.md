# 密码输入可见性切换设计

## 背景

当前 Web 登录、注册、Owner 初始化和账号改密表单只使用原生 `type=password` 输入框。部分浏览器会短暂显示自带的小眼睛，但输入完成、失焦或浏览器策略变化后图标会消失，用户无法稳定核对刚输入的密码或初始化凭证。

管理页面动态创建用户时也存在密码输入框，同样缺少一致的显示/隐藏能力。

## 目标

1. 所有密码及敏感凭证输入框都显示固定的小眼睛按钮。
2. 点击按钮在 `password` 和 `text` 间切换，输入内容和光标焦点不丢失。
3. 按钮始终存在，不依赖浏览器原生密码控件行为。
4. 支持键盘操作，并通过 `aria-label`、`aria-pressed` 和 `aria-controls` 表达状态。
5. 表单重新打开或重置时恢复隐藏状态。
6. 动态生成的管理员创建用户密码框使用同一套行为。

## 范围边界

- 不改变密码校验、提交载荷、autocomplete 或后端认证逻辑。
- 不保存、复制或记录密码明文。
- 不增加密码强度规则。
- 不依赖浏览器私有的密码显示按钮。

## 模块设计

- `index.html`：为 8 个静态敏感输入框增加固定 toggle button。
- `app.js`：提供统一初始化、切换和重置函数；动态管理员密码框创建后复用同一初始化逻辑。
- `styles.css`：统一眼睛按钮尺寸、焦点、hover 和两种图标状态；账号设置输入使用通用 wrapper。
- 静态资源 query version 更新，避免浏览器继续使用旧 CSS/JS。

## 数据流

```text
user clicks eye button
  -> resolve aria-controls target input
  -> password becomes text or text becomes password
  -> update aria-pressed and accessible label
  -> keep input value and focus
```

## 变更文件

- `web/static/index.html`
- `web/static/styles.css`
- `web/static/app.js`
- `tests/unit_test/app/test_gateway.py`
- `tests/unit_test/app/test_case.md`

## 测试方案

| 用例 | 预期 |
|---|---|
| 静态敏感输入 | 每个 input 都有对应 `data-password-toggle` 按钮 |
| JS 合同 | 存在初始化、切换和恢复隐藏状态逻辑 |
| 动态管理员密码 | 动态表单包含相同 toggle 标记并完成初始化 |
| CSS 合同 | 固定眼睛按钮和显隐图标状态样式存在 |
| 缓存版本 | CSS/JS query version 已更新 |

## 验收标准

1. 输入前、输入中、输入后小眼睛始终可见。
2. 登录、注册、Owner 初始化、初始化凭证、账号改密和管理员创建用户均可切换可见性。
3. 关闭再打开表单时默认恢复隐藏。
4. JS 语法、Ruff、聚焦测试和全量 pytest 通过。
