# 智策 Agent 第十一部分详细设计文档：MCP Tool 接入

> 状态：已实现并进入当前代码基线；Windows OS 级 stdio 读取隔离仍待硬化
>
> 设计记录：`docs_design/2026-07-17-mcp-tool-runtime-boundary-design.md`

> 实现说明：当前代码已完成配置解析、三种 transport、自动 Tool Catalog、共享 Runtime、OAuth refresh、同步 Adapter、actor-scoped artifact、Elicitation、`/mcp` 和有界关闭。stdio 已限制为专用临时 cwd、最小环境、无 shell 和 Windows Job Object 回收；由于仓库尚无 AppContainer/受限令牌基础设施，“进程不能读取用户目录”不能由 cwd/env 单独保证，验收标准第 7 项中的 OS 强隔离留作后续安全硬化。

---

## 1. 背景

ZhiCe-Agent 当前通过同步 `ToolProvider -> ToolRegistry -> ToolResult` 链路调用内置 Tool。Part 11 在这条链路上增加 MCP Client，使 Agent 能连接本地或远程 MCP Server，并把 Server 发现的 Tool 当作普通 Tool 调用。

MCP Server 的外部业务行为由 Server 和外部系统负责。ZhiCe-Agent 不判断发送邮件、修改 Issue、删除云端数据等外部行为是否危险，也不额外增加本地风险确认。ZhiCe-Agent 只保护自身边界：

- workspace 文件边界和本地进程沙箱。
- workspace 配置和 credential。
- Tool schema、参数、输出和超时。
- Session、日志和审计中的敏感信息。
- 调用失败后的重复执行风险。

---

## 2. 最终原则

1. 用户直接粘贴常见的 `mcpServers` 配置。
2. Runtime 连接 Server 后通过 `tools/list` 自动发现 Tool。
3. 通过名称、schema 和大小校验的 Tool 全部进入 Catalog，并向所有正常登录用户开放。
4. ZhiCe-Agent 不配置 Tool allowlist、风险等级或本地逐次确认。
5. MCP annotations 作为远端元数据保存，不参与本地授权判断。
6. MCP Server 发起 Elicitation 或其它交互请求时，ZhiCe-Agent 原样转给当前用户并回传响应。
7. MCP 配置、credential、Tool Catalog、远程连接和 stdio 进程都属于 workspace，所有正常登录用户共享。
8. stdio 进程不能直接访问用户目录，只能使用自己的临时沙箱；文件读取、保存和下载落盘由 Agent 文件服务按当前 actor 执行。
9. AgentLoop、LLMProvider 和 SessionStore 保持同步协议，不 import MCP SDK。
10. CLI、Web 和 external WebSocket 使用相同的 `/mcp`。

---

## 3. 实现范围

- `config/config.example.yml` 的 `mcp` 模板和运行态 `${ZHICE_AGENT_WORKSPACE}/config/config.yml` 的 `mcp` 分区。
- 常见 `mcpServers` JSON 配置解析。
- `stdio`、`streamable_http` 和旧 `sse` transport。
- Header、Bearer、直接/env credential 和 OAuth token refresh。
- initialize、`tools/list`、`tools/call` 和 Elicitation 转发。
- process-wide `McpRuntime` 和独立 asyncio event-loop thread。
- workspace 共享配置、credential、Tool Catalog、远程连接和 stdio 进程。
- MCP result artifact 到 Agent 用户文件目录的受控导入。
- 本地名称、schema/description/数量限制和结果归一化。
- Runtime Activity、Security Audit、trace 和 `/mcp`。

---

## 4. 现有边界接入

AgentLoop 继续只消费现有 ToolProvider：

```python
class ToolProvider(Protocol):
    def definitions(self) -> list[dict[str, Any]]: ...
    def execute(self, name: str, args: dict[str, Any]) -> ToolResult: ...
```

MCP Tool 通过 `McpToolAdapter` 注册到 ToolRegistry，复用工具名唯一性、JSON object 参数校验、错误包装和输出截断。

```python
UserScopedToolProvider(
    ...,
    extra_tools=mcp_runtime.tools_for_actor(actor),
)
```

所有 actor 获得相同的 MCP Tool 定义并复用同一 Server connection。adapter 携带 actor 只用于把 MCP 返回的 artifact 交给当前用户的文件服务。MCP Server 不自动获得 Session、Memory、auth DB、用户目录或完整 actor 数据。

---

## 5. 总体架构

