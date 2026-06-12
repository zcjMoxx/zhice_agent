# PromptLoader 单元测试用例

## 测试目标

验证 PromptLoader 只从 prompts 目录读取 UTF-8 Markdown prompt，并拒绝路径穿越。

## 用例覆盖

### Case 1: 按名称读取 prompt

- 输入：`identity`。
- 预期：读取 `identity.md`。
- 检查点：返回原始文本；中文内容按 UTF-8 正常保留。

### Case 2: 按文件名读取 prompt

- 输入：`skills_intro.md`。
- 预期：直接读取同名文件。
- 检查点：不会重复追加 `.md`。

### Case 3: 批量读取 prompt

- 输入：多个 prompt 名称。
- 预期：返回 name 到文本的映射。
- 检查点：全部文件存在时一次成功；任一缺失时抛出清晰错误。

### Case 4: 缺失文件

- 输入：不存在的 prompt 名称。
- 预期：抛出 `PromptNotFoundError`。
- 检查点：错误信息包含缺失文件名。

### Case 5: 路径穿越

- 输入：`../secret` 或绝对路径。
- 预期：抛出 `PromptPathError`。
- 检查点：不能读取 prompts 目录之外的文件。

### Case 6: 可用 prompt 列表

- 输入：扫描 prompts 目录。
- 预期：返回当前可加载的 prompt 名称。
- 检查点：只列出 Markdown prompt。
