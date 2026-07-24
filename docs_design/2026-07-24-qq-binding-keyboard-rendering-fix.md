# 2026-07-24 QQ 绑定按钮渲染修复设计记录

## 背景

Part 14 已按 QQ 单聊 API 为未绑定提示附加自定义 Keyboard，但真实 QQ 客户端只显示 `msg_type=0` 文本，没有渲染“绑定”按钮。官方单聊示例使用 `msg_type=2` Markdown 与 Keyboard 组合，当前文本与 Keyboard 组合不能作为可靠展示路径。

## 目标与边界

- 未绑定提示改用自定义 Markdown 并附加同一个 `/bind` 指令按钮；
- 保留 `/bind` 文本命令和 `/bind <绑定码>` 手动路径；
- Keyboard 未渲染或发送失败时依次降级为无按钮 Markdown、纯文本；
- 不修改身份、token、conversation route、AgentLoop 或 QQ 平台配置。

## 模块与数据流

`QQChannelAdapter -> build_binding_prompt() -> QQOutboundMessage(markdown, keyboard, fallback_text) -> BotpyQQTransport -> msg_type=2`

按钮仍使用 `action.type=2`、`data=/bind`、`enter=true`，由 QQ 客户端自动发送命令。

## 变更文件

- `agent/channels/qq/outbound.py`
- `tests/unit_test/channels/test_channels.py`
- `docs_design/zhice-agent-part14-external-channel-design.md`

## 测试与验收

- 未绑定提示的主 payload 是 Markdown，不再是文本；
- payload 同时包含“绑定”指令按钮；
- 降级文案仍明确提示 `/bind` 和 `/bind <绑定码>`；
- QQ Channel 专项测试与 Ruff 通过；真实客户端重启 Gateway 后能看到按钮。
