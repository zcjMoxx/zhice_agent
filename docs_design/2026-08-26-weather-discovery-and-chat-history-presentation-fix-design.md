# 天气 Tool 发现与聊天历史展示补丁设计

> 日期：2026-08-26
>
> 状态：已完成

## 1. 背景

普通聊天询问“明天重庆天气”时，Open-Meteo MCP 已正常注册 `geocode_place`、`get_forecast` 和 `get_historical_weather`，但按需 Tool 发现会把 `weather` / `weather forecast` 查询优先匹配到历史天气。原因是 forecast 描述缺少 `weather`，historical 描述又在否定句中同时包含 `weather` 和 `forecast`。模型因此只看到历史天气 schema，并错误回复没有实时预报工具。

同一微信 Session 在 Web 打开时还会展示空白 Assistant 气泡和原始 ToolResult JSON。SessionStore 正确保存了 `assistant(tool_calls)` 与 `tool` 消息，但 Web 历史恢复未经用户可见投影，直接渲染全部内部消息；微信渠道只发送最终 `result.content`，所以两端表现不一致。

## 2. 目标

1. `weather`、`weather forecast`、`current weather`、`tomorrow weather` 以及中文天气预报查询优先发现 `mcp__open-meteo__get_forecast`。
2. 历史天气查询仍能准确发现 `mcp__open-meteo__get_historical_weather`。
3. Web 打开任意 Web、QQ、微信或 CLI Session 时，只展示非空用户消息和不带 Tool Call 的最终 Assistant 文本。
4. SessionStore、LLM 上下文、审计与诊断继续保留完整工具调用链。

## 3. 范围边界

- 不修改 MCP transport、启动、重连、权限或调用协议。
- 不删除或改写 Session JSONL 中的任何消息。
- 不改变微信、QQ 的出站发送规则。
- 不新增工具日志面板；内部执行进度继续由现有 RuntimeStatus 呈现。
- 不把通用 Tool 发现改造成新的 LLM 分类器；仅移除短查询命中较长 Tool name 时不合理的完整名称奖励，并修正天气 Catalog 的可检索语义。

## 4. 模块设计

### 4.1 天气 Tool Catalog

`get_forecast` 的描述显式包含 live/current/future/weather/forecast/today/tomorrow 及对应中文关键词。`get_historical_weather` 使用 past/archive/historical/climate 语义，不再在否定句中引入会参与正向评分的 `forecast`，同时保留中文“历史天气/气候参考”。

现有评分还会在任意查询是完整 Tool name 的子串时奖励 4 分，导致短词 `weather` 仅因出现在 `get_historical_weather` 名称中压过 forecast。名称奖励收紧为“完整 Tool name 出现在查询中”才生效；普通关键词继续走 feature overlap，精确 Tool 激活继续使用 `names[]`，不需要模糊子串奖励。

Tool 发现仍使用现有确定性词法评分；测试用实际天气描述构造 Catalog，限制 `max_results=1`，确保常用实时查询只激活 forecast，历史查询只激活 historical。

### 4.2 Web Session 用户可见投影

前端 Session store 在加载 API 历史时执行纯展示过滤：

```text
API raw messages
  -> user: content 非空
  -> assistant: content 非空且 tool_calls 为空
  -> 丢弃 system / tool / assistant(tool_calls) / 空消息
  -> ChatPage
```

过滤只作用于前端展示状态，不触碰服务端 SessionStore。实时 Web Turn 原有的 pending Assistant 与 RuntimeStatus 流程保持不变。

## 5. 数据流

```text
AgentLoop
  -> SessionStore: user + assistant(tool_calls) + tool + final assistant
  -> GET /api/sessions/{id}: 完整历史
  -> Session store 用户可见投影
  -> ChatPage: user + final assistant

Weixin Adapter
  -> runtime.dispatch()
  -> result.content
  -> 微信最终文本
```

## 6. 变更文件

- `integrations/open_meteo_mcp/server.py`
- `agent/tools/discovery.py`
- `tests/unit_test/tools/test_tool_discovery.py`
- `tests/unit_test/tools/test_case.md`
- `web/frontend/src/stores/sessions.ts`
- `web/frontend/src/components/ChatPage.test.ts`
- `docs_design/zhice-agent-overall-design.md`
- `docs_design/zhice-agent-part16-web-product-design.md`
- `agent/web/static/*`（前端生产构建产物）

## 7. 测试方案

1. Tool 发现单测覆盖英文实时、英文历史和中文实时天气查询。
2. ChatPage 测试以真实 API shape 注入空 Assistant Tool Call、ToolResult 和最终回复，断言只显示用户与最终回复。
3. 运行 `python -m ruff check .` 与 `python -m pytest`。
4. 运行前端 lint、typecheck、test 和 production build。
5. 使用真实认证账号和微信来源 Session 请求明天天气，确认调用 forecast，并在刷新、重新打开 Session 后无空白行和 ToolResult JSON。

## 8. 验收标准

- Open-Meteo Runtime ready 且实际 `get_forecast` 调用成功。
- 明天天气不再误选历史工具。
- Web 历史只展示用户消息和最终 Assistant 回复。
- 微信 Session 在 Web 打开时与微信用户可见内容一致，不泄露原始 ToolResult。
- 本地验证通过后关闭本地服务；提交、推送、部署后验证线上镜像、健康、MCP 状态和原始用户流程。

## 9. 实施验证

- 后端全量串行测试通过：`1454 passed, 3 skipped, 15 deselected`。
- 前端 lint、typecheck、`227 tests` 和 production build 通过。
- 本地真实微信 Session 调用 `mcp__open-meteo__get_forecast` 成功；刷新历史后只展示用户消息和最终 Assistant 回复。
- 本地验收后已关闭 Gateway 与 Ops，端口 `10086`、`17681` 均无监听。
