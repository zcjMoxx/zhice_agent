# QQ 群聊手动绑定设计

## 背景

Part 14 第一版把 QQ 的网页授权和一次性码手动绑定都限制在私聊。实际使用中，用户已经在登录态 Web 主动生成了 10 分钟单次绑定码，希望能在可信群聊中直接发送 `/bind <一次性码>`，把命令发送者的 QQ 身份绑定到该 Web 用户。

## 目标

- 允许未绑定用户在 QQ 群聊中使用 `/bind <一次性码>` 手动绑定。
- 保持群聊裸 `/bind` 不生成网页授权链接，继续引导用户前往私聊。
- 保持绑定码按 channel、机器人 account key 校验并单次原子消费。
- 不让绑定命令进入 AgentLoop，不在回复或日志中回显绑定码。

## 范围边界

- 只调整 QQ Adapter 的绑定前置命令策略，不修改中性 Channel 协议、AgentLoop、QQ SDK transport 或数据库结构。
- 私聊 `/bind` 和私聊 `/bind <一次性码>` 的现有行为保持不变。
- 群聊使用绑定码属于用户主动暴露短期凭据的选择；成功消费后绑定码立即失效。公共群聊仍优先推荐私聊网页授权。

## 模块设计

`QQChannelAdapter._handle_unbound()` 按命令形态而不是先按会话类型整体拒绝：

1. 裸 `/bind`：仅私聊创建 Web 授权请求；群聊回复私聊引导。
2. `/bind <code>`：私聊和群聊都调用既有 `ExternalIdentityService.bind()`。
3. 其它未绑定消息：私聊返回带 Keyboard 的绑定提示；群聊返回私聊或手动码提示。
4. 已绑定身份在任意 QQ 会话发送 `/bind...` 时，统一回复已绑定，不进入 Runtime。

## 数据流

```text
Web 登录用户生成一次性码
  -> QQ 群发送 /bind <code>
  -> Adapter 提取当前消息发送者 external_user_id
  -> ExternalIdentityService.bind
  -> SQLite 原子消费 account-scoped token
  -> external_identities 绑定发送者
  -> 群聊返回成功或通用失败提示
```

## 变更文件

- `agent/channels/qq/adapter.py`
- `tests/unit_test/channels/test_channels.py`
- `tests/unit_test/channels/test_case.md`
- `docs_design/zhice-agent-part14-external-channel-design.md`
- `docs_design/README.md`

## 测试方案

- 正常：未绑定群成员用有效码绑定成功，Runtime 不被调用。
- 异常：群聊裸 `/bind` 不生成授权链接，只提示转私聊。
- 边界：同一码第二个群成员重放失败，且不会覆盖首个绑定。
- 边界：已绑定群成员发送 `/bind <code>` 不进入 Runtime，也不消费新码。
- 回归：私聊裸 `/bind` 与私聊手动绑定保持不变。

## 验收标准

- 群聊 `/bind <有效一次性码>` 绑定当前发送者。
- 群聊裸 `/bind` 不返回可被群成员打开的授权链接。
- 一次性码无法重复消费，绑定命令始终停留在 Adapter 边界。
- Ruff、渠道专项测试和全量 pytest 通过。
