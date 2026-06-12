# ZhiCe-Agent 终端配色设计

## 背景

Windows 的 `cmd.exe`、PowerShell 以及不同终端对 ANSI/VT 转义序列的支持并不完全一致。如果项目直接输出原始 ANSI 颜色码，某些环境中可能会看到 `[31m` 这类控制字符，而不是彩色文本。

## 目标

- 让 CLI 中的错误、警告、成功提示、命令和路径具备明确的视觉区分。
- 在终端不支持颜色时，避免泄漏原始 ANSI 控制码。
- 提供一个轻量且可复用的格式化层，供 CLI 和本地运行时输出复用。

## 范围边界

本设计只覆盖本地终端文本格式化，不引入：

- 日志框架
- 日志文件管理
- 结构化事件输出
- 运行时可观测性体系

## 设计方案

- 新增 `agent.console.Console` 作为轻量样式门面。
- 统一通过 `agent.console.console` 的语义化方法使用样式，而不是直接暴露颜色名函数。
- 对外提供以下语义方法：
  - `error()`：错误信息
  - `warning()`：警告和引导
  - `success()`：成功结果
  - `command()`：命令行命令
  - `path()`：文件系统路径
- 不保留模块级别的 `red()`、`yellow()`、`cyan()`、`green()`、`bold()` 之类包装函数。
- Windows 环境优先调用 `colorama.just_fix_windows_console()`。
- 如果 `colorama` 不可用，则尝试启用 Windows Virtual Terminal 模式。
- 如果输出不是 TTY，或设置了 `NO_COLOR`，则返回纯文本。

## 边界约束

- `Console` 只负责格式化字符串，不直接打印。
- Secret、endpoint 配置等逻辑仍由 `agent.config` 和 LLM Provider 相关代码负责。
- 后续文件日志如果存在，也只应把它用于面向人的终端输出，而不是机器可读日志。

## 验收与验证

- 在 `cmd.exe` 中不应看到原始 ANSI 转义字符。
- 在支持颜色的终端中，错误、警告、成功、命令、路径应有明显区分。
- `python -m ruff check .` 通过。
- `python -m pytest` 通过。
