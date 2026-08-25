# PromptLoader 单元测试用例

## 测试目标

验证 PromptLoader 只从 prompts 目录读取 UTF-8 Markdown prompt，并拒绝路径穿越。

## 用例覆盖

### 用例 1: 按名称读取 prompt

- 输入：`identity`。
- 预期：读取 `identity.md`。
- 检查点：返回原始文本；中文内容按 UTF-8 正常保留。

### 用例 2: 按文件名读取 prompt

- 输入：`skills_intro.md`。
- 预期：直接读取同名文件。
- 检查点：不会重复追加 `.md`。

### 用例 3: 批量读取 prompt

- 输入：多个 prompt 名称。
- 预期：返回 name 到文本的映射。
- 检查点：全部文件存在时一次成功；任一缺失时抛出清晰错误。

### 用例 4: 缺失文件

- 输入：不存在的 prompt 名称。
- 预期：抛出 `PromptNotFoundError`。
- 检查点：错误信息包含缺失文件名。

### 用例 5: 路径穿越

- 输入：`../secret` 或绝对路径。
- 预期：抛出 `PromptPathError`。
- 检查点：不能读取 prompts 目录之外的文件。

### 用例 6: 可用 prompt 列表

- 输入：扫描 prompts 目录。
- 预期：返回当前可加载的 prompt 名称。
- 检查点：只列出 Markdown prompt。

### 用例 7: 运行时 Prompt 能力契约

- 输入：仓库当前 `identity.md`、`tool_use_policy.md`、`diagnostics.md` 和 `exec.md`。
- 检查：Tool policy 只要求先通过 `discover_tools` 发现并激活最小能力集合，禁止未发现即声称不可用或用近似 Tool 冒充指定能力；诊断 Tool 名称、Trace 字段优先级和包装码限制只存在于 diagnostics Prompt；命令选择、风险确认、shell 与 stdout/stderr 处理只存在于 Exec Prompt。
- 预期：identity 不再包含旧阶段或“纯对话模式”，并明确按本轮 tool schemas 使用工具。
- 检查点：用户明确要求调用已提供工具时应实际调用；系统时间、日志、进程等真实状态必须验证，不能从 `session_id` 或历史回答猜测，也不能未经调用就声称工具不可用。
