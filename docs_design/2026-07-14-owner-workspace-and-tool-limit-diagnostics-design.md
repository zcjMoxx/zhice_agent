# Owner 全局工作区与工具上限诊断回退设计

## 背景

Owner 的 Web 会话已经复用全局 `contexts/sessions`，但 WebRuntime 为所有已认证用户创建 `UserScopedToolProvider` 时都使用 `contexts/users/{user_id}/files`。因此 Owner 在聊天中不能读取工作区的 `logs`、`state`、`contexts`，与本地单 Owner 运维和排查的预期不一致。

AgentLoop 达到 `max_tool_iterations` 后会保存未执行工具的错误 marker，并直接把固定英文错误返回给用户。模型没有机会基于本轮已回填的工具结果说明已达到上限、已经确认的证据和未完成的检查。

## 目标

- Owner 的 Web 工具工作目录为整个 `${ZHICE_AGENT_WORKSPACE}`；其他用户继续只访问各自 `files` 和只读 `shared` 挂载。
- Owner 仍只能通过 `diagnose_my_recent_activity` 查看自己的 trace/audit 摘要；全局文件权限不改变诊断数据的用户隔离规则。
- 工具迭代到上限时，保留结构化 `TOOL_ITERATION_LIMIT` marker，并追加一次无工具 LLM 调用，让模型给出基于已收集证据的最终说明。
- 若该最终调用失败或再次要求工具，则稳定返回包含实际限制值的上限说明。

## 范围边界

- 不改变 Owner 的日常会话列表范围，也不允许普通管理员获得 Owner 的工作区文件范围。
- 不取消 `exec` 的 workspace guard、风险分类、确认或权限策略。
- 不提高默认工具轮数；循环保护仍为每轮最多 4 次工具决策。

## 模块设计

### Owner 工具作用域

`WebRuntime` 解析会话后，按 `"owner" in actor.role_keys` 选择工具工作目录：

```text
Owner       -> AppConfig.workspace
其他用户    -> UserContext.files_dir
```

同一目录同时传入 ContextBuilder 和 UserScopedToolProvider，保证提示中的路径、只读工具和 exec workspace guard 采用一致根目录。`shared/` 虚拟只读挂载仅保留给非 Owner 用户。

### 上限后的最终说明

```text
LLM 请求第 N+1 次工具
  -> 写入每个未执行 tool_call 的 TOOL_ITERATION_LIMIT tool message
  -> 写入 limit assistant marker
  -> 以 tools=None 再调用一次 LLM
  -> LLM 基于本轮已有结果输出解释
  -> 若调用失败或仍请求工具，返回明确的 fallback
```

fallback 必须说明实际 `limit` 和“未生成最终分析”，而不是只输出无法定位的泛化英文。

## 变更文件

- `agent/app/runtime.py`
- `agent/core/loop.py`
- `tests/unit_test/auth/test_web_runtime_auth.py`
- `tests/unit_test/agent_loop/test_agent_loop_tools.py`
- `tests/unit_test/auth/test_case.md`
- `tests/unit_test/agent_loop/test_case.md`
- `docs_design/zhice-agent-part9-user-auth-permission-design.md`

## 测试方案

| 场景 | 预期 |
| --- | --- |
| Owner Web chat | ContextBuilder、工具和 exec root 均为 workspace |
| 普通用户 Web chat | 仍只能使用自己的 files root |
| 上限且模型可总结 | 保存 limit marker 后无工具总结并返回该总结 |
| 上限且最终模型仍要工具 | 返回带限制值的 fallback，不执行新工具 |
| 上限且最终模型失败 | 返回带限制值的 fallback，历史保持完整 |

## 验收标准

1. Owner 可在聊天中直接读取 workspace `logs`，并保留现有安全策略。
2. 普通用户不能读取 workspace 根目录。
3. 达到工具上限时，用户获得中文/模型最终说明；无法生成说明时看到实际限制值和明确原因。
4. 未执行的 tool call 仍有配对的结构化错误消息。
