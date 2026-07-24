# Part 14 外部渠道测试说明

## 测试目标

- 中性 Channel 数据结构不依赖 QQ SDK。
- `channels.yml` 缺失时默认禁用，环境变量引用、非法配置和 secret repr 安全。
- 一次性绑定码只保存 hash，按 channel/account 限定、过期和重放 fail closed。
- 私聊裸 `/bind` 创建绑定当前 QQ OpenID 的一次性 Web 授权链接；登录消费后写入同一 `external_identities`，过期和重放 fail closed。
- conversation route 持久复用，`/new` 原子切换到随机内部 Session。
- event receipt 在 Agent 前原子 claim，重复事件不能重复执行。
- QQ 私聊、群聊 `@`、未绑定门禁、绑定指令按钮、Markdown 授权链接、分块出站和 SDK 缺失状态可测试。
- QQ 群聊纯文本、Markdown 与降级回复通过 `message_reference` 引用触发消息；长文本只在第一块引用，私聊不附加群引用。
- QQ 普通 Agent 回复按标题、列表、引用、代码、链接和强调等结构选择 Markdown；短句和超长内容保持文本。
- QQ adapter 只调用 Channel runtime，不直接依赖 LLMProvider、ToolProvider 或 AgentLoop。

## 正常路径

- 已绑定身份解析为内部 actor。
- 同一外部 conversation 复用 Session，不同群用户隔离 Session。
- `/new` 保留旧 Session 并切换 route。
- QQ 私聊消息经 Fake transport 得到 runtime 最终回复。
- QQ 群聊回答引用触发者原消息，避免多人连续提问时回答归属不清。
- 普通未绑定提示附带自动发送 `/bind` 的“绑定”指令按钮。
- QQ 私聊裸 `/bind` 返回包含 `${web_base_url}/?channel_bind=<token>` 的 Markdown 超链接和 URL 按钮。
- `/bind <code>` 在私聊和群聊都直接绑定当前消息发送者；群聊裸 `/bind` 只引导私聊网页授权。
- QQ 私聊不能通过 `/sessions` 浏览、重命名或删除 Web/CLI Session，只能使用当前 route 和 `/new`。

## 异常路径

- 缺失环境变量、重复 account key、非法 transport/config 类型。
- 绑定码跨 account、过期或二次使用。
- 群聊绑定码被第二个成员重放时失败，不能覆盖首个绑定；已绑定成员的 `/bind <code>` 不消费新码。
- Web 授权 token 过期、二次消费或 identity 冲突。
- 未绑定普通消息不调用 runtime。
- 重复 event id 只处理一次。
- SDK 缺失或 credentials 缺失仅返回 unavailable。
- Keyboard 被拒绝时先降级为 Markdown 链接；Markdown 仍失败时降级为纯文本 URL，且不重复创建授权请求。
- 普通结构化回复 Markdown 失败时回退同一纯文本内容，不重复 Agent Turn。

## 边界路径

- 空 `channels.yml`、空文本、长文本分块。
- 多 account 相同 external user id 不冲突。
- 进程重建 service 后 route/receipt 仍从 SQLite 生效。
- QQ 群 capability 不暴露个人模型、Session 和历史命令。
- Keyboard payload 只在 QQ transport 组装，AgentLoop 和中性 Channel 协议不依赖 QQ SDK 类型。
