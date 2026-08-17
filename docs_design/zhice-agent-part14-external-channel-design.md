# 智策 Agent 第十四部分详细设计文档：外部渠道与 QQ 机器人接入

> 状态：QQ 实现一与微信 ClawBot 实现二已进入当前代码基线；微信单账号真实 POC 已通过，双真实账号并发待验收
>
> 日期设计记录：`docs_design/2026-07-23-qq-external-channel-boundary-design.md`、`docs_design/2026-07-23-cross-channel-session-binding-and-qq-markdown-design.md`、`docs_design/2026-07-24-qq-binding-keyboard-rendering-fix.md`、`docs_design/2026-07-24-qq-group-manual-binding-design.md`、`docs_design/2026-07-24-clear-session-command-rename-design.md`、`docs_design/2026-07-24-qq-group-reply-attribution-design.md`、`docs_design/2026-07-24-qq-group-markdown-reference-compatibility-fix.md`、`docs_design/2026-07-24-plain-text-presentation-and-qq-reply-sequence-design.md`、`docs_design/2026-07-24-qq-outbound-delivery-confirmation-design.md`、`docs_design/2026-07-24-weixin-clawbot-channel-design.md`、`docs_design/2026-07-25-weixin-qr-rendering-and-cancel-fix.md`、`docs_design/2026-08-08-channel-capability-aggregation-design.md`
>
> 承接文档：`docs_design/zhice-agent-part13-subagent-design.md`
>
> 身份与 Session 基线：`docs_design/zhice-agent-part9-user-auth-permission-design.md`

## 1. 背景

Part 14 启动时，ZhiCe-Agent 已具备 CLI、Web、REST/SSE、WebSocket、用户身份、权限、Session、Turn、RuntimeEvent、确认、Memory、MCP、Hook 和 Subagent，但尚无真实 IM / 协作平台适配器。当前 QQ 官方 Python SDK Adapter 与微信 ClawBot Node sidecar 均已进入代码基线。

Part 14 选择 QQ 机器人作为第一条真实外部渠道，原因是：

- QQ 官方已提供 Agent 接入、扫码连接器、Python Bot SDK 和较完整的 Node.js 协议 SDK；
- QQ 同时覆盖私聊、群聊、引用、文件、图片、按钮交互和私聊流式回复，能够检验渠道抽象是否足够完整；
- QQ 官方运行 SDK已经体现 WebSocket / Webhook 双传输、中间件、去重、限流、并发保护、引用解析和出站分块等成熟做法；
- 微信 ClawBot 实现二的事件格式和授权方式不同，但仍需要身份映射、会话路由、命令能力裁剪、回复目标、限流、重试和审计。

Part 14 的重点不是把 QQ SDK 直接塞进现有 Web 代码，而是先建立一个轻量、可复用、与 AgentLoop 解耦的外部渠道边界，再实现 QQ Adapter。

## 2. 当前代码基线

当前代码已经具备以下可复用能力：

- `agent/app/runtime.py` 的 `WebRuntime.run_chat_events()` 已经统一处理消息、slash command、Turn、停止、模型偏好、Memory、Tool、Subagent 和 RuntimeEvent；
- `ActorContext`、`ToolExecutionContext` 已经携带 actor、session、turn、channel 和 request 关联信息；
- `external_identities` 已经能把 `(channel, external_tenant_id, external_user_id)` 映射到内部用户；
- `SessionAccessService` 已经按内部 `user_id` 解析用户目录、JSONL Session 和 `session_index`；
- `session_index` 已经预留 `channel`、`external_chat_id` 和 `external_thread_id`；
- active turn 已按 `(actor, session_id)` 隔离，其他普通用户不能停止不属于自己的 Turn；
- Tool 仍经过 RBAC、确认、Hook、workspace guard、timeout、脱敏、SSRF 和审计；
- Web 与 external WS 已证明：命令语义应统一放在共享 runtime，差异由声明的客户端能力决定，不能只按 transport 判断。

当前缺口：

- 没有渠道中性入站事件和出站目标协议；
- 没有渠道账号、外部会话到内部 Session 的稳定路由；
- 没有外部身份自助绑定流程；
- 没有消息去重、渠道级限流和每会话串行调度；
- 没有 QQ SDK 生命周期、消息类型、群聊触发和发送限制适配；
- `WebRuntime` 虽已承担共享聊天语义，但名称和公开接口仍偏 Web，外部渠道不应直接依赖 HTTP / WebSocket route 细节。

## 3. 目标

Part 14 完成后，应满足：

1. QQ 私聊用户能够绑定内部 ZhiCe-Agent 用户并进行连续对话。
2. QQ 群聊默认只有 `@机器人` 才触发，并按触发用户隔离内部 Session。
3. QQ 消息继续复用现有 AgentLoop、Session、Turn、模型偏好、Memory、Tool、Hook、Subagent 和审计链。
4. slash command 只有一套语义实现，QQ 仅按私聊/群聊能力 Profile 裁剪可用命令和展示方式。
5. QQ 私聊可以使用平台支持的流式回复；群聊和不支持流式的平台使用节流后的分块发送。
6. 文本、引用、图片和文件先转换为中性消息，再进入运行链；平台原始 payload 不进入 AgentLoop。
7. 未绑定身份、重复事件、回声消息、超限消息和无权限消息不会触发 LLM 或 Tool。
8. QQ SDK、凭证、重连、限流和平台错误全部留在 app shell / channel adapter 层。
9. 微信 ClawBot 实现二不修改 AgentLoop，不复制身份、Session、命令和 Tool 安全系统。

## 4. 范围边界

### 4.1 本阶段包含

- 外部渠道协议、能力声明、Manager 和生命周期；
- QQ 账号配置、启动、停止、健康状态和重连状态接入；
- QQ 私聊和群聊 `@` 文本消息；
- 外部身份绑定与解绑的底层能力；
- 外部会话到内部 Session 的持久化路由；
- 引用消息、图片和文件的安全接收与中性描述；
- 私聊流式、群聊分块、Markdown 兼容和发送失败降级；
- `/help`、`/new`、`/model`、`/sessions`、`/stop` 等现有命令的能力裁剪；
- 私聊 Tool 确认交互；
- 去重、限流、并发保护、trace、Runtime Activity、Security Audit 和测试。

### 4.2 QQ 实现一不包含

- 微信 ClawBot 的具体 Transport、扫码和账号所有权实现；这些归入本 Part 的实现二；
- 把 QQ 群变成多个内部用户共同拥有的共享 Session；
- 未绑定 QQ 用户自动注册内部账号；
- QQ 频道 API 的任意模型直通 Tool；
- 让模型直接接触 AppSecret、access token、原始 webhook 签名或完整平台 payload；
- 公网反向代理、证书、容器和生产发布清单；这些仍属于 Part 17；
- 跨进程 active turn、共享队列和多实例 leader election；
- 语音识别、视频理解和通用多模态模型改造；
- 为每个平台重新实现一套 slash command 或 Tool policy。

## 5. QQ 官方能力与本设计取舍

### 5.1 官方接入事实

QQ Agent 接入文档和官方 SDK 当前提供：

- 通过 AppID / AppSecret 连接 QQ 机器人；
- `@tencent-connect/qqbot-connector` 扫码连接器，扫码成功返回凭据数组；当前通常为一个机器人，但调用方应按数组处理；
- `qq-botpy` 官方 Python SDK；
- `@tencent-connect/qqbot-nodejs` 协议 SDK，支持 HTTP REST、WebSocket / Webhook、消息与媒体、交互事件和 C2C 流式消息；
- WebSocket 长连接、心跳和恢复；Webhook 使用签名验证；
- 私聊、群聊、频道和私信等消息事件；
- 回声过滤、去重、限流、同用户/群并发保护、`@` 门禁、引用解析、附件和出站分块等参考实现；
- QQ 私聊支持 `stream_messages`，群聊不能假设支持同样的流式协议；
- 官方安全建议要求保护凭证、严格限制调用者、谨慎安装插件/Skill、检查异常调用日志并准备凭证重置和禁用流程。

