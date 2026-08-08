# Part 14 外部渠道测试说明

## 测试目标

- 中性 Channel 数据结构不依赖 QQ SDK。
- `config.yml.channels`缺失时默认禁用，环境变量引用、非法配置和Secret repr安全。
- 一次性绑定码只保存 hash，按 channel/account 限定、过期和重放 fail closed。
- 私聊裸 `/bind` 创建绑定当前 QQ OpenID 的一次性 Web 授权链接；登录消费后写入同一 `external_identities`，过期和重放 fail closed。
- conversation route 持久复用，`/new` 原子切换到随机内部 Session。
- event receipt 在 Agent 前原子 claim，重复事件不能重复执行。
- QQ 私聊、群聊 `@`、未绑定门禁、绑定指令按钮、Markdown 授权链接、分块出站和 SDK 缺失状态可测试。
- QQ 群聊纯文本、Markdown 与降级回复通过 `message_reference` 引用触发消息；长文本只在第一块引用，私聊不附加群引用。
- QQ 私聊普通 Agent 回复按标题、列表、引用、代码、链接和强调等结构选择 Markdown；群聊为保证引用显示统一使用文本，短句和超长内容也保持文本。
- QQ 群聊和 CLI 复用中性 Markdown-to-plain renderer；QQ 分块使用唯一递增 `msg_seq`，群聊最多 5 块、单聊最多 4 块，超限明确提示。
- QQ adapter 只调用 Channel runtime，不直接依赖 LLMProvider、ToolProvider 或 AgentLoop。

## 正常路径

- 已绑定身份解析为内部 actor。
- 同一外部 conversation 复用 Session，不同群用户隔离 Session。
- `/new` 保留旧 Session 并切换 route。
- QQ 私聊消息经 Fake transport 得到 runtime 最终回复。
- QQ 群聊回答使用普通文本引用触发者原消息，避免 Markdown 引用在真实客户端不展示以及多人连续提问时回答归属不清。
- 普通未绑定提示附带自动发送 `/bind` 的“绑定”指令按钮。
- QQ 账号显式 HTTPS `web_base_url` 能从配置加载；未配置时本地默认仍为 `http://127.0.0.1:10086`。QQ 私聊裸 `/bind` 使用显式公网账号返回移动优先的 `https://agent.zouzhou.xyz/bind/qq?token=<token>` 页面链接和 URL 按钮。
- `/bind <code>` 在私聊和群聊都直接绑定当前消息发送者；群聊裸 `/bind` 只引导私聊网页授权。
- 同一内部账号解绑现有 QQ 后，可通过 Web 授权或 `/bind <code>` 绑定新的 QQ。
- QQ 私聊不能通过 `/sessions` 浏览、重命名或删除 Web/CLI Session，只能使用当前 route 和 `/new`。

## 异常路径

- 缺失环境变量、重复 account key、非法 transport/config 类型。
- 绑定码跨 account、过期或二次使用。
- 群聊绑定码被第二个成员重放时失败，不能覆盖首个绑定；已绑定成员的 `/bind <code>` 不消费新码。
- Web 授权 token 过期、二次消费或 identity 冲突。
- Web 授权和 `/bind <code>` 都拒绝给已有 active QQ 的内部账号绑定其它 QQ，并提示先解绑。
- 未绑定普通消息不调用 runtime。
- 重复 event id 只处理一次。
- SDK 缺失或 credentials 缺失仅返回 unavailable。
- Keyboard 被拒绝时先降级为 Markdown 链接；Markdown 仍失败时降级为纯文本 URL，且不重复创建授权请求。
- 普通结构化回复 Markdown 失败时回退同一纯文本内容，不重复 Agent Turn。
- QQ API明确抛错时记录`channel.qq.send_failed`；SDK返回`None`时记录`channel.qq.send_unconfirmed`并把receipt标记为error，但不把仍在线的WebSocket渠道标记为degraded。
- QQ HTTP请求上限默认15秒、允许每账号配置1~60秒，并真实传入`botpy.Client(timeout=...)`。
- QQ 正常启动不展开 botpy 登录细节，在真实 `on_ready` 后输出 Uvicorn 风格 `[qq] channel ready | mode=shared`；启动期超时后若连接恢复，只补发一次 ready，状态降级输出 WARNING，且不输出 app secret。
- 微信正常启动按 `active/reconnect_required` 聚合账号数；消息接收、接受、发送开始/完成只进 DEBUG trace，sidecar、重连和发送失败以 Uvicorn 风格 WARNING 输出，并只使用内部账号短哈希。
- ChannelManager 只在 Adapter 尚未恢复时保留 `CHANNEL_START_FAILED`；Adapter 恢复 `available` 后清除历史启动失败，避免微信已恢复收发但 Capability 仍显示 unavailable。

## 边界路径

- 空`channels`分区、空文本、长文本分块。
- 多 account 相同 external user id 不冲突。
- QQ 多 account Adapter 和 Session 隔离继续保留，但账号 key 只用于内部路由、日志和诊断，不作为公共 Capability 卡片。
- 进程重建 service 后 route/receipt 仍从 SQLite 生效。
- QQ 群 capability 不暴露个人模型、Session 和历史命令。
- Keyboard payload 只在 QQ transport 组装，AgentLoop 和中性 Channel 协议不依赖 QQ SDK 类型。
- 投递状态未知时不继续发送Markdown降级或重试同一`msg_id + msg_seq`，避免重复回复；未确认异常在Adapter边界终止传播。
