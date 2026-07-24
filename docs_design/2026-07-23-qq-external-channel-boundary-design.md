# 2026-07-23 QQ 外部渠道边界设计记录

> 说明：当前代码已允许用户把 Web 主动生成的一次性码通过 QQ 群聊 `/bind <code>` 绑定到命令发送者；群聊裸 `/bind` 仍只引导私聊网页授权。该后续调整见 `2026-07-24-qq-group-manual-binding-design.md` 和当前 Part 14 活文档，本文正文保留当时的私聊边界方案。

> 状态：第一版代码已落地；当前实现应参考活文档 `docs_design/zhice-agent-part14-external-channel-design.md`

> 实现校准：本机真实 `qq-botpy 1.2.1` 使用 `Client.run(appid=..., secret=...)`、`on_c2c_message_create`、`on_group_at_message_create` 和消息对象 `reply(...)`。单聊发送接口真实支持 `msg_type=2`、`markdown` 和 `keyboard`；QQ 官方文档已说明单聊/群聊自定义 Markdown 对所有机器人开放，无需单独申请模板。当前版本仍不接入未验证的原生 C2C stream，能力声明保持 `text_streaming=false`，普通 Agent 输出使用最终文本分块；QQ SDK import 仅存在于 `agent/channels/qq/transport.py`。

> 绑定 UX 校准：QQ 私聊支持两条并存路径。普通未绑定提示附带“绑定”指令按钮，点击后由 QQ 自动发送 `/bind`；裸 `/bind` 为当前 QQ OpenID 创建 10 分钟、单次消费的 Web 授权请求，并通过 Markdown 超链接和“登录并绑定”URL 按钮返回。用户打开链接后，未登录则先登录，登录成功后自动绑定到当前内部用户。`/bind <一次性绑定码>` 保留为手动绑定路径。Markdown/Keyboard 发送失败只降级为纯文本链接，不重新创建授权请求，也不重新执行 Agent Turn。Web 个人设置只增加“生成 QQ 一次性绑定码”按钮，不增加渠道列表、account key、最后使用时间或管理员绑定管理页面。

## 1. 背景

Part 13 已完成，路线图下一阶段是 Part 14 外部渠道。用户确定第一条渠道为 QQ，并要求在实现 QQ 的同时为后续微信、飞书等渠道保留兼容边界。

当前代码虽然已有 `external_identities`、`session_index.channel`、actor-aware active turn 和 external WS command profile，但还没有真正的平台 Adapter、外部会话路由、绑定码、事件去重、附件 guard 和渠道出站渲染。

QQ 官方 Agent 接入资料显示：

- 机器人通过 AppID / AppSecret 接入；
- 官方扫码 connector 可返回 credential 数组；
- 官方协议 SDK 支持 WebSocket / Webhook、消息、媒体、交互和 C2C stream；
- 参考实现包含回声过滤、去重、限流、并发保护、`@` 门禁、引用解析和附件处理；
- 官方明确要求保护凭证、严格控制调用者、检查异常日志并准备凭证泄露应急流程。

## 2. 目标

- 确定 Part 14 的通用渠道协议和 QQ 第一实现路线。
- 保证 QQ SDK 与 AgentLoop 解耦。
- 延续 Part 9 的内部用户、权限和 Session ownership，不因渠道复制安全系统。
- 明确私聊、群聊、命令、确认、附件、去重、限流和日志边界。
- 给出代码变更文件、测试和验收顺序。

## 3. 关键决策

### 3.1 Python runtime 优先

QQ 消息运行第一版使用 `qq-botpy` 可选依赖和 WebSocket gateway，避免为了 QQ 常驻一个 Node sidecar。

官方 `@tencent-connect/qqbot-connector` 仅作为后续可选扫码配网能力。没有 Node.js 时，用户仍可手动创建机器人并通过环境变量注入 AppID / AppSecret。

Webhook transport 在协议层预留，待 Part 15 具备公网回调、HTTPS 和部署条件后实现。

### 3.2 不在本阶段大规模重命名 WebRuntime

新增窄 `ChannelChatRuntime` Protocol，让现有 application runtime 适配。这样 QQ 不依赖 HTTP/WS route，也不为了名称问题同时制造大范围 rename。

### 3.3 身份映射仍落到内部 user_id

机器人账号连接和最终用户身份绑定是两件事。未知 QQ 用户不能自动注册或触发 LLM，只能在私聊使用一次性绑定码映射到 `external_identities`。

第一版绑定入口调整为：

