# App / Web gateway tests

## 测试目标

- 验证普通自助注册默认关闭、Owner 独占开关、匿名安全投影、后端不可绕过、策略变更/拒绝审计与管理员手工创建不受影响。

覆盖 Part 6 HTTP/WS 基础、Part 9 Auth/RBAC 和 Part 16 Vue 产品面 app read model、包内静态资源与兼容协议。

## 用例覆盖

- `/health` 和 `/api/health` 返回基础状态、当前模型、auth 初始化状态和安全的可选 capability 状态；Subagent unavailable 不改变 overall `status=ok`，且不暴露 workspace/session 路径。
- QQ 账号级 Adapter 状态在 Runtime 内聚合成唯一 `channel.qq`；公共 health 和管理监控不暴露 `qq.main` 等内部账号 key，混合多账号状态降级为渠道级状态。
- 聊天 Web 不渲染或主动请求 capability 启动横幅；health 状态保留给诊断、系统监控和自动化检查。
- Gateway/CLI缺少`config.yml.skills`时按未启用的可选扩展静默处理；分区存在但非法时只记录一次结构化`skills.runtime_unavailable` WARNING，包含稳定code且不泄露绝对路径。
- `/` 从可替换 `static_dir` 返回 SPA 首页；`/_setup`、`/admin`、`/travel`、`/workflows`、`/workflows/{workflow_id}` 和 `/bind/qq` 继续返回同一 Vue 入口并保留服务端安全条件；所有 SPA 入口使用 `Cache-Control: no-store`，避免部署后旧入口继续引用过期资源或硬刷新工作流详情时 404。
- 默认静态目录定位 `agent/web/static` 包内 production build；首页引用 `/static/assets/*`，Python wheel 运行不要求 Node.js。
- `GET /api/sessions` 返回会话摘要并把更新时间格式化为 ISO 8601。
- `GET /api/sessions/{session_id}` 返回指定会话消息。
- `POST /api/chat` 调用 runtime 并返回 assistant 消息。
- `POST /api/chat` 遇到 slash command 时由 Web runtime 短路处理，不透传给 LLM。
- `POST /api/chat/stream` 返回 SSE `status`、`delta`、`done` 或 `error` 事件。
- `GET /api/models` 返回当前 endpoint 和可选模型。
- `POST /api/model/preference` 设置当前 endpoint 的模型偏好。
- 新对话模型预选：登录用户可在无 Session 时只读获取默认模型目录且不创建 Session；偏好写入仍要求 actor-owned Session，前端在首次发送创建 Session 后再落盘草稿预选模型。
- 空消息、缺失字段和非法 session id 返回 `REQUEST_VALIDATION_FAILED`，字段问题放入安全的 `details.issues`。
- HTTP 错误统一返回 `error.status/code/message/request_id/details`，body status 与真实 HTTP status 一致，body request_id 与 `X-Request-ID` 一致。
- runtime 抛出配置、LLM 和未知错误时返回领域化稳定错误码，不暴露堆栈。
- core 测试只使用 `agent.core.loop` 和 `agent.core.context` 新路径。
- gateway 测试确认不再保留 `agent/gateway.py` 顶层兼容导出模块。
- Gateway lifespan对正常渠道输出`channel.enabled/channel.ready`，禁用/异常渠道记录`channel.skip/channel.start_failed`，关闭记录`channel.stop`；外部渠道及ready日志严格遵循`config.yml.channels`映射顺序；可选渠道失败不阻断Web，日志不包含credential或外部账号标识。
- WebSocket `hello client=web` 返回 web command profile 能力，默认不支持 `/history` 和 `/exit`。
- WebSocket `hello client=external` 打开 external command profile 能力，`/history` 进入 external profile，`/exit` 关闭当前 WS 连接。
- 旅行接待的 `travel.planning_confirmed` 在 Turn 完成前通过 WebSocket 实时转发，前端无需刷新即可从 intake 切换到 planning；该事件不作为规划终态，后端继续同请求自动续跑。
- 旅行规划最多跨 6 个持久阶段 Turn 自动继续；后续阶段的一次 LLM Provider 瞬时失败从当前旅行状态生成新 Turn 重试，第二次失败仍返回结构化错误。
- `/sessions rename <id> <title>`、`/sessions delete <id>` 和 `/sessions delete` 在 runtime slash command 层有覆盖。
- assistant Markdown 使用本地打包的 Marked、DOMPurify 与 KaTeX 支持常用行内/块级公式；`\\bm{}` 映射到 `\\boldsymbol{}`，不再依赖运行时 CDN。

