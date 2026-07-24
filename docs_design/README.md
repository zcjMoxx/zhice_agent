# ZhiCe-Agent 设计文档索引

本文档是 `docs_design/` 的阅读入口和维护规则。

## 文档类型

### 当前活文档

无日期文件名表示当前活文档，始终以最新代码和已接受的当前阶段口径为准。新人学习、实现对齐、架构判断优先阅读这些文档。

- `zhice-agent-overall-design.md`：总体设计，当前架构与长期路线图入口。
- `zhice-agent-part1-foundation-design.md`：Part 1，可运行底座。
- `zhice-agent-part2-no-tool-chat-design.md`：Part 2，无工具聊天链路。
- `zhice-agent-part3-tool-calling-design.md`：Part 3，工具调用。
- `zhice-agent-part4-exec-tool-design.md`：Part 4，安全 `exec` 工具。
- `zhice-agent-part5-skill-loader-design.md`：Part 5，Skill 同步、加载与执行。
- `zhice-agent-part6-web-minimum-design.md`：Part 6，Web 最小版、REST/SSE 兼容接口与 WebSocket 主通道。
- `zhice-agent-part6-web-ui-design.md`：Part 6，前端 UI、流式反馈、Markdown 渲染与会话操作。
- `zhice-agent-part7-turn-context-design.md`：Part 7，Turn 运行单元、持久化字段、最近 3 + 旧相关最多 3 的混合上下文选择，以及 endpoint token 预算。
- `zhice-agent-part8-gateway-agent-logging-design.md`：Part 8，Gateway / Agent 运行日志优化。
- `zhice-agent-part9-user-auth-permission-design.md`：Part 9，用户、登录与权限执行边界设计。
- `zhice-agent-part10-memory-design.md`：Part 10，已实现的 CLI/Owner workspace Memory、普通用户私有 Memory、对话式用户授权写入、服从 ContextBudget 的后台高置信提取与受控检索。
- `zhice-agent-part11-mcp-design.md`：Part 11，已实现并进入当前代码基线，包含 stdio / Streamable HTTP / SSE、常见 `mcpServers` 直贴、自动 Tool 发现、共享 Runtime、ArtifactGateway、Elicitation 与 `/mcp`；Windows OS 级 stdio 读取隔离仍待硬化。
- `zhice-agent-part12-hooks-design.md`：Part 12，已实现并关闭；当前基线包含 Agent 生命周期 RuntimeEvent、现有 WS/SSE/CLI、前端真实状态，以及显式配置、无 shell、受限执行的 pre/post Tool Hook Runtime。
- `zhice-agent-part13-subagent-design.md`：Part 13，已实现并进入当前代码基线；包含有界并行 `delegate_tasks`、独立 child AgentLoop/Session/RuntimeEvent scope、能力 Profile 与 shared-readonly/worktree/shared-exclusive 隔离。
- `zhice-agent-part14-external-channel-design.md`：Part 14，第一版已实现并进入当前代码基线；包含中性 Channel 协议、外部身份绑定、conversation route、跨渠道 Session 可见/续写边界、QQ 私聊/群聊能力、持久去重、限流、附件 guard、Markdown 出站和后续微信/飞书兼容边界。

第九部分用户、登录与权限执行边界已经落地：登录用户的账号自身、本人 Session、聊天、模型、安全工具、已安装 Skill、诊断和本人 Memory 是基础能力；RBAC 只保留跨用户管理、系统管理、审计、危险执行和全局 Skill 同步等特权。基础能力收敛见 `2026-07-16-authenticated-user-baseline-capabilities-design.md`；当前自助诊断和 Runtime Activity / Security Audit 拆分见 `2026-07-16-self-diagnostics-activity-audit-separation-design.md`。

第十部分 Memory 已完成代码落地并进入当前基线。当前实现口径以 `zhice-agent-part10-memory-design.md` 为准；初始作用域设计见 `2026-07-15-memory-boundary-design.md`，当前对话式授权调整见 `2026-07-16-conversational-memory-consent-design.md`，Memory list/search、核心运行 ID 和终端日志收敛见 `2026-07-16-memory-read-runtime-id-terminal-log-convergence-design.md`。后台 extraction 使用 session 模型的 failover-safe ContextBudget，来源 Turn 过多时先缩减输入，不绕过 endpoint token 上限。

