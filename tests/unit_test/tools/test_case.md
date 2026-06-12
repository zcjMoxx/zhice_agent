# Tools 单元测试用例

## 测试目标

验证第三部分只读工具系统的协议、注册表、workspace guard、结构化错误和输出截断能力，为后续 exec 与 Skill 工具复用同一入口打基础。

## 用例覆盖

### Case 1: ToolRegistry 定义生成

- 输入：注册多个工具。
- 预期：生成 OpenAI-compatible tool definitions。
- 检查点：definition 使用副本，外部修改不会污染注册表内部状态。

### Case 2: ToolRegistry 调度错误

- 输入：重复工具名、非法工具名、未知工具、非 object 参数。
- 预期：重复和非法名称初始化失败；未知工具和坏参数返回结构化 `ToolResult`。
- 检查点：错误码分别为 `UNKNOWN_TOOL`、`INVALID_PARAM` 等。

### Case 3: list_dir

- 输入：workspace 内目录。
- 预期：列出直接子项，目录优先，名称稳定排序。
- 检查点：默认隐藏 dot 文件；`include_hidden=True` 时可显示；文件路径返回 `NOT_DIRECTORY`。

### Case 4: read_file

- 输入：UTF-8 文本文件、起始行、最大行数和最大字符数。
- 预期：带行号返回文本片段。
- 检查点：目录返回 `NOT_FILE`；非 UTF-8 返回 `DECODE_ERROR`；超长输出记录截断 metadata。

### Case 5: grep

- 输入：正则 pattern、搜索路径、大小写选项。
- 预期：返回 `path:line: text` 格式的匹配行。
- 检查点：非法正则返回 `INVALID_PATTERN`；默认跳过隐藏路径和缓存目录；命中上限时记录截断。

### Case 6: workspace 越界

- 输入：`..`、workspace 外绝对路径、指向 workspace 外的符号链接。
- 预期：所有只读工具都拒绝访问。
- 检查点：错误码为 `PATH_OUTSIDE_WORKSPACE`，不读取 workspace 外内容。

### Case 7: 工具内部异常

- 输入：工具实现内部抛出未预期异常。
- 预期：BaseTool 或 ToolRegistry 把异常转换为 `ToolResult(is_error=True)`。
- 检查点：不把 Python traceback 交给 AgentLoop。
