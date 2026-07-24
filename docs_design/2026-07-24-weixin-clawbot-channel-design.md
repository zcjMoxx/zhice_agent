# 2026-07-24 微信 ClawBot 渠道设计记录

> 状态：代码与单账号真实 Transport POC 已完成；双真实账号并发待第二名用户验收。本文属于 Part 14 外部渠道实现二，当前口径已合并进 `docs_design/zhice-agent-part14-external-channel-design.md`。

## 1. 背景

Part 14 已完成外部渠道通用运行链和 QQ 实现一。当前把 Part 14 实现二收敛为微信 ClawBot，并与已有 Web 用户体系组合：每个登录 ZhiCe-Agent 的用户独立发起一次微信扫码连接，得到自己的微信 AI 会话；所有会话仍进入同一个 ZhiCe-Agent 后端，但 Actor、Session、Memory、权限和凭证互相隔离。

这里的“一人一个”不是一个可分享的公共机器人账号，而是：

```text
ZhiCe Web 用户 A -> 独立扫码 -> 微信 AI A -> user A 的 Actor / Session / Memory
ZhiCe Web 用户 B -> 独立扫码 -> 微信 AI B -> user B 的 Actor / Session / Memory
ZhiCe Web 用户 C -> 独立扫码 -> 微信 AI C -> user C 的 Actor / Session / Memory
                                                |
                                                +-> 同一个 ZhiCe-Agent Runtime
```

## 2. 已核对的上游事实

2026-07-24 从 npm 实际发布包核对：

- `@tencent-weixin/openclaw-weixin` 当前版本为 `2.4.6`，许可证为 MIT，要求 Node.js `>=22`，peer dependency 为 OpenClaw `>=2026.5.12`；
- `@tencent-weixin/openclaw-weixin-cli` 当前版本为 `2.1.4`，许可证为 MIT；
- 插件支持扫码登录和多账号，凭证登录后保存在本地；
- 当前 Channel 声明 `chatTypes: ["direct"]`、`blockStreaming: true`、文本分块上限 `4000`，并具备媒体与 typing 能力；
- 消息入口是 `getUpdates` 长轮询，出站是 `sendMessage`，回复需要回传 `context_token`；
- 登录返回 AI 账号标识、bot token 和扫码用户标识；
- 包根入口只注册 OpenClaw Channel，没有发布独立、稳定的 Transport SDK；
- 插件的入站处理最终调用 OpenClaw `channelRuntime.reply.dispatchReplyFromConfig(...)`，直接进入 OpenClaw 自己的 Agent Runtime。

因此不能把完整 OpenClaw Gateway 直接当作 ZhiCe-Agent 的透明消息代理，否则会同时存在 OpenClaw AgentLoop 和 ZhiCe-Agent AgentLoop。也不能把 npm 包内部 `dist/src/*` 深路径当作稳定公共 API。

本方案采用微信专用 Node Transport sidecar。sidecar 的二维码、长轮询、发送和 token 处理必须来自当前官方 MIT 发布实现及其公开接口说明，保留上游版本、tarball integrity 和 LICENSE；不自行猜测或逆向 iLink 协议。升级必须重新做源码差异和真实微信回归。

代码许可证只覆盖发布代码，不自动等于微信在线服务的长期可用权或商业授权。真实接入前仍需验证服务条款、账号规则和当前服务可用性。

## 3. 目标

1. 一个内部 Web 用户最多绑定一个微信 ClawBot AI 账号。
2. 扫码、二维码状态、凭证和微信长轮询完全留在微信 Transport/sidecar 边界。
3. 微信入站消息复用 Part 14 的身份、路由、去重、限流、RuntimeEvent、RBAC、确认、Hook 和审计链。
4. 微信与 Web 共用内部 `user_id` 和用户级 Memory，但使用独立 Session。
5. 一个共享 sidecar 可同时维护多个内部用户的微信账号，不引入 tenant 或组织模型。
6. sidecar 异常只让微信能力局部降级，不阻断 Web、CLI 或 QQ。
7. 第一版先完成私聊文本、扫码绑定、解绑、重连和双用户隔离闭环。

## 4. 范围边界

第一版不包含：