第十一部分 MCP 已实现并进入当前代码基线。当前实现口径以 `zhice-agent-part11-mcp-design.md` 为准，本次边界和取舍记录见 `2026-07-17-mcp-tool-runtime-boundary-design.md`。Runtime 支持 stdio、Streamable HTTP 和 SSE，直接读取常见 `mcpServers` JSON，通过 `tools/list` 自动暴露有效 Tool，并提供共享连接、credential、OAuth refresh、ArtifactGateway、Elicitation 和 `/mcp`。stdio 当前使用专用临时 cwd、最小环境、无 shell、stderr 丢弃和 Windows Job Object 回收；真正的 OS 级读取隔离仍是明确的后续硬化项。

第十二部分生命周期事件与 Hook Runtime 已实现并关闭。当前实现口径以 `zhice-agent-part12-hooks-design.md` 为准，最终边界与取舍记录见 `2026-07-20-hook-runtime-boundary-design.md`，单 Hook 显式角色/权限豁免作用域见 `2026-07-21-hook-role-scope-design.md`。当前代码已经包含 turn/context/LLM/tool RuntimeEvent、现有 WS/SSE/CLI、前端状态，以及真实 pre/post Tool Hook 配置、Loader、Runner、安全重校验、身份作用域和异常策略。SkillExecutor、`skill.*` 和 ProgressSink 归入未来 Skill Runtime / Part 18，不是 Part 12 欠账。

第十三部分并行 Subagent 编排已经实现并进入当前代码基线。当前实现口径以 `zhice-agent-part13-subagent-design.md` 为准，边界取舍记录见 `2026-07-21-subagent-runtime-boundary-design.md`，启动能力分级与诊断证据闭环见 `2026-07-21-startup-capability-and-subagent-diagnostics-design.md`，可选能力告警出口收敛见 `2026-07-22-optional-capability-warning-surface-design.md`，内置能力与可选扩展启用状态见 `2026-07-22-built-in-capability-enable-state-design.md`，人类命令与机器错误载荷分层见 `2026-07-22-human-command-error-presentation-design.md`，按身份展示内部详情的边界见 `2026-07-22-role-aware-capability-error-presentation-design.md`。主 Agent默认直接完成简单任务；只有并行、上下文隔离、专业能力或独立复核收益明确时，才通过批量 `delegate_tasks` 在同一 Turn 内并行运行 child，再 fan-in 返回稳定、有界且允许 partial 的结果供父 Agent归纳。`/subagent` 的 `auto/off/once` 使用 Session sidecar 真值和原子 one-shot 消费；child 使用独立 AgentLoop、内部 Session、RuntimeEvent scope 和取消 token，以新鲜 child Session 开始任务，但继承父 Turn 的 failover-safe ContextBudget。Tool/Skill/MCP 能力经过父可见集合、Profile allow/deny 与内核 deny 三重收窄；可写任务进入独立 worktree，共享状态任务进入进程级 shared-exclusive lane。现有 RBAC、确认、Hook、workspace guard、MCP artifact 与审计链保持不变。核心启动依赖继续阻断；未配置的 Skill source、Subagent、MCP 作为正常 disabled，不报警；显式启用的可选扩展依赖异常和内置 Memory extraction Prompt 异常只局部禁用并通过结构化终端 WARNING 与 trace 告警；显式 Hook 安全策略非法时仍阻断。`/api/health` 只保留通用 capability 状态，聊天 Web 不常驻展示启动告警。CLI、本地操作者、Owner 和具备 `audit.read` 的管理员可查看真实原因；普通 Web 用户的 `/subagent`、force-once、unavailable Tool 和自助诊断只返回暂时不可用并联系管理员，真实 cause 继续保留在终端、trace 和有权限的诊断出口。当前上下文统一采用最近 3 个 Turn 加旧相关最多 3 个，并受 60 message 与 endpoint token budget 双重约束；详细设计见 `2026-07-22-endpoint-context-budget-and-hybrid-turn-selection-design.md`。自助诊断可沿父 Turn 的 root 关联读取安全 child terminal trace，旧 trace 若没有 child 终态证据则不能事后恢复具体根因。