```text
config/config.yml#mcp
  -> McpConfigLoader
  -> McpServerSpec[]
  -> process-wide McpRuntime
       -> dedicated asyncio event-loop thread
       -> ServerConnection[server_id]
            -> stdio / Streamable HTTP / SSE
            -> workspace credential
            -> shared stdio temp sandbox
       -> initialize + tools/list
       -> McpCatalogSnapshot
            -> all valid McpToolDescriptor
  -> McpResultAdapter
       -> text / JSON -> ToolResult
       -> file / binary / resource -> McpArtifactGateway
            -> ActorFileService
            -> current actor files/mcp/
  -> McpToolAdapter[]
  -> ToolRegistry
  -> AgentLoop
```

Runtime 管连接、发现、调用、Elicitation、重连和关闭；Catalog 保存全部有效远端 Tool；Adapter 负责同步到异步的调用桥接。

---

## 6. 配置设计

### 6.1 文件位置

```text
仓库模板：config/config.example.yml 的 mcp 分区
运行配置：${ZHICE_AGENT_WORKSPACE}/config/config.yml 的 mcp 分区
```

- 仓库模板不保存真实 credential。
- 运行态 credential 可以写直接值，也可以使用 `${ENV_VAR}`。
- 缺少配置表示 MCP 未启用。
- 配置在 CLI/Gateway 启动时加载，修改后重启生效。

### 6.2 迁移常见 MCP 配置

```yaml
mcp:
  servers:
    filesystem:
      command: npx
      args: [-y, "@modelcontextprotocol/server-filesystem", "."]
```

从其它客户端复制配置时，将 `mcpServers` 下的 server 项迁入上述 `servers` mapping；用户不需要提前填写 Tool 名，Runtime 连接成功后通过 `tools/list` 获取。

### 6.3 固定解析规则

`config.yml` 的 `mcp.servers` 不是任意 mapping。Loader 识别：

| 字段 | 作用 |
| --- | --- |
| `command` / `args` / `cwd` / `env` | stdio |
| `url` / `headers` | Streamable HTTP 或 SSE |
| `proxy_mode` | 远程 transport 的 `direct`（默认）或显式 `environment` |
| `transport` / `type` | 显式指定 transport |
| `oauth` | access/refresh token、token URL 和 client credential |
| `startup_timeout_seconds` | 默认 15 |
| `connect_timeout_seconds` | 默认 15 |
| `call_timeout_seconds` | 默认 60 |

存在 `command` 时识别为 stdio；存在 `url` 且未指定 transport 时识别为 Streamable HTTP；明确写 `sse` 时使用 SSE。远程 transport 默认 `proxy_mode: direct`，不会因启动终端残留代理变量而改变路由；确需读取 httpx 支持的代理、CA 或 netrc 环境时必须显式写 `proxy_mode: environment`。Server 专有参数放在 args、env、headers 或 oauth。未识别字段返回明确配置错误。

其它 MCP Client 的 JSON 配置可以复用常见 server 字段，但需要放入统一 YAML 的 `mcp.servers` 分区；客户端专有占位符需要转换为 `${ENV_VAR}`。

### 6.4 Credential

stdio env、HTTP Header、Bearer token 和 OAuth 字段都支持直接值或 `${ENV_VAR}`。credential 不进入 Session、ToolResult、`/mcp`、trace、Runtime Activity 或 Security Audit 参数摘要。

---

## 7. Tool 发现与 Catalog

```text
connect server
  -> initialize
  -> tools/list
  -> validate name/schema/size
  -> build descriptors
  -> publish catalog
```

`tools/list` 返回 raw name、description、input schema 和 annotations。通过校验的 Tool 全部生成 adapter。

本地名称：

```text
mcp__{server_id}__{normalized_remote_name}
```

名称使用安全字符，总长度最多 64；截断追加稳定 hash；碰撞时拒绝冲突 Tool；不能覆盖内置 Tool。

默认上限：

```text
max_servers = 16
max_tools_per_server = 64
max_tools_total = 128
max_tool_description_chars = 1200
max_tool_schema_chars = 16000
max_schema_depth = 12
```

input schema 顶层必须是 object。超限或非法 Tool 不进入 Catalog，Server 标记 degraded。

---

## 8. 连接与内部隔离

每个 configured MCP Server 维护一个 workspace 系统级 connection 或 stdio 进程，所有用户共享同一实例、credential 和 Tool Catalog。

stdio 进程运行在独立的 MCP runtime sandbox：

- executable、args 和 env 只能来自配置。
- 不通过 shell 拼接 command。
- 不能直接读取或写入 `contexts/users/`、Session、Memory、config 和 credential 文件。
- 只允许写入 `state/mcp_runtime/{server_id}/tmp` 之类的专用临时目录。
- 临时目录有大小、文件数、生命周期和清理限制。
- 应用 OS sandbox、超时、输出限制和退出回收。

