# PromptLoader 单元测试用例

## 测试目标

验证 PromptLoader 能以 UTF-8 读取 Markdown prompt，并拒绝路径穿越。

## 测试用例

### Case 1: 按名称读取 prompt

- **输入**: `identity`
- **预期**: 读取 `identity.md`
- **检查点**:
  - 中文内容不乱码
  - 返回原始文本

### Case 2: 按文件名读取 prompt

- **输入**: `skills_intro.md`
- **预期**: 直接读取同名文件
- **检查点**:
  - 不重复追加 `.md`

### Case 3: 缺失文件

- **输入**: 不存在的 prompt 名称
- **预期**: 抛出 `PromptNotFoundError`

### Case 4: 路径穿越

- **输入**: `../secret`
- **预期**: 抛出 `PromptPathError`