- 公共可分享的统一微信联系人；
- 微信群聊；
- 未登录 Web 用户自动注册或自动绑定；
- 第二套 OpenClaw Agent、Session、Memory 或 Tool Runtime；
- 主动营销、定时群发或脱离有效会话上下文的任意主动消息；
- 语音、图片、视频和文件收发；
- 多 Gateway 实例共同消费同一 bot token；
- 在 Gateway 启动时联网安装 npm 包；
- 在 AgentLoop 中加入微信专用判断。

## 5. 核心架构决策

### 5.1 一个共享 sidecar，多用户多账号

ZhiCe-Agent Gateway 启动一个 Node.js sidecar。sidecar 内为每个已绑定用户维护独立的微信账号状态和长轮询任务：

```text
微信 iLink AI Bot 服务
  <-> Weixin Transport sidecar（Node.js，二维码/token/long-poll/send）
  <-> stdio NDJSON bridge
  <-> WeixinClawAdapter（Python）
  <-> Part 14 Channel services
  <-> ChannelChatRuntime
  <-> ZhiCe-Agent AgentLoop
```

不为每个用户启动一个 Node 进程。用户隔离依靠 `account_key -> owner_user_id`、独立 credential、独立同步游标和现有 Actor/Session 边界完成。

### 5.2 stdio NDJSON，而不是本地 HTTP 端口

第一版 sidecar 与 Python 使用单进程子进程 stdio 协议：

- stdin/stdout 只传一行一个 JSON frame；
- sidecar 日志只写 stderr；
- frame 必须包含 `protocol_version`、`type`、`request_id` 和必要的 `account_key`；
- stdout 出现非 JSON 内容视为协议损坏，当前 sidecar 进入 unavailable；
- Python 对 request 建立有界 pending map，并限制单 frame 大小、超时和并发；
- Gateway stop 时先请求 sidecar 停止长轮询并发送上游 stop 通知，再终止子进程；
- 子进程异常退出采用有界退避重启，连续失败后停止自动重启并暴露 capability cause。

这样不新增监听端口、回调 Secret、反向代理或公网要求，也能由 Gateway 统一管理生命周期。

### 5.3 不直接深度 import npm 包内部路径

sidecar 不依赖 `@tencent-weixin/openclaw-weixin/dist/src/...` 这类未承诺稳定的深路径。实现阶段二选一并在 POC 后锁定：

1. 优先：从官方 MIT tarball 提取经过审计的 Transport 源文件，保留原 LICENSE、来源版本、integrity 和最小补丁清单；
2. 如果官方随后发布稳定 Transport API，则改为直接依赖公开 API，并删除 vendored Transport。

禁止重新发明二维码、鉴权头、同步游标、消息结构或 CDN 加解密协议。任何上游升级都必须先更新来源清单并跑契约测试。

## 6. Web 用户与微信账号模型

### 6.1 新增 channel account 所有权

现有 `external_identities` 表表达“外部发送者映射到内部用户”，但不能完整表达“某个内部用户拥有一个带 token 的微信 AI 账号”。新增渠道账号记录：

```text
channel_accounts
  id
  channel                = weixin
  account_key            = 内部生成的稳定 opaque key
  owner_user_id          = ZhiCe user_id
  external_account_id    = ilink bot id
  external_user_id       = 扫码微信用户 id
  credential_ref         = workspace 内相对路径引用
  status                 = active / reconnect_required / disabled / cleanup_pending
  linked_at
  updated_at
  last_seen_at
```

约束：

- `(channel, owner_user_id)` 唯一；
- `(channel, external_account_id)` 唯一；
- `(channel, external_user_id)` 唯一；
- `account_key` 不使用微信原始 ID，避免把平台标识扩散到日志、URL 和文件名；
- token 不进入数据库。

绑定完成时，同一事务内创建 `channel_accounts` 和现有 `external_identities` 映射：

```text
channel=weixin
external_tenant_id=account_key
external_user_id=扫码用户 id
user_id=当前 Web 用户
```

之后每条消息仍通过 `ExternalIdentityService.resolve(...)` 得到 Actor，不为微信增加旁路权限系统。

### 6.2 凭证与同步游标