MCP Tool 如果需要读取用户文件，由 Agent 文件服务先按 actor 校验路径，再把明确选择的内容或临时副本交给 MCP。MCP Tool 如果返回文件、下载内容或 JSON/文本结果，由 Agent 结果适配层决定是否写入当前用户目录；MCP Server 本身不能直接选择用户目录中的目标路径。

---

## 9. Runtime 生命周期

启动：

1. 读取并规范化 `mcpServers`。
2. 启动 event-loop thread。
3. 连接远程 Server。
4. 启动 configured stdio 系统级沙箱进程。
5. initialize 和 `tools/list`。
6. 校验 Tool 并发布 Catalog。

调用：

```text
LLM requests mcp__mail__send_email
  -> ToolRegistry
  -> McpToolAdapter
  -> McpRuntime.call_tool_sync
  -> ClientSession.call_tool
  -> ToolResult
```

调用前只检查 Server 状态、Tool 是否存在、arguments schema 和大小上限。ZhiCe-Agent 不根据 Tool 名称、description 或 annotations 增加本地业务确认。

失败时当前调用不自动重放；Runtime 可在下一次调用前重建连接；timeout 提示远端结果可能未知。

关闭时停止新调用，有界等待活动调用，关闭 ClientSession、stdio 子进程、HTTP/SSE client 和 event-loop thread。

---

## 10. 外部交互透传

ZhiCe-Agent 不主动发起 MCP 业务确认。交互只由 MCP Server 或外部系统触发。

MCP Elicitation 流程：

```text
MCP Server requests user input
  -> Runtime emits channel interaction event
  -> CLI/Web/WS shows server-provided prompt
  -> current user submits response
  -> Runtime sends response to Server
  -> original tools/call continues
```

展示时标明来源 Server，并限制长度、脱敏 credential-like 字段。ZhiCe-Agent 不修改 Server 的问题，也不替用户作答。

如果外部系统把“需要二次确认”作为普通 ToolResult 返回，该内容按普通 ToolResult 展示；后续是否再次调用由用户和模型根据返回内容决定。

---

## 11. 结果归一化与文件落盘

text 和 structured content 直接转换为有界 ToolResult。文件、binary、image、audio 和可下载 resource 进入 `McpArtifactGateway`：

```text
MCP result
  -> validate content type / size / filename
  -> copy or stream into Agent-owned artifact buffer
  -> ActorFileService.save(actor, safe_name, content)
  -> contexts/users/{user_id}/files/mcp/{server_id}/...
  -> ToolResult returns artifact reference
```

规则：

- 目标目录由 Agent 根据 actor 决定，忽略 Server 返回的绝对路径和父目录。
- suggested filename 只保留安全 basename。
- 同名文件使用稳定去重或版本名，不静默覆盖。
- 文件大小、单次总量、用户配额和临时目录大小都有上限。
- 导入成功或失败写 Runtime Activity；路径越界、配额和类型拒绝写 Security Audit。
- CLI workspace operator 和 Owner 使用现有 workspace files root。

如果 stdio MCP 返回自己临时目录中的文件路径，Runtime 只允许读取该 Server 专用临时目录内的文件，导入后按策略清理。大块 base64 不回填给 LLM。

远端 `isError=true` 映射为失败 ToolResult。默认文本结果上限 12000 字符，structured result 上限 16000 字符。

---

## 12. /mcp

`/mcp` 列出 ready 且至少发现一个有效 Tool 的 Server：

```text
当前可用 MCP：

- 邮箱
  搜索邮件、读取邮件、发送邮件

- GitHub
  查询仓库、搜索 Issue、创建和更新 Issue
```

能力摘要从 Server 信息和经过限制的 Tool descriptions 生成。不展示 credential、transport、原始 schema 或诊断错误。CLI、Web 和 external WebSocket 语义一致，`/help` 展示 `/mcp`。

---

## 13. 可观测性与错误

Trace 事件：

```text
mcp.runtime_started
mcp.server_connecting
mcp.server_ready
mcp.server_degraded
mcp.tools_discovered
mcp.tool_call
mcp.tool_done
mcp.tool_error
mcp.elicitation_requested
mcp.elicitation_completed
mcp.reconnect_started
mcp.reconnect_done
mcp.runtime_closed
```

远程 Server 在初始化或 transport 异常后不得永久停留在 degraded。Runtime 按 1/2/4/8/16/30 秒上限的有界指数退避自动重连；手动 reconnect、shutdown 和新排队请求可打断等待。当前失败 Tool 调用不自动重放。日志记录稳定 reason code、顶层异常类型和脱敏叶子异常类型，详细口径见 `2026-08-12-mcp-degraded-auto-recovery-design.md`。

