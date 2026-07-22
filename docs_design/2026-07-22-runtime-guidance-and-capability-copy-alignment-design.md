# ZhiCe-Agent 运行引导与能力文案统一设计

> 日期：2026-07-22
>
> 状态：已实现并进入当前代码基线
>
> 承接：`2026-07-22-built-in-capability-enable-state-design.md`

## 1. 背景

当前 `zcagent init` 完成提示仍要求用户设置 “endpoint, model, api_key, and Skill sources”，把可选 Skill source 与聊天必需的 LLM 配置并列。与此同时，README 仍称 `context_window` 缺失时不会设置默认值，但真实加载代码已经使用 `131072` 默认值。用户可见引导与当前运行语义不一致。

## 2. 目标

1. 初始化完成提示明确区分必需 LLM 配置、已有默认值的 token 预算和可选能力。
2. Skill source、MCP、Subagent 未配置时使用正常 disabled 语义，不提示为启动错误或使用前必配项。
3. 显式启用的可选能力配置非法时继续 WARNING；内置运行 Prompt 缺失时继续局部降级。
4. 当前活文档、CLI 测试和真实代码保持一致。

## 3. 文案规则

### 3.1 核心必需

聊天前必须存在至少一个 enabled LLM endpoint，并配置与真实供应商一致的 protocol/base URL 或 provider、model 和 api_key。

### 3.2 有默认值但需校准

`context_window` 缺失时默认 `131072`，`max_tokens` 缺失时沿用兼容默认值；`zcagent init` 新模板写入当前推荐值。提示用户仅在所选模型限制不同或需要调整输出预算时修改，不能描述为每次初始化后必须手工填写。

### 3.3 可选能力

Skill source、MCP、Subagent 和 Hook 未配置时可以保持 disabled。只有用户需要启用相应能力时才配置；显式配置后非法或依赖缺失再产生 warning 或安全阻断。

## 4. 变更范围

- `agent/cli.py`：更新 `zcagent init` 完成提示。
- CLI Skill startup 文案统一为 missing=`disabled` 静默、invalid=`unavailable` 单次警告、同步失败=`degraded` 单次警告。
- `tests/unit_test/cli/test_cli_init.py` 与 `tests/unit_test/cli/test_case.md`：覆盖必需、默认和可选三类说明。
- `README.md`、`docs_design/README.md`、`docs_design/zhice-agent-part2-no-tool-chat-design.md`、`docs_design/zhice-agent-overall-design.md`：同步当前活口径。
- 不回写已经完成的旧日期设计正文；旧记录继续保存当时决策。

## 5. 测试

- `zcagent init` 输出包含真实 LLM endpoint 校验提示。
- 输出明确 `context_window/max_tokens` 已有默认值。
- 输出明确 Skill source 是可选能力，不再要求“使用前必须设置”。
- 全量 Ruff、pytest、前端 JavaScript 语法检查和 diff check 通过。

## 6. 验收标准

1. 新用户不会把 Skill source 误认为聊天启动前置条件。
2. 文档不再声称 `context_window` 缺失时无默认值。
3. 未配置可选能力与显式配置失败的文案语义保持区分。

## 7. 验证结果

- `python -m ruff check .` 通过。
- `python -m pytest --basetemp .tmp/pytest_runtime_guidance_full`：`608 passed, 1 skipped`。
- `node --check web/static/app.js` 与 `node --check web/static/runtime-event-state.js` 通过。
- `git diff --check` 通过；仅保留 Windows 工作区既有的 LF/CRLF 提示。