## 关键检查点

- API 测试只使用 fake runtime，不访问真实 LLM 或网络。
- gateway `--check` 仍只做配置检查，不启动 HTTP 服务。
- Web/API 层不反向进入 AgentLoop 之外的业务分支。

## Part 7 Turn Coverage

- WebRuntime accepts an optional `turn_id` and passes it to AgentLoop.
- WebSocket accepted, text, done, stopped, and error events carry the aligned turn id.
- Session history API exposes optional message turn fields.
- SSE status, delta, done, stopped, and error payloads carry one consistent turn id.
- WS 使用 `runtime_event` 信封转发 RuntimeEvent；SSE 使用 `event: runtime`，`skill.*` 的 `skill_run_id` 也必须原样保留，且均保持旧 text/status/interaction 兼容。
- 浏览器 RuntimeEvent reducer 按 turn_id + sequence 忽略旧状态，并在 terminal turn Event 清理运行状态。
- Subagent child RuntimeEvent 按 root session/turn 归并，并按 agent/task 维护独立 sequence 与并行任务状态，不能跨 child 用同一 sequence 覆盖。
- WebSocket 转发 Child RuntimeEvent 时 payload 保留真实 Child 关联字段，外层信封使用 root session/turn，使旅行页能够接收所属并行任务进度。
- Web `/help` 只新增顶层 `/subagent`；裸命令显示 mode、force-once、可用 Profile，并在 Tip 中提示 `auto/off/once`。`/clear` 与清空当前 Session 只清 one-shot、保留 mode；旧 `/reset` 不再支持。QQ群帮助展示 `/clear`。
- Subagent unavailable 时，Owner 的 `/subagent` 与 force-once 返回真实 message/hint；普通用户只看到暂时不可用并联系管理员，不直接展示 JSON、Prompt 文件名、初始化命令、`code` 或 `cause_code`；one-shot 仍只消费一次。公共 health 同样只返回通用 capability 状态。
- Subagent unavailable 且 Session 为 auto 时，普通 Web Turn 注入只返回通用不可用文案的 `delegate_tasks` facade，内部 cause 仅写日志与 trace；它不创建 child，防止模型用其它 Tool 冒充明确的子代理请求。
- Web LLM-facing ToolProvider 首轮只暴露 `discover_tools`；发现后才按当前 actor 可见集合动态增加 schema，未激活业务 Tool 不能执行。

## Part 8 Logging Coverage

- Gateway logging options split Agent lifecycle log, HTTP access log, HTTP server log, and workspace trace log.
- Terminal Agent log lines use fixed Beijing time `[YYYY-MM-DD HH:MM:SS] | LEVEL | component.event | fields` without milliseconds；TTY 下 WARNING 整行使用高亮红色，ERROR/CRITICAL 整行使用红色，普通日志继续按组件着色。
- Workspace trace writes JSONL with `+08:00` timestamps to Beijing-date `logs/log-YYYY-MM-DD.jsonl`, with `component` and no full internal logger name.
- 本地 Ops supervisor 的受控 Gateway child 可在 PIPE 后恢复原终端 ANSI 配色；`NO_COLOR` 仍具有最高优先级。
- Logging configuration is idempotent and can disable terminal Agent logs while keeping trace on.
- Preview helpers redact sensitive fields, collapse multiline text, and truncate long values.
- WebRuntime relies on `turn.start/done` for ordinary chat lifecycle and only keeps Web stop/error, cancel, Session mutation, real model changes, and background Memory extraction events.
- Tool terminal lines render `TOOL name | START/DONE/FAILED`, use username and turn index, and hide full session/request/tool-call ids while JSONL trace retains them.
- WebSocket chat does not propagate the derived `ws-{turn_id}` value as a second Agent request identity.

## Part 9 Auth Coverage

