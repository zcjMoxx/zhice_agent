# MCP Tool 接入与运行时边界设计

> 日期：2026-07-17
> 状态：基础实现已落地；Windows OS 级 stdio 读取隔离仍待硬化
> 当前实施依据：`docs_design/zhice-agent-part11-mcp-design.md`

> 说明：当前代码已按本记录完成主要运行链路。stdio 具备专用临时 cwd、最小环境、无 shell 和 Job Object 回收，但 cwd/env 不能等价于 AppContainer/受限令牌；OS 级读取隔离仍应参考当前 Part 11 活文档中的实现说明。

---

## 1. 背景

Part 11 要让 ZhiCe-Agent 直接使用网上常见的 MCP Server 配置，通过 MCP 协议自动发现 Tool，并接入现有 ToolRegistry。

本次最终确认的责任边界是：

- ZhiCe-Agent 负责自己的 workspace、进程、credential、参数、输出、超时和审计。
- MCP Server 和外部系统负责外部业务行为及其安全策略。
- ZhiCe-Agent 不判断外部 Tool 是否危险，也不额外增加本地确认。
- 外部 Server 主动要求用户交互时，ZhiCe-Agent 负责原样转发。

---

## 2. 核心决策

### 2.1 配置直接兼容常见 mcpServers

运行态配置：

```text
${ZHICE_AGENT_WORKSPACE}/config/mcp.json
```

用户可以直接粘贴常见 JSON：

```json
{
  "mcpServers": {
    "filesystem": {
      "command": "npx",
      "args": [
        "-y",
        "@modelcontextprotocol/server-filesystem",
        "."
      ]
    }
  }
}
```

Loader 使用固定解析规则：

- `command/args/cwd/env` 对应 stdio。
- `url/headers` 对应 Streamable HTTP 或 SSE。
- `transport/type` 可以显式指定 transport。
- `oauth` 保存 access/refresh token 和刷新参数。
- credential 支持直接值或 `${ENV_VAR}`。
- 未识别字段返回配置错误。

用户不需要在配置中提前填写 Tool 名。

### 2.2 Tool 通过协议自动发现

```text
connect
  -> initialize
  -> tools/list
  -> validate name/schema/size
  -> publish Catalog
```

通过校验的 Tool 全部进入 definitions。ZhiCe-Agent 不维护本地 Tool allowlist、风险等级或逐次确认配置。

MCP annotations 作为远端元数据保存，不参与本地授权和风险判断。

### 2.3 外部交互原样转发

MCP Server 发起 Elicitation 时：

```text
server interaction request
  -> current channel
  -> current user response
  -> server
  -> original call continues
```

如果外部系统把二次确认要求作为普通 ToolResult 返回，该内容按 ToolResult 展示。ZhiCe-Agent 不自行生成业务确认。

### 2.4 MCP 共享，文件操作由 Agent 收口

配置、credential、Tool Catalog、远程 connection 和 stdio 进程都属于 workspace，所有正常登录用户共享。

stdio 运行在 Server 专用临时沙箱，不能直接访问用户目录、Session、Memory 或 config。MCP 返回 text/JSON 时直接形成 ToolResult；返回文件、下载内容或 resource 时，由 Agent ArtifactGateway 校验并按当前 actor 写入本人 `files/mcp` 目录。

MCP 只提供结果，目标用户目录和最终文件路径由 Agent 文件服务决定。这与 exec 统一经过内部执行策略的思路一致：外部能力不直接绕过 ZhiCe-Agent 的资源写入边界。

---

## 3. 目标

1. 一次支持 stdio、Streamable HTTP 和 SSE。
2. 直接读取常见 `mcpServers` JSON。
3. 通过 `tools/list` 自动发现全部 Tool。
4. 把有效 Tool 适配到同步 ToolProvider。
5. 配置、credential、Catalog、远程连接和 stdio 进程由 workspace 共享。
6. 支持 Header、Bearer、直接/env credential 和 OAuth token refresh。
7. 支持 MCP Elicitation 转发。
8. MCP artifact 由 Agent 文件服务按 actor 保存。
9. 单 Server 故障不影响其它 Server和普通聊天。
10. CLI、Web 和 WS 使用相同的 `/mcp`。

---

## 4. 总体架构

```text
mcp.json
  -> config normalization
  -> McpRuntime
       -> workspace shared connections/processes
       -> initialize + tools/list
       -> Catalog
       -> tools/call
       -> Elicitation bridge
       -> ArtifactGateway
            -> ActorFileService
  -> McpToolAdapter
  -> ToolRegistry
  -> AgentLoop
```

AgentLoop、LLMProvider 和 SessionStore 保持同步，不依赖 MCP SDK。

---

## 5. 内部安全边界

ZhiCe-Agent 只执行可在本系统内部验证的规则：

