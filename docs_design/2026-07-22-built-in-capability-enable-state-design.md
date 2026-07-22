# ZhiCe-Agent 内置能力与可选扩展启用状态设计

> 日期：2026-07-22
>
> 状态：已实现并进入当前代码基线
>
> 承接：`docs_design/2026-07-22-optional-capability-warning-surface-design.md`

## 1. 背景

可选能力告警出口统一后，进一步检查发现“文件不存在是否属于异常”仍缺少统一判断：

- Subagent 和 MCP 没有配置时按 `disabled` 处理，不报警；显式配置后依赖缺失才报警。
- Skill source 完全没有 `skill_sources.yml` 时却记录 `SKILL_SOURCE_CONFIG_MISSING`，把未安装可选扩展当成异常。
- Memory extraction 没有用户启用配置，Gateway 始终装配该后台能力；它实际属于系统内置能力，但错误码仍使用 `NOT_CONFIGURED`，容易误解为用户漏配可选插件。

## 2. 目标

1. 建立统一判断：可选扩展未配置等于 `disabled`，不记录 WARNING。
2. 显式启用或显式声明的可选能力依赖缺失时，记录 WARNING 并局部禁用。
3. Memory extraction 明确为系统内置后台能力；内置 Prompt 缺失属于运行模板不完整，记录 WARNING 并局部降级。
4. 显式 Hook 安全策略和核心聊天依赖继续维持启动阻断，不纳入普通可选能力规则。

## 3. 分类规则

| 分类 | 未配置 | 已启用但依赖缺失 |
|---|---|---|
| 可选扩展：Skill source、Subagent、MCP | `disabled`，不报警 | `unavailable/degraded`，WARNING |
| 内置后台能力：Memory extraction | 默认启用并检查内置资源 | Prompt 缺失或非法时 WARNING，禁用后台提取 |
| 显式安全策略：Hook | 未配置时 disabled | 配置或脚本非法时阻断启动 |
| 核心聊天依赖：基础 Prompt、LLM、workspace | 不适用 | 缺失时阻断启动 |

## 4. 模块设计

### 4.1 Skill source

- `config/skill_sources.yml` 不存在：返回空 SkillLoader，不打印、不记 WARNING。
- 配置文件存在但非法：`SKILL_SOURCE_CONFIG_INVALID`。
- 配置要求同步但同步失败：`SKILL_SYNC_FAILED`，已有可用 Skill roots 仍可加载。
- CLI 和 Gateway 使用同一缺失语义，不再由 CLI 单独打印初始化提醒。

### 4.2 Memory extraction

当前 Gateway 始终启用系统内置 Memory extraction checker。`prompts/memory_extraction.md` 是随 `zcagent init` 安装的内置运行 Prompt，不是用户可选配置。

错误码细分：

- 文件不存在：`MEMORY_EXTRACTION_PROMPT_NOT_FOUND`。
- 文件为空、不可读或编码非法：`MEMORY_EXTRACTION_PROMPT_INVALID`。

两者都只禁用后台自动提取；显式 Memory read/write 不受影响，普通聊天继续。

## 5. 数据流

```text
optional extension config absent
  -> disabled
  -> no warning

optional extension explicitly configured
  -> validate dependencies
  -> missing/invalid => structured WARNING + local disable

built-in Memory extraction
  -> validate bundled runtime prompt
  -> missing/invalid => structured WARNING + extraction disabled
```

## 6. 变更文件

- `agent/cli.py`
- `agent/app/runtime.py`
- `agent/memory/startup.py`
- `agent/memory/extraction.py`
- `tests/unit_test/cli/*`
- `tests/unit_test/app/*`
- `tests/unit_test/memory/*`
- `README.md`
- `docs_design/README.md`
- `docs_design/zhice-agent-overall-design.md`
- `docs_design/zhice-agent-part5-skill-loader-design.md`
- `docs_design/zhice-agent-part10-memory-design.md`
- `docs_design/zhice-agent-part13-subagent-design.md`

## 7. 测试方案

- 缺少 `skill_sources.yml` 时 CLI/Gateway 均不产生 WARNING。
- 存在非法 `skill_sources.yml` 时 Gateway 只记录一次结构化 WARNING。
- Memory extraction Prompt 缺失与空文件分别返回稳定、不同的错误码。
- 使用期 Memory extraction 缺 Prompt 与 startup checker 使用相同缺失错误码。
- 运行 Ruff、全量 pytest、前端 `node --check` 和 `git diff --check`。

## 8. 验收标准

1. 未安装 Skill source 不再显示任何错误或警告。
2. 显式配置 Skill source 后配置非法或同步失败仍能准确告警。
3. Memory extraction 明确呈现为内置能力资源缺失，而不是用户“未配置”。
4. 所有降级均不阻断普通聊天，核心与安全阻断边界保持不变。

## 9. 实现结果

- CLI 和 Gateway 缺少 `skill_sources.yml` 时均静默使用空 SkillLoader，不再打印或记录 WARNING。
- Skill source 文件存在但非法时仍记录单个 `SKILL_SOURCE_CONFIG_INVALID`；启动同步失败时记录 `SKILL_SYNC_FAILED`。
- Memory extraction 继续作为系统内置后台能力默认检查，缺 Prompt 返回 `MEMORY_EXTRACTION_PROMPT_NOT_FOUND`，空白、不可读或编码非法返回 `MEMORY_EXTRACTION_PROMPT_INVALID`。
- Memory extraction 使用期缺 Prompt 与 startup checker 使用相同缺失错误码；普通聊天和显式 Memory read/write 保持可用。
- Owner workspace 实测只保留已启用 Subagent 缺 Prompt 的 WARNING，未配置 Skill source 不再输出。
- 验证结果：`python -m ruff check .` 通过；`python -m pytest --basetemp .tmp/pytest_capability_enable_state_final` 为 `583 passed, 1 skipped`；两个前端 JavaScript 文件通过 `node --check`；`git diff --check` 通过。