第十四部分外部渠道第一版已经实现并进入当前代码基线。当前方案以 `zhice-agent-part14-external-channel-design.md` 为准，初始边界取舍记录见 `2026-07-23-qq-external-channel-boundary-design.md`，跨渠道 Session、用户自助解绑和 QQ Markdown 收敛见 `2026-07-23-cross-channel-session-binding-and-qq-markdown-design.md`，群聊手动一次性码边界见 `2026-07-24-qq-group-manual-binding-design.md`，群聊回复归属见 `2026-07-24-qq-group-reply-attribution-design.md`，Session 清空命令统一改名见 `2026-07-24-clear-session-command-rename-design.md`。第一条真实渠道选择 QQ：运行态使用官方 Python SDK 的 WebSocket 连接；渠道层已经建立中性事件、能力声明、外部身份绑定、conversation route、持久去重、per-conversation 串行、附件 guard 和 RuntimeEvent 出站渲染。Web/CLI 作为私有控制面可见本人跨渠道历史，QQ 私聊可跨端继续，QQ群聊在 Web 只读并通过派生新 Web Session 继续；外部入口不能反向管理其它渠道 Session。QQ 群聊回答通过官方 `message_reference` 引用触发者原消息，避免多人并发提问时归属混乱。CLI、Web、external WebSocket 与 QQ 当前统一使用 `/clear` 清空当前 Session，旧 `/reset` 不再执行清空。后续微信、飞书只新增 Adapter 和平台策略，不复制核心运行链。

原 Part 16 的 Capability Selection 子能力已提前完成，设计记录见 `2026-07-21-on-demand-tool-discovery-design.md`。当前 CLI/Web/child Turn 首轮只暴露 `discover_tools`，发现后下一 LLM 步只增加已激活 Tool schema；Catalog 先经过 actor/Profile 过滤，未激活 dispatch fail closed。Part 16 其它 Provider retry、系统级诊断和 MCP reload 仍按原路线保留。

维护规则：

- 当前活文档可以随着代码和阶段边界更新。
- 当前活文档不保留已经被放弃的旧方案细节，只保留必要的背景和当前准则。
- 如果专题设计落地后成为当前主线，应同步更新相关活文档。

### 日期设计记录

带 `YYYY-MM-DD-` 前缀的文件表示当次设计记录，用于保留演进痕迹。

维护规则：

- 日期设计记录完成并落地后原则上不再改写方案内容。
- 同一日期、同一功能且代码尚未落地时，直接更新当天同一份日期设计记录，不为讨论中的每次口径变化重复建文件。
- 跨日期继续迭代时，按新日期新增设计记录，在背景里说明承接了哪个旧方案、旧方案哪里不足、这次如何改进。
- 如果后续设计已经改变了旧日期设计记录的方案，不回头重写旧正文；只在旧文档标题下方增加 `> 说明：...`，说明当前代码采用什么、旧方案哪里不再适用、应参考哪份新文档或当前活文档。
- 允许修复链接、错别字、编码、排版等不改变方案含义的维护。
- 日期设计记录和当前代码冲突时，以当前活文档和当前代码为准。

## 推荐阅读顺序

