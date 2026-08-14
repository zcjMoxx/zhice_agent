# 旅行外部来源完成门槛与高德本地构建设计

## 背景

真实“重庆到乌鲁木齐”旅行 Session 只调用了 Tool 发现、Skill 加载、optimizer 和 finalizer，没有调用任何 MCP，却仍用纯 `model_estimate` evidence 保存了计划。与此同时，本地直接执行 `npm run build` 没有加载已经写入私有 `.env` 的高德 JS Key 与安全密钥，覆盖后的静态包退回文字地图。自然语言输入为空时纸飞机处于禁用态，但界面没有明确说明，用户会把无反应理解为点击故障。

## 目标

- travel Session 必须实际尝试已配置的地图、天气、交通、网页、社区来源；至少一个外部来源成功且最终计划包含非 `model_estimate` evidence，才允许保存。
- 单源失败仍可降级，但不能跳过所有 MCP 后伪装成完整计划。
- 来源账本只记录类别、Tool 名与成功状态，不保存参数、响应正文、URL 或 Secret。
- 本地 Vite dev/build 从 Git 忽略的私有 `.env` 安全加载高德浏览器变量；显式进程环境变量优先。
- 纸飞机与 Enter 调用同一函数；空输入状态提供明确的可访问提示。

## 边界

- AgentLoop 不包含旅行判断。
- MCP Adapter 只提供通用、可选的结果观察回调；旅行分类与完成门槛位于 `agent/applications/travel`。
- 仅持久化 `channel=travel` 的 Session 注册与校验来源账本；普通聊天不受影响。
- MCP failed/timeout、结构化 payload `status=error` 或非成功 code 只记 attempted，不记 successful。
- 账本按 Session 有界保存，计划成功后释放；进程重启不伪造恢复前外部调用。
- 前端构建只读取变量是否存在并注入 Vite；测试和日志不得输出变量值。

## 模块设计

### 来源账本

`TravelSourceLedger` 维护：

- expected：当前 MCP Catalog 中存在的旅行来源类别；
- attempted：该 Session 实际调用过的类别；
- successful：返回可用结构化结果的类别。

类别固定为 maps/weather/transport/web/social。WebRuntime 取得 actor-scoped MCP Tools 后登记 expected，并通过 McpToolAdapter 的通用 result observer 登记 attempted/successful。

### finalizer 完成门槛

`FinalizeTravelPlanTool` 在 `context.channel=travel` 时先检查：

1. 所有当前 expected 类别均已 attempted；
2. 至少一个类别 successful；
3. plan evidence 至少包含一条非 `model_estimate` 证据。

失败返回 `TRAVEL_RESEARCH_INCOMPLETE` 或 `TRAVEL_EVIDENCE_INSUFFICIENT`，Agent 必须继续发现并调用来源，不能保存。

### 高德本地构建

Vite config 使用 `loadEnv` 加载：

1. `deploy/private/.env`；
2. `${ZHICE_AGENT_WORKSPACE}/config/.env` 或默认用户 `.zhice/config/.env`；
3. 当前进程显式环境变量覆盖文件值。

只把 `VITE_AMAP_JS_API_KEY` 与 `VITE_AMAP_JS_SECURITY_CODE` 放入 Vite `define`，不扩大其它 Secret 到浏览器。

## 变更文件

- `agent/applications/travel/source_ledger.py`
- `agent/applications/travel/service.py`
- `agent/applications/travel/tools.py`
- `agent/tools/mcp.py`
- `agent/mcp/runtime.py`
- `agent/app/runtime.py`
- `web/frontend/vite.config.ts`
- `web/frontend/src/components/travel/TravelPlanForm.vue`
- 后端、前端测试与 `tests/unit_test/travel/test_case.md`

## 测试与验收

- 正常：五类来源均 attempted、至少一类 success、外部 evidence 存在，finalizer 成功。
- 异常：未调用 MCP、漏调用已配置类别、所有来源失败、只有 model estimate 均拒绝保存。
- 边界：普通 Web Session 不校验；结构化 `status=error` 不记成功；账本成功后清理；未知 MCP 不进入旅行类别。
- 构建：私有文件变量存在时 Vite 产物不再包含“未配置高德 JS API Key”；不输出真实值。
- 输入：有文字时点击与 Enter 都调用同一提取端点；空输入按钮保持禁用并有明确提示。
