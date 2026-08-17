# Tools 单元测试用例

## 测试目标

验证第三部分只读工具系统和第四部分安全 `exec` 工具的协议、注册表、workspace guard、结构化错误、命令拦截、超时和输出截断能力，为后续 Skill 工具复用同一入口打基础。Part 12 还验证 pre Hook 修改参数后重新经过完整 JSON Schema 校验器，支持本地 `$ref/$defs`，不能绕过 required/type/additionalProperties 等公开约束；外部、无效或无法解析引用 fail closed。

## 用例覆盖

### Case 0: Turn-scoped Tool discovery

- 首次 schema 只暴露 `discover_tools`，不会把全部业务 Tool 与 system prompt 一起注入。
- `discover_tools` 只从已做 actor/Profile 过滤的 Provider catalog 中匹配并激活有界候选。
- 下一次 definitions 只增加已激活 schema；未激活 Tool dispatch 返回 `TOOL_NOT_ACTIVATED`。
- 多次发现可累积激活，contextual dispatch 仍保留可信 actor/session/turn 上下文。
- 初始化或动态发现后若已激活当前全部有效 Tool，则隐藏无意义的 `discover_tools`，只保留可直接调用的业务或错误 facade。

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

### Case 8: shell_policy 命令策略

- 输入：普通单命令、复杂 shell 语法、workspace-bounded 危险命令、网络/安装命令、环境导出命令。
- 预期：普通单命令允许；已知 network/install 和 cwd-bounded destructive 命令进入高风险确认分类；其它高风险命令硬拒绝。
- 检查点：`tool.exec.dangerous` 只允许进入确认；env dump、复杂 shell、绝对路径或无法证明在 workspace 内的破坏命令继续拒绝。

### Case 9: exec 工具执行

- 输入：workspace 内安全命令、子目录 cwd、非 0 exit code、超时命令、长输出和 secret-like 输出。
- 预期：成功命令返回 stdout/stderr/exit_code；失败、超时、cwd 越界和策略拦截都返回 `ToolResult(is_error=True)`。
- 检查点：命令只在 workspace 内执行；输出会截断；疑似 secret 会脱敏；危险命令不会真实执行。

### Case 10: Memory 工具

- 输入：绑定 actor MemoryContext 的 `memory_read(mode=list/search)` 与用户对话授权后的 `memory_write`。
- 预期：列表按固定类别整理并返回分页数量，搜索要求具体 query；读取只支持 `list/search`，写入支持 add/replace/delete。
- 检查点：敏感内容拒绝时不回显原文；ToolResult metadata 只包含 id/category/mode/count 等安全字段。

### Case 11: 正式 `run_skill` 上下文工具

- 输入：无上下文直接调用、可信 actor/turn 上下文调用、额外参数、指令型/不可执行 Skill、未授权 source、Subagent Profile 禁止、取消、timeout 和 Executor 异常。
- 预期：只有 contextual dispatch 可执行；通过 `SkillExecutor` 返回结构化结果，并产生关联外层 Tool Event 的 `skill.*`；所有已开始的异常路径都以 `skill.failed` 收敛。
- 检查点：参数只含 `skill` 与 `params`，上下文、取消令牌和 RuntimeEvent publisher 经 Registry/Scoped/Discovery/Filtered/Augmented 链保持；运行活动的参数与结果预览不记录 `params` 值或 Skill `data`。

### Case 12: `load_skills` 冗余来源归一化

- 输入：同时传 `name=official/review` 与相同 `source=official`，以及正常的完整名称、单独 source 和歧义别名。
- 预期：相同来源是等价冗余并正常加载；冲突来源仍返回结构化名称错误，歧义别名继续返回候选列表。
- 检查点：只兼容完全相同的 qualifier，不放宽 Skill 名称或 source 权限校验。