### 5.2 第一实现选择

ZhiCe-Agent 是 Python 项目，第一实现采用：

```text
QQ runtime transport: qq-botpy optional dependency + WebSocket gateway
QQ provisioning: AppID/AppSecret 手动或环境变量配置
QR provisioning: 可选能力，后续通过官方 qqbot-connector 接入
Webhook transport: 保留 QQTransport 协议，待 Part 17 公网部署条件具备后启用
```

理由：

- 保持主运行时为 Python，不要求用户为了收发 QQ 消息常驻 Node sidecar；
- WebSocket 更适合当前本地单进程阶段，不要求先完成公网回调和证书；
- QQ SDK 被隐藏在 `QQTransport` 后面，将来切换 Webhook 或官方 Node bridge 不改变渠道上层和 Agent 内核；
- 扫码连接是配网 UX，不应与消息运行时强绑定。没有 Node.js 时仍可通过开放平台创建机器人并配置凭据。

## 6. 总体架构

```text
QQ Open Platform
  -> QQTransport (qq-botpy, WebSocket)
  -> QQChannelAdapter
       -> inbound normalize / dedup / access gate / attachment guard
       -> ExternalIdentityService
       -> ChannelConversationService
       -> ChannelChatRuntime protocol
            -> existing shared command + session + turn semantics
            -> AgentLoop
                 -> LLM / Tool / Skill / MCP / Memory / Subagent
       <- RuntimeEvent / final result / confirmation state
       <- QQOutboundRenderer
  <- QQ text / stream / media / interaction response
```

后续渠道：

```text
WeChatAdapter ─┐
FeishuAdapter ─┼─> ChannelManager -> ChannelChatRuntime -> AgentLoop
QQAdapter ─────┘
```

AgentLoop 只看到内部用户消息、actor、session、turn 和 ToolExecutionContext，不 import QQ 或微信 SDK。

## 7. 依赖方向

保持：

```text
cli/app/channels -> agent core -> protocols
tools            -> protocols/message/base types
skills           -> no agent imports
```

新增约束：

- `agent/protocols/channel.py` 只放中性数据结构和 Protocol，不 import QQ SDK；
- `agent/channels/` 属于 app shell，不进入 `agent/core/`；
- `agent/channels/qq/` 可以 import `botpy`，其它目录不能依赖 QQ 类型；
- QQ 原始 payload 只允许停留在 transport / adapter 内部；
- 渠道 Adapter 不能直接调用 LLMProvider 或 ToolProvider；
- 渠道 Adapter 只能通过 `ChannelChatRuntime` 请求聊天、停止和确认恢复。

## 8. 中性渠道协议

### 8.1 ChannelCapabilities

每个 Adapter 和每种会话场景显式声明能力，不能用“是不是 WebSocket”推断：

```python
@dataclass(frozen=True)
class ChannelCapabilities:
    text: bool = True
    markdown: bool = False
    text_streaming: bool = False
    message_edit: bool = False
    reply_quote: bool = False
    inbound_media: frozenset[str] = frozenset()
    outbound_media: frozenset[str] = frozenset()
    interactions: bool = False
    typing_indicator: bool = False
    can_close_conversation: bool = False
    command_profile: str = "external"
```

QQ 至少区分：

- `qq_c2c`：私聊、可流式、可确认、可引用；
- `qq_group`：群聊、默认需 `@`、非 token 流式、受限确认；
- 未来 `qq_guild` / `qq_dm`：只有真实实现后才注册，不能因为 SDK 有类型就虚假声明可用。

### 8.2 InboundChannelEvent

```python
@dataclass(frozen=True)
class InboundChannelEvent:
    channel: str
    account_key: str
    event_id: str
    message_id: str
    event_type: str
    conversation_type: str
    external_conversation_id: str
    external_thread_id: str
    external_user_id: str
    external_display_name: str
    text: str
    quote: ChannelQuote | None
    attachments: tuple[ChannelAttachment, ...]
    reply_target: ChannelReplyTarget
    occurred_at: str
    safe_metadata: dict[str, str]
```

规则：

- `safe_metadata` 只保留 allowlist 字段；
- 不保存 AppSecret、access token、签名、完整 header 或完整原始 payload；
- 外部 ID 不直接拼入文件路径和内部 session id；
- `event_id` / `message_id` 用于去重和审计关联，不作为授权依据；
- 文本、引用和附件是数据，不是系统指令。

### 8.3 ChannelReplyTarget

回复目标统一表达：

```python
@dataclass(frozen=True)
class ChannelReplyTarget:
    channel: str
    account_key: str
    conversation_type: str
    external_conversation_id: str
    external_thread_id: str = ""
    reply_to_message_id: str = ""
```

AgentLoop 不持有该对象。Adapter 在 Turn 外维护 `turn_id -> reply target`，防止平台目标污染 Session 消息。

### 8.4 ChannelChatRuntime

第一阶段不为了命名整洁大规模重写 `WebRuntime`。新增一个窄协议，由当前 application runtime 适配：

```python
class ChannelChatRuntime(Protocol):
    def run_chat_events(
        self,
        actor: ActorContext,
        session_id: str,
        message: str,
        *,
        turn_id: str,
        on_event: RuntimeEventCallback,
        command_profile: str,
        request_id: str,
        channel_context: ChannelExecutionContext,
    ) -> ChatTurnResult: ...

    def stop_turn(self, actor: ActorContext, session_id: str, turn_id: str | None = None) -> bool: ...
    def resume_confirmation(self, actor: ActorContext, confirmation_id: str, decision: str) -> object: ...
```

当前 `WebRuntime` 可以通过 adapter 或兼容实现满足该协议。等 Web、QQ 和其它渠道都稳定后，再单独决定是否正式重命名为 `ChatRuntime`，不在 Part 14 同时做无收益的大范围 rename。

### 8.5 ChannelExecutionContext

为避免把 `qq_group` 编码进普通字符串，渠道场景单独表达：

```python
@dataclass(frozen=True)
class ChannelExecutionContext:
    channel: str
    account_key: str
    conversation_type: str
    external_conversation_id: str
    external_thread_id: str = ""
    capabilities: ChannelCapabilities = ChannelCapabilities()
```

`ToolExecutionContext.channel` 继续保留稳定顶层值 `qq`。如果 Tool policy 需要区分私聊/群聊，应读取受控的 channel context，不从用户文本或 platform payload 猜测。

## 9. 配置与凭证

### 9.1 配置文件

运行态配置：

```text
${ZHICE_AGENT_WORKSPACE}/config/config.yml#channels
```

仓库只提交：

```text
config/config.example.yml 的 channels 分区
```

示例：

```yaml
channels:
  qq:
    enabled: false
    transport: websocket
    accounts:
      - key: main
        app_id: ${QQBOT_APP_ID}
        app_secret: ${QQBOT_APP_SECRET}
        web_base_url: http://127.0.0.1:10086
        c2c_enabled: true
        group_enabled: true
        group_require_mention: true
        max_parallel_conversations: 8
        max_attachment_bytes: 20971520
```

`web_base_url` 是账号级绑定入口。`http://127.0.0.1:10086` 只作为未显式配置时的本地开发默认值；公网启用 QQ 时，每个账号都必须在私有部署配置中显式填写与 `PublicUrl` 对齐的真实 HTTPS 地址。Adapter 不从公开模板推导该字段，也不改变本地默认语义。

### 9.2 Secret 规则