```text
${ZHICE_AGENT_WORKSPACE}/config/channels/weixin/accounts/{account_key}.json
${ZHICE_AGENT_WORKSPACE}/state/channels/weixin/{account_key}/sync.json
```

- credential 文件保存 bot token、外部 AI 账号 id 和必要服务地址；原子写入并使用当前平台可提供的最严格文件权限；
- DB 只保存相对 `credential_ref`；
- 同步游标属于 state，不和 Secret 混放；
- 二维码内容、bot token、`context_token`、完整微信 ID 不进入普通日志、trace、audit 或浏览器 URL；
- Web 绑定接口返回 `Cache-Control: no-store`；
- 当前本地开发阶段不伪装具备通用静态加密能力。未来如果需要远程部署，必须接平台 Secret/KMS，而不是在同目录保存“加密密钥”。

## 7. 扫码绑定状态机

### 7.1 Web API

建议新增认证后的本人接口：

```text
GET    /api/channels/weixin
POST   /api/channels/weixin/binding-attempts
GET    /api/channels/weixin/binding-attempts/{attempt_id}
DELETE /api/channels/weixin/binding-attempts/{attempt_id}
DELETE /api/channels/weixin/binding
POST   /api/channels/weixin/reconnect
```

所有 attempt 在服务端绑定当前 `actor.user_id`，URL 中不接受 user id。第一版用 Web 轮询查询状态，不为二维码状态另建公网 WebSocket。

### 7.2 状态流

```text
unbound
  -> creating_qr
  -> waiting_scan
  -> scanned_pending_confirm（如果上游要求确认/验证码）
  -> connected
  -> persist credential + account ownership + external identity
  -> start account long-poll
  -> active
```

失败状态包括 `expired`、`cancelled`、`already_bound`、`account_conflict`、`verification_failed`、`upstream_unavailable` 和 `persist_failed`。

固定规则：

1. 一个用户同一时刻最多一个 active attempt；重复点击返回原 attempt。
2. 用户已有 active binding 时拒绝新扫码，先显式解绑。
3. sidecar 返回的扫码用户必须与最终入站允许用户一致。
4. finalize 使用数据库事务和唯一约束处理并发；不能“最后写入覆盖”。
5. credential 文件先写临时文件，数据库提交成功后原子 rename；任一步失败都清理临时文件并停止新账号。
6. 二维码过期后 attempt 终止；用户重新点击生成新 attempt，不在 UI 无限自动刷新。
7. Web logout 不自动解绑已建立账号；未完成 attempt 可以取消。

### 7.3 解绑

解绑顺序：

1. 将账号标记为 disabled，立即拒绝新入站；
2. sidecar 停止该账号长轮询并通知上游；
3. 删除 `external_identities` 映射和当前账号所有权；
4. 删除 credential 文件和同步游标；
5. 保留已有 Session、Session metadata 和 Memory。

如果 credential 删除失败，不恢复消息处理；记录 `cleanup_pending`，只向 Owner/有权限诊断出口暴露安全原因。普通用户看到“已停止连接，凭证清理待管理员处理”。

## 8. sidecar 协议

### 8.1 Python -> Node

```text
hello
binding.start
binding.cancel
account.start
account.stop
message.send
typing.set
health.get
shutdown
```

### 8.2 Node -> Python

```text
hello.ok
binding.qr
binding.status
binding.connected
binding.failed
message.received
message.send_result
account.status
health.status
protocol.error
```

`binding.connected` 只把 token 交给 Python 的专用 credential writer；不得进入通用 event bus。`message.received` 只包含 allowlist 后的消息字段和 opaque `context_token_ref`。sidecar 自己维护真实 `context_token`，Python 回复时只回传 ref，避免 token 扩散到 Agent、Session 或 trace。

### 8.3 长轮询游标与去重

每个账号独立维护 `get_updates_buf`：

1. sidecar 拉取一批消息；
2. 为每条消息生成稳定 event id 并发给 Python；
3. Python 完成 `ChannelDedupService.claim(...)` 后返回 accepted/duplicate/rejected ACK；
4. 全批消息都得到确定 ACK 后，sidecar 原子保存新游标；
5. sidecar 崩溃造成重投时，由持久 receipt 去重。

