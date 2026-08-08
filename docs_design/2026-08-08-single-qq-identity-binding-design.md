# 单账号单 QQ 身份绑定设计

## 背景

当前 `external_identities` 只约束 `(channel, external_tenant_id, external_user_id)` 唯一，因此一个 QQ 身份不能同时映射多个内部用户，但同一个内部用户可以存在多条 active QQ identity。渠道设置会如实列出这些记录；当 QQ 平台没有提供昵称时，多条记录都显示为“QQ 身份”，用户无法区分。

当前产品只部署一个共享 QQ Bot，用户口径明确为“一个 ZhiCe-Agent 账号只绑定一个 QQ”。现有一对多行为需要在数据库和两条绑定入口同时收紧。

## 目标

1. 一个内部用户最多拥有一条 active QQ identity，与 QQ Bot account key 数量无关。
2. 已绑定 QQ 的用户再次绑定另一个 QQ 时拒绝操作，并提示先解绑原 QQ。
3. 网页授权 `/bind/qq` 和手动 `/bind <code>` 使用同一条底层约束。
4. 历史数据库若存在多条 active QQ，迁移时保留最近绑定的一条，其余改为 disabled，避免唯一索引导致 Gateway 无法启动。
5. disabled 历史记录继续保留审计价值；解绑后允许绑定新的 QQ。

## 非目标

- 不静默用新 QQ 顶替旧 QQ。
- 不删除历史 Session、审计记录或 disabled identity。
- 不改变微信单账号约束和其它未来渠道的多身份策略。
- 不限制一个 QQ Bot 服务多个不同内部用户。

## 数据库设计

初始化 schema 后执行幂等迁移：

1. 找出每个 `user_id` 的多条 active QQ identity；
2. 按 `linked_at DESC, id DESC` 保留最新一条；
3. 将其余记录更新为 `disabled`；
4. 创建 partial unique index：

```sql
CREATE UNIQUE INDEX IF NOT EXISTS uq_external_identities_active_qq_user
ON external_identities(user_id)
WHERE channel='qq' AND status='active';
```

partial index 允许同一用户保留多条 disabled 历史记录，同时从数据库层阻止并发请求绕过应用检查。

## 业务流程

### 网页授权

`consume_external_authorization_request` 在 `BEGIN IMMEDIATE` 事务中先检查当前用户是否已有另一条 active QQ：

- 没有：消费 token 并创建 identity；
- 已有同一 QQ：允许幂等刷新；
- 已有另一 QQ：抛出稳定 conflict，不消费 token，返回 HTTP 409 与 `CHANNEL_QQ_USER_ALREADY_BOUND`。

### 手动绑定码

`link_external_identity` 使用相同事务检查。若冲突，QQ Adapter 捕获结构化异常并回复“当前账号已绑定其他 QQ，请先在网页渠道连接中解绑”，不进入 AgentLoop。

手动短码在底层 identity 写入前已经消费；冲突后用户完成解绑，需要重新生成短码。这一点在提示和测试中明确。

## 变更文件

- `agent/auth/store.py`
- `agent/app/api/routes.py`
- `agent/channels/qq/adapter.py`
- `tests/unit_test/auth/test_auth_store.py`
- `tests/unit_test/channels/test_channels.py`
- `tests/unit_test/app/test_auth_routes.py`
- 对应 `test_case.md` 与 Part 14 活文档

## 测试方案

- 同一用户绑定两个不同 QQ：第二次底层写入抛 conflict。
- 同一 QQ 幂等刷新：仍只有一条 active identity。
- 用户解绑后绑定新 QQ：成功。
- 网页授权冲突：HTTP 409、稳定错误码、原 QQ 保持 active、新 QQ不写入。
- 手动绑定冲突：返回明确中文提示，不触发 AgentLoop。
- 历史重复 active 数据迁移：保留最新一条、禁用旧记录、唯一索引建立成功。
- 并发/数据库兜底：partial unique index 阻止第二条 active QQ。

## 验收标准

- 渠道设置对任一用户最多返回一条 active QQ binding。
- 已绑定时从另一个 QQ 打开 `/bind` 页面，页面明确提示先解绑。
- 解绑后可使用新 QQ 重新绑定。
- 现有 QQ 对话、微信凭据和历史 Session 不受影响。
