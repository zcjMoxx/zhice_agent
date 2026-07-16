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
- `zhice-agent-part7-turn-context-design.md`：Part 7，Turn 运行单元、持久化字段与上下文治理。
- `zhice-agent-part8-gateway-agent-logging-design.md`：Part 8，Gateway / Agent 运行日志优化。
- `zhice-agent-part9-user-auth-permission-design.md`：Part 9，用户、登录与权限执行边界设计。
- `zhice-agent-part10-memory-design.md`：Part 10，已实现的 CLI/Owner workspace Memory、普通用户私有 Memory、对话式用户授权写入、后台高置信提取与受控检索。

第九部分用户、登录与权限执行边界已经落地：登录用户的账号自身、本人 Session、聊天、模型、安全工具、已安装 Skill、诊断和本人 Memory 是基础能力；RBAC 只保留跨用户管理、系统管理、审计、危险执行和全局 Skill 同步等特权。基础能力收敛见 `2026-07-16-authenticated-user-baseline-capabilities-design.md`；当前自助诊断和 Runtime Activity / Security Audit 拆分见 `2026-07-16-self-diagnostics-activity-audit-separation-design.md`。

第十部分 Memory 已完成代码落地并进入当前基线。当前实现口径以 `zhice-agent-part10-memory-design.md` 为准；初始作用域设计见 `2026-07-15-memory-boundary-design.md`，当前对话式授权调整见 `2026-07-16-conversational-memory-consent-design.md`，Memory list/search、核心运行 ID 和终端日志收敛见 `2026-07-16-memory-read-runtime-id-terminal-log-convergence-design.md`。后续开发从 Part 11 MCP 开始。

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
13. `2026-07-16-memory-extraction-concurrency-design.md`
14. `2026-07-16-prompt-language-convergence-design.md`
15. `2026-07-16-minimal-memory-content-protocol-design.md`
16. `2026-07-16-turn-done-output-preview-design.md`
17. `2026-07-16-terminal-adaptive-duration-design.md`
18. `2026-07-16-remove-unclosed-session-summary-design.md`
19. `2026-07-16-memory-command-display-and-session-summary-design.md`
20. `2026-07-16-memory-command-semantics-design.md`
21. `2026-07-16-background-memory-extraction-and-trace-convergence-design.md`
22. `2026-07-16-memory-read-runtime-id-terminal-log-convergence-design.md`
23. `2026-07-16-conversational-memory-consent-design.md`
24. `2026-07-15-memory-boundary-design.md`
25. `2026-07-10-session-model-preference-scope-design.md`
26. `2026-07-08-user-auth-permission-boundary-design.md`
27. `2026-07-06-context-relevance-selection-design.md`
28. `2026-07-06-next-stage-sequencing-design.md`
29. `2026-07-04-turn-runtime-and-context-design.md`
30. `2026-07-02-gateway-runtime-logging-design.md`
31. 按需阅读其它日期设计记录，理解某次改动的背景和权衡。

## 日期设计记录清单

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
