# QQ 群聊 Markdown 引用兼容修复

## 背景

`2026-07-24-qq-group-reply-attribution-design.md` 落地后，Gateway 已在新代码之后重启，Transport 也为 QQ 群 Markdown 请求携带了 `message_reference`，但真实 QQ 客户端仍未显示原消息引用。平台接口文档允许 Markdown 与 `message_reference` 同时出现，却未承诺各客户端组合渲染一致；当前实测不能把该组合视为可靠能力。

> 说明：本记录是对当天初始群聊回复归属方案的客户端兼容修复。当前实现以本文和 Part 14 活文档为准。

## 目标

- QQ 群聊回答稳定展示官方引用关系，优先保证多人问答归属。
- QQ 私聊继续使用结构化 Markdown。
- 不改变 Agent 输出内容，不重新执行 Agent Turn。

## 范围边界

- 只调整 QQ Adapter 的 Runtime 出站选择。
- Transport 仍保留 Markdown + reference 组装与降级能力，但普通群聊 Agent 回复不再选择该组合。
- 不引入未公开的群成员 `@` 语法，不暴露 member OpenID。

## 模块设计

`QQChannelAdapter._send_runtime_content()` 在 `conversation_type=group` 时直接进入 `_send()`：

```text
group -> plain text chunks -> first chunk message_reference
c2c  -> structured Markdown when suitable -> text fallback
```

群聊长回答仍只在第一块引用触发消息，后续块保持顺序发送。

## 变更文件

- `agent/channels/qq/adapter.py`
- `tests/unit_test/channels/test_channels.py`
- `tests/unit_test/channels/test_case.md`
- `docs_design/zhice-agent-part14-external-channel-design.md`
- `docs_design/README.md`

## 测试方案

- 结构化群聊回答仍走文本，不进入 Rich transport。
- 第一条群聊文本携带 quote 标记。
- 私聊结构化回答继续使用 Markdown。
- 群聊长文本只引用第一块。

## 验收标准

- QQ 群聊 Runtime 回答使用普通文本引用触发消息。
- 私聊 Markdown、群聊分块与降级回归通过。
- Ruff、渠道专项测试和全量 pytest 通过。
