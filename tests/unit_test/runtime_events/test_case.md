# RuntimeEvent 单元测试用例

## 测试目标

验证 Part 12 transport-neutral RuntimeEvent 的字段白名单、状态映射、turn-scoped sequence、安全展示字段和 best-effort sink 行为。

## 用例覆盖

- 合法 Event 可稳定序列化，非法版本、type/status、sequence 和 timestamp 被拒绝。
- display、ui_metadata、metadata 执行字段、类型、大小和敏感键限制。
- 同一 emitter 从 1 单调递增，不同 emitter 相互隔离。
- sink 抛异常时不影响 emitter 调用方。
- RuntimeEvent payload 可与旧 `text_delta` / interaction dict 区分。
- Subagent 的 agent/root/parent/batch/task/depth scope 可稳定序列化，depth 超过第一阶段硬上限时拒绝。
