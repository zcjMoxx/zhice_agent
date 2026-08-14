# 本地小红书上游托管与 Tavily 有界结果设计

## 背景

本地旅行规划的 `xhs-readonly` MCP 只是只读适配器，真正执行浏览器查询的 `xiaohongshu-mcp` 仍需人工启动；适配器 ready 并不代表上游端口可用，且手工端口容易与配置漂移。Tavily 搜索即使关闭 raw content，也可能返回超过 MCP 通用 16000 字符上限的 structuredContent，当前实现会丢弃整个结果并返回 `MCP_OUTPUT_TOO_LARGE`。

## 目标

- 本地 loopback 小红书上游随 Gateway 启动、运行中自动恢复、随 Gateway 有序退出。
- 启动端口直接取自 `XHS_READONLY_UPSTREAM_URL`，避免手工命令与配置不一致。
- 二进制、Cookie 和数据目录全部从 `ZHICE_AGENT_WORKSPACE` 及受信 MCP 配置派生。
- Tavily 搜索默认使用不含正文和图片的有界参数。
- 结构化文本结果过大时保留可解析的精简 JSON，而不是整次失败；二进制安全上限不放宽。

## 范围边界

- 只托管 `xhs-readonly` 指向 `127.0.0.1`、`localhost` 或 `::1` 的本地上游。Docker 私网 sidecar 和远端 HTTPS 不由主进程拉起。
- 只使用 workspace 固定目录下的平台二进制，不联网下载、不动态执行配置命令。
- 已有外部进程占用配置端口时复用但不接管，Gateway 退出时不终止非自身进程。
- Tavily 参数策略位于旅行 Hook，MCP JSON 裁剪位于结果规范化边界，不在 AgentLoop 添加业务分支。

## 模块设计

`LocalXhsSidecarSupervisor` 从 MCP specs 找到 `xhs-readonly`，验证 URL 为 loopback HTTP，解析端口和配置 Cookie。Windows 使用 `xiaohongshu-mcp-windows-amd64.exe`，其它平台只在存在明确固定文件时启用。supervisor 先同步确保端口 ready，再用后台线程定期检查；自有进程退出或端口消失时按有界退避重启。进程通过 `ManagedProcessTree` 持有，stdout/stderr 追加到数据目录，日志不包含 Cookie 内容。

`build_web_runtime` 在构造 `McpRuntime` 前启动 supervisor，并把所有权交给 `WebRuntime`。`shutdown` 先关闭 MCP/Channel，再停止 supervisor。

`TravelProgressHookRuntime` 仅修改 Tavily search Tool：`max_results<=5`、`include_raw_content=false`、图片字段为 false，并修正 `fast/ultra-fast + country`。

`normalize_mcp_result` 对超大 structuredContent 执行确定性压缩：移除 raw content 字段、限制列表和字符串长度、保留 `results/title/url/content` 等结构，并在 metadata 与 JSON 根节点标记截断。压缩后仍过大时退化为有界 JSON preview。Artifact 数量和字节上限继续硬失败。

## 测试方案

- sidecar：非 XHS、非 loopback、缺二进制、已有监听、成功启动、启动失败、关闭自有进程、外部进程不被关闭。
- Tavily Hook：search 默认参数、非法 country/depth 组合、extract 不被误改、已有 Hook 修改结果继续叠加。
- MCP result：小结果不变；超大 Tavily 风格结构化结果成功、JSON 可解析、raw content 删除、metadata 标记；Artifact 超限仍失败。
- 全量 Ruff、Pytest、前端 lint/typecheck/test/build 和本地 Gateway 重启健康检查。

## 验收标准

- 用户只启动 Gateway 即可使用本地小红书上游，无需单独执行 exe。
- 配置 URL 的端口是唯一真值，日志可看到 sidecar ready/restart/stop 的安全事件。
- Tavily 搜索不再因为正文和图片参数膨胀；超大 JSON 返回精简结果而非 `MCP_OUTPUT_TOO_LARGE`。
- 现有 AgentLoop、Tool、Session、LLMProvider、workspace 与 Secret 边界不改变。