- Web bootstrap 创建唯一 Owner、设置 cookie 并直接登录；已有普通用户不阻塞 Owner 初始化，已有 Owner 后重复 bootstrap 返回 409。
- Owner 初始化页面仅通过 `/_setup` 提供；未配置 setup token 或 Owner 已存在时返回 404，普通首页不展示入口。
- Web Owner 用户名由服务端固定为 `owner`；页面只输入一次 Owner 密码和一次 setup credential，不要求确认密码。
- bootstrap 非法用户名或弱密码返回稳定 400，且登录页仍保持可初始化状态。
- 匿名用户在 Owner 初始化前后都可自定义用户名和密码注册；服务端固定授予 `viewer`，忽略客户端伪造的角色字段并自动登录。
- Web 普通注册只接收用户名/密码；Owner bootstrap 额外要求部署 setup token。两者均令 `display_name=username`，额外伪造的 display_name 不覆盖默认值。
- 普通注册会按需初始化 schema；重复用户名返回 `USER_USERNAME_ALREADY_EXISTS`，非法字段不创建用户。
- 普通 Admin 默认不能任命管理员；Owner 可直接委派 `auth.admin.manage`，且该能力不会传播给新 Admin。
- 未被委派的 Admin 管理其他管理员时返回 `403 AUTH_ADMIN_MANAGEMENT_NOT_DELEGATED`，并在 `details.required_permission` 返回所需权限。
- 未登录 HTTP API 返回 `401 AUTH_REQUIRED`，WebSocket 返回错误并以 1008 关闭。
- 登录成功设置 HttpOnly、SameSite=Lax cookie；logout 撤销服务端 auth session。
- 当前用户可修改显示名；修改密码成功后撤销全部登录态、清 Cookie 并要求重新登录，错误当前密码不改变凭据。
- HTTP、SSE 和 WebSocket 都把解析后的 ActorContext 传入 WebRuntime。
- 模型读写携带 session_id，不能修改 gateway 进程级共享 provider。
- 普通 viewer 可以查看、设置和重置自己会话的模型偏好，模型切换不再阻断正常聊天。
- 登录、注册和 Owner 初始化复用 Vue AuthLayout；桌面端品牌/表单横向滑动，移动端内容切换，密码可见性、原生表单提交和 reduced-motion 均保留。
- 登录页、侧边栏和 favicon 使用 A 版 ZC 星芒 Logo PNG；用户头像使用单个 initials 文本节点，英文取首尾词首字母，中文稳定取一至两个字符。
- Owner 和其他角色的聊天侧栏默认只列当前用户自己的 session；前端账号切换会清空上一账号的 active session 和 messages。
- Recent diagnostics 不出现在常驻 Web UI，也不保留 REST 表单入口；普通聊天通过 `diagnose_my_recent_activity` 自动诊断当前 Session 的上一轮或最近失败。
- 普通成功 HTTP 请求不写 Security Audit；认证失败、特权拒绝和安全相关操作继续审计。
- Web 个人设置只提供“生成 QQ 一次性绑定码”，返回 10 分钟单次使用的 `/bind <code>`；不增加渠道列表或管理员绑定管理。
- Web 个人设置显示当前用户自己的极简 QQ 绑定状态并支持解绑；不暴露完整 OpenID、account key 或其他用户绑定。
- QQ 绑定成功页提供“关闭并返回 QQ”主入口，浏览器拒绝自动关闭时显示右上角手动关闭提示，并保留进入 ZhiCe-Agent 的次入口。
- 管理后台创建用户表单禁止把当前登录凭据自动填入新用户字段；已停用非 Owner 用户支持用户名二次确认永久删除，Owner、启用账号和仍绑定微信的账号拒绝删除。
- 永久删除确认值不匹配时按钮仍可提交并显示明确行内错误；QQ 绑定认证页将“立即创建/返回登录”作为独立蓝色动作文本。
- Gateway 为 `/bind/qq` 显式返回 SPA index，保证手机从 QQ 直接打开子路由不会 404；QQ 裸 `/bind` 返回的授权 token 进入该移动优先页面，在未登录时保留到登录或注册完成，认证成功后自动调用授权 API；旧 `/?channel_bind=` 链接重定向兼容，token 过期、重放或冲突返回稳定错误且不写入 identity。
- 已绑定其它 QQ 的账号消费 Web 授权 token 时返回 HTTP 409 和稳定错误码 `CHANNEL_QQ_USER_ALREADY_BOUND`，不自动顶替原绑定。
- Web Session 列表标明 Web、CLI、QQ 私聊、QQ群聊来源；QQ群聊在 Web 只读并可派生新的 Web Session。
- 服务端拒绝 Web 直接向 QQ 群 Session 发送普通消息或 slash command，不能只依赖前端禁用输入框。

## Part 16 Vue Web Product Coverage

