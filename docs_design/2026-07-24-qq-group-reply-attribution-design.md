# QQ 群聊回复归属设计

## 背景

Part 14 已支持 QQ 群内按成员隔离 conversation route，但机器人出站消息只携带被动回复所需的 `msg_id`，客户端不会始终把回答明确展示为属于哪位提问者。多人连续提问时，即使后端 Session 隔离正确，群内阅读仍容易串线。

QQ 开放平台当前“发送群聊消息”接口没有独立的成员 mention 参数，自定义 Markdown 支持格式也没有 QQ 群成员 `@` 语法；频道 mention 语法不能套用到 QQ 群。官方稳定支持的是 `message_reference` 引用原消息。因此当前方案使用引用回复建立可见归属，不发送伪造 `@昵称`，也不暴露 `member_openid`。

官方依据：

- <https://bot.q.qq.com/wiki/develop/api-v2/autogen/api/v2_groups_group_openid_messages.post.html>
- <https://bot.q.qq.com/wiki/develop/api-v2/server-inter/message/type/markdown.html>

## 目标

- QQ 群聊的每个机器人回答明确引用触发它的原消息。
- 纯文本、Markdown 和 Markdown 降级路径保持相同归属。
- 长文本分块时只引用第一块，避免连续重复引用刷屏。
- 私聊行为保持不变，不把平台字段带入 AgentLoop 或中性协议。

## 范围边界

- 只调整 QQ Adapter 到 Transport 的出站参数以及 botpy payload。
- 不声称实现平台未公开支持的群成员原生 `@`。
- 不使用 `external_user_id` 拼接可见文本；OpenID 继续保持内部标识。
- `msg_id` 仍用于被动回复配额，`message_reference.message_id` 只负责客户端引用展示。

## 模块设计

`QQTransport.send_text()` 增加关键字参数 `quote`。Adapter 在群聊分块发送时只为第一块设置 `quote=true`。`BotpyQQTransport` 将其转换为：

```json
{
  "msg_type": 0,
  "content": "...",
  "message_reference": {"message_id": "触发消息 ID"}
}
```

富消息由 `send_message()` 根据 `event.conversation_type=group` 自动给每个降级尝试附加同一个 `message_reference`；一次调用只会有一个尝试成功，因此不会产生重复引用消息。

## 数据流

```text
QQ GROUP_AT_MESSAGE_CREATE
  -> normalize(member_openid, message_id)
  -> Adapter / Runtime
  -> text: first chunk quote=true
     markdown: group event auto quote
  -> Transport adds message_reference
  -> QQ 客户端显示引用的原提问
```

## 变更文件

- `agent/channels/qq/adapter.py`
- `agent/channels/qq/transport.py`
- `tests/unit_test/channels/test_channels.py`
- `tests/unit_test/channels/test_case.md`
- `docs_design/zhice-agent-part14-external-channel-design.md`
- `docs_design/README.md`

## 测试方案

- 正常：群聊纯文本回复包含原消息 `message_reference`。
- 正常：群聊 Markdown 及其降级尝试均包含原消息引用。
- 边界：群聊长文本只在第一块引用原消息。
- 回归：私聊纯文本和 Markdown 不附加 `message_reference`。
- 回归：绑定、去重、route、Markdown 降级和 Runtime 调用次数保持不变。

## 验收标准

- 多名群成员连续提问时，每条机器人回答在 QQ 客户端可通过引用原消息识别归属。
- 不使用未公开的 QQ 群成员 mention 语法，不泄露 member OpenID。
- Ruff、渠道专项测试、前端语法检查与全量 pytest 通过。
