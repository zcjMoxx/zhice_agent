# 微信 ClawBot 二维码显示与取消修复设计

## 背景

微信官方 Transport 的 `qrcode_img_content` 是待编码的授权内容，不是可直接展示的图片地址。当前 Web 将其赋给 `<img src>`，因此二维码显示失败。同时，绑定取消会等待最长 35 秒的状态长轮询退出，而 Python sidecar 请求约 10 秒即超时，造成取消接口失败。

真实绑定继续验证时还发现：`binding.connected` 由 Python sidecar stdout reader 同步分发，绑定持久化随后在同一线程调用 `account.start` 并等待响应；该响应也依赖 stdout reader，形成重入自锁。Node 虽已收取消息，Python reader 却在超时后退出，导致消息没有进入 receipt 和 Channel Runtime。

## 目标

- 在 Node sidecar 内把官方二维码内容本地编码为 PNG data URI，Web 只接收可直接展示的图片数据。
- 取消绑定立即应答，并中止正在进行的官方状态长轮询。
- 取消后禁止迟到的轮询结果产生 `binding.connected` 或其他绑定状态事件。
- 绑定完成后的账号启动不得阻塞 sidecar stdout reader；启动失败必须保留绑定并进入可重连状态。

## 范围边界

- 仅修改微信 Transport sidecar、其依赖清单、上游适配补丁和 Node 测试。
- 不使用外部二维码生成服务，不记录或额外持久化二维码原文。
- 不修改 AgentLoop，不引入个人微信自动化，不改变现有绑定 API 契约。

## 模块设计与数据流

1. `official-driver` 收到官方 `qrcode_img_content` 后，使用本地 `qrcode` 库生成 `data:image/png;base64,...`。
2. sidecar 通过既有 NDJSON 响应把 data URI 返回给 Python/Web。
3. 取消请求设置绑定状态为 cancelled、触发 `AbortController.abort()` 并立即返回；轮询任务在后台收敛。
4. vendored `apiGetFetch` 合并内部超时与外部中止信号，使 GET 长轮询可立即退出。
5. 轮询在解析响应后、发送任何事件前再次检查 cancelled/aborted 状态，阻止迟到事件。
6. Python 完成凭据与账号所有权持久化后，用独立后台线程请求 `account.start`；stdout reader 立即返回继续消费响应和入站事件，失败则把账号标记为 `reconnect_required`。

## 变更文件

- `integrations/weixin_sidecar/package.json`、`package-lock.json`
- `integrations/weixin_sidecar/src/official-driver.js`
- `integrations/weixin_sidecar/vendor/openclaw-weixin-2.4.6/api/api.js`
- `integrations/weixin_sidecar/vendor/upstream-manifest.json`
- `integrations/weixin_sidecar/test/official-driver.test.js`
- `agent/channels/weixin/binding.py`
- `tests/unit_test/weixin_channel/test_weixin_channel.py`、`test_case.md`

## 测试方案

- 正常：授权内容被编码为本地 PNG data URI，确认后仍只输出允许的凭据字段。
- 异常：取消时在途 GET 被中止，取消调用快速返回。
- 边界：即使 fetch 忽略中止并迟到返回，取消后也不再发送 connected/status/failed 事件。
- 回归：模拟 `account.start` 阻塞，断言绑定事件处理仍快速返回；启动失败状态可重连。

## 验收标准

- Web 能显示可扫描二维码。
- 点击“取消扫码”后接口及时成功，状态变为 cancelled。
- 取消后不会因迟到结果完成绑定。
- 真实入站 direct text 能进入 receipt、Conversation Route 和共享 Runtime，不因 sidecar reader 重入而丢失。
- Node 测试、Python 测试、Ruff 与前端语法检查通过。
