# `/memory` 展示与 Session Summary 命令收敛设计

> 说明：当前代码已进一步删除未形成消费闭环的 Session Summary 能力，`/memory` 只展示长期 Memory；本文正文保留当时方案，当前口径参考 `2026-07-16-remove-unclosed-session-summary-design.md` 和 Part 10 活文档。

> 状态：已确认，进入代码落地。

## 1. 背景

此前 `/memory` 被定义为手动扫描当前 Session 并提取长期 Memory，`/memory list` 才负责展示已有 Memory，另有 `/memory extract <session_id>` 和 `/memory summarize [session_id]`。这组命令暴露了过多内部处理概念，也让最常用的“查看系统记住了什么”需要额外子命令。

当前长期 Memory 已有两条写入路径：用户明确要求或自然确认时由 `memory_write` 写入；Web Session 空闲后由后台高置信 Extractor 写入。用户侧再提供手动“提取并存入记忆”命令没有必要。

## 2. 目标

- `/memory` 直接展示当前 actor 的长期 Memory。
- `/memory session [session_id]` 生成或更新当前 Session，或当前 actor 可访问的指定 Session 摘要。
- 主 `/help` 只展示 `/memory` 顶层入口。
- `/memory` 结果末尾用与 `/sessions`、`/model` 一致的 `Tip:` 格式提示高级子命令。
- 删除 `/memory list`、`/memory extract` 和 `/memory summarize` 的用户命令入口，不保留本地兼容分支。

## 3. 边界

- 不删除后台 Memory Extractor；它继续负责 Web Session 空闲后的高置信长期信息提取。
- 不删除 `memory_write`；用户明确要求记忆、修改或忘记时仍由模型直接调用。
- Session Summary 仍是可删除、可重建的派生文件，不替代 Session JSONL。
- Session Summary 当前不自动注入普通聊天上下文，也不等于自动上下文压缩；它只提供手动快照和后续显式读取能力。

## 4. 命令设计

```text
/memory
  展示当前 actor 的长期 Memory

/memory session
  生成或更新当前 Session Summary

/memory session <session_id>
  生成或更新当前 actor 可访问的指定 Session Summary
```

展示结果末尾：

```text
Tip: use `/memory session [session_id]` to save a Session Summary.
```

## 5. 变更文件

- `agent/cli.py`
- `agent/app/runtime.py`
- `agent/memory/presentation.py`
- `tests/unit_test/cli/test_cli_init.py`
- `tests/unit_test/app/test_runtime_commands.py`
- `tests/unit_test/cli/test_case.md`
- `tests/unit_test/app/test_case.md`
- `README.md`
- `docs_design/zhice-agent-part10-memory-design.md`
- `docs_design/zhice-agent-overall-design.md`
- `docs_design/README.md`

## 6. 测试方案

- CLI/Web `/memory` 均展示当前作用域 Memory。
- CLI/Web `/memory session` 均生成当前 Session Summary。
- 可选 `session_id` 只允许访问当前 actor 已授权 Session。
- `/help` 只展示简洁 `/memory` 入口。
- `/memory list`、`/memory extract`、`/memory summarize` 不再作为有效命令。
- Memory 结果包含统一 `Tip:`。

## 7. 验收标准

1. CLI 与 Web 使用完全一致的 Memory 命令语义。
2. `/memory` 不触发 LLM 提取调用。
3. 手动提取命令不再暴露。
4. Session Summary 被明确描述为手动派生摘要，不宣称已经接入自动上下文压缩。
5. 专项测试、Ruff 和全量测试通过。
