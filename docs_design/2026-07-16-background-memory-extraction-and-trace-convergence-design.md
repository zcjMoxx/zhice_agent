# 后台 Memory 提取与 Trace 收敛设计

> 说明：本文记录最初的 per-Session idle timer 方案。当前代码已改用单一协调线程、有界 Worker Pool 和用户级串行调度，不再为每个 Session 创建 `threading.Timer`；参考 `docs_design/2026-07-16-memory-extraction-concurrency-design.md` 和 Part 10 活文档。

## 1. 背景

Part 10 当前依赖普通聊天模型在回答末尾自行识别长期偏好并询问用户。真实测试中，模型连续五轮遵守“先给结论，最多三点”，但没有稳定提出 Memory 保存询问。与此同时，Web 每轮重复提交相同模型，运行时会重复记录 `web.model.switched` 和 `web.model.turn_selected`，trace 还包含多组与 Turn、LLM、Tool 生命周期重复的事件。

本次按调查结果把长期信息识别从普通 AgentLoop 中拆出，并收敛运行事件。用户明确要求“记住/修改/忘记”时仍由当前 Turn 直接调用 Memory Tool；普通对话不增加隐藏的逐 Turn LLM 调用。

## 2. 目标

- Session 空闲后异步运行一次独立 Memory Extractor。
- 只自动保存高可信、长期稳定且有重复证据的 `profile`、`preferences`、`constraints`。
- 至少三个用户 Turn 才允许提取，每项必须引用两到三条不同 Turn 的用户证据。
- 中低可信结果直接丢弃，不创建候选、审批或弹窗状态机。
- 自动写入后，在该用户下一次对话开始时显示一条简短通知。
- 只在模型真实变化时记录 `model.switched`，删除每轮 `model.turn_selected`。
- 删除与主生命周期重复、价值低的成功事件和内容预览，错误事件继续保留。

## 3. 范围边界

### 3.1 本次包含

- Web/外部 WebSocket Session 的空闲提取调度。
- 可独立单测的 Memory 提取服务、严格输出解析、检查点和通知。
- 真实模型切换判断。
- AgentLoop/Web trace 事件收敛。

### 3.2 本次不包含

- 每个 Turn 结束后的同步总结。
- Memory 候选确认、修改、拒绝和前端弹窗。
- 跨用户 Memory 查询或管理员 Memory 管理。
- 独立监控平台与系统级 Trace 诊断界面。
- CLI 常驻后台调度；CLI 明确 Memory 请求仍按现有 Tool 流程执行。

## 4. 模块设计

### 4.1 `MemoryExtractionService`

新增独立服务，输入已授权的 `MemoryStore`、Session messages 和绑定后的 `LLMProvider`。服务只处理尚未检查的用户 Turn：

1. Session 至少包含三个用户 Turn。
2. 最多读取最近四十个用户 Turn，并受字符上限约束。
3. LLM 必须返回严格 JSON，只允许 `profile`、`preferences`、`constraints`。
4. 每项包含规范化内容、`confidence=high` 和两到三条不同 `turn_index` 证据。
5. 服务校验证据确实来自输入中的用户消息，再经过 `MemorySafetyPolicy`。
6. 与现有 Memory 完全重复时不重复写入；合法新项直接写入。
7. 无论本次是否写入，都推进该 Session 的检查点，避免反复审查同一批 Turn。

检查点和待展示通知属于派生运行状态，放在当前 actor 的 Memory 目录下，不进入 Session JSONL，也不改变 Memory Tool 协议。

### 4.2 空闲调度

`WebRuntime` 在普通 Turn 成功保存后，为 `(actor, session)` 重置一个后台定时器，默认空闲五分钟触发。新 Turn 开始时取消旧定时器；Gateway 关闭时取消全部定时器。

提取任务使用该 Session 已授权的 SessionStore、MemoryStore 和模型选择，不能访问其他用户目录。后台失败只记录错误，不影响聊天结果。

### 4.3 用户通知

Extractor 自动写入新项后，把简短文案写入该 Memory 作用域的待通知文件。该 actor 下一次普通聊天开始时消费一次，并通过现有文本流显示：

