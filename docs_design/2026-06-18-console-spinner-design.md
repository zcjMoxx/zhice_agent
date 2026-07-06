# ZhiCe-Agent 终端等待 Spinner 设计

- 日期：2026-06-18
- 状态：已实现
- 关联：`docs_design/2026-06-11-console-color-design.md`

## 背景

CLI 对话模式下，`AgentLoop.run_turn()` 是同步阻塞调用。用户发送消息后，终端在 LLM 响应返回前没有任何视觉反馈，容易误以为程序卡死或无响应。

## 目标

- 在等待 LLM 响应期间，终端显示旋转动画和已用时间，让用户知道 agent 正在工作。
- 响应返回后自动清除 spinner 行，不留残留字符。
- 非交互环境（输出重定向、CI）下静默跳过，不污染管道。
- 不改动 AgentLoop、LLMProvider 或工具系统。

## 非目标

- 不做流式 token 输出（后续独立设计）。
- 不在 gateway / HTTP 层加 spinner。
- 不引入新运行时依赖。

## 设计方案

### 实现层级

Spinner 放在 `agent.console` 模块，与已有 `Console` 样式门面同层。CLI 通过 context manager 调用，其它入口（gateway、未来 Web）不受影响。

### `Spinner` 类

```python
class Spinner:
    _FRAMES = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"   # Braille 旋转帧
    _INTERVAL = 0.08              # 每帧间隔 80ms

    def __init__(self, label: str = "thinking"): ...
    def __enter__(self) -> Spinner: ...
    def __exit__(self, *exc) -> None: ...
```

运行机制：

1. `__enter__`：如果 `sys.stderr.isatty()`，启动 daemon 线程循环刷新动画。
2. 线程每 80ms 写入一帧到 stderr：`\r⠹ thinking... 3.2s`。
3. spinner 字符使用 cyan 色（`\033[36m`），复用已有 `_colors_enabled()` 判断。
4. `__exit__`：设置 `threading.Event` 停止线程，join 后用空格覆盖 spinner 行。
5. 非 TTY 时 `__enter__` / `__exit__` 均为空操作。

### 输出目标

写入 `sys.stderr` 而非 `sys.stdout`，原因：

- `AgentLoop.run_turn()` 的返回值通过 `print()` 写入 stdout。
- Spinner 是瞬态 UI，不应出现在管道输出或日志捕获中。
- stderr 和 stdout 分离后，`zcagent > output.txt` 只包含对话内容。

### CLI 调用方式

```python
# agent/cli.py
with Spinner("thinking"):
    result = agent_loop.run_turn(session_id, user_text)
print(result)
```

只在 `_run_chat` 的用户普通输入分支使用。`/model`、`/help` 等斜杠命令不经过 LLM，不需要 spinner。

## 显示效果

```text
> 你好
⠹ thinking... 1.4s
```

响应返回后 spinner 行被清除，紧接着打印 assistant 回复。

## 边界约束

- `Spinner` 只负责终端动画，不感知 LLM 调用细节。
- 线程为 daemon，主线程异常退出时自动终止，不会阻塞进程。
- `_colors_enabled()` 检测结果由 `console.py` 已有逻辑统一管理。

## 变更文件

- `agent/console.py`：新增 `Spinner` 类，新增 `threading`、`time` import。
- `agent/cli.py`：import `Spinner`，在 `run_turn` 调用处包裹 context manager。

## 增强：Ctrl+C 中断与计时保留

### 背景

基础 Spinner 实现后发现两个体验问题：

1. 计时在响应返回后消失（`__exit__` 清行），用户看不到总耗时。
2. 等待期间 Ctrl+C 导致 traceback 崩溃，无法优雅中断。

参考项目（sthg_nanobot_agent）的停止能力在 AgentLoop 消息总线层实现，服务于 Telegram 等多 channel 场景。但 CLI 交互循环在 thinking 期间阻塞（`await turn_done.wait()`），用户无法在 thinking 时打字，CLI 实际中断靠 Ctrl+C。

本阶段沿用 Ctrl+C 方案，保持 threading 同步架构。

### 显示效果

正常完成：

```text
> 你好
⠿ thinking 3.2s
你好！有什么可以帮到你的？
```

Ctrl+C 中断：

```text
> 你好
⠿ thinking 1.4s [interrupted]
```

spinner 行变为静态最终行（`⠿` 全点字符），保留在终端中不清除。

### Spinner 改动

- `__init__`：新增 `_start`、`elapsed`、`interrupted` 属性。
- `__enter__`：在启动线程前记录 `_start = time.monotonic()`。
- `__exit__`：接收 `exc_type` 参数，检测 `KeyboardInterrupt`：
  - 正常退出：`\r⠿ thinking 3.2s\n`
  - 中断退出：`\r⠿ thinking 1.4s [interrupted]\n`（`[interrupted]` 用黄色）
  - 不再清空行。

### CLI 中断处理

```python
try:
    with Spinner("thinking"):
        result = agent_loop.run_turn(session_id, user_text)
    print(result)
except KeyboardInterrupt:
    session_store.append(session_id, [
        Message(role="user", content=user_text),
        Message(role="assistant", content="[interrupted]",
                metadata={"interrupted": True}),
    ])
```

流程：Ctrl+C → `KeyboardInterrupt` 抛出 → `Spinner.__exit__` 打印带 `[interrupted]` 的最终行 → `except` 块保存中断记录到 session → 回到 `> ` 提示符。

### 不做的事

- 不改 AgentLoop 或 LLMProvider（`KeyboardInterrupt` 自然中断阻塞的 HTTP 调用）。
- 不引入 asyncio 或 prompt_toolkit。
- 不做运行中输入控制命令（留待消息总线架构）。

## 验收

- 交互终端中发送消息后能看到 braille 旋转动画和秒数计时。
- 响应返回后 spinner 行变为 `⠿ thinking Xs`，保留在终端，下方紧接 assistant 回复。
- Ctrl+C 中断后显示 `⠿ thinking Xs [interrupted]`，回到 `> ` 提示符。
- 中断后 `/history` 可见 `[interrupted]` 记录。
- `zcagent > output.txt` 不含 spinner 残留。
- `python -m ruff check .` 通过。
- `python -m pytest` 通过。