```text
QQ /bind
  -> 创建绑定当前 QQ OpenID 的 Web 授权请求
  -> 返回一次性 Markdown Web 登录链接和 URL 按钮
  -> 登录成功后自动绑定当前内部用户

普通未绑定提示 -> “绑定”指令按钮 -> QQ 自动发送 /bind

Web 个人设置 -> 生成 QQ 一次性绑定码
QQ /bind <code> -> 直接绑定
```

两条路径最终写入同一 `external_identities`。授权链接和绑定码都只保存 hash、10 分钟过期、单次消费并绑定 `channel=qq` 与 `account_key`；授权链接额外绑定发起消息的 QQ OpenID，URL 不暴露 OpenID、AppID 或内部 user id。

### 3.4 群聊采用 per-user session

第一阶段不创建群共享 Session。同一 QQ 群内，每个已绑定内部用户拥有独立 conversation route 和 Session。群输出仍是公开消息，因此高风险 Tool 不在群聊确认。

### 3.5 渠道差异由 capability 表达

QQ 私聊、QQ 群聊、未来微信和飞书分别声明 text、stream、interaction、media、quote、command profile 等能力。禁止按 transport 类型推断客户端语义。

### 3.6 持久去重必须在 Agent 前完成

SDK 内存去重不足以防止进程重启、webhook 重投或重连后的重复 Tool 执行。新增 receipt 表，先 claim event，再进入 Turn。

## 4. 模块设计

新增：

- `agent/protocols/channel.py`：中性协议；
- `agent/channels/`：Manager、配置、identity、conversation、dedup、limits 和 runtime adapter；
- `agent/channels/qq/`：transport、normalize、outbound、confirmation、attachments 和 startup；
- `config/channels.example.yml`：无真实 secret 的模板；
- `tests/unit_test/channels/test_case.md` 和专项测试。

修改：

- `agent/app/runtime.py`：接收 channel context，并通过窄协议暴露 chat/stop/confirmation；
- `agent/auth/schema.py` / `store.py`：绑定 token、conversation route 和 receipt；
- `agent/cli.py` / app builder：channels status、启动和管理；
- `pyproject.toml`：`qq` optional dependency；
- health、trace、Runtime Activity 和 Security Audit 的 channel 状态。

## 5. 数据流

```text
QQ event
  -> normalize
  -> dedup claim
  -> mention/access/rate gate
  -> external identity
  -> conversation route
  -> attachment guard
  -> ChannelChatRuntime
  -> AgentLoop
  -> RuntimeEvent
  -> QQ outbound renderer
```

## 6. 安全边界

- 未绑定消息在 LLM 前拒绝。
- 群聊默认 `@` 触发。
- 群聊拒绝 confirmation-required / high-risk Tool。
- secret、token、绑定码、签名和完整外部 ID 脱敏。
- 附件经过 SSRF、大小、类型、超时和用户目录边界。
- SDK 升级固定兼容范围并先跑专项测试。
- QQ 不可用只局部降级，不阻断 Web/CLI。

## 7. 测试方案

- FakeQQTransport 覆盖正常、异常和边界路径。
- 持久去重证明同 event 只运行一次 Agent。
- 多用户群聊证明 Session 隔离。
- Tool policy 证明群聊高风险拒绝、私聊确认只允许发起者。
- 附件 guard 覆盖 SSRF、超限和文件名清洗。
- 真实 QQ E2E 仅在 `ZHICE_AGENT_QQ_E2E=1` 时开启。
- 完成后运行 Ruff、全量 pytest 和相关前端语法检查。

## 8. 验收标准

完整验收标准以 `docs_design/zhice-agent-part14-external-channel-design.md` 第 25 节为准。核心判断是：QQ 已形成真实消息闭环，同时 AgentLoop、用户权限、SessionStore 和命令语义没有出现 QQ 专用分支或复制实现。

## 9. 参考资料

- [QQ 机器人 Agent 接入与安全使用须知](https://bot.q.qq.com/wiki/agent-qqbot/#%E5%AE%89%E5%85%A8%E4%BD%BF%E7%94%A8%E9%A1%BB%E7%9F%A5)
- [`@tencent-connect/qqbot-connector`](https://www.npmjs.com/package/@tencent-connect/qqbot-connector)
- [`@tencent-connect/qqbot-nodejs`](https://www.npmjs.com/package/@tencent-connect/qqbot-nodejs)
- [`tencent-connect/botpy`](https://github.com/tencent-connect/botpy)
