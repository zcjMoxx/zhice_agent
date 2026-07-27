# 微信可靠发送与重连补偿设计记录

> 日期：2026-07-26
>
> 状态：已落地。

## 背景

真实trace显示：Agent回复已写入Session后，Python等待Sidecar发送10秒超时；Node发送上限却是15秒。发送异常只结束当前入站处理，不触发账号恢复，也没有待发送队列。稍后独立长轮询报告`WEIXIN_POLL_FAILED`才触发重连；重连成功后首次回复不会补发，导致Session认为已回答、用户实际未收到。

## 目标

- Python等待窗口覆盖Node发送上限，避免10秒提前超时造成结果未知。
- 每个回复分块使用稳定`delivery_id/client_id`，同一分块重放不生成新ID。
- 回复在调用Sidecar前写入SQLite Outbox；只有收到`message.send_result`才标记sent。
- 发送失败立即把微信能力标记degraded并启动已有账号重连。
- 账号恢复active后自动、顺序重放当前账号pending分块，不重新调用LLM。
- 进程重启后仍能恢复pending分块；已发送记录有界清理。

## 边界

- Session JSONL仍是对话真值；Outbox只保存渠道交付派生状态，不替代Session。
- 不在AgentLoop加入微信判断；补偿全部位于Channel Adapter、Sidecar和本地状态存储。
- 不重放已标记sent的分块；同一入站事件仍只执行一次Agent Turn。
- Token失效仍进入`reconnect_required`，不得用旧凭据循环重试。
- Outbox按`account_key + event_id + chunk_index`唯一，账号解绑时由现有账号清理链删除。

## 数据设计

SQLite新增`weixin_outbound_messages`：

```text
delivery_id PK
account_key, event_id, peer, context_token_ref
chunk_index, text
status: pending|sent
attempt_count, last_error_code
created_at, updated_at, sent_at
UNIQUE(account_key, event_id, chunk_index)
```

`delivery_id`由账号、事件和chunk序号确定性派生；Sidecar将它作为官方发送API的`client_id`，不再每次随机生成。

## 数据流

```text
Agent Turn完成
  -> 所有chunk原子/幂等入Outbox(pending)
  -> message.send(timeout > Node timeout, stable client_id)
  -> 成功: sent
  -> 失败: pending + degraded + account retry
  -> account active
  -> drain pending in original order
  -> 全部sent，不重新执行Agent Turn
```

## 变更文件

- `agent/auth/schema.py`、`agent/auth/store.py`
- `agent/channels/weixin/adapter.py`、`agent/channels/weixin/sidecar.py`
- `integrations/weixin_sidecar/src/official-driver.js`
- 微信、Auth与Sidecar测试及对应`test_case.md`

## 测试方案

- 正常：首次发送成功、稳定client_id、sent状态。
- 异常：发送超时/transport error立即降级并调度重连；重连后补发一次。
- 边界：进程重启恢复、重复入站不重跑LLM、多chunk部分成功只补余下chunk、stale token不循环重试、已发送历史有界清理。
- 回归：Ruff、Node Sidecar测试、微信专项、Auth/Channel/App相关回归与全量pytest。

## 验收标准

- trace能看到`outbox_enqueued/replay_start/replay_done`及稳定安全错误码。
- 发送失败后无需用户再发第二条消息即可进入恢复流程。
- 恢复后首次回复自动送达且不重复调用LLM。
- 旧数据库启动时幂等建表，不破坏现有账号、Session和事件去重记录。

## 落地结果

- Python `message.send`等待窗口为20秒，覆盖Node官方发送API的15秒上限；超时与写入失败使用稳定安全错误码。
- SQLite Outbox、稳定`delivery_id/client_id`、同账号发送串行锁、主动账号恢复、重连/进程重启补发均已实现。
- 多chunk部分成功时只重放pending部分；恢复过程不重新执行Agent Turn；解绑删除对应Outbox，sent记录按7天保留期清理。
- Ruff全项目通过；微信/Auth专项`65 passed`，微信/Auth/Channel/App相关回归`252 passed`，Node Sidecar`11 passed`，全量`751 passed, 1 skipped`。
- 真实工作区`auth.sqlite3`已完成幂等建表，迁移时pending为0；Gateway已在`127.0.0.1:10086`重启，`/api/health`返回`ok`，微信账号ready为`active=1/reconnect_required=0`。
