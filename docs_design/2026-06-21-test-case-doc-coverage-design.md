# 测试用例说明补齐设计

## 背景

当前 `tests/unit_test` 下大多数测试主题目录都有 `test_case.md`，用于说明测试目标、覆盖场景和检查点。但新增的 `console` 与 `skills` 测试目录还缺少对应说明，后续继续扩展时容易出现测试代码有覆盖、文档没有同步的情况。

## 目标

- 为缺失的测试主题目录补齐 `test_case.md`。
- 在 `AGENTS.md` 的测试规范中写明测试主题目录应维护 `test_case.md`。
- 规则保持轻量：按测试主题目录维护，不要求每个测试文件单独配一份说明。

## 范围边界

- 只补测试说明文档和开发规范，不改变测试代码与运行行为。
- 不重新整理已有测试目录的 `test_case.md`。

## 变更文件

- `AGENTS.md`
- `tests/unit_test/console/test_case.md`
- `tests/unit_test/skills/test_case.md`

## 测试方案

- 扫描 `tests/unit_test` 下包含 `test_*.py` 的主题目录，确认都有 `test_case.md`。
- 本次为文档规范补齐，不需要运行完整单元测试。

## 验收标准

- `console` 与 `skills` 测试目录都有测试用例说明。
- `AGENTS.md` 明确要求新增或扩展测试主题目录时同步维护 `test_case.md`。
