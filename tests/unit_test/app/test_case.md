# App / Web gateway tests

## 测试目标

覆盖 Part 6 Web 最小版的 HTTP app、API schema、静态资源服务和 core 直接导入。

## 用例覆盖

- `/health` 和 `/api/health` 返回基础状态、当前模型、auth 初始化状态和安全的可选 capability 状态；Subagent unavailable 不改变 overall `status=ok`，且不暴露 workspace/session 路径。
- 静态聊天 Web 不渲染或主动请求 capability 启动横幅；health 状态保留给诊断和自动化检查。
- Gateway/CLI 缺少 `skill_sources.yml` 时按未启用的可选扩展静默处理；文件存在但非法时只记录一次结构化 `skills.runtime_unavailable` WARNING，包含稳定 code 且不泄露绝对路径。
- `/` 从可替换 `static_dir` 返回静态首页。
- `/admin` 返回独立管理路由入口；聊天页 Administration 导航到该路由，不再打开管理弹窗。
- `GET /api/sessions` 返回会话摘要并把更新时间格式化为 ISO 8601。
- `GET /api/sessions/{session_id}` 返回指定会话消息。
- `POST /api/chat` 调用 runtime 并返回 assistant 消息。
- `POST /api/chat` 遇到 slash command 时由 Web runtime 短路处理，不透传给 LLM。
- `POST /api/chat/stream` 返回 SSE `status`、`delta`、`done` 或 `error` 事件。
- `GET /api/models` 返回当前 endpoint 和可选模型。
- `POST /api/model/preference` 设置当前 endpoint 的模型偏好。
- 空消息、缺失字段和非法 session id 返回 `REQUEST_VALIDATION_FAILED`，字段问题放入安全的 `details.issues`。
- HTTP 错误统一返回 `error.status/code/message/request_id/details`，body status 与真实 HTTP status 一致，body request_id 与 `X-Request-ID` 一致。
- runtime 抛出配置、LLM 和未知错误时返回领域化稳定错误码，不暴露堆栈。
- core 测试只使用 `agent.core.loop` 和 `agent.core.context` 新路径。
- gateway 测试确认不再保留 `agent/gateway.py` 顶层兼容导出模块。
- WebSocket `hello client=web` 返回 web command profile 能力，默认不支持 `/history` 和 `/exit`。
- WebSocket `hello client=external` 打开 external command profile 能力，`/history` 进入 external profile，`/exit` 关闭当前 WS 连接。
- `/sessions rename <id> <title>`、`/sessions delete <id>` 和 `/sessions delete` 在 runtime slash command 层有覆盖。
- assistant Markdown 使用异步 KaTeX 支持 `$...$`、`$$...$$`、`\\(...\\)`、`\\[...\\]`；`\\bm{}` 映射到 `\\boldsymbol{}`。KaTeX 加载失败时保留原始公式文本且不阻断页面，`pre/code` 不参与数学渲染。

## 关键检查点

- API 测试只使用 fake runtime，不访问真实 LLM 或网络。
- gateway `--check` 仍只做配置检查，不启动 HTTP 服务。
- Web/API 层不反向进入 AgentLoop 之外的业务分支。

## Part 7 Turn Coverage

- WebRuntime accepts an optional `turn_id` and passes it to AgentLoop.
- WebSocket accepted, text, done, stopped, and error events carry the aligned turn id.
- Session history API exposes optional message turn fields.
- SSE status, delta, done, stopped, and error payloads carry one consistent turn id.
- WS 使用 `runtime_event` 信封转发 RuntimeEvent；SSE 使用 `event: runtime`，均保持旧 text/status/interaction 兼容。
- 浏览器 RuntimeEvent reducer 按 turn_id + sequence 忽略旧状态，并在 terminal turn Event 清理运行状态。
- Subagent child RuntimeEvent 按 root session/turn 归并，并按 agent/task 维护独立 sequence 与并行任务状态，不能跨 child 用同一 sequence 覆盖。
- Web `/help` 只新增顶层 `/subagent`；裸命令显示 mode、force-once、可用 Profile，并在 Tip 中提示 `auto/off/once`。`/reset` 与清空当前 Session 只清 one-shot、保留 mode。
- Subagent unavailable 时，Owner 的 `/subagent` 与 force-once 返回真实 message/hint；普通用户只看到暂时不可用并联系管理员，不直接展示 JSON、Prompt 文件名、初始化命令、`code` 或 `cause_code`；one-shot 仍只消费一次。公共 health 同样只返回通用 capability 状态。
- Subagent unavailable 且 Session 为 auto 时，普通 Web Turn 注入只返回通用不可用文案的 `delegate_tasks` facade，内部 cause 仅写日志与 trace；它不创建 child，防止模型用其它 Tool 冒充明确的子代理请求。
- Web LLM-facing ToolProvider 首轮只暴露 `discover_tools`；发现后才按当前 actor 可见集合动态增加 schema，未激活业务 Tool 不能执行。

## Part 8 Logging Coverage

- Gateway logging options split Agent lifecycle log, HTTP access log, HTTP server log, and workspace trace log.
- Terminal Agent log lines use `[YYYY-MM-DD HH:MM:SS] | LEVEL | component.event | fields` without milliseconds；TTY 下 WARNING 整行使用高亮红色，ERROR/CRITICAL 整行使用红色，普通日志继续按组件着色。
- Workspace trace writes JSONL to `logs/YYYY-MM-DD/trace.log` with `component` and no full internal logger name.
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
- 登录、注册、Owner 初始化、setup credential、账号改密和动态管理员创建用户的敏感输入框都有固定的小眼睛按钮；按钮可切换显示/隐藏并在表单重开时恢复隐藏状态，不进入 Tab 顺序。Enter 保留浏览器原生表单行为；Edge 原生密码 reveal/clear 控件被隐藏，避免双眼睛。
- 登录页、侧边栏和 favicon 使用用户选定的 A 版 ZC 星芒 Logo PNG 资产；用户入口采用 C 版交叠双字母结构与 F 版白底深蓝配色，取用户名前两个字符。
- Owner 和其他角色的聊天侧栏默认只列当前用户自己的 session；前端账号切换会清空上一账号的 active session 和 messages。
- Recent diagnostics 不出现在常驻 Web UI，也不保留 REST 表单入口；普通聊天通过 `diagnose_my_recent_activity` 自动诊断当前 Session 的上一轮或最近失败。
- 普通成功 HTTP 请求不写 Security Audit；认证失败、特权拒绝和安全相关操作继续审计。

## Part 10 Memory Coverage

- `/memory` 只展示当前 actor 的长期 Memory；Session Summary 和手动提取子命令均已删除。
- Owner Web 与 CLI 使用全局 Memory，普通用户使用各自目录；Web turn 绑定 Memory Tool、候选策略和 confirmation broker。
- `/mcp` 在 Web/external WS 使用同一共享 Runtime 摘要；MCP Elicitation 响应帧回传到 Runtime。
- Memory 写入通过普通对话授权；持久 trace/audit 不保存 query、写入内容或读取结果原文。
- Web 不提供 Memory 专用确认弹窗或编辑 API；Memory 授权通过普通对话完成。
