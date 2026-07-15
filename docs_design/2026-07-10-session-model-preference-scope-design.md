# ZhiCe-Agent Session 级模型偏好范围设计记录

> 日期：2026-07-10
>
> 状态：设计已确认；代码尚未实现。
>
> 承接：`docs_design/2026-07-08-user-auth-permission-boundary-design.md`、`docs_design/zhice-agent-part9-user-auth-permission-design.md`
>
> 说明：后续实现中 Owner Web 只复用 CLI 全局 `contexts/sessions_meta`，不复制到 Owner 用户目录；普通用户 CLI 历史导入暂未纳入当前实现。本文正文里的“显式导入 DB 用户目录”仅保留为当时设想，当前口径以 Part 9 活文档为准。

## 1. 背景

第九部分需要解决多用户环境下 Web gateway 共享模型偏好导致的串扰。前一版修正方案把模型偏好放到用户层，但这仍会让同一用户的多个独立会话互相影响。

最终确认：模型偏好属于 session，不增加用户默认模型层。第九部分只把原 gateway 进程级偏好细分到 session 级，保留 `/model`、`/model reset` 和 `/new` 的产品语义。

## 2. 目标

- 每个 session 独立保存 endpoint/model 偏好。
- 同一用户的不同 session 可以使用不同模型。
- 不同用户、不同渠道和不同 session 之间都不共享可变模型状态。
- `/model reset` 清除当前 session 偏好，恢复系统默认模型。
- `/new` 创建新 session；新 session 没有模型偏好，因此使用系统默认模型。
- 模型选择按 turn 解析，AgentLoop 仍只依赖 `LLMProvider` 协议。

## 3. 范围边界

第一版包含：

- Session 级模型偏好持久化。
- Web、REST、SSE、WebSocket、CLI 和未来外部渠道统一按 `session_id` 解析模型。
- `model.view` / `model.switch` 权限检查。
- `/model`、`/model reset`、`/new` 的 session 语义。
- 偏好失效、endpoint failover、trace 和 audit 口径。

第一版不包含：

- 用户默认模型。
- 按角色或渠道设置默认模型。
- session 之外的额外偏好继承层。
- 完整 endpoint 管理页。
- 修改 `llm_endpoints.json` 中的系统默认配置。

## 4. 数据模型

模型偏好属于 session metadata，不新增 `user_model_preferences` 表，也不把偏好放入用户记录。

建议扩展现有 `sessions_meta/{session_id}.json`：

```json
{
  "title": "...",
  "preferred_endpoint_name": "openai_gpt5",
  "preferred_model_name": "gpt-5-mini"
}
```

路径：

```text
CLI:
  contexts/sessions_meta/{session_id}.json

Web / 外部渠道 DB 用户:
  contexts/users/{user_id}/sessions_meta/{session_id}.json
```

规则：

- 两个偏好字段同时为空或不存在时，使用系统默认 endpoint/model。
- `session_index` 不重复保存模型偏好，只负责 session 查询、owner、渠道和归档索引。
- 删除 session 时同时删除模型偏好 metadata。
- CLI session 显式导入 DB 用户目录时，一并复制模型偏好。

## 5. 命令语义

```text
/model
  -> 查看当前 session 的有效 endpoint/model

/model <endpoint>[/<model>]
  -> 写入当前 session 的偏好

/model reset
  -> 清除当前 session 的偏好字段
  -> 当前 session 恢复系统默认模型

/new
  -> 创建并切换到新的 session_id
  -> 新 session 不继承旧 session 偏好
  -> 使用系统默认模型
```

切回已有 session 时，应重新加载该 session 自己的模型偏好。

## 6. 模块设计

```text
SessionModelPreferenceStore
  -> get(session_context, session_id)
  -> set(session_context, session_id, endpoint_name, model_name)
  -> reset(session_context, session_id)

SessionModelPreferenceResolver
  -> validate configured endpoint/model
  -> return effective ModelSelection
  -> use system default when absent or stale
```

app/runtime 根据 actor 权限和用户目录边界定位 session metadata，再把不含用户信息的 `ModelSelection` 绑定成 turn-local `LLMProvider`：

