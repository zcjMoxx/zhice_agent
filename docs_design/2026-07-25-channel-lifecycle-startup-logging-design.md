# 2026-07-25 Gateway 渠道生命周期日志补齐设计

## 背景

Gateway 已有统一终端格式与 JSONL trace，Turn、LLM、Tool、QQ 发送失败等路径也已有结构化事件，但启动阶段只有 Uvicorn 自身日志。`ChannelManager.start()` 对可选渠道静默启动并隔离异常，导致操作者无法从终端直接确认 Web、QQ、微信是否启动；微信 sidecar 又将 stderr 隔离，进一步放大了诊断缺口。

## 目标

- Gateway lifespan 启动时，为 Web、QQ、微信分别输出一条结构化结果日志。
- 日志区分 `available`、`disabled`、`degraded/unavailable`，并保留稳定 capability code。
- Gateway 关闭后输出实际启动渠道与 Web 的停止日志。
- 继续复用现有 `log_event`、终端 formatter、trace JSONL 和敏感字段脱敏，不另建日志系统。

## 范围边界

- 不修改 AgentLoop、Channel Adapter 业务逻辑或渠道可用性判定。
- 不把微信 token、外部账号标识、context token、QQ Secret 写入日志。
- 可选渠道启动失败仍局部降级，不阻断 Web Gateway。
- Web 表示同端口 FastAPI/ASGI 控制面，不伪装成 `ChannelManager` Adapter。

## 模块设计

`agent.app.gateway` 在 lifespan 中：

1. 记录 Web `gateway.channel.start`，字段为 `channel=web state=available code=WEB_GATEWAY_AVAILABLE`。
2. 调用现有 `ChannelManager.start()`。
3. 从 `runtime.capability_statuses()` 读取 `channel.*` 聚合结果：
   - `available`：INFO `gateway.channel.start`；
   - `disabled`：INFO `gateway.channel.skip`；
   - `degraded/unavailable`：WARNING `gateway.channel.start_failed`。
4. 关闭时先执行现有 runtime shutdown，再为实际 Adapter 和 Web 记录 `gateway.channel.stop`。

终端继续使用当前固定格式；trace 保留原始 event、state、code、error_type 等结构字段。

## 变更文件

- `agent/app/gateway.py`
- `tests/unit_test/app/test_gateway.py`
- `tests/unit_test/app/test_case.md`
- `README.md`
- `docs_design/README.md`
- `docs_design/zhice-agent-part8-gateway-agent-logging-design.md`

## 测试方案

- 正常：Web、QQ、微信 available 时各产生一条 start 日志，关闭时产生 stop 日志。
- 异常：渠道 unavailable/start failure 记录 WARNING 与稳定 code，Gateway lifespan 仍可进入。
- 边界：disabled 渠道记录 skip；无 ChannelManager 的最小 runtime 仍记录 Web start/stop；日志字段不包含 credential。

## 验收标准

- 操作者执行 `zcagent gateway` 后无需访问 `/health` 即可看到 Web、QQ、微信启动结果。
- `/health` 与日志使用同一份聚合状态，不出现一边 available、一边 unavailable。
- Ruff、全量 pytest、Node 测试和 sidecar build 通过。
