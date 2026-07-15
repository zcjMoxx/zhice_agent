# SessionStore 单元测试用例

## 测试目标

验证 JSONL SessionStore 能安全、稳定地追加、读取、清理和列出会话消息，并保持 Session 作为上下文的一部分。

## 用例覆盖

### Case 1: 读取不存在的 session

- 输入：`load("default")`。
- 预期：返回空消息列表。
- 检查点：不创建额外文件，不抛异常。

### Case 2: 追加后读取

- 输入：追加 user 和 assistant 消息。
- 预期：再次读取时顺序保持一致。
- 检查点：role、content、timestamp 都被保留。

### Case 3: UTF-8 JSONL

- 输入：写入中文消息。
- 预期：文件以 UTF-8 JSONL 保存，内容可读。
- 检查点：JSON 记录包含 role、content、timestamp、name、tool_call_id、tool_calls、metadata。

### Case 4: 非法 session_id

- 输入：`../escape`、`bad/name`、`bad.name`、空字符串。
- 预期：抛出 `InvalidSessionIdError`。
- 检查点：session_id 不能逃出 sessions 目录。

### Case 5: 兼容未知字段

- 输入：JSONL 记录中包含未来字段。
- 预期：读取时忽略未知字段。
- 检查点：已知消息字段和 timestamp 仍能恢复。

### Case 6: 清空 session

- 输入：先写入消息，再调用 `clear(session_id)`。
- 预期：对应 JSONL 文件被删除。
- 检查点：再次读取返回空 session。

### Case 7: 列出 session

- 输入：多个 session 文件。
- 预期：返回按更新时间倒序排列的摘要。
- 检查点：preview 优先来自第一条 user 消息，message_count 正确。

## Part 7 Turn Coverage

- Write top-level `turn_id`, `turn_index`, and `parent_turn_id` for new JSONL records.
- Restore turn fields only from top-level records.
- Do not promote `metadata.turn_id` or other metadata turn fields into `Message` turn fields.
- Ensure session listing remains based on preview, timestamp, and message count for new records.

## Part 9 Session Model Preference Coverage

- Session 模型偏好保存在 sidecar metadata，并保留已有 title 等字段。
- reset 只删除模型偏好字段，不删除消息或标题。
- 不同 session 的 provider 绑定互不修改共享状态；失效偏好回退系统默认。
