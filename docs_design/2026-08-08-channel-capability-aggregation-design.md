# 渠道 Capability 聚合与恢复状态设计

## 背景

当前 QQ 配置保留 `accounts[]`，一个 Gateway 可以为每个配置账号构造独立 Adapter。运行时因此同时向 Capability 输出：

- `channel.qq`：QQ 配置和 SDK 启动检查；
- `qq.<account_key>`：具体 Adapter 的运行状态。

在当前生产边界中，一个 Gateway 只使用一个共享 QQ Bot，管理页同时展示 `channel.qq` 与 `qq.main` 会让用户误认为存在两个 QQ 渠道。与此同时，`ChannelManager` 会永久保留 Adapter 启动异常；微信在凭据丢失后重新扫码并恢复收发，旧启动异常仍覆盖当前 Adapter 的 `available` 状态。

## 目标

- 对外 Capability 只展示稳定渠道级 key：`channel.qq`、`channel.weixin`。
- 保留 QQ 多账号配置、独立 Adapter、账号级 Session 路由、限流、日志和内部诊断能力。
- 将具体 QQ Adapter 状态聚合为真实的 `channel.qq`，不退回只展示静态配置检查。
- Adapter 从启动异常恢复为 `available` 后，自动清除对应历史启动失败。

## 范围边界

- 不删除 `channels.qq.accounts[]`、`QQAccountConfig` 或数据库 `account_key`。
- 不迁移现有 QQ 身份绑定、Session、receipt 或配置文件。
- 不在公开 Capability 的名称、message、code 或 details 中暴露账号 key。
- 不改变 QQ/微信消息收发、AgentLoop、SessionStore 或 Channel Runtime 调用链。

## 模块设计

### ChannelManager 恢复

`ChannelManager.statuses()` 仍先读取每个 Adapter 当前状态。若某 key 有历史启动失败：

- 当前 Adapter 尚未恢复为 `available`：继续返回 `CHANNEL_START_FAILED`；
- 当前 Adapter 已为 `available`：删除历史失败并返回 Adapter 当前状态。

### QQ 渠道聚合

`WebRuntime.capability_statuses()` 消费 `ChannelManager.statuses()` 时，不再把 `qq.*` 放入公开结果，而是聚合到 `channel.qq`：

- 全部账号 `available`：`channel.qq=available`；
- 全部账号 `disabled`：`channel.qq=disabled`；
- 没有可用账号且至少一个 `unavailable`：`channel.qq=unavailable`；
- 其它混合状态或任一 `degraded`：`channel.qq=degraded`。

聚合 details 只包含 `account_count`，不包含 `main` 等账号 key。若 QQ 启动检查失败、没有构造 Adapter，则继续保留原 `channel.qq` 启动状态。

## 数据流

```text
QQAccountAdapter.status() × N
  -> ChannelManager.statuses()（内部 qq.*）
  -> WebRuntime 聚合
  -> channel.qq
  -> /health、管理监控

WeixinAdapter 恢复 available
  -> ChannelManager 清除旧 _failures[channel.weixin]
  -> channel.weixin=available
```

## 变更文件

- `agent/channels/manager.py`
- `agent/app/runtime.py`
- `tests/unit_test/channels/test_channels.py`
- `tests/unit_test/channels/test_case.md`
- `tests/unit_test/app/test_runtime_commands.py`
- `tests/unit_test/app/test_case.md`
- `docs_design/README.md`
- `docs_design/zhice-agent-part14-external-channel-design.md`

## 测试方案

- 历史启动失败在 Adapter 恢复 `available` 后被清除。
- 未恢复时仍返回 `CHANNEL_START_FAILED`。
- 单个 QQ 账号可用时只输出 `channel.qq`，不输出 `qq.main`。
- 多账号全可用、全不可用和混合状态按规则聚合。
- 公开聚合结果不包含账号 key。
- 运行 Ruff、渠道/App 定向测试和全量 pytest。

## 验收标准

1. 管理页和 `/health` 不再出现 `qq.main`。
2. `channel.qq` 反映具体 QQ Adapter 的当前运行状态。
3. 微信重新扫码恢复后，`channel.weixin` 能从旧启动失败恢复为 `available`。
4. QQ 多账号内部处理、身份与 Session 隔离能力不变。
5. 现有配置和数据库无需迁移。
