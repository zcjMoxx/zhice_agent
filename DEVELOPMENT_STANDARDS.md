# Zhice-Agent 开发规范

> 本文档定义 Zhice-Agent 的开发约束。根目录 `AGENTS.md` 是速查版，本文是展开版。

---

## 1. 总原则

Zhice-Agent 优先保持轻量、清晰、可测试、可逐步演进。

开发时遵守以下原则：

1. AgentLoop 保持通用，不承载具体业务流程。
2. 业务能力通过 Tool 或 Skill 扩展。
3. 关键边界先定义协议接口，再写具体实现。
4. Prompt、配置、会话数据都放在清晰的位置，避免散落在代码里。
5. 新增行为必须有对应测试或明确的验证方式。

---

## 2. 模块边界

推荐依赖方向：

```text
cli/app -> agent core -> protocols
tools   -> base types / protocols
skills  -> no agent imports
```

要求：

- `agent/protocols/` 只定义接口和数据结构，不写业务逻辑。
- `agent/loop.py` 只通过协议端口调用 LLM、Tool、Skill、Session。
- `agent/tools/` 中的 LLM 可调用工具不要互相 import。
- 共享逻辑放在明确的基础模块里，不藏在某个具体工具中。
- `skills/*/scripts/` 不 import `agent.*`。

---

## 3. AgentLoop 规范

AgentLoop 负责：

- 接收用户消息。
- 加载历史会话。
- 构建上下文。
- 调用 LLM。
- 调度工具。
- 回填工具结果。
- 保存本轮消息。

AgentLoop 不负责：

- 判断某个业务应该怎么处理。
- 直接调用外部业务 API。
- 处理渠道格式。
- 处理 UI 渲染。
- 写死某个 Skill 的参数逻辑。

如果出现 `if user_asks_xxx` 这类业务判断，优先改成 Tool 或 Skill。

---

## 4. Prompt 规范

所有会进入 LLM messages 的长文本指令必须放在 `prompts/*.md`。

包括：

- system prompt。
- 工具使用规则。
- Skill 使用规则。
- 会话摘要规则。
- 后续子任务或反思类指令。

允许留在 Python 里的内容：

- Tool 名称。
- 很短的 Tool description。
- 1 到 2 句兜底错误文案。

新增 prompt 文件时：

- 文件名使用语义名，不带版本号。
- 变量使用 `{name}` 形式。
- 由 `PromptLoader` 统一读取。

---

## 5. 配置与路径

配置集中放在 `config/`。

运行路径从 `ZHICE_AGENT_WORKSPACE` 派生：

```text
config/
prompts/
contexts/
contexts/sessions/
skills/
logs/
```

要求：

- Secret 不提交到仓库。
- 仓库只提交 `.env.example`。
- 所有路径在启动时解析成绝对路径。
- 文件工具和 exec 默认限制在 workspace 内。

---

## 6. Tool 规范

Tool 必须提供：

- `name`
- `description`
- `parameters`
- `execute(args)`

Tool 返回统一 `ToolResult`：

```python
ToolResult(
    output="...",
    is_error=False,
    metadata={},
)
```

要求：

- 参数使用 JSON Schema 描述。
- 异常转成结构化错误结果。
- 不把 Python traceback 直接暴露成最终回答。
- 输出过长时截断，并在 metadata 中记录截断信息。

`exec` 工具额外要求：

- 限制工作目录在 workspace。
- 设置超时。
- 截断 stdout/stderr。
- 拦截明显危险命令。
- 破坏性操作必须来自用户明确请求。

---

## 7. Skill 规范

Skill 最小结构：

```text
skills/{skill_name}/
  SKILL.md
  scripts/{entry}.py
```

`SKILL.md` frontmatter：

```yaml
---
name: example-skill
description: 简短说明，供 LLM 判断是否使用
category: default
readonly: false
---
```

`SKILL.md` 正文必须包含：

- 使用场景。
- 参数表。
- 完整可执行示例。
- 返回 JSON 格式。
- 错误码表。
- 每个错误码的重试策略。
- 边界情况和不适用场景。

脚本调用方式：

```bash
python skills/{skill_name}/scripts/{entry}.py --params '{JSON}'
```

脚本 stdout 最后一行必须输出：

```json
{
  "status": "success",
  "code": "OK",
  "data": {},
  "message": "",
  "error_stack": ""
}
```

要求：

- `status` 只能是 `success` 或 `error`。
- 成功 code 使用 `OK`、`OK_PARTIAL`、`OK_EMPTY`。
- 参数错误使用 `INVALID_PARAM` 或 `MISSING_PARAM`。
- 未分类异常使用 `INTERNAL_ERROR`。
- `except Exception` 时填 `traceback.format_exc()[:1500]`。
- 相同参数失败后不要无限重试。
- Skill 脚本不 import `agent.*`，Skill 之间不互相 import。

---

## 8. Session 规范

Session 是上下文的一部分，不能只当日志。

第一阶段使用 JSONL：

```text
contexts/sessions/{session_id}.jsonl
```

要求：

- 每条消息一行 JSON。
- 保留 `role`、`content`、`timestamp`、`metadata`。
- `session_id` 必须做路径安全校验。
- 读取时兼容未知字段，方便后续演进。
- 写入时保持追加语义，避免覆盖历史。

---

## 9. 测试规范

单元测试：

- 覆盖正常路径、异常路径、边界条件。
- 可以 mock 外部 LLM、HTTP、文件系统边界。
- 不 mock 被测模块自身行为。

AgentLoop 测试：

- 优先使用 Fake LLM。
- 验证工具调用、工具结果回填、最终回答保存。
- 验证最大迭代次数。

真实 LLM 测试：

- 只做 smoke。
- 通过环境变量显式开启。
- 默认不在普通测试中运行。

提交前建议运行：

```bash
python -m ruff check .
python -m pytest
```

---

## 10. 设计先行

以下情况先写设计文档：

- 涉及 3 个及以上文件修改。
- 新增核心模块。
- 新增或修改协议接口。
- 改动 AgentLoop、Tool、Skill、Session、配置加载。
- 引入新的运行时依赖或外部服务。

设计文档放在 `docs_design/`，文件名使用语义名，例如：

```text
docs_design/zhice-agent-tool-system-design.md
docs_design/zhice-agent-skill-loader-design.md
```

不要用日期作为总体设计文件名前缀。

---

## 11. 第一阶段暂不纳入

第一阶段暂不纳入：

- 多用户体系。
- 多渠道接入。
- 完整 Web 前端。
- Skill 市场。
- 审批和通知。
- 自动演化。
- 图谱化长期记忆。
- 复杂部署编排。

这些能力后续按模块单独设计和实现。