- Vue 3/Vite/TypeScript、Vue Router、Pinia、Vitest、Vue Test Utils、Lucide Vue 与 CSS Design Tokens 由 `web/frontend` 管理；构建产物提交到 `agent/web/static`。
- typed API client 保留稳定 HTTP error，typed WebSocket client 保留 hello/message/stop/confirmation/MCP elicitation frame；RuntimeEvent reducer 覆盖乱序、终态和 child 独立 sequence。
- Session 窄侧栏不显示消息数；三点菜单支持 ESC 关闭、重命名和二次确认删除；外部群 Session 只读并可 fork 到 Web。
- 设置中心包含常规、个性化、个人资料、账号与安全、渠道连接；主题按登录身份保存在 browser localStorage，支持系统/浅色/暗色曜石。
- 管理后台按权限独立显示概览、账号、角色、运行诊断和安全审计；运行诊断默认展示带账号与 Session 标题的失败记录；所有内置 permission key 都有中文能力域映射，未知 key 回退技术名称，Owner 固定只读，Admin 角色权限仅允许 Owner 修改。
- `GET /api/admin/monitor` 需要 `turn.read.any`，只聚合 Gateway、Capability 与结构化 Runtime Activity 真值，不返回根因诊断。
- `GET /api/admin/diagnostics` 独立要求 `diagnostics.system.use`；Owner 默认可查，普通角色不能用 `turn.read.any` 替代该权限，并支持 component/error_code 等有界筛选。
- `GET /api/audit/events` 保持旧 `limit/session_id/turn_id` 兼容，并增加事件、操作者、结果、时间和 cursor 筛选；`audit.export` 独立保护 CSV 导出。
- Gateway lifespan 对同一 workspace 持有跨平台单实例锁；关闭时拒绝新 Turn、取消 active Turn 与 MCP 调用，并在释放锁前关闭渠道、Memory 和 MCP。
- `run_gateway` 在构造 Runtime 前取得 workspace 单实例锁；Uvicorn 启动、绑定或 lifespan 进入失败时仍幂等关闭 Runtime 并释放锁，不遗留已初始化的后台组件。
- 删除仍有活动 Turn 的 Session 时，Runtime 先发送取消并等待 Turn 注销完成，再删除 Session 文件和索引；超时则保留 Session 并返回失败，避免后台回写形成孤儿 CLI 会话。
- “MCP 与 Skills”将协议服务和外部账号分区：MCP 监控只统计真实 Server；小红书连接、Catalog、调用与服务重启留在 MCP 卡，扫码/Cookie 登录移入仅 Owner 可见的“外部平台账号”。非 Owner API 返回 403，响应和审计不包含 Cookie、路径、PID 或原始工具输出。
- 携程只进入“外部平台账号”，不进入 MCP 网格、Server 数或 Catalog；与小红书账号卡双列等高。账号 API 使用 `/api/admin/external-platforms/ctrip/*` 并返回 `platform_id=ctrip`；保存接口将密码写入 Git 忽略的 runtime `config/.env` 并立即启动登录助手，Linux/Docker/云平台也可使用外部 Secret 注入；状态、删除与重登响应只返回脱敏账号提示和稳定码，非 Owner 403，审计使用 `external_platform_account` 且不记录账号或密码。
- 小红书登录检查兼容 MCP structured content 与 text content 形成的连续 JSON 文档；真实 success/OK 结果缓存为 authenticated，页面刷新不再误判为 unavailable。

## Part 18B Skill 与 Ops Web 投影覆盖

- `GET /api/admin/skills/sources`独立要求`skill.sources.read`，只返回持久source状态、安全错误摘要和当前actor可见Skill，不泄露路径、仓库URL或原始stderr。
- 单source同步继续独立要求`skill.sync`；索引刷新要求source读取权限；两者拒绝非法source名并只记录source、结果和安全错误类型审计。
- 被显式授予Skill权限的Admin可管理source；普通用户拒绝；`GET /api/admin/operations/terminal`即使对Admin也拒绝，只允许唯一Owner。
- Ops API只投影`enabled/configured/url/presentation/mode/target_type/target_name`，运行态 endpoint 优先于静态配置；不代理日志、Docker 动作、重启、终端字节流或宿主机认证信息。

## Part 10 Memory Coverage

- `/memory` 只展示当前 actor 的长期 Memory；Session Summary 和手动提取子命令均已删除。
- Owner Web 与 CLI 使用全局 Memory，普通用户使用各自目录；Web turn 绑定 Memory Tool、候选策略和 confirmation broker。
- `/mcp` 在 Web/external WS 使用同一共享 Runtime 摘要；MCP Elicitation 响应帧回传到 Runtime。
- Memory 写入通过普通对话授权；持久 trace/audit 不保存 query、写入内容或读取结果原文。
- Web 不提供 Memory 专用确认弹窗或编辑 API；Memory 授权通过普通对话完成。
