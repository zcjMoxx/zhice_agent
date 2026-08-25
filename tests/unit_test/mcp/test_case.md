# MCP 单元测试说明

## 测试目标

- 验证`config.yml.mcp.servers`的严格解析、transport推断和环境变量展开。
- 验证远端 Tool 名称、schema、数量和碰撞边界。
- 验证 MCP 结果归一化与 actor-scoped artifact 落盘。
- 使用本地 fake stdio Server 验证 initialize、`tools/list`、`tools/call`、同步 Adapter 和关闭流程。
- 验证 Part17 版本化 Catalog 的手动刷新、`tools/list_changed` 等价入口、非法 Catalog 原子拒绝和单 Server 重连。
- 验证初始化/transport 瞬时失败后按有界退避自动重连，成功后恢复 Catalog；ExceptionGroup 仅记录安全叶子异常类型。
- 验证远程 MCP 默认 `proxy_mode: direct`，坏的终端代理不会污染 Streamable HTTP、SSE 或 OAuth refresh；确需环境代理时必须显式配置。
- 验证活动调用取消会传播到远端请求，并形成稳定 `MCP_TOOL_CANCELLED` 结果。
- 验证连接历史、OAuth 状态、Tool 调用次数、延迟、错误码与取消次数均以无凭据快照暴露。
- 验证管理端 MCP 状态接口只聚合 Server/Catalog/调用/重连安全字段，并沿用 `skill.sources.read` 权限，不暴露配置和凭据。
- 验证 artifact 使用有界流式导入、有界预览和仅作用于 actor MCP 目录的保留策略。
- 验证无配置时 Runtime 保持禁用，不启动线程、不记录启停日志，也不影响内置 Tool。
- 验证startup checker将缺失/空分区标记为disabled；MCP根结构错误或全部server非法时标记为unavailable；单个server非法时标记为degraded并保留其它合法spec；旧`mcp.json`不再读取。

## 关键检查点

- credential 不出现在 Catalog 和 ToolResult。
- 超长 MCP 文本保持 12000 字符上限，同时保留首尾内容和中间截断标记，使按时间排序的长列表在重启恢复后仍保留末尾候选。
- stdio cwd 固定在 `state/mcp_runtime/{server_id}/tmp` 下。
- 绝对 cwd、父目录 cwd、越界临时文件和超大 artifact 被拒绝。
- 参数 schema 在远端调用前执行基础校验。
- 当前调用 timeout/transport error 不自动重放。
- degraded 自动恢复不重放当前未知结果调用；退避可被 shutdown、手动 reconnect 或新请求打断。
- Catalog 仅在完整校验成功后增加版本并替换；失败刷新保留上一可用 snapshot。
- 单 Server refresh/reconnect 失败不清空其它 Server Catalog，也不影响本地 Tool。
- 取消只匹配指定 Server/用户的活动调用，不重放未知远端结果。
- startup warning 不记录 credential、环境变量名、原始配置错误或绝对路径。
- `${VAR}`继续要求非空，`${VAR:-}`允许可选空值；`gateway --check`对degraded或unavailable MCP配置返回失败。

## 执行分层

- 默认 `python -m pytest` 跳过会真实启动子进程或本地 Server 的 `integration` 用例。
- 修改 MCP transport、OAuth 或进程生命周期后运行 `python -m pytest -m integration tests/unit_test/mcp`。