所有 MCP Tool 调用和 artifact 导入进入 Runtime Activity。stdio sandbox 拒绝、artifact 路径/配额拒绝、credential 配置变更和 Elicitation 用户响应进入 Security Audit。远端 Tool 的业务效果由外部系统审计。

错误码：

```text
MCP_CONFIG_INVALID
MCP_SERVER_UNAVAILABLE
MCP_TOOL_NOT_FOUND
MCP_SCHEMA_INVALID
MCP_TRANSPORT_ERROR
MCP_TOOL_TIMEOUT
MCP_REMOTE_ERROR
MCP_OUTPUT_TOO_LARGE
MCP_UNSUPPORTED_CONTENT
MCP_CREDENTIAL_MISSING
MCP_TOKEN_REFRESH_FAILED
MCP_ELICITATION_FAILED
MCP_STDIO_SANDBOX_DENIED
MCP_ARTIFACT_INVALID
MCP_ARTIFACT_TOO_LARGE
MCP_ARTIFACT_QUOTA_EXCEEDED
```

---

## 14. 模块设计

- `agent/protocols/mcp.py`：ServerSpec、AuthSpec、ToolDescriptor、Status、CatalogSnapshot、RuntimeFacade 和 interaction 数据结构。
- `agent/mcp/config.py`：`mcpServers`、transport 推断、URL、cwd、env、headers、OAuth、placeholder、timeout 和字段校验。
- `agent/mcp/naming.py`：名称规范化、长度和碰撞。
- `agent/mcp/catalog.py`：`tools/list`、schema/description/annotations 限制和 Catalog。
- `agent/mcp/runtime.py`：event-loop、workspace 系统级连接/进程、call/reconnect/close 和 Elicitation。
- `agent/mcp/auth.py`：Header/Bearer、直接/env credential 和 OAuth refresh。
- `agent/mcp/result.py`：content、structured JSON、binary descriptor 和输出上限。
- `agent/mcp/artifacts.py`：临时文件校验、artifact buffer 和 ActorFileService 导入。
- `agent/tools/mcp.py`：同步 McpToolAdapter。
- `agent/tools/scoped.py`：注入当前 Catalog adapter。
- `agent/app/runtime.py`：actor scope、interaction event、`/mcp` 和 Web 生命周期。
- `agent/cli.py`：Runtime 生命周期、CLI Elicitation 和 `/mcp`。

---

## 15. 实际变更文件

新增：

```text
agent/protocols/mcp.py
agent/mcp/__init__.py
agent/mcp/config.py
agent/mcp/naming.py
agent/mcp/catalog.py
agent/mcp/runtime.py
agent/mcp/auth.py
agent/mcp/result.py
agent/mcp/artifacts.py
agent/tools/mcp.py
config/mcp.example.json
tests/unit_test/mcp/test_case.md
tests/unit_test/mcp/fake_stdio_server.py
tests/unit_test/mcp/fake_http_server.py
tests/unit_test/mcp/test_mcp_config.py
tests/unit_test/mcp/test_naming.py
tests/unit_test/mcp/test_catalog.py
tests/unit_test/mcp/test_runtime.py
tests/unit_test/mcp/test_auth.py
tests/unit_test/mcp/test_result.py
tests/unit_test/mcp/test_artifacts.py
tests/unit_test/mcp/test_http_runtime.py
tests/conftest.py
```

修改：

```text
pyproject.toml
agent/config.py
agent/protocols/__init__.py
agent/tools/__init__.py
agent/tools/scoped.py
agent/app/runtime.py
agent/cli.py
tests/unit_test/app/test_case.md
tests/unit_test/app/test_runtime_commands.py
tests/unit_test/app/test_ws_routes.py
web/static/app.js
README.md
docs_design/README.md
docs_design/zhice-agent-overall-design.md
```

---

## 16. 测试矩阵

### 16.1 配置与发现

- command/args/env stdio 配置直接解析。
- url/headers 远程配置直接解析。
- transport=sse 使用 SSE。
- OAuth、直接 credential 和 env placeholder 正确解析并脱敏。
- 未识别字段返回 `MCP_CONFIG_INVALID`。
- `tools/list` 的全部有效 Tool 进入 Catalog。
- annotations 保存但不改变调用行为。

### 16.2 Runtime 与隔离

