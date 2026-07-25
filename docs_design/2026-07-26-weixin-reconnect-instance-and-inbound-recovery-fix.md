# 微信重连实例与入站恢复修复

> 日期：2026-07-26
>
> 状态：设计确认并同步实现。

## 背景

微信账号执行 `Reconnect` 时，sidecar 会为同一 `account_key` 创建新轮询实例并直接覆盖 Map，旧实例未停止。旧实例收到的消息携带其私有 context 引用，而回复会交给 Map 中的新实例，导致发送阶段出现 `CONTEXT_TOKEN_REFERENCE_INVALID`。同时，数据库处于 `reconnect_required` 时，即使现存 sidecar 实例仍能收到身份匹配的真实消息，Adapter 也会静默拒绝，无法通过入站活动自动恢复。

## 目标

- 同一微信账号任意时刻只保留一个轮询实例。
- `reconnect_required` 账号收到可信、身份匹配的入站消息时自动恢复为 `active` 并继续处理该消息。
- 只有微信明确返回 token stale 才进入 `reconnect_required`；临时启动和轮询错误自动退避恢复。
- `account.start` 使用 `notifyStart` 的明确返回码给出初始 active/degraded 状态，轮询成功后再从 degraded 转为 active。
- trace 记录 sidecar 返回的安全错误码，能够区分 context、账号和上游发送错误。
- 每个入站消息都闭合为 accepted、duplicate、rejected、done 或 failed；拒绝、ACK 和 worker 异常带 allowlist 原因码。

## 范围边界

- 不改变二维码授权、凭据格式和用户隔离模型。
- 不尝试绕过微信明确返回的 token stale；没有入站活动时仍由用户重新扫码授权。
- 不重放已经完成 Agent Turn 但发送失败的历史回复。

## 模块设计与数据流

1. Node sidecar 按 account key 串行执行 start/stop；重复 `account.start` 先等待旧实例 `stop()` 完成，再创建并登记新实例。
2. `notifyStart` 明确成功时初始状态为 active，临时失败时实例保持 degraded 并由长轮询自动退避；明确 `-14` 时返回 `WEIXIN_TOKEN_STALE`。
3. Python Adapter 仅在安全错误码为 `WEIXIN_TOKEN_STALE` 时持久化 `reconnect_required`，其它启动异常保持绑定状态并记录 reconnecting/degraded。
4. Python Adapter 校验 account 和 external user；若状态为 `reconnect_required`，可信入站活动将状态恢复为 `active`，随后正常执行去重、ACK、Turn 和回复。
5. sidecar protocol error 仅在满足安全错误码格式时进入 trace 和 receipt；其它异常继续退化为异常类型。
6. 入站校验、去重、ACK、身份解析和限流分别记录安全 lifecycle 事件，不记录消息正文和完整外部标识。

## 变更文件

- `integrations/weixin_sidecar/src/main.js`
- `integrations/weixin_sidecar/src/official-driver.js`
- `integrations/weixin_sidecar/test/sidecar.test.js`
- `integrations/weixin_sidecar/test/official-driver.test.js`
- `agent/channels/weixin/adapter.py`
- `agent/channels/weixin/binding.py`
- `agent/channels/weixin/sidecar.py`
- `tests/unit_test/weixin_channel/test_weixin_channel.py`
- `tests/unit_test/weixin_channel/test_case.md`

## 测试方案

- Node：同账号连续两次 start，断言旧实例先 stop，发送只使用新实例。
- Node：token stale 在首次验证时直接返回 reauth；临时轮询错误进入 degraded 并在成功后恢复 active。
- Python：`reconnect_required` 账号收到匹配消息后恢复 active、ACK accepted、执行 Turn 并回复。
- Python：普通 account.start 异常不再污染持久化账号状态，明确 stale 才要求人工重连。
- Python：disabled、未知账号和错误 external user 仍拒绝。
- 静态检查、微信专项测试和 sidecar Node 测试全部通过。

## 验收标准

- 重连后不再出现同账号双 poller。
- 入站活动能够修复数据库与实际 sidecar 连接状态的不一致。
- 发送失败 trace 包含脱敏的 `error_code`，不包含 token、context token 或完整外部标识。
