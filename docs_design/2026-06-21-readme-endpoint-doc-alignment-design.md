# README 与 endpoint 示例口径对齐设计

## 背景

当前代码已推进到第五部分，根目录 `README.md` 仍以第二阶段无工具聊天为主线。`config/llm_endpoints.example.json` 也已收敛为 `openai_gpt5` 与 `litellm_claude` 两个示例，需要同步当前态文档。

## 目标

- 将根目录 README 更新为第五部分当前能力说明。
- README 中的 endpoint 示例与 `config/llm_endpoints.example.json` 保持一致。
- 当前总体设计文档中的 LLM 配置示例同步到同一口径。

## 范围边界

- 不修改历史 milestone 文档里的早期示例，避免把历史设计记录改成当前实现说明。
- 不改变 endpoint schema、配置加载逻辑或 Provider 行为。

## 测试方案

- 校验 `config/llm_endpoints.example.json` 是合法 JSON。
- 运行 Markdown/文档相关的轻量 grep 检查，确认当前态文档不再残留旧示例名。
