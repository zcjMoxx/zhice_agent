# 纯文本展示适配与 QQ 回复序号设计

## 背景

LLM 的规范输出允许 Markdown。Web 与 QQ 私聊能够渲染 Markdown，但 CLI 当前直接 `print(result)`，QQ 群聊为获得稳定的被动回复 `@提问者` 也必须发送普通文本，两者都会把 `**`、反引号和链接语法原样展示。

同时，QQ 被动回复要求同一 `msg_id` 的多条回复使用不同 `msg_seq`。当前分块发送一直依赖 botpy 默认值 `1`，第二块开始可能被平台判定为重复。QQ 官方限制为群聊单条入站消息最多回复 5 次，单聊最多回复 4 次。

## 目标

- 提供中性、确定性的 Markdown 到可读纯文本 renderer，供 CLI 与不支持 Markdown 的渠道复用。
- QQ 群聊在发送前渲染纯文本，保留标题、列表、链接、引用和代码的可读结构。
- CLI 最终回答与 `/history` 展示使用同一个 renderer。
- QQ 分块按同一入站消息递增 `msg_seq`，群聊最多 5 块，单聊最多 4 块。

## 范围边界

- Renderer 位于 `agent/presentation/`，不依赖 CLI、QQ SDK、AgentLoop 或 Provider。
- Renderer 只改变展示文本，不修改 Session 中保存的原始 Markdown。
- QQ 的 `msg_id`、`msg_seq`、自动 `@`、引用尝试和平台块数限制继续留在 QQ Adapter/Transport。
- Web 与 QQ 私聊保留原生 Markdown，不经过纯文本 renderer。
- 超出被动回复块数时，最后一块使用明确截断提示；不转主动消息，不绕过平台配额。

## 模块设计

### 通用 renderer

新增 `markdown_to_plain_text(text)`，按行维护 fenced-code 状态并做确定性转换：

- 标题删除 `#`，保留标题与段落；
- 粗体、斜体、删除线、行内代码删除样式符号，保留内容；
- 无序列表统一为 `•`，有序列表保留序号；
- task list 转换为 `☑` / `☐`；
- 链接转换为 `标题：URL`，图片转换为 `[图片：说明] URL`；
- 引用转换为 `│ 内容`；
- fenced code 删除围栏、保留语言提示和代码原文；
- Markdown 表格删除分隔行，将数据行转换为 `列名：值`；
- 不识别的内容原样保留，转换异常时调用方仍可使用原始文本。

### QQ 分块与序号

```text
render plain text
  -> chunk_text(limit=1800)
  -> group: keep at most 5 chunks
     c2c: keep at most 4 chunks
  -> send_text(msg_seq=index + 1)
```

只有群聊第一块请求 `message_reference`；每一块使用唯一 `msg_seq`。若原始内容超过平台被动回复上限，最后一块预留空间并追加“回答过长，请在私聊或 Web 查看完整内容”。

## 数据流

```text
AgentLoop result Markdown
  ├─ Web / QQ c2c -> Markdown renderer
  ├─ CLI -> shared plain-text renderer -> stdout
  └─ QQ group -> shared plain-text renderer -> chunks -> msg_seq 1..5
```

## 变更文件

- `agent/presentation/__init__.py`
- `agent/presentation/plain_text.py`
- `agent/cli.py`
- `agent/channels/qq/adapter.py`
- `agent/channels/qq/transport.py`
- `tests/unit_test/presentation/*`
- `tests/unit_test/cli/*`
- `tests/unit_test/channels/*`
- Part 2、Part 14 与总体设计活文档

## 测试方案

- 正常：标题、强调、列表、链接、引用、代码、表格得到可读纯文本。
- 异常：未闭合代码围栏和不完整 Markdown 不丢失正文。
- 边界：空文本、普通文本幂等、Windows 换行和超长内容。
- QQ：同一消息多块依次发送 `msg_seq=1..N`，群聊不超过 5，单聊不超过 4。
- QQ：第一块引用、后续块不引用；超限最后一块包含提示。
- CLI：最终回答和历史展示不再打印 Markdown 样式标记。

## 验收标准

- QQ 群聊和 CLI 对同一 Markdown 回答得到一致、清楚的纯文本主体。
- Session JSONL 仍保存 LLM 原始 Markdown。
- QQ 不因重复 `msg_id + msg_seq` 丢失第二块及后续块。
- Ruff、专项测试、前端语法检查和全量 pytest 通过。
