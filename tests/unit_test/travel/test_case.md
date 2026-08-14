# 智能旅行规划测试说明

## 测试目标

- 旅行规划 Prompt 与官方 Skill 文档必须公开同一套每日分钟、强度公式和 pace 硬上限，并要求按拒绝原因最多定向重试一次，避免真实 AgentLoop 对确定性门槛盲目循环。

验证 Milestone 19 的领域协议、纯计算 optimizer、用户隔离持久化、内部 finalizer、RuntimeEvent、只读 MCP 适配边界、quick/deep Agent 调用序列和 Travel REST API。默认测试不访问真实外网，也不读取真实 API key 或 Cookie。

## 用例覆盖

- `TravelRequestV1`：目的地、日期、总天数、人数、预算、节奏与 quick/deep 边界。
- `EvidenceItemV1`：HTTP(S) URL、时间戳、来源类型、live/snapshot/historical/estimate/unknown 一致性、SHA-256、短摘录截断、URL/content 去重与 evidence id 重映射。
- `TravelPlanV1`：每日活动、时间重叠、路线段、跨区域、预算区间、未知证据、Credential-like 字段、非法来源与计划大小。
- optimizer：正常选择、硬预算、每日时间、跨城路线、开放时间、活动重叠、明显折返、取消、错误结果与输出上限。
- Store/Tool：actor context 派生路径、owner 重写、Session/Turn 关联、列表、读取、删除、跨用户隔离和 `travel.plan_ready`；finalizer Tool schema 明确公开 request/evidence/day/activity/route/budget 嵌套字段白名单并拒绝 `mode`、`metadata`、`total_minutes` 等非协议字段。
- Fake MCP：Open-Meteo 预报/历史标签与窗口边界；小红书只读 Catalog、Cookie volume guard、提示注入正文仅作为 untrusted content、英文筛选枚举到上游中文值映射、默认筛选 UI 绕过、`max_results` 强制截断、非 JSON 兼容和对外结构化输出上限；一个 Server schema 失败不抹掉其它来源 Catalog。
- AgentLoop：Fake LLM 走真实 tool loop；quick 不调用 child，deep 一批最多三个 `travel-research` child，部分失败后仍继续 optimizer/finalizer。
- API：登录壳下的 `/api/travel/plans` 列表、读取、删除与稳定错误码。
- 生成连续性：应用级旅行订阅跨路由常驻；恢复 API 仅投影当前 actor 拥有的 `channel=travel` Session，覆盖 running/pending/completed/failed/stopped/idle，并拒绝其它用户或非旅行 Session。
- 完成提醒：后台生成完成后按用户保存 `0/1` 未读状态，聊天旅行入口显示数字徽标；进入旅行页即清除，当前已在旅行页时不产生未读。
- 兼容提取：旧 `/requirements/extract` 仍覆盖严格白名单、非法结构、Provider 失败与安全错误，但不再是旅行工作台正常入口。
- 最小确认：固定公历节日加明确天数可确定性补齐日期；同行人群、节奏和模式不作为用户错误阻塞，人数仍需显式确认；系统策略在提交文本中标记为假设。
- 接待 Agent：新旅行 Session 默认 `travel_phase=intake`，继续使用当前 session 默认 LLM；真实装配只暴露 `update_travel_draft`、`offer_main_chat_handoff` 与 `confirm_and_start_travel_planning`，不创建 MCP、exec、Skill 或 subagent 能力，并注入独立 `travel_intake` Prompt。
- 渐进补全：Agent 通过增量 patch 合并、清空和校验同一草稿；问候或旅行知识讨论允许空 patch，条件缺失时自然追问一至两项，完整时显示确认入口，不再由前端套固定问题列表。
- 阶段切换：actor-owned 确认接口拒绝不完整、非法、越权或非旅行草稿；成功后原子写 `travel_phase=planning`，正式 Turn 才装配旅行 Prompt、内部规划 Tool、来源 MCP、optimizer 与候选审核。
- 自然语言确认：条件齐全后用户回复“确认 / 开始执行”时，接待 Agent 必须调用确认 Tool 复用同一服务端校验，并通过 `travel.planning_confirmed` 让前端进入生成态、让 WebSocket 同请求续跑正式规划。
- 接待恢复：`travel_intake_turn_ids` 投影 AgentLoop 保存的 user/assistant 消息；刷新和已完成计划均通过 travel draft API 恢复自然回复，完成的接待 Turn 在统一任务列表中仍是 collecting，不误标为规划失败。
- 手工入口：“补充数据”只打开表格且不调用需求提取 LLM；手工路径同样必须补齐关键字段并明确确认后才执行。
- 旅行终态：普通文本回复不能作为完成；Gateway 在同一 travel Session 内最多自动续跑两次，只有 `travel.plan_ready`、结构化澄清、用户停止或稳定错误才能结束。
- 结构化澄清：`request_travel_clarification` 一次返回全部用户问题，前端回到需求对话；Agent/Tool/MCP/Provider 自身问题不得伪装成用户信息不足。
- 输入交互：Enter 发送、Shift+Enter 换行，界面不展示快捷键说明。
- 用户视角过程：隐藏 `load_skills`/`discover_tools` 等内部动作；高德、天气、铁路、网页与小红书只读查询投影来源、查询目标、返回数量和最多五个候选摘要；optimizer 展示比较数量、采用方案、预算及路线门控；非 JSON、超长与失败结果安全降级且不影响普通 Web channel。
- 外部来源完成门槛：travel Session 对当前可用地图、天气、铁路、网页与社区来源登记 expected/attempted/successful；漏调已配置来源、全部失败或最终只有 model estimate evidence 时 finalizer 拒绝保存；账本不保留参数/正文/URL/Secret，成功后清理且有进程级 Session 上限。
- 应用 Session：旅行 Session 以 `channel=travel` 持久化并可继续供 AgentLoop 使用，但从普通聊天 Session 列表排除；普通 Web Session 保持可见。
- 对话历史：用户确认后需求问答先写入 actor-owned `channel=travel` Session；历史计划按 `source_session_id` 把需求确认阶段的 user/assistant 文本恢复到原 TravelPlanForm 问答窗口，拒绝跨用户和非 travel 写入，并过滤 Tool、规划执行回复、空 tool-call、自动续跑与纯 JSON；相同请求幂等，超量和超长输入拒绝；删除计划同步删除关联 travel Session 与需求问答。
- 草稿与统一列表：首次有效输入即创建 travel Session，collecting 阶段可原子替换需求问答并保存严格结构化草稿；刷新可恢复且不误判为运行中；正式生成复用同一 Session，已有 Turn 后拒绝历史覆盖；左栏统一投影 collecting/running/awaiting_candidate/failed/completed，未完成任务可删除。
- 完整进度：当前规划超过 12 条用户可见记录时保留首尾全过程，不再静默删除较早来源查询；刷新或恢复候选状态后按 session 恢复过程，多个旅行 session 不互串。
- 来源稳健性：Tavily 强制关闭原始正文、限制结果数并修正 `fast/ultra-fast + country`；小红书异常组分别映射本地上游未启动、超时、认证和限流，且不泄漏异常正文。
- 候选确认：optimizer 返回全部可行候选的受限摘要；多候选写入 actor-scoped Store 并发出等待事件，刷新可恢复；未知选择、越权 Session、未确认和最终计划不匹配均拒绝；前端卡片提交真实选择后续跑同一旅行 Session；用户文案不泄漏候选机器 ID。
- 本地上游生命周期：loopback 小红书 URL 从配置派生唯一端口，固定 workspace 二进制由 Gateway 托管；远端/Docker 上游、缺二进制和已有外部监听不被错误接管，Gateway 只关闭自己创建的进程树。
- 大结果裁剪：Tavily 风格 structuredContent 超限时删除 raw content、限制列表和摘要长度并保持 JSON 可解析，不再整次失败；非搜索 Tool 不套用搜索参数。
- MCP 结果展示：兼容 structuredContent 与 text 形成的连续多段 JSON、`data.text` 嵌套 JSON、小红书 `note_card` 和 Tavily content 摘要；区分真实空结果与格式暂不可展示。
- 用户侧命名：12306 等已知来源即使收到通用 `mcp__... 执行完成` 标题，也只显示平台产品名，不暴露内部 Tool 标识。
- 强制候选审核：旅行频道 finalizer 在无候选审核、待选择或候选不匹配时拒绝保存；只有至少两个候选已经展示且用户选择后才能完成。
- 信息化地图与未知项：无 Key 时仍展示逐日地点、交通方式、距离和时长；有坐标时显示编号地点、真实路线或顺序参考线；来源失败技术原句在 UI 投影为原因和重查建议。
- 旅行助手边界：问候、身份、能力、目的地知识和条件修正由接待 Agent 直接生成自然回复；无关问题不回答实质内容，必须通过结构化 handoff event 携带原问题回主聊天，且不自动发送。
- 交接持续性：无关问题的交接卡不会在下一条操作追问发送前消失；只有真实旅行字段变化或用户主动关闭才退出当前交接提示。
- 能力隔离：接待阶段即使 Prompt 判断偏差也无法调用通用 Tool 或旅行外部来源；规划阶段不再看到接待 Tool，阶段切换后接待 Tool 自身也会拒绝执行。
- 固定话术清理：TravelPlanForm 不再包含 greeting/identity/capability/help/unrelated 分支，不再调用提取 API；固定文本只保留异常兜底、按钮和表单校验提示。
- 并发工作台：正式规划运行时仍可脱离当前 Session 新建独立草稿；后台 Session 的完成事件只刷新任务列表和未读提醒，不覆盖前台草稿。
- 删除竞态：运行中 Session 必须在活动 Turn 完成后才删除文件和索引，避免后台回写复活并被识别成 `cli_legacy`。
- 地图坐标：新计划每个活动必须包含可绘制经纬度，路线 `path` 使用坐标对象；历史计划缺坐标时前端地理编码后仍能绘制地点。
- 检索恢复：网页与社区首次空结果或临时失败要求一次收窄重试，第二次后不循环；认证失败不盲目重试；Tavily 已有成功结果不被后续补充超时清除。
- 小红书认证：显式空 `feeds` 会核对独立 MCP 登录态，未登录返回认证错误；普通 `items` 兼容结果不触发额外登录检查。
- 小红书 Cookie 兼容：本地 supervisor 优先选择版本最高的 RedNote 兼容二进制并回退通用文件；自有 sidecar 监听 Cookie 文件签名变化后自动重启加载新登录态，外部 listener 不接管。
- 小红书扫码闭环：Cookie 内容稳定更新后自动关闭登录助手、重载 owned sidecar，重载完成前保持 pending；同内容重写不触发重载，本地 upstream 不继承终端代理。
- 页面终态：规划中和历史计划隐藏确认按钮，进入规划或完成时清理陈旧的需求对话错误；主聊天交接草稿不等待会话列表刷新。
- 结果格式兼容：小红书来源投影同时识别 snake_case 与 RedNote camelCase 的笔记卡片、标题和用户昵称，已返回的公开笔记必须形成可读筛选摘要。
- 实时状态收敛：需求回复期间可脱离旧 Session 新建独立计划；回合结束会读取权威草稿补齐交接卡，旧异步读取和晚到事件不得覆盖新工作区。
- 完成进度：打开已保存计划时历史 `solve` 缓存不能覆盖完成态，缺少终态记录时只补一次，六个进度节点全部显示完成。

## 关键检查点

- 模型提交的 `owner_user_id` 和 `plan_id` 永远不作为可信身份；finalizer 用 ToolExecutionContext 重写。
- 酒店 POI、未开售车票和历史天气的语义由 Prompt/计划标签保留，不以实时房态、无票或预报展示。
- 小红书 Server Catalog 不存在发布、评论、点赞、收藏或删除 Tool。
- API key、Cookie、Authorization 和 Token 字段不能进入 TravelPlanV1。
- 真实外部 smoke 位于 `tests/integration_test/travel`，只有显式环境变量开启；缺少凭据时默认跳过。
- Prompt 与 Skill 同时列出 TravelRequestV1、EvidenceItemV1 精确 allowlist，避免模型在长响应后依赖 schema 错误逐字段猜测。
- 非 model_estimate evidence 的 source URL 缺失时，finalizer 错误正文与 metadata 都包含安全字段路径，供唯一一次定向修正使用。
