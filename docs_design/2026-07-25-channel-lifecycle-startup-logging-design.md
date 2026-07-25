# 2026-07-25 Gateway 渠道生命周期日志补齐设计

## 背景

Gateway 已有统一终端格式与 JSONL trace，Turn、LLM、Tool、QQ 发送失败等路径也已有结构化事件，但启动阶段只有 Uvicorn 自身日志。`ChannelManager.start()` 对可选渠道静默启动并隔离异常，导致操作者无法从终端直接确认 Web、QQ、微信是否启动；微信 sidecar 又将 stderr 隔离，进一步放大了诊断缺口。

## 目标

- Gateway lifespan 启动时，以 Uvicorn 风格输出渠道汇总和按配置顺序排列的 ready 结果。
- 日志区分 `available`、`disabled`、`degraded/unavailable`，并保留稳定 capability code。
- Gateway 关闭后输出实际启动渠道与 Web 的停止日志。
- 继续复用现有 `log_event`、终端 formatter、trace JSONL 和敏感字段脱敏，不另建日志系统。

## 范围边界

- 不修改 AgentLoop；渠道 readiness 仍由 Transport/Adapter 的真实状态判定。
- 不把微信 token、外部账号标识、context token、QQ Secret 写入日志。
- 可选渠道启动失败仍局部降级，不阻断 Web Gateway。
- Web 表示同端口 FastAPI/ASGI 控制面，不伪装成 `ChannelManager` Adapter。

## 模块设计

`agent.app.gateway` 在 lifespan 中：

1. `channels.yml` 的映射顺序保存为 `ChannelConfiguration.order`，Adapter 启动与终端 ready 均按该顺序执行。
2. 调用现有 `ChannelManager.start()`，QQ 有界等待真实 botpy `on_ready`，微信读取 sidecar health 与账号状态聚合：
   - `available`：加入汇总并输出 `channel.ready`；
   - `disabled`：INFO `gateway.channel.skip`；
   - `degraded/unavailable`：WARNING `gateway.channel.start_failed`。
3. 先输出 `channel.enabled`，再按同一配置顺序输出各渠道 ready/异常；`channels` 始终包含 Web。
4. 关闭时先执行现有 runtime shutdown，再为实际 Adapter 和 Web 记录 `gateway.channel.stop`。

`configure_gateway_logging()` 同时为 `zcagent.agent` 与 `zcagent.gateway` 配置终端和 trace handler；测试必须覆盖真实 handler，不能只依赖 pytest `caplog` 捕获传播事件。

终端 formatter 将 `gateway.channel.*` 与 `channel.qq.*`、`channel.weixin.*` 渲染成 Uvicorn 的 `LEVEL:` 结构和配色；Agent Turn、LLM、Tool 继续使用带时间的 ZhiCe 格式。QQ 是共享机器人，正常启动只在真实 `on_ready` 后显示 `[qq] channel ready | mode=shared`；超时有界，之后恢复会补发 ready。微信是每用户独立插件账号，只显示 `accounts/active/reconnect_required` 聚合；正常接收/发送进入 DEBUG trace，sidecar、重连和发送失败才显示 WARNING。

终端继续使用当前固定格式；trace 保留原始 event、state、code、error_type 等结构字段。

## 变更文件

- `agent/app/gateway.py`
- `agent/channels/qq/transport.py`
- `tests/unit_test/app/test_gateway.py`
- `tests/unit_test/channels/test_channels.py`、`test_case.md`
- `tests/unit_test/app/test_case.md`
- `README.md`
- `docs_design/README.md`
- `docs_design/zhice-agent-part8-gateway-agent-logging-design.md`

## 测试方案

- 正常：配置顺序与 Adapter/ready 输出一致；QQ ready 来自真实 on_ready；微信输出隐私安全的聚合计数。
- 异常：渠道 unavailable/start failure 记录 WARNING 与稳定 code，Gateway lifespan 仍可进入。
- 边界：QQ readiness 等待超时有界且迟到恢复可见；disabled 渠道记录 skip；日志字段不包含 credential 或外部账号标识。

## 验收标准

- 操作者执行 `zcagent gateway` 后无需访问 `/health` 即可看到 Web、QQ、微信启动结果。
- `/health` 与日志使用同一份聚合状态，不出现一边 available、一边 unavailable。
- Ruff、全量 pytest、Node 测试和 sidecar build 通过。