```text
💾 根据上次对话，我记住了：回答时先给结论，最多列三点。
```

通知不写回 Session，不伪装成用户或模型推理，也不要求再次确认。用户仍可用自然语言修改或删除。

### 4.4 Prompt

新增 `prompts/memory_extraction.md`，只供后台服务调用。普通 `memory_policy.md` 删除“模型自行识别后主动询问”的职责，保留明确请求、自然语言修改/删除和 Memory Read/Write 规则。

## 5. Trace 与终端日志收敛

### 5.1 模型事件

- 删除 `web.model.turn_selected`。
- `web.model.switched` 仅在新 endpoint/model 与当前 Session 有效选择不同时写入。
- `/model reset` 仅在原来确实存在 Session 偏好时记录 `model.reset`。
- 实际调用的模型信息由 `llm.call` / `llm.done` 承担，不再额外生成每 Turn 选择事件。

### 5.2 删除的重复事件

- `web.chat.accepted`、`web.chat.done`：与 `turn.start`、`turn.done` 重复。
- `llm.direct`：最终回答已经存在 Session，且 `llm.done` 已描述调用结果。
- `session.save` 成功事件：Turn 完成即表示成功保存；保留 `session.save_failed`。
- `tool.args`：安全参数摘要合并到 `tool.start`。
- `turn.done.output_preview`：正文已经存在 Session；Turn 日志只留状态、索引、耗时和错误码。

保留 `turn.*`、`llm.call/done/error`、`tool.start/done/error`、Session 结构变更、模型真实切换/重置/降级及全部错误事件。

## 6. 数据流

```text
普通聊天完成
  -> Session JSONL 保存
  -> 重置该 Session 的 idle timer
  -> 空闲五分钟
  -> MemoryExtractionService 读取未检查用户 Turn
  -> LLM 返回严格 JSON
  -> 证据、类别、置信度和安全校验
  -> 高可信新项写入 Memory
  -> 写入一次性通知
  -> 下一次聊天先显示通知，再正常执行 AgentLoop
```

## 7. 变更文件

- `agent/memory/extraction.py`：提取、检查点和通知服务。
- `agent/protocols/memory.py`：提取结果数据结构。
- `agent/app/runtime.py`：空闲调度、actor 作用域绑定、通知消费和模型事件去重。
- `agent/app/gateway.py`：Gateway shutdown 清理。
- `agent/core/loop.py`、`agent/app/logging.py`：事件收敛。
- `prompts/memory_extraction.md`、`prompts/memory_policy.md`：职责拆分。
- `tests/unit_test/memory/`、`tests/unit_test/app/`、`tests/unit_test/agent_loop/`：新增和调整测试。
- Part 8、Part 10 活文档：同步当前主线行为。

## 8. 测试方案

- 少于三个用户 Turn 不调用 LLM。
- 证据不足、证据 Turn 不存在、类别非法、置信度非 high、敏感内容均不写入。
- 高可信重复行为写入一次，重复运行不重复写入。
- 检查点阻止重复处理；新增 Turn 后允许再次提取。
- 新 Turn 会取消旧 timer；空闲任务失败不影响聊天。
- 自动写入通知只消费一次。
- 相同模型不写 `model.switched`，真实切换只写一次。
- 不再出现 `model.turn_selected`、`web.chat.accepted/done`、`llm.direct`、成功 `session.save`、`tool.args` 和 `turn.done.output_preview`。

## 9. 验收标准

1. 连续表达同一长期风格至少三轮后，空闲提取可自动写入 Memory。
2. 普通 Turn 不增加同步 LLM 调用和用户可感知延迟。
3. 自动写入仅限三类高可信长期信息，并有两到三条真实用户 Turn 证据。
4. 无候选弹窗、审批状态机或逐 Turn review。
5. 下一次对话能看到一次简短 Memory 通知。
6. 相同模型不会在每轮重复产生模型事件。
7. Trace 保留完整故障诊断链，但明显重复的成功事件被删除。
8. `python -m ruff check .` 与 `python -m pytest` 通过。