- 仓库禁止提交真实 AppID / AppSecret；
- Docker、CI、云部署优先使用环境变量或平台 Secret；
- 本地 workspace 不属于仓库，可按现有 `models.json` / `.env` 口径保存本地 Secret，但启动、日志、异常和诊断必须脱敏；
- Adapter 不把 secret 放入 dataclass repr、RuntimeEvent、trace metadata 或错误 message；
- 修改凭证采用原子写，旧文件保留最短必要备份且备份同样受保护；
- 发现泄露时支持禁用账号、重置凭证、清理 token cache 和重新启动连接。

### 9.3 可选扫码连接

后续提供：

```text
zcagent channels qq connect --account main
```

该命令使用官方 `@tencent-connect/qqbot-connector`：

- Node.js >= 18 只在扫码配网时需要，不是 QQ runtime 必需依赖；
- 始终按凭据数组处理，不假设永远只有一个结果；
- 二维码过期自动刷新，用户取消时必须停止轮询；
- 成功后只写入 runtime config / secret 注入目标，不在终端回显完整 AppSecret；
- 如果环境没有 Node.js，明确提示使用开放平台手动创建和环境变量配置，不影响消息运行。

## 10. 渠道账号与生命周期

`ChannelManager` 负责：

- 读取 `config.yml` 的 `channels` 分区；
- 只加载显式启用的 Adapter；
- 校验可选依赖和凭证引用；
- 启动、停止、优雅退出和 capability status；
- 按 account key 隔离连接、限流、事件去重和日志；
- 防止同一 account key 在一个进程内重复启动。

启动分级沿用 Part 13：

- 未配置 QQ：`disabled`，不报警；
- 显式启用但缺少 `qq-botpy`：QQ `unavailable`，Gateway 其它能力继续运行并输出结构化 warning；
- 显式启用但凭证非法：该 QQ account 启动失败，不泄露凭证；
- QQ 断线重连：状态为 `degraded/reconnecting`，不阻断 Web / CLI；
- 核心 workspace、Prompt、LLM 或 Auth 错误仍按原规则处理。

## 11. 身份绑定

### 11.1 两类绑定必须分开

```text
机器人账号连接：ZhiCe-Agent <-> QQ Bot AppID/AppSecret
用户身份绑定：QQ openid <-> ZhiCe-Agent internal user_id
```

扫码创建机器人不能自动证明消息发送者对应哪个内部用户。

### 11.2 未绑定用户

未绑定 QQ 用户只允许：

- 查看简短绑定帮助；
- 在私聊发送裸 `/bind` 获取一次性 Web 登录授权链接；
- 在私聊或群聊发送 `/bind <one-time-code>` 直接绑定当前消息发送者；
- 收到不泄露系统信息的错误提示。

禁止：

- 进入 LLM；
- 创建用户目录或 Session；
- 执行 Tool、Skill、MCP、Memory 或 Subagent；
- 在群聊完成绑定。

### 11.3 一次性绑定码

内部已登录用户从 Web 账号设置或 CLI 创建短期绑定码：

```text
zcagent channels link-code qq
```

聊天 `/help` 不增加一组渠道管理命令。绑定码：

- 只保存 hash；
- 默认 10 分钟过期；
- 单次使用；
- 绑定渠道和 account key；
- 可在 QQ 私聊或群聊提交，群聊提交意味着用户主动公开该短期凭据；
- 成功后写 `external_identities`；
- 重复、过期和跨账号使用均 fail closed；
- 创建、成功、失败、解绑写 Security Audit，但不记录明文码。

Web 个人设置只提供一个“生成 QQ 一次性绑定码”按钮，显示绑定码、过期时间和 `/bind <code>` 示例。第一版不增加已绑定渠道列表、机器人 account key、最后使用时间或管理员绑定管理页面；这些内部字段继续用于路由、安全和审计，但不暴露给普通用户。

当前个人设置在此基础上增加最小自助管理：只显示“QQ 已绑定”和可选安全展示名，并提供“解除绑定”。不显示 account key、完整 OpenID、最后使用时间或管理员内部字段。解绑只禁用当前用户自己的 external identity，不删除历史 Session、conversation route 或审计记录。

当前产品采用“一个内部账号只绑定一个 active QQ 身份”。数据库保留 disabled 历史记录，但通过应用事务检查和 partial unique index 双重保证每个 `user_id` 最多一条 active QQ identity；已绑定时再次绑定其它 QQ 必须先解绑，不允许静默顶替。历史重复 active 数据迁移时保留最新绑定并禁用其余记录。详细方案见 `docs_design/2026-08-08-single-qq-identity-binding-design.md`。

### 11.3.1 Web 登录授权链接

未绑定 QQ 用户在私聊发送裸 `/bind` 时：

1. Adapter 为当前 `(qq, account_key, external_user_id)` 创建一次性授权请求；
2. 通过 Markdown 超链接和“登录并绑定”URL 按钮返回移动优先的 `${web_base_url}/bind/qq?token=<opaque-token>` 独立绑定页；旧 `${web_base_url}/?channel_bind=<opaque-token>` 由首页兼容重定向；
3. Web 未登录时先完成现有用户名/密码登录，并保留该 token；
4. 独立绑定页持有 token，登录或新注册成功后监听认证状态自动消费授权请求，把 QQ identity 绑定到当前内部 `user_id`，不依赖认证子组件的一次事件；
5. 已登录用户打开链接时直接完成绑定；
6. token 只保存 hash，默认 10 分钟过期、单次消费，URL 不包含 OpenID、AppID 或内部用户 id。

生产部署必须保证生成链接中的 `web_base_url` 是 QQ 用户设备可访问的公网 HTTPS origin。若云端账号遗漏该字段，配置加载会回退到本地 loopback 默认值，链接虽然格式正确却无法从手机访问。当前修复保持 Adapter 直接消费账号配置，仅在云端私有账号配置中显式设置公网地址；临时 Tunnel 与后续正式域名的切换都只修改私有配置。详细记录见 `docs_design/2026-08-04-qq-public-binding-url-deployment-fix.md`。

移动端绑定闭环采用独立 `/bind/qq` 页面持有 token、认证状态变化后自动消费的流程；成功与失败均原地给出任务结果，失败时保留大号重试入口。手机设置页继续使用单列全屏布局，绑定输入与按钮上下两行，详见 `docs_design/2026-08-08-mobile-channel-binding-ux-design.md`。

QQ 提示语义固定为：

```text
/bind          -> 获取网页授权链接
/bind <code>   -> 使用 Web 生成的一次性绑定码直接绑定
```

群聊只开放 `/bind <code>` 手动绑定当前消息发送者；群聊裸 `/bind` 不创建或返回 Web 授权链接，而是引导用户转到机器人私聊。手动绑定仍在 Adapter 前置消费，不进入 AgentLoop，且回复不得回显绑定码。

普通未绑定消息附带“绑定”指令按钮；按钮由 QQ 客户端自动发送 `/bind`，避免手机用户手工输入命令，同时仍显示 `/bind` 和 `/bind <code>` 作为兼容入口。指令按钮只触发既有 Adapter 前置命令，不进入 AgentLoop。

授权回复使用 QQ 自定义 Markdown，不依赖平台 Markdown 模板；同时附带 URL 跳转按钮。Markdown/Keyboard 发送失败时，Transport 使用同一个已生成授权 URL 降级为纯文本，不重新创建授权请求、不重复消费 token，也不重新执行 Agent Turn。

### 11.4 external identity 字段

QQ 映射建议：

```text
channel = qq
external_tenant_id = account_key
external_user_id = QQ Open Platform 提供的用户 OpenID
external_display_name = 安全截断后的展示名
metadata_json = conversation capabilities 等非敏感 allowlist 信息
```