- 只连接配置文件中声明的 Server。
- stdio executable 和 args 不能由模型生成。
- stdio 不能直接访问用户目录，只能使用 Server 专用临时目录。
- 文件、下载内容和 resource 由 ArtifactGateway 按 actor 导入用户 files/mcp 目录。
- credential 不进入 Session、ToolResult、普通日志或 `/mcp`。
- Tool name、description、schema、数量、参数和输出都有上限。
- timeout 或 transport failure 不自动重放当前调用。
- 单 Server 异常隔离。
- Runtime Activity 记录调用；Security Audit 记录 sandbox 拒绝、credential 变更和用户交互。

远端邮件、代码平台、数据库或云服务中的行为不进入 ZhiCe-Agent 本地风险分类。

---

## 6. /mcp

`/mcp` 根据 Server 信息和 Tool descriptions 生成能力摘要：

```text
当前可用 MCP：

- 邮箱
  搜索邮件、读取邮件、发送邮件

- GitHub
  查询仓库、搜索 Issue、创建和更新 Issue
```

只展示 ready 且存在有效 Tool 的 Server，不展示 credential、transport、schema 或诊断详情。

---

## 7. 模块与文件

新增模块：

```text
agent/protocols/mcp.py
agent/mcp/config.py
agent/mcp/naming.py
agent/mcp/catalog.py
agent/mcp/runtime.py
agent/mcp/auth.py
agent/mcp/result.py
agent/mcp/artifacts.py
agent/tools/mcp.py
config/mcp.example.json
```

关键职责：

- config：常见 `mcpServers` 解析和 transport/credential 规范化。
- runtime：workspace 系统级连接/进程、调用和 Elicitation。
- artifacts：临时文件校验和 actor-scoped 文件导入。
- catalog：全部有效 Tool 的 descriptor snapshot。
- naming：本地名称与碰撞检查。
- result：有界 ToolResult。
- tools/mcp：同步 Adapter。

---

## 8. 测试方案

### 8.1 配置与发现

- 常见 stdio 和远程 JSON 可以直接解析。
- 三种 transport 正确连接。
- 直接值和 env credential 正确解析并脱敏。
- `tools/list` 的全部有效 Tool 进入 Catalog。
- 非法 schema、名称碰撞和超限 Tool 被隔离。

### 8.2 内部隔离

- 远程连接和 stdio 进程由多用户共享。
- stdio 不能直接访问用户目录。
- 两个 actor 保存同一 artifact 时分别进入各自 files/mcp 目录。
- 绝对路径、父目录、超大文件和超配额被 ArtifactGateway 拒绝。
- command/args 不通过 shell。
- timeout 不重放。
- shutdown 无残留进程和线程。

### 8.3 外部交互

- Elicitation 在 CLI/Web/WS 展示。
- 用户响应回到正确 Server 和原调用。
- 取消、超时和断连返回结构化错误。
- Server 通过普通 ToolResult 返回的确认要求正常展示。

---

## 9. 实现顺序

1. 增加 fake stdio、HTTP/SSE 和 Elicitation Server。
2. 新增 MCP 协议数据结构。
3. 实现 `mcpServers` 配置、credential 和 actor root。
4. 实现 naming、Catalog 和结果限制。
5. 接入官方 MCP SDK 和三种 transport。
6. 实现三种 transport 的 workspace 系统级连接和 stdio 临时沙箱。
7. 实现 ArtifactGateway 和 ActorFileService 导入。
8. 把全部发现 Tool 注入 ToolProvider。
9. 实现 Elicitation channel bridge。
10. 接入 Gateway/CLI 生命周期和 `/mcp`。
11. 增加 trace、Activity、Audit 和错误码。
12. 更新 README、总体设计和索引。
13. 运行 ruff、相关测试和全量 pytest。

---

## 10. 验收标准

1. 常见 `mcpServers` JSON 可以直接粘贴。
2. 三种 transport 都能连接、发现和调用。
3. 全部有效 Tool 自动进入 definitions。
4. ZhiCe-Agent 不做远端 Tool 风险判断或本地业务确认。
5. 外部 Elicitation 能转给用户并回传。
6. 配置、credential、Catalog、远程连接和 stdio 进程由 workspace 共享。
7. stdio 不能直接访问用户目录，只能使用专用临时目录。
8. 文件和下载结果由 Agent 按 actor 写入本人 files/mcp 目录。
9. 所有正常登录用户共享同一 Tool 集合。
10. credential、完整参数和结果不泄漏。
11. 调用失败不自动重放。
12. 单 Server 故障不影响其它能力。
13. `/mcp` 展示可用 Server 和能力摘要。
14. CLI/Gateway 退出后无残留进程、连接或线程。
15. 相关测试和静态检查通过。

---

## 11. 和其它部分的关系

- Part 3：ToolProvider 和 ToolRegistry。
- Part 4：本地 workspace guard、超时和输出限制。
- Part 8：trace。
- Part 9：actor、Runtime Activity 和 Security Audit。
- Part 10：用户目录与 Owner/workspace operator 边界。