```text
actor + session_id
  -> authorize session access
  -> resolve session model preference
  -> bind turn-local LLMProvider
  -> AgentLoop.run_turn(..., llm_override=turn_llm)
```

Web / 外部渠道不能通过共享 `EndpointFailoverProvider.set_preferred()` 修改 gateway 全局状态。

## 7. API 与渠道

模型 API 必须携带或解析当前 session：

```text
GET    /api/models?session_id={session_id}
POST   /api/model/preference
DELETE /api/model/preference?session_id={session_id}
```

POST 请求至少包含 `session_id` 和 `model`。服务端先校验 actor 对该 session 的访问权限，再读写该 session 的 metadata。

外部渠道先通过 `external_identities` 得到内部用户，再通过外部 chat/thread 映射到内部 `session_id`；模型偏好最终仍以 session 为边界。

## 8. 数据流

切换当前 session 模型：

```text
actor + session_id + model
  -> authorize session access and model.switch
  -> validate endpoint/model
  -> update sessions_meta/{session_id}.json
  -> audit model.switched
```

执行普通 turn：

```text
actor + session_id
  -> resolve session metadata path
  -> load session model preference
  -> resolve system default when absent
  -> bind turn-local LLMProvider
  -> AgentLoop.run_turn(llm_override=turn_llm)
```

新建 session：

```text
/new or Web new_session
  -> create new session_id
  -> do not copy previous model metadata
  -> effective model = system default
```

## 9. Failover 与审计

- Session 保存的是首选 endpoint/model。
- Failover 只影响当前 turn 的实际调用，不覆盖 session 偏好。
- `turn_runs` / trace 记录 preferred 与 actual endpoint/model。
- `model.switched` / `model.reset` 记录 actor、session、旧值和新值。

## 10. 变更文件

设计阶段新增或修改：

```text
docs_design/2026-07-10-session-model-preference-scope-design.md
docs_design/2026-07-08-user-auth-permission-boundary-design.md
docs_design/2026-06-15-model-command-and-endpoint-failover-design.md
docs_design/zhice-agent-part9-user-auth-permission-design.md
docs_design/zhice-agent-overall-design.md
docs_design/zhice-agent-part6-web-minimum-design.md
docs_design/zhice-agent-part6-web-ui-design.md
docs_design/README.md
```

实现阶段预计涉及：

```text
agent/protocols/session.py
agent/protocols/llm.py
agent/session/model_preferences.py
agent/app/runtime.py
agent/app/api/routes.py
agent/app/api/ws.py
agent/app/api/schemas.py
agent/core/loop.py
agent/cli.py
web/static/app.js
tests/unit_test/session/*
tests/unit_test/app/*
tests/unit_test/agent_loop/*
tests/unit_test/cli/*
```

## 11. 测试方案

- 同一用户的 session A 切换模型后，session B 保持系统默认或自己的偏好。
- 不同用户使用相同 session 名称时仍按用户目录隔离。
- `/model reset` 只清除当前 session 偏好。
- `/new` 创建新 session 后使用系统默认，不继承旧 session 偏好。
- 切回已有 session 时恢复该 session 偏好。
- Web、REST、SSE、WebSocket 和外部渠道按 session 读取一致结果。
- 无 `model.switch` 权限时不能写入或重置偏好。
- 偏好失效时回退系统默认，但不静默改写 metadata。
- Failover 不覆盖 session 首选模型。
- 并发 session 使用各自 turn-local provider，不发生共享状态串扰。

## 12. 验收标准

1. 模型偏好的唯一业务范围是 session，不存在用户默认层。
2. `/model` 修改当前 session，`/model reset` 恢复当前 session 的系统默认。
3. `/new` 创建的 session 不继承旧 session 模型偏好。
4. 同一用户的不同 session 可同时使用不同模型。
5. Web / 外部渠道不修改共享 gateway provider 的全局偏好。
6. AgentLoop 不查询 session metadata，只消费 app/runtime 绑定的 `LLMProvider`。