不使用 QQ 昵称作为身份，不把 AppID 当作 secret 记录到每条 trace，也不把 raw OpenID 放入文件路径。

## 12. 会话路由与群聊边界

跨渠道 Session 采用“账号统一、历史可见、会话有来源、继续受限制”：Web/CLI 作为私有控制面，可以查看当前内部用户的 Web、CLI、QQ 私聊和 QQ 群聊历史；外部渠道只使用当前 conversation route，不能反向浏览或管理其它渠道 Session。QQ 私聊 Session 可在 Web/CLI 继续；QQ 群聊 Session 在 Web 只读，只能复制历史派生新的 Web Session，防止 Web 私密上下文随后进入公开群回复。Session 所有者仍可在 Web 删除 QQ/微信历史；删除同步移除当前 route，下一条渠道消息创建全新 Session，账号绑定、receipt、审计与平台侧消息保持不变。

### 12.1 新表 channel_conversations

仅依靠 `session_index.external_chat_id` 不足以支持多账号、重新 `/new` 和未来多渠道。新增：

```sql
CREATE TABLE channel_conversations (
  id TEXT PRIMARY KEY,
  channel TEXT NOT NULL,
  account_key TEXT NOT NULL,
  conversation_type TEXT NOT NULL,
  external_conversation_id TEXT NOT NULL,
  external_thread_id TEXT NOT NULL DEFAULT '',
  owner_user_id TEXT NOT NULL REFERENCES users(id),
  current_session_id TEXT NOT NULL REFERENCES session_index(session_id),
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  UNIQUE(
    channel,
    account_key,
    conversation_type,
    external_conversation_id,
    external_thread_id,
    owner_user_id
  )
);
```

内部 Session ID 使用随机稳定 ID，例如 `qq_<uuid>`，不拼接 OpenID、群号或昵称。

### 12.2 私聊

私聊路由键：

```text
(qq, account_key, c2c, external_user_id, '', internal_user_id)
```

同一 QQ 用户与同一机器人默认进入当前映射 Session。`/new` 创建新 Session 并原子替换 `current_session_id`；旧 Session 仍保留在用户 Session 列表。

### 12.3 群聊

第一阶段不创建“群共同所有”的共享 Session。群聊按触发用户隔离：

```text
(qq, account_key, group, external_group_id, '', triggering_internal_user_id)
```

结果：

- 同一群中不同已绑定用户拥有不同内部 Session；
- 不会把 A 的 Memory、模型偏好、Tool 结果和历史暴露给 B；
- 回复仍发到群里，因此用户应把群消息视为公开输出；
- 默认只响应 `@机器人`；未 `@`、机器人自身回声和系统消息直接忽略；
- 群历史不自动拼入某个用户的 Agent 上下文。

真正群共享 Session 需要成员可见性、加入/退出、共享文件、权限交集和审计规则，留给后续独立设计。

## 13. 入站处理流程

```text
QQ SDK event
  -> account lookup
  -> schema/type validation
  -> self-echo/system-event filter
  -> persistent dedup claim
  -> group mention gate
  -> sender/account/group rate limit
  -> external identity resolve
  -> command-only gate for unbound user
  -> conversation route -> internal session
  -> attachment guard/download
  -> neutral user content build
  -> per-conversation serialized dispatch
  -> ChannelChatRuntime.run_chat_events()
```

任何前置步骤失败，都不能先触发 LLM 再补救。

## 14. 消息内容、引用与附件

### 14.1 文本

- 移除 SDK 已确认的机器人 `@` marker；
- 保留普通用户文本，不执行其中的 XML/JSON/Markdown 指令；
- 空文本且没有附件时不创建 Turn；
- slash command 仍交给共享 runtime 解析，不在 QQ Adapter 复制实现。

### 14.2 引用

引用消息转换为 `ChannelQuote`：

- 引用 message id；
- 可安全获得时保留发送者展示名；
- 引用文本限制长度并标记为引用数据；
- 不因引用另一用户消息而获得另一用户的内部 Session 历史；
- 无法解析时保留“用户引用了一条不可读取消息”的中性提示。

### 14.3 入站附件

下载前执行：

- URL scheme allowlist；
- DNS / IP SSRF guard；
- 超时、最大字节数和 Content-Type allowlist；
- 文件名清洗和随机落盘名；
- 写入当前内部用户目录下 `files/channels/qq/`；
- account / conversation 仅使用内部安全 key 分目录；
- 下载失败只生成安全错误描述，不把异常堆栈发给模型或用户。

第一阶段 Agent 输入使用“文本 + 附件描述/本地引用”。只有当前 LLMProvider 明确支持对应多模态内容时，才在后续扩展为真正图片/音频输入。

## 15. 命令语义与能力裁剪

命令仍由共享 runtime 实现。QQ 只声明 Profile：

### 15.1 QQ 私聊 Profile

- `/help`：支持，输出紧凑命令列表；
- `/new`：支持，替换当前 conversation route；
- `/clear`：支持，清空当前 conversation route 指向的 Session 历史但保留 route；
- `/model`：支持当前 Session 模型偏好；
- `/sessions`：不支持跨渠道列表和管理，提示前往 Web；QQ 只维护当前 conversation route；
- `/stop`：支持停止当前 actor/current session active turn；
- `/history`：按 QQ 消息长度安全截断或分页；
- `/exit`：不支持，因为 QQ 会话没有可关闭的 socket 所有权语义；
- `/bind`：Adapter 前置命令；裸命令只在未绑定私聊创建 Web 授权链接，`/bind <code>` 可在私聊或群聊绑定当前发送者，均不进入通用 command registry。

### 15.2 QQ 群聊 Profile

- 默认只在 `@机器人` 后识别命令；
- 支持 `/help`、`/new`、`/clear`、`/stop` 和普通对话；
- `/model`、`/sessions`、`/history` 默认提示转私聊，避免在群里展示个人配置和历史；
- `/exit` 不支持；
- 具体裁剪由能力 Profile 表驱动，不在每个 handler 里写 `if channel == "qq"`。

## 16. Tool 确认与群聊安全

### 16.1 私聊确认

QQ interaction 事件可承载：

```text
允许一次 / 拒绝
```

确认 payload 必须绑定：

- confirmation id；
- internal user id；
- external QQ user id；
- account key；
- session id / turn id / tool call record id；
- 过期时间和单次消费状态。

只有发起者可以确认。按钮不可用时，可回退为带短 nonce 的私聊文本确认，但不能只解析模糊的“是/好/继续”。“始终允许”涉及持久权限变化，不在 Part 14 默认开放。

### 16.2 群聊确认

第一阶段 QQ 群聊不执行需要高风险确认的 Tool：

- 群消息是公开表面，不能公开完整命令、路径或敏感参数；
- Tool policy 在 `conversation_type=group` 时拒绝 confirmation-required / high-risk 操作；
- 用户收到“请转到私聊或 Web 完成该操作”的安全提示；
- safe/read-only Tool 仍按用户实际权限执行；
- 不能因为用户拥有 Web 权限就自动放宽群聊暴露面。

## 17. 出站渲染

### 17.1 RuntimeEvent 到渠道输出

QQ Adapter 消费已有 RuntimeEvent，但不把每个内部状态都发成消息：

- `turn.started`：私聊可发送 typing indicator；
- `llm.*` / `tool.*`：默认只更新本地状态，不刷屏；
- `text_delta`：私聊进入 stream controller，群聊进入 debounce buffer；
- `waiting_confirmation`：渲染安全确认卡片；
- `turn.completed`：完成 stream 或发送最终分块；
- `turn.failed/stopped`：发送稳定用户文案，不发送 stack trace。

### 17.2 私聊流式

如果当前 transport 支持 C2C stream：

