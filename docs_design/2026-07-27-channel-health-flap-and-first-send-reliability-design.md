# 渠道健康抗抖与首次发送可靠性设计

## 背景

2026-07-26 的真实 trace 暴露了两个不同问题：

- 微信每次 `getUpdates` 非正常长轮询失败都会立即从 `active` 切到 `degraded`，下一次成功又立即恢复，终端持续打印 `reconnecting/reconnected`。正常的 35 秒客户端长轮询超时已经由 vendor 返回空响应，因此这些事件来自额外的网络、HTTP、上游错误或响应解析失败，但当前统一折叠为 `WEIXIN_POLL_FAILED`。
- QQ 群回复在 `qq-botpy` 默认 5 秒 HTTP 上限处超时。SDK 记录 warning 后返回 `None`，Transport 正确标记为 `QQ_SEND_UNCONFIRMED`，但异常继续冒泡到 `botpy.Client.on_error`，把仍然在线的 WebSocket 渠道误标记为 `degraded`。

本机安装的 `qq-botpy 1.2.1` 明确说明，相同 `msg_id + msg_seq` 重复发送会失败。HTTP 超时后无法判断 QQ 服务端是否已经接收，因此不能通过盲目重发同时保证不丢消息和不重复消息。

## 目标

- 微信单次瞬断不改变账号健康状态，不产生 reconnect 日志风暴。
- 微信连续失败达到阈值时仍诚实进入 `degraded`，恢复后只输出一次 `reconnected`。
- 微信 poll 失败保留脱敏、可诊断的错误类别和连续失败次数。
- Python等待`account.start`的窗口大于Node `notifyStart`上限，避免双方同为10秒造成启动竞态。
- QQ HTTP 请求上限由 5 秒提高为显式可配置的安全值，降低真实网络抖动造成的未确认投递。
- QQ 单次发送未确认只把 event receipt 标记为 error，不把 WebSocket 渠道误标记为 degraded。
- 不重跑 Agent Turn，不把 LLM、渠道 SDK 或业务判断放进 AgentLoop。

## 范围边界

- 微信只在 sidecar Driver 和 Adapter 状态事件边界实现抗抖与诊断；Session、AgentLoop 和 LLM 不感知渠道健康策略。
- QQ 继续使用同一 `msg_id + msg_seq` 的平台去重约束。结果未知时坚持 at-most-once，不自动换序号补发，避免用户收到两份回答。
- QQ 明确 API 错误、Transport 断开和 WebSocket 连接异常仍按原有失败路径处理。
- 不修改第三方 `qq-botpy` 源码。
- 不编辑 `docs_design/zhice-agent-overall-design.md`；总体设计同步点由主窗口处理。

## 模块设计

### 微信 poll 抗抖

`OfficialAccount.pollLoop()`维护连续失败计数：

```text
第 1~2 次连续失败
  -> 保持现有 active 状态
  -> emit account.poll_retry
  -> 指数退避

第 3 次连续失败
  -> account.status = degraded
  -> 只输出一次 reconnecting

后续成功
  -> failures = 0
  -> 只有之前真实 degraded 才切回 active
  -> 只输出一次 reconnected
```

错误分类只输出固定 allowlist code：DNS、连接重置、连接超时、HTTP、响应解析、上游业务返回和通用 poll failure。原始 URL、token、响应正文和异常 message 不进入 frame/trace。

### QQ HTTP 上限与状态隔离

`channels.qq.accounts[].http_timeout_seconds`默认 15，限制为 1~60 秒，并传给 `botpy.Client(timeout=...)`。这覆盖 SDK 的全部 QQ HTTP 请求，不改变 WebSocket heartbeat。

`QQSendUnconfirmedError`属于 QQ 出站共享类型。Adapter 捕获它后：

- event receipt 写 `error / QQSendUnconfirmedError`；
- 不再次抛给 botpy event dispatcher；
- 不触发 `Client.on_error`，因此渠道保持 available；
- 不再次调用 LLM，也不重发相同或新 `msg_seq`。

## 变更文件

- `agent/channels/config.py`
- `agent/channels/qq/outbound.py`
- `agent/channels/qq/adapter.py`
- `agent/channels/qq/transport.py`
- `agent/channels/weixin/adapter.py`
- `config/config.example.yml`
- `integrations/weixin_sidecar/src/official-driver.js`
- `integrations/weixin_sidecar/test/official-driver.test.js`
- `tests/unit_test/channels/test_channels.py`
- `tests/unit_test/channels/test_case.md`
- `tests/unit_test/weixin_channel/test_weixin_channel.py`
- `tests/unit_test/weixin_channel/test_case.md`

## 测试方案

- 正常：微信稳定 poll 不产生 retry/reconnect；QQ 在配置上限内返回确认并记录 send_done。
- 异常：微信连续三次失败进入 degraded；QQ 返回 `None` 时 receipt 为 error，但 adapter 不向 botpy 冒泡。
- 边界：微信单次失败后成功不产生 reconnecting/reconnected；错误分类不泄露异常正文；QQ timeout 配置拒绝 0、负数和超过 60 秒。
- 回归：微信 Outbox、账号恢复、QQ Markdown/文本降级、Gateway readiness 和统一配置继续通过。

## 验收标准

- 真实单次微信 poll 瞬断不再形成 WARNING/INFO 成对刷屏。
- 持续微信故障仍可在有限时间内进入 degraded，并在真实恢复后输出一次恢复日志。
- QQ 观察到的 5.2 秒超时不再受 SDK 默认 5 秒限制。
- `QQ_SEND_UNCONFIRMED`不再导致 `channel.qq.degraded`。
- Ruff、Node sidecar 测试、渠道专项、相关回归与全量 pytest 通过。