当前 receipt 在 claim 后即永久阻止重复执行，因此进程恰好在 claim 后、Turn 完成前崩溃时仍可能留下 `processing` receipt。Part 14 实现二不在微信 Adapter 内私建任务队列；需要共享 durable inbox 时应另做通用 Channel Runtime 设计。

## 9. 微信入站与出站

### 9.1 入站归一化

第一版只接受 direct text，并转换为：

```text
channel                  = weixin
account_key              = 内部 opaque account key
conversation_type        = c2c
external_conversation_id = 微信发送者 id
external_user_id         = 微信发送者 id
event_id/message_id      = 上游稳定消息标识
safe_metadata            = 仅保留协议版本、消息状态等非敏感 allowlist
```

进入 Agent 前依次执行：账号 active、发送者等于绑定用户、持久去重、限流、identity resolve、conversation route 和 per-conversation serialization。任一失败都不能触发 LLM 或 Tool。

### 9.2 Capability

第一版诚实声明：

```text
text=true
markdown=false
text_streaming=false
message_edit=false
reply_quote=false
inbound_media=[]
outbound_media=[]
interactions=false
typing_indicator=true
can_close_conversation=false
command_profile=weixin_c2c
```

官方包虽已有媒体实现，第一版仍不开放，先避免 CDN、文件类型、SSRF、解密和 workspace 文件授权同时进入首个闭环。

### 9.3 出站

- ZhiCe-Agent RuntimeEvent 在 Python 侧聚合为最终文本；
- Markdown 使用现有共享 plain-text renderer 转换；
- 每块不超过当前上游声明的 4000 字符，并按段落边界切分；
- 发送每块都带正确 account、peer 和 sidecar 保存的 context token；
- typing 可在 Turn 开始/结束发送，失败只降级，不影响 Turn；
- 任一发送失败不得重新执行 Agent Turn；
- 第一版不把 tool progress 单独刷成多条微信消息。

## 10. Session、Memory 和权限

- 微信私聊使用现有 `ChannelConversationService` 创建 `weixin_<uuid>` Session；
- Web 和微信共享同一个内部 `user_id`，所以用户级 Memory 和基础能力一致；
- Web Session 与微信 Session 不直接合并，避免两个入口并发写同一 JSONL；
- Web 可查看并继续本人微信私聊 Session，沿用 Part 14 的私有跨渠道规则；
- `/new`、`/clear`、`/model`、`/memory`、`/stop` 和文本 `/confirm` 复用共享命令语义，只按 `weixin_c2c` capability 展示；
- Tool、Skill、MCP、Subagent 继续走当前 actor RBAC、确认、Hook、workspace guard 和审计；
- 微信账号归属不产生新角色，不改变 Owner/admin/user 含义。

## 11. 生命周期与局部降级

`zcagent gateway` 仅在 `channels.yml` 显式启用微信时检查：

- Node.js `>=22`；
- sidecar 构建产物和 lockfile；
- vendored upstream manifest、integrity 与 LICENSE；
- credential/state 目录可用；
- sidecar protocol version 匹配。

Gateway 启动后加载 active accounts 并逐个启动长轮询。单账号 token 失效只把该账号标记为 `reconnect_required`；sidecar 整体不可用时 `weixin` capability 为 unavailable。两种情况都不能阻断 Web/CLI/QQ。

同一个 workspace 同一时刻只允许一个 Gateway 持有微信 sidecar lease，避免两个进程消费同一 token。第一版使用 workspace 进程锁；不承诺跨机器共享。

## 12. 配置草案

仓库模板只包含非 Secret：

```yaml
channels:
  weixin:
    enabled: false
    transport: sidecar_stdio
    node_path: node
    sidecar_entry: integrations/weixin_sidecar/dist/main.js
    binding_timeout_seconds: 480
    max_parallel_conversations: 8
    text_chunk_limit: 4000
```

账号不写入 `channels.yml`，真实 token 不允许使用环境变量数组或仓库配置维护。账号由 Web 扫码流程动态创建，凭证位于 workspace runtime config。

## 13. 预计变更文件

新增：