- 单 Turn 只创建一个 stream；
- 按最小间隔合并 delta，避免每 token 请求；
- complete 前保证 Markdown code fence 闭合；
- stream 失败时降级为普通分块文本；
- 降级不能重新执行 Agent Turn；
- stop 时关闭或标记当前 stream，不继续发送迟到 delta。

如果 `qq-botpy` 当前版本不暴露稳定 stream API，第一实现使用 debounce + 普通消息；`ChannelCapabilities.text_streaming=false` 必须如实声明，不能模拟平台流式能力。

### 17.3 群聊和普通分块

- 不逐 token 发送；
- 按平台长度限制分块；
- 优先在段落、列表和代码块边界切分；
- Markdown 表格必要时转换为普通文本或多个块；
- QQ 群消息接口没有独立成员 mention 参数，自定义 Markdown 也没有群成员 `@` 语法；群聊回答统一通过 `message_reference` 引用触发者原消息建立可见归属，不拼接或暴露 member OpenID；
- 引用只用于第一条文本分块，后续块保持同一 Turn 关联；单条 Markdown 及其发送降级尝试保持同一引用；
- 同一入站消息的文本分块使用递增 `msg_seq`；群聊最多 5 块，单聊最多 4 块，超限时最后一块明确提示转私聊或 Web 查看完整内容；
- 发送重试必须有次数和总时限，不能无限重试。

普通 Agent 回复只有在 QQ 私聊、包含标题、列表、引用、代码、链接或强调等明确结构且能在单个安全块内发送时才使用 QQ 自定义 Markdown；普通短句和超长内容继续使用文本。真实 QQ 客户端未稳定展示群聊 Markdown 携带的 `message_reference`，所以群聊 Runtime 回答先经共享 Markdown-to-plain renderer 转为可读普通文本，再分块并在第一块引用触发消息。Markdown 发送失败使用相同内容降级为纯文本，不重新执行 Agent Turn。

### 17.4 绑定消息的 Markdown 与键盘

- 普通未绑定提示使用 `msg_type=2` 自定义 Markdown 并附带 Keyboard；真实 QQ 客户端对纯文本附带 Keyboard 不稳定，“绑定”指令按钮点击后自动发送 `/bind`；
- `/bind` 授权回复使用 `msg_type=2` 和自定义 Markdown，把授权地址渲染为“登录并绑定智策 Agent”超链接；
- 授权回复同时附带 URL 跳转按钮，手机端可直接打开 Web 登录页面；
- 按钮与 Markdown 使用 QQ Adapter 内部的 SDK 无关描述，`qq-botpy` payload 只在 Transport 组装；
- 富消息发送失败时只降级为纯文本提示或纯文本 URL；降级不重新调用 identity service，不重新执行 runtime；
- `/bind <code>` 的解析、一次性消费和成功/失败语义保持不变。

### 17.5 出站附件

只允许发送：

- 当前用户 workspace / files 边界内的文件；
- MCP ArtifactGateway 已导入并授权的 artifact；
- 明确 allowlist 的内存 buffer。

禁止根据模型输出的任意 URL 直接代表服务器下载并转发。需要远程获取时，先经过现有 SSRF、大小、类型和授权边界。

## 18. 去重、并发、限流与重试

### 18.1 持久去重

新增 `channel_event_receipts`：

```sql
CREATE TABLE channel_event_receipts (
  channel TEXT NOT NULL,
  account_key TEXT NOT NULL,
  event_id TEXT NOT NULL,
  message_id TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL,
  first_seen_at TEXT NOT NULL,
  finished_at TEXT,
  error_code TEXT NOT NULL DEFAULT '',
  PRIMARY KEY(channel, account_key, event_id)
);
```

流程先原子 claim，再执行 Turn。SDK 的内存去重只能作为第一层优化，不能替代持久去重，否则 webhook 重试或进程重启可能重复执行 Tool。

### 18.2 串行与并行

- 同一 `(account, conversation route, owner)` 串行处理；
- 不同会话受全局 bounded executor 控制并行；
- 同一 Session 有 active turn 时，普通新消息默认返回忙碌提示或按配置排队，不能并发写同一 JSONL；
- `/stop` 走高优先级控制路径，不被普通消息队列阻塞；
- QQ SDK asyncio callback 不直接运行同步 AgentLoop，使用受控 worker，并把 outbound coroutine 安全调回 event loop。

### 18.3 限流

至少四层：

- sender；
- conversation/group；
- account；
- global channel worker。

限流发生在 LLM 前。群聊异常高频、未知用户遍历绑定码、附件洪泛应产生安全事件和临时冷却。

### 18.4 重试

- gateway 重连由 transport 管理；
- 出站发送只对明确 retryable 的网络、限流和部分 5xx 做有限重试；
- 已确认发送成功或状态未知时避免盲目重复；
- inbound event 重投依靠 receipt 返回已有状态，不能重复跑 Agent；
- Tool、LLM 和 channel send 重试彼此独立，发送失败不能重新执行 Tool。

## 19. 安全设计

结合 QQ 官方安全使用建议，Part 14 固定：

1. 默认关闭 QQ 渠道，只有显式配置才启动。
2. 默认不允许所有 QQ 用户使用，必须绑定内部用户。
3. 群聊默认 `@` 触发，且高风险 Tool 不在群聊确认。
4. AppSecret、token、绑定码、签名和完整外部 ID 全链路脱敏。
5. 未绑定、禁用、限流、签名失败和非法附件在 LLM 前拒绝。
6. 渠道附件仍受 workspace、SSRF、大小、类型和路径边界约束。
7. Skill、MCP 和 Subagent 只继承内部 actor 已有能力，QQ 不提供提权入口。
8. QQ SDK 依赖作为可选 extra 固定兼容版本范围，升级先做专项验证。
9. 定期检查异常调用、连续绑定失败、陌生 account、发送激增和重连风暴。
10. 提供应急动作：禁用 account、停止 adapter、重置凭证、撤销 external identity、检查 trace/audit。

## 20. 可观测性

### 20.1 Capability status

`/api/health` 和终端状态增加：

```text
channel.qq = disabled | available | degraded | unavailable
```

多 account 时返回安全聚合和 account key，不返回 AppID/AppSecret。

### 20.2 Trace

渠道 trace 至少关联：

```text
request_id
channel=qq
account_key
conversation_type
safe external conversation hash
safe external user hash
session_id
turn_id
event_id hash
stage
status
error_code
duration_ms
```

不记录完整用户消息、完整附件 URL、secret、token 或签名。

QQ 出站发送额外区分：

- `channel.qq.send_start`：已经构造安全 payload，准备调用平台 API；
- `channel.qq.send_done`：QQ API 返回有效消息响应，只表示服务端确认接受；
- `channel.qq.send_failed`：SDK 或平台明确抛出错误；
- `channel.qq.send_unconfirmed`：botpy 返回 `None`，投递结果未知，receipt 必须为 error。

出站trace只记录平台ID的短hash、消息类型、`msg_seq`、字符数、引用/Keyboard标志和耗时。`channels.qq.accounts[].http_timeout_seconds`默认15秒并限制为1~60秒，避免受`qq-botpy`默认5秒上限影响。`send_unconfirmed`仍不自动重试同一`msg_id + msg_seq`，因为平台明确拒绝相同组合重复发送，超时后无法证明首次请求是否已接收；该异常在Adapter边界把receipt闭合为error，但不再冒泡到botpy并误把WebSocket渠道标记为degraded。

### 20.3 Runtime Activity 与 Security Audit

- 正常收到消息、开始 Turn、发送完成进入 Runtime Activity / trace；
- 身份绑定、解绑、权限拒绝、群聊高风险拒绝、绑定码爆破、凭证错误和管理员禁用进入 Security Audit；
- 平台断线、限流和发送失败属于运行诊断，除非存在明显安全含义，不滥写安全账本。

