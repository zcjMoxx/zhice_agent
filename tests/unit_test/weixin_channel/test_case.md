# 微信 ClawBot 渠道测试说明

## 测试目标

- 验证一个内部用户最多绑定一个微信账号，微信 AI 账号和扫码用户不能跨用户复用。
- 验证凭证只写入 workspace runtime config，API/普通状态不返回 token 或完整外部标识。
- 验证未绑定、disabled、发送者不匹配和重复事件均在 Agent Runtime 前拒绝。
- 验证 direct text 复用身份、Conversation Route、receipt 和纯文本 4000 字符分块。
- 验证 stdio NDJSON 握手、协议损坏、真实 Node 进程 smoke 与明确的 POC 阻塞状态。

## 用例覆盖

- 正常：绑定 finalize、账号启动、入站 Turn、ACK、出站分块、解绑保留 Session 数据。
- 异常：所有权冲突、凭证缺失、sidecar 不可用、错误发送者、发送失败。
- 边界：双用户隔离、重复消息、4000 字符、二维码 no-store、协议版本和 frame 上限。
- Node Transport：官方授权内容只在本地编码为 PNG data URI；取消在途二维码长轮询时立即应答，且取消后的迟到确认事件不得完成绑定。
- 绑定完成：`binding.connected` 事件处理不得在 sidecar stdout reader 内同步等待 `account.start` 响应；账号启动失败时进入 `reconnect_required`。

## 关键检查点

- 测试不得通过 AgentLoop 微信分支完成；必须经过 `WeixinClawAdapter` 和共享 runtime。
- 测试 fixture 中的 token/context token 不得出现在响应对象或日志断言中。
- 真实微信 E2E 仅在 `ZHICE_AGENT_WEIXIN_E2E=1` 和隔离 workspace 下人工启用。