- 两个用户复用同一远程连接和同一 stdio 进程。
- stdio 进程不能直接访问用户目录、Session、Memory 和 config。
- stdio 只能写 Server 专用临时目录。
- 同一 MCP artifact 由不同 actor 保存时分别进入各自 files/mcp 目录。
- Server 返回绝对路径、`..`、超大文件或超配额时 ArtifactGateway 拒绝。
- 单 Server 失败不影响其它 Server。
- timeout 和 transport error 不重放当前调用。
- shutdown 后无残留 thread、process 和 connection。

### 16.3 Elicitation、输出和回归

- Elicitation 能显示在 CLI/Web/WS，响应回到正确调用。
- 用户取消或连接中断返回结构化错误。
- text、structured content 和 text resource 正常返回。
- binary 只返回 descriptor。
- 无 MCP 配置时现有 definitions 不变化。
- AgentLoop、Session、Memory、stop 和模型切换行为不变。
- 默认测试不访问真实外网。

---

## 17. 已完成落地顺序

1. 增加 MCP 测试主题和 fake Server。
2. 新增协议数据结构。
3. 实现 `mcpServers` 配置、credential 和 sandbox root。
4. 实现 naming、Catalog 和结果限制。
5. 引入官方 MCP Python SDK并接入三种 transport。
6. 实现三种 transport 的 workspace 系统级连接和 stdio 临时沙箱。
7. 实现同步 Adapter、ArtifactGateway 和 ActorFileService 导入。
8. 注入全部发现 Tool。
9. 实现 MCP Elicitation channel bridge。
10. 接入 Gateway/CLI 生命周期和 `/mcp`。
11. 增加 trace、Activity、Audit 和错误码。
12. 更新 init、README、总体设计和设计索引。
13. 运行 ruff、相关测试和全量 pytest。

---

## 18. 验收标准

当前状态：除第 7 项中的 Windows OS 级强读取隔离为部分完成外，其余基础验收项均已落地。默认单元测试与真实 MCP integration 测试分层执行。

1. 常见 JSON `mcpServers` 配置可以直接粘贴使用。
2. stdio、Streamable HTTP 和 SSE 都能通过本地 fake Server 验证。
3. Runtime 通过 `tools/list` 自动发现 Tool。
4. 所有有效 Tool 都进入 definitions。
5. 所有正常登录用户看到相同的 MCP Tool 定义。
6. MCP 配置、credential、Tool Catalog、连接和 stdio 进程都是 workspace 系统级共享数据。
7. stdio 进程不能直接访问用户目录，只能使用 Server 专用临时目录。
8. MCP 返回的文件和下载内容由 Agent ArtifactGateway 按 actor 写入本人 files/mcp 目录。
9. ZhiCe-Agent 不对远端 Tool 做本地风险判断或逐次确认。
10. MCP Elicitation 可以转给当前用户并回传响应。
11. transport error 和 timeout 不自动重放当前调用。
12. 单 Server 故障不影响其它 Server、内置 Tool 或普通聊天。
13. credential 支持直接值和 `${ENV_VAR}`，OAuth token 可以刷新。
14. Tool name、description、schema、数量和输出都有上限。
15. trace、Session、Activity、Audit 和 `/mcp` 不泄漏 credential。
16. `/mcp` 只展示 ready Server 和能力摘要。
17. Gateway/CLI 退出后无残留子进程、client 或 event-loop thread。
18. ruff、相关测试和全量 pytest 通过，或明确记录无关历史失败。

---

## 19. 和其它文档的关系

- Part 3 提供 ToolProvider 和 ToolRegistry。
- Part 4 提供本地 workspace guard、进程超时和输出限制经验。
- Part 8 提供 trace 格式。
- Part 9 提供 actor、Runtime Activity 和 Security Audit。
- Part 10 提供普通用户目录与 Owner/workspace operator 边界。
- Part 12 为 MCP Tool 和内置 Tool 增加统一 `tool.started/completed/failed` RuntimeEvent，并通过真实 post Hook 为 MCP 归一化结果补充受限业务展示 metadata；pre Hook 修改参数后仍重新经过核心 schema、RBAC 和 MCP/ArtifactGateway 边界，不能改变“不自动重放当前远端调用”的规则。当前设计见 `docs_design/zhice-agent-part12-hooks-design.md`。
- Part 19 复用本 Part 的 workspace-shared Runtime 和 actor-scoped Tool adapter 接入高德、Tavily、12306，并交付 Open-Meteo 与小红书只读适配。小红书写操作在远端 Catalog 层不存在；任一旅行 Server degraded 不改变其它 MCP、内置 Tool 或普通聊天。当前应用口径见 `docs_design/zhice-agent-part19-intelligent-travel-planner-design.md`。