## 21. 目录与模块设计

```text
agent/
  protocols/
    channel.py
  channels/
    __init__.py
    config.py
    manager.py
    identity.py
    conversation.py
    dedup.py
    limits.py
    runtime_adapter.py
    qq/
      __init__.py
      adapter.py
      transport.py
      normalize.py
      outbound.py
      confirmation.py
      attachments.py
      startup.py
  app/
    runtime.py
  auth/
    schema.py
    store.py
  prompts/
    channel_attachment_context.md   # 仅在确有长文本进入 LLM messages 时增加
config/
  channels.example.yml
tests/
  unit_test/
    channels/
      test_case.md
      test_config.py
      test_manager.py
      test_identity.py
      test_conversation.py
      test_dedup.py
      qq/
        test_normalize.py
        test_outbound.py
        test_confirmation.py
        test_adapter.py
  integration/
    test_qq_channel_fake_gateway.py
```

`agent/channels/qq/transport.py` 是唯一直接接触 `botpy` 的位置。单元测试默认使用 FakeQQTransport，不访问真实 QQ。

## 22. 依赖与 CLI

### 22.1 可选依赖

```toml
[project.optional-dependencies]
qq = [
  "qq-botpy>=1.2.1,<2",
]
```

不把 QQ SDK 放入核心 dependencies，避免未启用渠道的用户承担额外依赖和启动风险。

### 22.2 CLI

顶层保持紧凑：

```text
zcagent channels
zcagent channels status
zcagent channels qq start
zcagent channels qq connect
zcagent channels link-code qq
zcagent channels unlink qq <user-or-identity>
```

具体子命令通过 `zcagent channels --help` 展示，不把所有渠道操作塞进聊天 `/help`。

Gateway 启动时自动启动显式 enabled 的渠道；独立 `start` 主要用于诊断和开发。长期运行仍建议由同一 app lifecycle 管理，避免 Gateway 与 QQ Adapter 各自构建不同的 Agent dependencies。

## 23. 实现顺序

当前第一版已完成阶段 A、阶段 B 的 QQ 私聊/群聊文本闭环，以及阶段 C 的持久去重、群聊高风险拒绝、基础附件 guard 和文本确认入口。阶段 D 扫码 connector、Webhook 与 QQ 原生 C2C stream 仍按原顺序留待后续；当前安装的 `qq-botpy 1.2.1` 公开接口稳定提供 `on_c2c_message_create`、`on_group_at_message_create` 和普通 `reply`，未把未验证的 stream 能力声明为可用。

### 阶段 A：中性渠道内核

1. 增加 `channel.py` 中性协议和 capabilities。
2. 增加 channel config、startup checker 和 Manager。
3. 增加 identity link token、conversation route 和 receipt schema/service。
4. 为现有 application runtime 增加窄 `ChannelChatRuntime` 适配和 channel context。
5. 建立 FakeChannelAdapter 测试，先证明不依赖 QQ 也能完成身份、Session、Turn 和命令链。

### 阶段 B：QQ 文本闭环

1. 增加 `qq` optional dependency 和 `QQTransport`。
2. 接入 WebSocket ready/error/reconnect/message 生命周期。
3. 完成私聊绑定、文本、`/new`、`/stop` 和连续 Session。
4. 完成群聊 `@`、per-user group session 和群聊命令裁剪。
5. 完成 outbound debounce / chunk 和失败降级。

### 阶段 C：安全交互与附件

1. 接入引用消息。
2. 接入图片/文件安全下载和中性附件描述。
3. 接入私聊确认按钮与 initiator 校验。
4. 接入群聊高风险拒绝和安全提示。
5. 完成 persistent dedup、四层限流、异常调用审计。

### 阶段 D：配网与传输兼容

1. 增加可选官方扫码 connector。
2. 验证凭据数组、取消和二维码过期刷新。
3. 在 Part 17 公网条件具备后增加 Webhook transport，实现签名验证和同一 Adapter 上层复用。
4. 评估 C2C 原生 stream；不可用时继续诚实声明 debounce 能力。

## 24. 测试方案

新增或扩展 `tests/unit_test/channels` 时，同目录维护 `test_case.md`。

### 24.1 正常路径

- 已绑定私聊文本进入正确内部用户和 Session；
- 同一用户连续消息复用 conversation route；
- `/new` 原子切换 current session，旧 Session 仍可列出；
- 两个群用户在同一群得到不同内部 Session；
- 群聊 `@` 后触发，未 `@` 不触发；
- `/stop` 只能停止当前 actor 的 Turn；
- RuntimeEvent 被正确渲染为私聊 stream/debounce 或群聊分块；
- QQ adapter 与 Web/CLI 使用同一个模型偏好和 Tool 安全链。

### 24.2 异常路径

- SDK 未安装、凭证缺失、凭证非法、断线和重连；
- 未绑定用户普通消息不触发 LLM；
- 绑定码过期、重放、跨 account 和群内提交；
- 重复 event id 只执行一次 Turn；
- 发送限流、retryable 失败、永久失败和状态未知；
- stream 中途失败降级但不重复 Agent Turn；
- 附件超限、非法 scheme、SSRF、超时和落盘失败；
- 非发起者点击确认按钮；
- 群聊请求高风险 Tool；
- Adapter 停止时仍有 active Turn 和 outbound buffer。

### 24.3 边界路径

- 空文本 + 单附件；
- 超长文本、长 Markdown 表格、未闭合代码块；
- 引用消息已过期或不可访问；
- connector 返回 0、1、多个 credential；
- 同一 QQ identity 绑定到另一个内部用户时的显式重绑策略；
- 多 account 相同 external user id 不冲突；
- 进程重启后 receipt 和 conversation route 仍有效；
- 同会话快速连续消息与高优先级 `/stop`；
- 取消后迟到 RuntimeEvent 不再发送。

### 24.4 E2E

默认测试不连接 QQ。真实冒烟测试使用显式环境变量开启：

```text
ZHICE_AGENT_QQ_E2E=1
QQBOT_APP_ID=...
QQBOT_APP_SECRET=...
```

E2E 必须走真实 ChannelManager -> QQ Adapter -> ChannelChatRuntime -> AgentLoop 调用链，不能直接 import 内部 normalize 函数伪装端到端。

提交前至少运行：

```text
python -m ruff check .
python -m pytest --basetemp .tmp/pytest_part14
```

## 25. 验收标准

Part 14 第一版当前验收结果：

1. QQ SDK 不被 AgentLoop、Tool 或 protocols import。
2. 未启用 QQ 时，现有 CLI/Web 启动、测试和依赖行为不变。
3. 已绑定 QQ 私聊能完成连续多 Turn 对话并写入正确用户 JSONL Session。
4. 未绑定用户不能触发 LLM、Tool 或创建 Session。
5. 群聊默认 `@` 触发，并按内部用户隔离 Session。
6. QQ 和 Web 的 slash command 复用同一语义实现，仅能力 Profile 不同。
7. `/stop` 不能跨 actor 停止 Turn。
8. 重复事件不会重复执行 LLM 或 Tool。
9. 同一 conversation route 不并发写 Session。
10. 私聊输出可按真实 SDK 能力 stream 或 debounce，群聊不会逐 token 刷屏。
11. 附件经过 SSRF、大小、类型和用户目录边界。
12. 私聊确认只允许发起者，群聊高风险 Tool 默认拒绝。
13. AppSecret、token、绑定码、签名和完整外部 ID 不进入日志、trace、audit 或用户错误。
14. QQ 断线或不可用不会阻断 Web/CLI，health 能显示真实 capability 状态。
15. 新增测试主题目录有 `test_case.md`，正常、异常和边界路径完整。
16. Fake transport 测试稳定，真实 QQ E2E 只在显式环境变量下运行。
17. 总体设计、文档索引、配置示例和 README 使用方式同步更新。
18. 微信 ClawBot 实现二不需要修改 AgentLoop、现有身份解析核心含义或 SessionStore。

