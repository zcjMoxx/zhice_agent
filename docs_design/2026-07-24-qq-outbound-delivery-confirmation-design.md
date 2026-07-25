# QQ 出站投递确认与可观测性设计

## 背景

2026-07-24 的真实群聊 trace 证明一条 QQ 消息已经完成入站、身份解析、Session 路由和 LLM 生成，但 QQ 客户端没有展示回复。`channel_event_receipts` 仍被写成 `done`。

核对当前安装的 `qq-botpy 1.2.1` 后发现，`BotHttp.request()` 在请求超时，以及连接重置递归重试返回值未向上传递时，可能返回 `None` 而不抛异常。当前 `BotpyQQTransport` 只判断是否抛异常，因此把“投递结果未知”误记为发送成功。

## 目标

- QQ API 返回有效响应时才确认发送成功。
- SDK 返回 `None` 时标记为 `QQSendUnconfirmedError`，让持久 event receipt 记录真实错误终态。
- 为文本、Markdown、Keyboard 和纯文本降级发送补齐结构化 trace。
- 状态未知时不盲目重发同一个 `msg_id + msg_seq`，避免平台已经接收时产生重复回复。

## 范围边界

- 修复只位于 QQ Transport 和 Adapter 已有异常传播边界，不修改 AgentLoop、Session 内容或 LLM 调用。
- 不修改第三方 botpy 源码。
- 不承诺确认 QQ 客户端已经完成最终渲染；有效 API 响应只表示 QQ 服务端确认接受请求。
- 不记录完整 QQ event id、message id、conversation id、用户 id、消息正文或凭证。
- 本次不实现主动消息补偿队列；未知状态需要可观测、可诊断，但不能自动重复发送。

## 模块设计

`BotpyQQTransport` 增加统一 `_reply()` 边界：

```text
message.reply(payload)
  -> raises Exception
       -> channel.qq.send_failed
       -> rich output may use an explicitly safer fallback
  -> returns None
       -> channel.qq.send_unconfirmed
       -> raise QQSendUnconfirmedError
       -> do not fallback/retry the same logical reply
  -> returns response
       -> channel.qq.send_done
```

所有事件关联字段使用短 hash。trace 只记录：

- `account_key`；
- `conversation_type`；
- `event_id_hash`、`source_message_id_hash`；
- `msg_type`、`msg_seq`、是否引用、是否带 Keyboard；
- 内容字符数、发送时长；
- 成功时的响应 message id hash；
- 失败时的异常类型或 `QQ_SEND_UNCONFIRMED`。

Adapter 当前已经在发送异常时把 receipt 终态写为 `error`，因此无需新增数据库字段。`QQSendUnconfirmedError` 会成为 `error_code`，不会重新执行 Agent Turn。

## 变更文件

- `agent/channels/qq/transport.py`
- `tests/unit_test/channels/test_channels.py`
- `tests/unit_test/channels/test_case.md`
- `docs_design/zhice-agent-part14-external-channel-design.md`

## 测试方案

- 正常：QQ API 返回消息对象，记录 `channel.qq.send_done`。
- 异常：API 明确抛异常，记录 `channel.qq.send_failed`；Rich 输出仍按既有策略降级。
- 边界：API 返回 `None` 时抛出 `QQSendUnconfirmedError`，记录 `channel.qq.send_unconfirmed`，Rich 输出不继续降级，避免重复发送。
- 持久状态：异常传播到 Adapter 后，event receipt 为 `error` 且错误码可诊断。

## 验收标准

- 不再把 botpy 的 `None` 返回值记为成功。
- 每次 QQ 出站都有开始与确定终态 trace。
- trace 不包含消息正文、完整平台 ID 或 Secret。
- 未确认发送不触发第二次 Agent Turn，也不自动重复发送相同序号。
- Ruff、渠道专项测试和全量 pytest 通过。
