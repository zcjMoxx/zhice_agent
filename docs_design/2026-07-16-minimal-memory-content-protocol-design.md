# 极简 Memory 内容协议设计

> 状态：已确认，进入代码落地。

## 1. 背景

当前 `MEMORY.md` 为每条内容保存长 ID、created_at、updated_at 和 Session/Turn source。实际代码中，ID只用于 replace/delete，updated_at只用于排序，created_at/source 没有反查或用户功能消费。把这些字段迁移到 sidecar 仍然需要维护无实际用途的数据，违背当前轻量边界。

## 2. 目标

- `MEMORY.md` 只保存分类和内容。
- 删除 Memory ID、创建时间、更新时间和来源。
- add 按分类和规范化内容去重。
- replace 使用 `category + old_content + content`。
- delete 使用 `category + content`。
- 不增加 sidecar、数据库表或兼容解析器。
- 迁移真实 workspace 时保留内容，删除旧元数据。

## 3. 文件格式

```markdown
# ZhiCe-Agent Memory

<!-- zhice-memory:start -->

## profile

## preferences

- 喜欢吃西瓜

## projects

## constraints

## decisions

<!-- zhice-memory:end -->
```

分类内禁止规范化后完全重复的内容。读取顺序就是文件顺序，不再根据时间排序。

## 4. Tool 协议

```text
add:
  operation + category + content + authorization

replace:
  operation + category + old_content + content + authorization

delete:
  operation + category + content + authorization
```

replace/delete 目标不存在时返回 `MEMORY_ENTRY_NOT_FOUND`。replace 的新内容已经存在时，删除旧内容并合并到已有内容，不产生重复条目。

## 5. 数据结构

```python
@dataclass(frozen=True)
class MemoryEntry:
    category: str
    content: str
```

MemoryStore 不再接收 source 参数，也不返回稳定 entry ID。

## 6. 迁移

本地项目不保留旧格式兼容解析。代码切换后，真实 workspace 的现有 `MEMORY.md` 在人工备份后原地转换为新格式；只迁移 category/content。

## 7. 变更文件

- `agent/protocols/memory.py`
- `agent/memory/markdown_store.py`
- `agent/memory/extraction.py`
- `agent/tools/memory.py`
- `agent/tools/__init__.py`
- `agent/tools/scoped.py`
- `agent/core/loop.py`
- Memory Store、Tool、AgentLoop 和运行态测试。
- Part 10 活文档、README 和 Prompt。

## 8. 验收标准

1. `MEMORY.md` 不出现 ID、created_at、updated_at 或 source。
2. add/replace/delete 不接受或返回 entry_id。
3. 修改和删除通过分类与原内容精确完成。
4. 自动提取写入同样的纯内容条目。
5. 真实 workspace 内容迁移后仍保留“喜欢吃西瓜”。
6. 无 sidecar 和旧格式兼容代码。
7. Ruff、相关测试和全量 pytest 通过。
