# 自动新建会话默认值设计

> 说明：这是一份历史实验记录。当前代码并不采用“每次启动都新建唯一会话”的行为，而是默认使用当天会话 `chat-YYYYMMDD`，并通过 `/new` 显式新建临时会话。

## 背景

早期 CLI 在用户未传入 `--session` 时，默认使用固定的 `default` 会话。后续为了减少旧历史污染普通问候和短对话，当前实现改为按天生成 `chat-YYYYMMDD`。

对 LLM 聊天来说，尤其在 Prompt 和 endpoint 行为仍在频繁调整时，这种隐式继承历史会带来明显的混淆。

## 当时的实验目标

- 让 `zcagent` 在每次未显式指定 `--session` 时默认创建一个新会话。
- 保留 `--session <id>` 作为恢复既有会话的显式方式。

## 范围边界

本变更只影响 CLI 启动时的 session id 选择逻辑，不改变：

- Session 持久化格式
- 存储路径
- gateway 行为

## 当时的设计方案

- 将 chat 参数解析器中的 `--session` 默认值改为 `None`。
- 当用户未传 `--session` 时，由 CLI 生成新会话 id：
  - 格式：`session-YYYYMMDD-HHMMSS-ffffff`
- 当用户显式传入 `--session` 时，保持原值不变。
- 启动时打印最终采用的 session id，便于用户后续复用。

## 边界约束

- session id 校验仍由 `JsonlSessionStore` 负责。
- 自动生成的 id 只使用字母、数字和短横线，兼容现有校验规则。
- 已存在的 session 文件保持可读取和可恢复。

## 影响文件

- `agent/cli.py`
- `tests/unit_test/cli/test_cli_init.py`
- `README.md`

## 验证结果

- 这条实验方案已经被撤回。
- 当前代码改为默认使用 `chat-YYYYMMDD`，并由 `/new` 生成新会话。
- 对应的当前实现记录在 [CLI 会话命令设计](/C:/Users/84953/Desktop/zhice_agent/docs_design/2026-06-12-cli-session-commands-design.md:1) 中。