1. `zhice-agent-overall-design.md`
2. `zhice-agent-part1-foundation-design.md`
3. `zhice-agent-part2-no-tool-chat-design.md`
4. `zhice-agent-part3-tool-calling-design.md`
5. `zhice-agent-part4-exec-tool-design.md`
6. `zhice-agent-part5-skill-loader-design.md`
7. `zhice-agent-part6-web-minimum-design.md`
8. `zhice-agent-part6-web-ui-design.md`
9. `zhice-agent-part7-turn-context-design.md`
10. `zhice-agent-part8-gateway-agent-logging-design.md`
11. `zhice-agent-part9-user-auth-permission-design.md`
12. `zhice-agent-part10-memory-design.md`
13. `zhice-agent-part11-mcp-design.md`
14. `zhice-agent-part12-hooks-design.md`
15. `zhice-agent-part13-subagent-design.md`
16. `zhice-agent-part14-external-channel-design.md`
17. `2026-07-24-qq-group-reply-attribution-design.md`
18. `2026-07-24-qq-group-manual-binding-design.md`
19. `2026-07-24-qq-binding-keyboard-rendering-fix.md`
20. `2026-07-23-cross-channel-session-binding-and-qq-markdown-design.md`
21. `2026-07-23-qq-external-channel-boundary-design.md`
22. `2026-07-22-endpoint-context-budget-and-hybrid-turn-selection-design.md`
23. `2026-07-22-endpoint-budget-config-simplification-design.md`
24. `2026-07-22-immediate-turn-reference-retention-design.md`
25. `2026-07-22-human-command-error-presentation-design.md`
26. `2026-07-22-built-in-capability-enable-state-design.md`
27. `2026-07-22-optional-capability-warning-surface-design.md`
28. `2026-07-21-startup-capability-and-subagent-diagnostics-design.md`
29. `2026-07-21-subagent-runtime-boundary-design.md`
30. `2026-07-21-hook-role-scope-design.md`
31. `2026-07-21-on-demand-tool-discovery-design.md`
32. `2026-07-20-hook-runtime-boundary-design.md`
33. `2026-07-17-mcp-tool-runtime-boundary-design.md`
34. `2026-07-16-memory-extraction-concurrency-design.md`
35. `2026-07-16-prompt-language-convergence-design.md`
36. `2026-07-16-minimal-memory-content-protocol-design.md`
37. `2026-07-16-turn-done-output-preview-design.md`
38. `2026-07-16-terminal-adaptive-duration-design.md`
39. `2026-07-16-remove-unclosed-session-summary-design.md`
40. `2026-07-16-memory-command-display-and-session-summary-design.md`
41. `2026-07-16-memory-command-semantics-design.md`
42. `2026-07-16-background-memory-extraction-and-trace-convergence-design.md`
43. `2026-07-16-memory-read-runtime-id-terminal-log-convergence-design.md`
44. `2026-07-16-conversational-memory-consent-design.md`
45. `2026-07-15-memory-boundary-design.md`
46. `2026-07-10-session-model-preference-scope-design.md`
47. `2026-07-08-user-auth-permission-boundary-design.md`
48. `2026-07-06-context-relevance-selection-design.md`
49. `2026-07-06-next-stage-sequencing-design.md`
50. `2026-07-04-turn-runtime-and-context-design.md`
51. `2026-07-02-gateway-runtime-logging-design.md`
52. 按需阅读其它日期设计记录，理解某次改动的背景和权衡。

## 日期设计记录清单

- `2026-07-24-qq-group-reply-attribution-design.md`
- `2026-07-24-clear-session-command-rename-design.md`
- `2026-07-24-qq-group-manual-binding-design.md`
- `2026-07-24-qq-binding-keyboard-rendering-fix.md`
- `2026-07-23-cross-channel-session-binding-and-qq-markdown-design.md`
- `2026-07-23-qq-external-channel-boundary-design.md`
- `2026-07-22-endpoint-context-budget-and-hybrid-turn-selection-design.md`
- `2026-07-22-endpoint-budget-config-simplification-design.md`
- `2026-07-22-immediate-turn-reference-retention-design.md`
- `2026-07-22-human-command-error-presentation-design.md`
- `2026-07-22-role-aware-capability-error-presentation-design.md`
- `2026-07-22-runtime-guidance-and-capability-copy-alignment-design.md`
- `2026-07-22-direct-trace-evidence-diagnostics-design.md`
- `2026-07-22-diagnostics-prompt-boundary-design.md`
- `2026-07-22-exec-prompt-boundary-design.md`
- `2026-07-22-web-math-rendering-and-init-copy-design.md`
- `2026-07-22-built-in-capability-enable-state-design.md`
- `2026-07-22-optional-capability-warning-surface-design.md`
- `2026-07-21-startup-capability-and-subagent-diagnostics-design.md`
- `2026-07-21-subagent-runtime-boundary-design.md`
- `2026-07-21-hook-role-scope-design.md`
- `2026-07-21-on-demand-tool-discovery-design.md`
- `2026-07-20-hook-runtime-boundary-design.md`
- `2026-07-17-test-runtime-optimization-design.md`
- `2026-07-17-mcp-tool-runtime-boundary-design.md`
- `2026-07-16-remove-unclosed-session-summary-design.md`
- `2026-07-16-memory-command-display-and-session-summary-design.md`
- `2026-07-16-memory-command-semantics-design.md`
- `2026-07-16-terminal-adaptive-duration-design.md`
- `2026-07-16-turn-done-output-preview-design.md`
- `2026-07-16-minimal-memory-content-protocol-design.md`
- `2026-07-16-prompt-language-convergence-design.md`
- `2026-07-16-memory-extraction-concurrency-design.md`
- `2026-07-16-background-memory-extraction-and-trace-convergence-design.md`
- `2026-07-16-memory-read-runtime-id-terminal-log-convergence-design.md`
- `2026-07-16-conversational-memory-consent-design.md`
- `2026-07-15-memory-boundary-design.md`
- `2026-07-11-api-error-code-contract-design.md`
- `2026-07-11-owner-session-admin-route-diagnostics-tool-design.md`
- `2026-07-11-password-change-reauthentication-design.md`
- `2026-07-10-owner-admin-delegation-design.md`

