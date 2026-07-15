# Owner CLI 会话索引对账设计

## 背景

Owner 的 Web 与 CLI 共用全局 `contexts/sessions`，但 Web 侧栏只读取 `session_index`。既有 CLI JSONL 没有索引时，Owner 会看见空侧栏或仅看见新 Web 会话。

## 目标

Owner 列出会话时，自动为全局目录中尚未归属的 CLI JSONL 建立 Owner 索引；不复制、移动或改写 JSONL/metadata，也不接管已归属其它用户的 session。

## 数据流

```text
Owner list_sessions
  -> scan global JsonlSessionStore summaries
  -> session_index missing only
  -> create owner row (channel=cli_legacy)
  -> persist title/preview/message_count/updated_at
  -> normal indexed listing
```

## 测试与验收

- 未索引的全局 CLI session 在 Owner 列表中出现并写入 Owner 索引。
- 已归属普通用户的同名 session 不被接管。
- 对账不创建副本、不移动原文件。
