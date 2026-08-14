# 新对话模型预选设计

## 背景

主聊天“新对话”采用本地草稿模式，只有发送第一条消息时才创建 Session。现有模型 Store 在 `session_id` 为空时直接清空模型目录，聊天页又以“没有活动 Session”为条件禁用选择器，导致用户必须先发送消息、创建 Session 后才能选择模型。

## 目标

- 新对话尚未创建 Session 时即可查看和选择可用模型。
- 点击模型选择器不提前创建空 Session。
- 第一条消息发送前创建 Session，并将草稿阶段预选的模型写入该 Session。
- 已有 Session 继续使用各自独立的模型偏好，不改变隔离边界。

## 范围边界

- 无 Session 的模型接口只返回默认 endpoint 的只读模型目录，不读写 Session 元数据。
- 模型偏好仍必须绑定到真实、当前用户可写的 Session；空 Session 不允许提交偏好。
- 不改变 WebSocket 消息协议、LLMProvider 协议或 AgentLoop。
- 不启动或重启 Gateway。

## 模块设计

### 只读模型目录

`GET /api/models` 在登录态下允许省略 `session_id`。`WebRuntime.model_state(actor, "")` 直接从 `ConfiguredLLMProviderResolver` 解析默认 endpoint/model 与可选模型，不调用 `SessionAccessService.ensure_session`，因此不会创建空 Session。

带 `session_id` 的读取仍先执行 actor-owned Session 校验并读取该 Session 偏好。

### 草稿阶段预选

模型 Store 在空 `session_id` 时照常加载只读目录。用户选择模型时只更新前端草稿状态，不调用偏好写接口。模型目录和偏好请求使用递增序号收敛，避免新 Session 创建时并发刷新把刚选择的模型覆盖回默认值。

### 首次发送落盘

Chat Store 在创建 Session 前先捕获草稿模型；Session 创建成功后调用现有 `/api/model/preference` 写入该 Session，再以同一模型发送第一条消息。任何失败都会停止发送并显示错误，不产生错误模型的对话 Turn。

## 数据流

```text
打开新对话
  -> GET /api/models (无 session_id，只读目录)
  -> 本地选择 model-b（仍无 Session）
  -> 发送第一条消息
  -> 创建 Web Session
  -> POST /api/model/preference (新 session_id, model-b)
  -> WebSocket 以 model-b 发送消息
```

## 变更文件

- `agent/app/api/routes.py`
- `agent/app/runtime.py`
- `web/frontend/src/stores/models.ts`
- `web/frontend/src/stores/chat.ts`
- `web/frontend/src/components/ChatPage.vue`
- `tests/unit_test/app/test_auth_routes.py`
- `tests/unit_test/auth/test_web_runtime_auth.py`
- `tests/unit_test/app/test_case.md`
- `web/frontend/src/components/ChatPage.test.ts`

## 测试方案

- API 测试：登录用户可无 Session 读取模型目录，仍不能无 Session 写偏好。
- Runtime 测试：无 Session 模型目录读取不创建 Session 索引或元数据。
- 前端测试：新对话未发消息时选择器可用；选中模型不创建 Session；首次发送才创建 Session、落盘偏好并使用该模型。
- 运行 Ruff、Pytest、前端 lint/typecheck/Vitest/build。

## 验收标准

- 新对话打开后模型下拉框立即可用。
- 选择模型后，左侧不会出现空会话。
- 第一条消息使用所选模型，并在后续刷新中保持该 Session 的模型偏好。
- 其他 Session 的模型偏好不受影响。
