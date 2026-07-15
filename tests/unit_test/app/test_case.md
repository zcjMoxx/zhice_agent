# App / Web gateway tests

## 测试目标

覆盖 Part 6 Web 最小版的 HTTP app、API schema、静态资源服务和 core 直接导入。

## 用例覆盖

- `/health` 和 `/api/health` 只返回基础状态、当前模型和 auth 初始化状态，不暴露 workspace/session 路径。
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

## 关键检查点

- API 测试只使用 fake runtime，不访问真实 LLM 或网络。
- gateway `--check` 仍只做配置检查，不启动 HTTP 服务。
- Web/API 层不反向进入 AgentLoop 之外的业务分支。

## Part 7 Turn Coverage

- WebRuntime accepts an optional `turn_id` and passes it to AgentLoop.
- WebSocket accepted, text, done, stopped, and error events carry the aligned turn id.
- Session history API exposes optional message turn fields.
- SSE status, delta, done, stopped, and error payloads carry one consistent turn id.

## Part 8 Logging Coverage

- Gateway logging options split Agent lifecycle log, HTTP access log, HTTP server log, and workspace trace log.
- Terminal Agent log lines use `[YYYY-MM-DD HH:MM:SS] | LEVEL | component.event | fields` without milliseconds, and can color the timestamp and component/event segment on TTY.
- Workspace trace writes JSONL to `logs/YYYY-MM-DD/trace.log` with `component` and no full internal logger name.
- Logging configuration is idempotent and can disable terminal Agent logs while keeping trace on.
- Preview helpers redact sensitive fields, collapse multiline text, and truncate long values.
- WebRuntime keeps correlated `chat.accepted` and `chat.done` events at DEBUG with `session_id` and `turn_id`, while stop/error events remain visible at higher levels.

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
- Recent diagnostics 不出现在常驻 Web UI，也不保留 REST 表单入口；诊断由 `diagnose_my_recent_activity` 工具按当前用户 trace/audit 证据触发。