## 26. 微信 ClawBot 实现二复用原则

微信 ClawBot 实现二复用：

- `InboundChannelEvent`；
- `ChannelReplyTarget`；
- `ChannelCapabilities`；
- external identity link；
- conversation route；
- persistent dedup；
- per-conversation serialization；
- ChannelChatRuntime；
- RuntimeEvent outbound rendering；
- RBAC / confirmation / Hook / audit。

只允许平台 Adapter 自己处理：

- SDK 鉴权和 token 生命周期；
- event payload 解码与签名验证；
- 平台 user/chat/thread id；
- `@`、引用、按钮、卡片、文件和消息长度；
- 平台限流码和 retry-after；
- 平台特有发送 API。

如果微信实现需要改变 AgentLoop、Session ownership 或 Tool policy 的核心含义，必须先写新的日期设计记录，不能把平台例外直接写进通用循环。

## 27. 微信 ClawBot 实现二阶段定位

微信 ClawBot 是 Part 14 的第二个真实渠道实现。产品形态固定为一个 ZhiCe-Agent Web 用户连接一个微信 AI 账号：每个人首次都从本人已登录的 Web 设置页单独扫码；所有微信账号共享同一个 ZhiCe-Agent 服务，但不共享 Actor、Session、Memory、权限或凭证。

```text
ZhiCe Web 本人设置页
  -> 发起本人 binding attempt
  -> 微信专用 Node Transport sidecar 生成二维码
  -> 用户微信扫码确认
  -> channel account ownership + external identity + credential
  -> 每账号独立 getUpdates 长轮询
  -> WeixinClawAdapter
  -> ChannelChatRuntime
  -> ZhiCe-Agent AgentLoop
  -> sidecar sendMessage
  -> 微信 AI 私聊
```

sidecar 是 Transport，不是第二套 Agent。二维码、bot token、context token、长轮询、同步游标和发送 API 只存在于微信模块；AgentLoop、Tool、Memory、Session 和权限继续由 ZhiCe-Agent 负责。

## 28. 微信上游边界

2026-07-24 核对版本：

- `@tencent-weixin/openclaw-weixin 2.4.6`；
- `@tencent-weixin/openclaw-weixin-cli 2.1.4`；
- Node.js `>=22`；
- 代码许可证 MIT。

官方插件根入口只提供 OpenClaw Channel 注册，并把消息交给 OpenClaw 的 Agent Runtime，不是独立 Transport SDK。因此实现二不运行 OpenClaw AgentLoop，也不依赖不稳定的 npm 深路径。第一版 sidecar 从官方 MIT tarball 提取并审计最小 Transport 来源，保存版本、integrity、LICENSE 和补丁清单；如果官方发布稳定 Transport API，再切换到公开 API。

代码许可证与微信在线服务使用权分开判断。Transport POC 必须先验证真实扫码、AI 标识、私聊、收发和服务条款；失败时停止，不改用个人微信自动化。

## 29. 微信账号所有权与身份

现有 `external_identities` 表达外部发送者映射，但不能完整表达内部用户拥有一个带 token 的微信 AI 账号。实现二新增 `channel_accounts`：

```text
内部 user_id
  -> channel_accounts(channel=weixin, account_key, external_account_id, credential_ref)
  -> external_identities(channel=weixin, tenant=account_key, external_user_id)
  -> ActorContext
```

固定约束：

- 一个内部用户最多一个 active 微信账号；
- 一个微信用户和一个 AI account 不能绑定到两个内部用户；
- `account_key` 使用内部 opaque key，不直接暴露微信 ID；
- token 不进入数据库；
- 未绑定或发送者不匹配的消息在 LLM 前拒绝；
- 不自动创建内部用户，不产生新角色或 tenant。

## 30. 微信凭证与运行状态

```text
${ZHICE_AGENT_WORKSPACE}/config/channels/weixin/accounts/{account_key}.json
${ZHICE_AGENT_WORKSPACE}/state/channels/weixin/{account_key}/sync.json
```

credential 原子写入并使用本机严格文件权限；同步游标独立保存。二维码、bot token、context token 和完整微信 ID 不得进入普通日志、trace、audit、Session 或浏览器 URL。Web 二维码接口必须返回 `Cache-Control: no-store`。

第一版本地运行不伪装通用 Secret 加密。远程部署时必须在 Part 17 另接平台 Secret/KMS。

## 31. 微信 sidecar 协议与生命周期

Gateway 启动一个共享 Node 子进程，使用 stdio NDJSON 双向通信。stdout 只允许协议 frame，stderr 只允许脱敏日志。

```text
Python -> Node:
  hello / binding.start / binding.cancel / account.start / account.stop
  message.send / typing.set / health.get / shutdown

Node -> Python:
  hello.ok / binding.qr / binding.status / binding.connected / binding.failed
  message.received / message.send_result / account.status / health.status
```

每个账号有独立长轮询、AbortController、同步游标和退避状态。同一个 `account_key` 的 start/stop/reconnect 串行执行，重复 start 必须先完整停止旧 poller；stop 会释放等待 Python ACK 的入站消息，不能阻塞重连或 Gateway 退出。单账号失败只影响本人；sidecar 失败只影响微信 capability。Gateway stop 先停止轮询并通知上游，再回收子进程。同一个 workspace 只允许一个 Gateway 持有微信 sidecar lease。

账号持久化状态表达授权是否仍可用：只有微信明确返回`WEIXIN_TOKEN_STALE`才写入`reconnect_required`。`notifyStart`、stdio和持续轮询错误只进入运行态`degraded/reconnecting`并保留绑定；Python等待`account.start`使用15秒窗口，必须大于Node `notifyStart`的10秒上限，避免同水位超时竞态。Node长轮询的单次或两次连续失败只发送`account.poll_retry`进入DEBUG trace，不改变账号状态，连续第三次失败才进入degraded，恢复成功后只切回一次active。本地`account.start`从1秒开始、Node长轮询从2秒开始指数退避，均以30秒为上限。poll错误按DNS、连接重置、连接超时、HTTP、响应解析和上游业务码安全分类，不输出原始异常正文。可信且账号/发送者匹配的入站消息也可修复数据库与实际sidecar状态不一致，不要求用户手动点击Reconnect。

## 32. 微信 Web 绑定

本人接口：

```text
GET    /api/channels/weixin
POST   /api/channels/weixin/binding-attempts
GET    /api/channels/weixin/binding-attempts/{attempt_id}
DELETE /api/channels/weixin/binding-attempts/{attempt_id}
DELETE /api/channels/weixin/binding
POST   /api/channels/weixin/reconnect
```

attempt 只从当前登录 Actor 得到 owner，不接收 URL/body user id。一个用户同一时刻最多一个 attempt；已有 active binding 时必须先解绑。二维码过期后由用户重新发起，不无限刷新。

绑定 finalize 同时写账号所有权、external identity 和 credential。唯一约束冲突必须失败，不能覆盖原绑定。解绑先禁用账号，再停止轮询、删除 identity/ownership/credential/cursor；历史 Session 和 Memory 保留。

## 33. 微信消息运行链

```text
getUpdates
  -> sidecar allowlist normalize
  -> account active/recoverable + sender match
  -> persistent receipt claim
  -> rate limit
  -> ExternalIdentityService.resolve
  -> ChannelConversationService.resolve
  -> per-conversation serialization
  -> ChannelRuntimeAdapter.dispatch
  -> RuntimeEvent aggregate
  -> shared plain-text renderer
  -> <= 4000 chars chunks
  -> sidecar sendMessage with context token
```

