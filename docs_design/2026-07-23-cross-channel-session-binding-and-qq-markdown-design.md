# 2026-07-23 跨渠道 Session、渠道解绑与 QQ Markdown 设计记录

> 状态：已确认，待本次代码落地；承接 `2026-07-23-qq-external-channel-boundary-design.md` 和当前 Part 14 活文档。

## 1. 背景

Part 14 第一版已完成 QQ 身份绑定、conversation route 和文本出站，但真实使用暴露出三类边界问题：

- Web 个人设置只能生成绑定码，不能查看和解除当前用户自己的渠道绑定；
- 同一用户的 Web、CLI、QQ 私聊和 QQ 群聊 Session 当前全部进入同一列表，但列表没有来源和可继续状态，QQ 私聊还可以通过 `/sessions` 管理 Web/CLI Session；
- QQ Adapter 对普通 Agent 回复始终使用纯文本，导致列表、标题、代码块、链接和强调等已有 Markdown 结构显示较差。

## 2. 目标

1. 采用“账号统一、历史可见、会话有来源、继续受限制”的跨渠道 Session 模型。
2. Web/CLI 作为用户私有控制面，可以看到本人全部渠道历史；外部渠道不能反向浏览或管理其它渠道 Session。
3. QQ 私聊 Session 可以在 Web/CLI 继续；QQ 群聊 Session 在 Web 只读，只能派生新的 Web Session。
4. 在个人设置提供最小的绑定状态和自助解绑，不暴露 account key、完整 OpenID 等内部字段。
5. QQ 普通回复按内容合理选择 Markdown，发送失败时回退原始文本，不重复执行 Agent Turn。

## 3. 范围边界

包含：

- Session 列表来源标签、conversation type 和 continuation mode；
- Web 对 QQ 群 Session 的服务端只读门禁与派生入口；
- QQ `/sessions` 能力收窄；
- 当前用户渠道绑定查询和按 owner 原子解绑；
- QQ Markdown 检测、发送与文本降级；
- Web UI、API、SQLite 兼容迁移、测试和活文档同步。

不包含：

- 管理员代替普通用户管理渠道绑定；
- 跨用户共享 Session；
- QQ 群 Session 与 Web Session 双向同步；
- 用 LLM 重新总结派生会话；第一版使用同一用户可见历史的确定性复制；
- 把 QQ SDK 类型放入 AgentLoop、中性 Channel Protocol 或 Web API。

## 4. Session 模型

`session_index` 增加 `conversation_type`，与创建 Session 时的 `channel` 一起作为不可变来源信息：

```text
channel: web | cli | qq | external_ws | cli_legacy
conversation_type: "" | c2c | group
```

对普通用户返回：

```text
channel
conversation_type
continuation_mode = writable | fork_only
```

规则：

- Web/CLI Session：`writable`；
- QQ 私聊 Session：`writable`，允许 Web/CLI 继续；
- QQ 群聊 Session：`fork_only`，Web 可读但不能直接追加消息；
- 外部渠道只解析当前 conversation route，不提供全局 Session 管理入口。

服务端必须在执行 slash command 或 Agent Turn 前拒绝 Web 对 `fork_only` Session 的写入，不能只依赖前端禁用输入框。

## 5. 群聊 Session 派生

Web 对 QQ 群 Session 展示“在 Web 中继续”。点击后：

1. 校验源 Session 属于当前内部用户且为 `fork_only`；
2. 创建新的 `channel=web` Session；
3. 复制当前持久消息作为新 Session 的初始上下文；
4. 新 Session 不写入原 QQ conversation route；
5. Web 打开新 Session，后续消息只影响新 Session。

原群 Session、route 和群内上下文保持不变。

## 6. 渠道解绑

新增当前用户自助接口：

```text
GET    /api/channels/bindings
DELETE /api/channels/bindings/{binding_id}
```

列表只返回安全字段：binding id、channel、可选展示名和绑定状态。解绑 SQL 必须同时匹配 `id + user_id + status=active`，不能仅凭外部 ID 或前端传入 user id。

解绑只禁用 `external_identities`：

- 不删除 Session；
- 不删除 conversation route；
- 不删除审计历史；
- 外部用户下一条消息立即回到未绑定门禁；
- 同一内部用户以后重新绑定可继续原 route；其它内部用户不能读取原用户 Session。

## 7. QQ Markdown

普通 Agent 回复采用保守检测：内容包含 Markdown 标题、列表、代码围栏、链接、引用、强调或行内代码时，且单块未超过 QQ 安全长度，使用 `msg_type=2` 自定义 Markdown；普通短句继续使用 `msg_type=0`。

Markdown 发送失败时使用相同内容降级为纯文本。发送失败不能重新调用 Runtime、LLM、Tool 或 identity service。超长或无法安全保持结构的内容继续使用现有纯文本分块。

## 8. 变更文件

- `agent/auth/schema.py`、`agent/auth/store.py`、`agent/auth/session_access.py`
- `agent/protocols/session.py`
- `agent/app/runtime.py`、`agent/app/api/routes.py`、`agent/app/api/schemas.py`
- `agent/channels/identity.py`、`agent/channels/qq/adapter.py`、`agent/channels/qq/outbound.py`
- `web/static/index.html`、`web/static/app.js`、`web/static/styles.css`
- `tests/unit_test/auth`、`tests/unit_test/app`、`tests/unit_test/channels` 及各自 `test_case.md`

## 9. 测试方案

正常路径：

- Web 列出 Web、CLI、QQ 私聊、QQ群聊 Session，并返回来源与 continuation mode；
- Web 能继续 QQ 私聊 Session；
- QQ 群 Session 能派生为新 Web Session；
- 当前用户能查看和解绑自己的 QQ identity；
- QQ 列表、代码块和链接回复使用 Markdown。

异常路径：

- Web 直接向 QQ 群 Session 发消息或命令时服务端拒绝；
- 用户不能解绑其他用户的 binding id；
- QQ 不能通过 `/sessions` 查看或删除 Web/CLI Session；
- Markdown 或 Keyboard 被平台拒绝时回退文本。

边界路径：

- 旧 SQLite 缺少 `conversation_type` 时幂等迁移；
- 旧 Session 来源字段为空时保持可读；
- 派生空 Session、长 Session、已删除 Session；
- 解绑后历史仍在 Web 可见，QQ 不再解析为 actor；
- 超长 Markdown 退回文本分块。

## 10. 验收标准

- Web/CLI 保持本人跨渠道历史可见；
- QQ 私聊可跨端继续，QQ群聊不能被 Web 直接续写；
- QQ 外部入口不能管理其它渠道 Session；
- 用户可在个人设置中查看“QQ 已绑定”并解除绑定；
- 解绑不删除历史或 route；
- QQ 结构化文本合理使用 Markdown，失败安全降级；
- AgentLoop 和中性 Channel Protocol 不依赖 QQ SDK；
- Ruff、专项测试和全量 pytest 通过。