- `docs_design/zhice-agent-part14-external-channel-design.md`（修改现有唯一 Part 14 活文档）
- `integrations/weixin_sidecar/package.json`
- `integrations/weixin_sidecar/package-lock.json`
- `integrations/weixin_sidecar/src/*`
- `integrations/weixin_sidecar/vendor/upstream-manifest.json`
- `integrations/weixin_sidecar/LICENSES/*`
- `agent/channels/weixin/__init__.py`
- `agent/channels/weixin/adapter.py`
- `agent/channels/weixin/sidecar.py`
- `agent/channels/weixin/normalize.py`
- `agent/channels/weixin/outbound.py`
- `agent/channels/weixin/binding.py`
- `agent/app/api/weixin.py`
- `tests/unit_test/weixin_channel/test_case.md`
- Python 与 Node 专项测试。

修改：

- `agent/auth/schema.py`、`agent/auth/store.py`：channel account ownership；
- `agent/channels/startup.py`、`agent/channels/manager.py`：sidecar lifecycle/status；
- `agent/app/gateway.py`、app builder：绑定 API 与生命周期装配；
- `agent/app/api/routes.py`：本人微信接口；
- `web/static/*`：个人设置里的微信连接、二维码、状态和解绑；
- `config/channels.example.yml`：微信非 Secret 配置；
- `README.md`、总体设计和 Part 14/15 活文档。

实际编码前应再次按当前目录核对，避免为了设计文件名机械拆分已有模块。

## 14. 实施顺序

### 阶段 A：Transport POC

1. 锁定官方 npm tarball 版本、integrity、LICENSE 和最小来源文件。
2. 单用户扫码，确认微信客户端出现带 AI 标识的 direct 会话。
3. 验证 `getUpdates -> sendMessage` 文本回声、context token、停止通知和重启恢复。
4. 验证不启动 OpenClaw Agent Runtime 也能合法、稳定地使用 Transport 来源实现。

阶段 A 不修改 AgentLoop，也不先做完整 Web UI。若官方服务或条款不允许该宿主方式，停止实施，不降级为个人微信自动化。

### 阶段 B：单用户 ZhiCe 闭环

1. 完成 stdio protocol、Python sidecar client 和 WeixinClawAdapter。
2. 用已有内部用户预置测试绑定，跑通文本 Turn、Session、Memory 和 Tool 安全链。
3. 验证 Gateway restart、token stale、sidecar crash 和 duplicate event。

### 阶段 C：Web 一人一个绑定

1. 增加 channel account schema/store。
2. 增加绑定 attempt API 和个人设置 UI。
3. 完成扫码、冲突、过期、取消、解绑和 reconnect 状态。
4. 用两个真实 Web 用户和两个微信号验证并发隔离。

### 阶段 D：收口

1. health、trace、activity、audit 和脱敏检查。
2. Node/Python 契约测试、Ruff、pytest 和前端语法检查。
3. 同步 README、总体设计、Part 14 活文档和运行说明。

## 15. 测试方案

### Python 单元测试

- Fake sidecar 正常启动、超时、协议损坏、崩溃和退避；
- 未绑定、账号 disabled、发送者不匹配均在 LLM 前拒绝；
- account/user/external id 唯一约束和并发 finalize；
- 两用户 Actor、Session、Memory 路径和模型偏好隔离；
- receipt ACK、重投、重复消息和游标边界；
- 文本渲染、4000 字符分块、部分发送失败不重跑 Turn；
- QR/token/context token/完整外部 ID 不进入日志和 API；
- 解绑保留历史并停止新消息。

### Node 单元测试

- QR start/status/expire/cancel/verify 状态；
- 多账号独立 long-poll、cursor 和 AbortController；
- getUpdates 返回码、token stale、网络退避和 stop/start；
- sendMessage account/peer/context token 选择；
- stdout 仅 NDJSON、stderr 脱敏；
- upstream manifest 与 tarball integrity 校验。

### 契约与 E2E

- Python/Node protocol golden fixtures；
- sidecar 真进程 smoke test，不连接微信；
- 真实微信 E2E 仅在 `ZHICE_AGENT_WEIXIN_E2E=1` 且显式提供隔离 workspace 时运行；
- 真实 E2E 必须从 Web 登录扫码开始，经过 sidecar、WeixinClawAdapter、ChannelChatRuntime、AgentLoop，再回到微信客户端。