sidecar 在 Python 对批次消息给出 accepted/duplicate/rejected ACK 后原子保存新游标。崩溃重投由现有 receipt 去重。当前 claim 后、Turn 完成前崩溃的 `processing` 窗口不在微信 Adapter 内另建队列解决。

微信已完成Agent Turn的回复在发送前按纯文本chunk写入SQLite Outbox。每个chunk使用由`account_key + event_id + chunk_index`确定性派生的稳定`delivery_id/client_id`；只有Sidecar明确返回`sent`才标记完成。发送失败不重新执行Agent Turn，而是保留pending、立即把微信能力降级并调度账号恢复；账号重新active或进程重启后按原顺序只重放pending chunk。已sent记录不重放并按保留期清理，解绑删除本账号Outbox但保留Session历史。

Python的`message.send`等待窗口必须大于Node官方发送API上限，避免客户端提前超时制造未知状态。每条入站消息在trace中闭合为accepted、duplicate、rejected、done或failed；额外记录`outbox_enqueued/outbox_replay_start/outbox_replay_done/outbox_replay_failed`，只带安全账号/交付引用、chunk序号、数量和allowlist错误码。token、context token、消息正文、原始响应和完整外部标识不进入lifecycle事件。

## 34. 微信第一版 Capability

```text
ChannelCapabilities(
    text=True,
    markdown=False,
    text_streaming=False,
    message_edit=False,
    reply_quote=False,
    inbound_media=frozenset(),
    outbound_media=frozenset(),
    interactions=False,
    typing_indicator=True,
    can_close_conversation=False,
    command_profile="weixin_c2c",
)
```

第一版只做direct text。RuntimeEvent聚合为最终文本，Markdown转共享纯文本，按4000字符安全分块。typing失败只降级；发送失败不重新执行Agent Turn，而由稳定client_id的持久化Outbox在重连后补发；不发送密集tool progress消息。

## 35. 微信 Session、Memory 与命令

- 每个微信 direct route 使用独立 `weixin_<uuid>` Session；
- Web 与微信共享内部 user 和用户级 Memory；
- Web Session 与微信 Session 不合并；
- Web 可以查看并继续本人微信私聊 Session；
- `/new`、`/clear`、`/model`、`/memory`、`/stop`、文本 `/confirm` 复用共享命令语义；
- Tool、Skill、MCP、Subagent 继续走 Actor RBAC、确认、Hook、workspace guard 和审计。

## 36. 微信配置

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

Gateway 不在启动时联网安装依赖。未启用微信时不检查 Node 或 sidecar，不改变现有运行行为。

## 37. 微信实现顺序

1. 先完成独立 Transport POC，验证真实 AI 标识、扫码、收发、重启和服务条款。
2. 再做单用户 sidecar -> WeixinClawAdapter -> ChannelChatRuntime 闭环。
3. 再做 Web binding attempt、channel account schema、唯一约束和解绑。
4. 最后用两个 Web 用户、两个微信号验证并发隔离，并完成日志/trace/audit 脱敏。

代码实施不得跳过 POC，且不得在 AgentLoop 中加入微信分支。

## 38. 微信测试方案

- Fake sidecar 覆盖启动、超时、协议损坏、崩溃和退避；
- 未绑定、账号 disabled、发送者不匹配均在 LLM 前拒绝；
- account/user/external id 唯一约束和并发 finalize；
- 两用户 Actor、Session、Memory 路径和模型偏好隔离；
- receipt ACK、重投、重复消息和游标边界；
- 文本渲染、4000 字符分块、部分发送失败不重跑 Turn；
- QR/token/context token/完整外部 ID 不进入日志和 API；
- Node 侧覆盖 QR 状态、多账号 long-poll、cursor、token stale、网络退避、stop/start 和 stdout/stderr 边界；
- Python/Node 使用 golden fixtures 做协议契约测试；
- 真实微信 E2E 只在 `ZHICE_AGENT_WEIXIN_E2E=1` 且显式提供隔离 workspace 时运行。

新增 `tests/unit_test/weixin_channel` 时必须维护同目录 `test_case.md`。

## 39. 微信实现二 Definition of Done

当前代码已完成 stdio NDJSON 协议、共享 sidecar 生命周期、`channel_accounts`、本人绑定 API/UI、凭证边界、direct-text Adapter、receipt ACK、限流、身份/路由/Runtime 复用、纯文本分块和双用户唯一约束测试。2026-07-24 已用真实微信完成 AI 标识、扫码、direct text 入站/出站、context token、游标恢复和 notifyStop POC；实现只 vendoring 腾讯 `2.4.6` 的审计 Transport 来源，不加载 OpenClaw Channel 或 Agent Runtime，也没有改用个人微信自动化。以下双真实账号条目仍需第二名用户验收：

- 两个 Web 用户分别扫码得到各自微信 AI direct 会话；
- 双账号同时在线并正确映射 Actor、Session、Memory 和权限；
- 未绑定、发送者不符、重复事件、disabled account 不触发 Agent；
- Web/微信共享用户级 Memory 但不共写 Session；
- token、二维码、context token、完整微信 ID 无泄漏；
- sidecar 和单账号失败局部降级；
- 重启恢复、workspace 单实例 lease、解绑清理和历史保留正确；
- 不存在第二套 AgentLoop；
- 上游来源、integrity、LICENSE、补丁和服务条款可追溯；
- Python、Node、契约与显式真实 E2E 测试完整。

完整变更文件、状态机、异常码和验收细节见日期设计记录 `docs_design/2026-07-24-weixin-clawbot-channel-design.md`。

### 39.1 当前 Capability 展示边界

QQ `accounts[]`、账号级 Adapter、身份 namespace、Session route 和 receipt 隔离继续作为内部兼容能力保留；当前产品只使用一个共享 QQ Bot。公共 `/health`、管理监控和 Capability 页面只展示聚合后的 `channel.qq`，不展示 `qq.main` 等内部账号 key。聚合状态来自真实 Adapter：全部在线为 available，全部不可用为 unavailable，混合或重连中为 degraded。

微信同样只展示 `channel.weixin`。`ChannelManager` 的启动失败只在 Adapter 未恢复时有效；重新扫码或轮询恢复使 Adapter 回到 available 后，历史启动失败必须自动清除，不能继续覆盖当前运行真值。完整方案见 `docs_design/2026-08-08-channel-capability-aggregation-design.md`。

## 40. 参考资料

- QQ 机器人官方文档，[Agent 接入与安全使用须知](https://bot.q.qq.com/wiki/agent-qqbot/#%E5%AE%89%E5%85%A8%E4%BD%BF%E7%94%A8%E9%A1%BB%E7%9F%A5)，访问日期：2026-07-23。
- QQ 官方扫码连接器，[`@tencent-connect/qqbot-connector`](https://www.npmjs.com/package/@tencent-connect/qqbot-connector)，设计核对版本：1.2.0。
- QQ 官方 Node.js 协议 SDK，[`@tencent-connect/qqbot-nodejs`](https://www.npmjs.com/package/@tencent-connect/qqbot-nodejs)，设计核对版本：1.0.4。
- QQ Bot Python SDK，[`tencent-connect/botpy`](https://github.com/tencent-connect/botpy)，设计核对 PyPI 版本：`qq-botpy 1.2.1`。
- 微信 OpenClaw Channel，[`@tencent-weixin/openclaw-weixin`](https://www.npmjs.com/package/@tencent-weixin/openclaw-weixin)，设计核对版本：`2.4.6`。
- 微信 OpenClaw 安装器，[`@tencent-weixin/openclaw-weixin-cli`](https://www.npmjs.com/package/@tencent-weixin/openclaw-weixin-cli)，设计核对版本：`2.1.4`。