- `2026-06-11-cli-gateway-entry-design.md`
- `2026-06-11-console-color-design.md`
- `2026-06-11-local-llm-secret-config-design.md`
- `2026-06-11-packaging-entry-design.md`
- `2026-06-12-auto-session-default-design.md`
- `2026-06-12-cli-session-commands-design.md`
- `2026-06-14-litellm-provider-design.md`
- `2026-06-15-model-command-and-endpoint-failover-design.md`
- `2026-06-18-console-spinner-design.md`
- `2026-06-19-endpoint-config-simplification-design.md`
- `2026-06-21-readme-endpoint-doc-alignment-design.md`
- `2026-06-21-runtime-template-chinese-localization-design.md`
- `2026-06-21-skill-repo-placeholder-design.md`
- `2026-06-21-skill-sync-default-refresh-design.md`
- `2026-06-21-test-case-doc-coverage-design.md`
- `2026-06-30-skill-source-namespace-design.md`
- `2026-07-01-design-doc-governance-design.md`
- `2026-07-01-web-core-import-and-model-selector-design.md`
- `2026-07-01-web-minimum-implementation-design.md`
- `2026-07-01-web-stream-command-markdown-design.md`
- `2026-07-01-websocket-primary-chat-design.md`
- `2026-07-02-gateway-import-convergence-design.md`
- `2026-07-02-gateway-runtime-logging-design.md`
- `2026-07-02-websocket-command-channel-design.md`
- `2026-07-04-turn-runtime-and-context-design.md`
- `2026-07-06-context-relevance-selection-design.md`
- `2026-07-06-gateway-default-port-design.md`
- `2026-07-06-next-stage-sequencing-design.md`
- `2026-07-06-ws-client-profile-naming-design.md`
- `2026-07-08-user-auth-permission-boundary-design.md`
- `2026-07-10-session-model-preference-scope-design.md`

## 新设计写法

涉及核心边界、三个及以上文件、运行时配置、Tool、Skill、Session 或 AgentLoop 行为变化时，先新增日期设计记录：

```text
docs_design/YYYY-MM-DD-{topic}-design.md
```

新日期设计记录至少说明：

- 背景：承接哪个旧方案或当前活文档中的哪段口径。
- 问题：旧方案哪里不够。
- 目标和非目标。
- 模块设计、数据流、变更文件。
- 测试方案和验收标准。

代码落地后，再把已经成为当前准则的内容收敛进无日期活文档。

如果新方案覆盖了旧日期设计记录，不要改写旧文档正文。旧文档只在开头补一段说明，例如：

```markdown
> 说明：这是一份历史实验记录。当前代码并不采用“每次启动都新建唯一会话”的行为，而是默认使用当天会话 `chat-YYYYMMDD`，并通过 `/new` 显式新建临时会话。
```
