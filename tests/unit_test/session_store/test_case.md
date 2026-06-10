# SessionStore 单元测试用例

## 测试目标

验证 JSONL SessionStore 能安全、稳定地追加和读取会话消息。

## 测试用例

### Case 1: 读取不存在的 session

- **输入**: `load("default")`
- **预期**: 返回空消息列表

### Case 2: 追加后读取

- **输入**: 追加 user 和 assistant 消息
- **预期**: 再次读取时顺序保持一致
- **检查点**:
  - role 顺序正确
  - content 顺序正确
  - timestamp 保留

### Case 3: UTF-8 JSONL

- **输入**: 写入中文消息
- **预期**: 文件以 UTF-8 保存，内容可读

### Case 4: 非法 session_id

- **输入**: `../escape`、`bad/name`、空字符串
- **预期**: 抛出 `InvalidSessionIdError`

### Case 5: 兼容未知字段

- **输入**: JSONL 中包含未来字段
- **预期**: 读取时忽略未知字段，不影响消息恢复