## 16. 验收标准

1. 两个 ZhiCe Web 用户能分别扫码连接各自微信 AI，不能互相覆盖或读取凭证。
2. 微信客户端真实显示 AI 标识和 direct 会话体验。
3. 两个微信号同时发消息时进入正确 Actor、Session、Memory 和权限边界。
4. 未绑定、错误发送者、重复事件和 disabled account 不触发 LLM/Tool。
5. Web 与微信共享用户级 Memory，但不并发写同一 Session。
6. 微信文本回复正确分块；发送失败不重复执行 Agent Turn。
7. 二维码、bot token、context token 和完整微信 ID 不出现在普通日志、trace、audit、Session 和浏览器 URL。
8. sidecar 或单账号失败不影响 Web、CLI 和 QQ。
9. Gateway 重启后 active accounts 能恢复长轮询；同 workspace 双 Gateway 被 lease 阻止。
10. 解绑立即停止新消息、删除 credential，并保留历史 Session/Memory。
11. 不存在 OpenClaw AgentLoop 与 ZhiCe-Agent AgentLoop 双重执行。
12. 上游来源版本、integrity、LICENSE、补丁清单和真实服务条款核对结果可追溯。
13. 新模块配正常、异常和边界单测，并维护 `test_case.md`。
14. `python -m ruff check .`、`python -m pytest` 和 sidecar Node 测试通过。

## 17. 参考资料

- npm：[`@tencent-weixin/openclaw-weixin`](https://www.npmjs.com/package/@tencent-weixin/openclaw-weixin)，核对版本 `2.4.6`。
- npm：[`@tencent-weixin/openclaw-weixin-cli`](https://www.npmjs.com/package/@tencent-weixin/openclaw-weixin-cli)，核对版本 `2.1.4`。
- 当前 npm tarball 内 `README.zh_CN.md`、`package.json`、`src/auth/login-qr.ts`、`src/api/api.ts`、`src/monitor/monitor.ts`、`src/messaging/process-message.ts` 和 `src/channel.ts`。

## 18. 实施结果与真实 POC

2026-07-24 实时执行 `npm view` 与 `npm pack`，确认官方包版本 `2.4.6`、MIT、Node `>=22`、tarball shasum `c7744c5b2d0232703c886b2f4e71681b0170695d`、integrity `sha512-qw9k3PLTiMWGNjjsknHgcTManH1w4j+Ji1ArWIaYLKCq3aFRsVwcqnPi127bvOoVMJGW4dbyJ8NECEMgoO+iRw==`。审计再次确认根入口只注册 OpenClaw Channel，官方 monitor 直接需要 OpenClaw `channelRuntime`。来源、LICENSE、审计文件和 POC 状态保存在 `integrations/weixin_sidecar/vendor/upstream-manifest.json` 与 `integrations/weixin_sidecar/LICENSES/`。

已落地：

- Python stdio NDJSON client、protocol version/frame/pending/timeout 边界；
- Node 共享 sidecar、基于腾讯 `2.4.6` 审计来源的默认 Transport driver 和多账号隔离测试；
- `channel_accounts` 所有权与四组唯一约束、workspace credential writer；
- 本人状态、扫码 attempt、查询、取消、解绑 API 与 `no-store`；
- Web Account settings 扫码/状态/取消/解绑 UI；
- direct text normalize、账号/发送者前置拒绝、receipt ACK、限流、identity、conversation route、per-conversation 串行、共享 Runtime、typing 降级和 4000 字符纯文本分块；
- Python/Node 正常、异常和边界测试，以及 `tests/unit_test/weixin_channel/test_case.md`。

真实 POC 已确认手机端 AI 标识、二维码授权、`getUpdates` direct text 入站、带 context token 的 `sendMessage` 出站、插件 stop/start、sync cursor/context token 恢复和 `notifyStop`。期间观察到 `getUpdates` TCP connect timeout 后自动恢复；官方 OpenClaw logger 会输出完整平台账号标识，因此 ZhiCe vendored logger 已改为 stderr-only 强脱敏。官方 README 只说明手机授权，没有单独链接在线服务条款。双真实微信账号并发仍待第二名用户验收；不得依赖 npm 深路径或切换到个人微信自动化。
