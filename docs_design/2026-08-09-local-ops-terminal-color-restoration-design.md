# ZhiCe-Agent 本地终端与 Ops 日志配色恢复设计

> 日期：2026-08-09
>
> 状态：方案已确认，随本文落地
>
> 归属：Part 18 多运行形态 restricted Ops 修正

## 1. 背景

本地 Ops supervisor 为同时监控 Gateway 和向原 PowerShell tee 输出，将 Gateway child 的 stdout/stderr 接入 PIPE。Gateway 的日志颜色判断依赖 `isatty()`，PIPE 会返回 false，因此 ANSI 配色在 child 写出前已经被关闭，supervisor 无法在转发时恢复。

Ops 浏览器目前只展示移除 ANSI 后的纯文本，并将所有日志放在同一种颜色的 `<pre>` 中。虽然避免了直接显示控制字符，但时间、级别、动作和字段没有视觉区分。

## 2. 目标

1. 普通终端启动且 supervisor 输出目标是真实 TTY 时，Gateway child 恢复原有 ANSI 配色。
2. `NO_COLOR` 和非 TTY 重定向仍然关闭颜色，不向文件或管道写入多余控制字符。
3. Gateway 启动摘要继续使用原版 Console 配色：标题加粗，地址和路径为 ANSI `36` 青色；新增 Ops 启动行复用同一规则。
4. Ops 浏览器继续只接收无 ANSI 的安全文本，但按原终端日志语义渲染颜色。
5. 浏览器不得通过未转义 `innerHTML` 渲染日志。

## 3. 范围边界

- 不改变 Gateway 日志内容、结构化 JSONL、日志等级或脱敏。
- 不把 ANSI 原文返回给 Ops API。
- 不引入前端依赖或完整终端模拟器。
- 不改变 Docker/服务器 ttyd 的既有运行边界。

## 4. 模块设计

### 4.1 原终端

supervisor 仅在自身 stdout 是 TTY 且没有 `NO_COLOR` 时，为受控 Gateway child 注入内部环境变量 `ZHICE_FORCE_TERMINAL_COLOR=1`。Gateway 日志颜色检测在 `NO_COLOR` 之后识别该变量，即使 child 输出目标是 PIPE 也生成 ANSI；同一显式判定传给 Uvicorn，恢复启动与 HTTP 日志配色；supervisor 将原始文本写回真实终端。

该变量只由拥有 child 生命周期的 supervisor 使用，不作为通用用户配置。若 supervisor 自身被重定向，则不注入，保持无颜色文本。

### 4.2 Ops 浏览器

supervisor 继续在写入有界浏览器缓冲前移除 ANSI。页面使用 DOM `textContent` 和 `createElement` 将人类可读日志拆为安全 span，并复刻原终端的语义映射：

- timestamp：绿色；
- Server INFO/WARNING/ERROR/CRITICAL：绿/黄/红/亮红；
- Agent WARNING/ERROR：与原终端一致整行亮红/粗红；
- Agent/Web/Gateway/WS/Tool：青/洋红/蓝/绿/黄；
- fields/message：正文灰白色。

无法识别的行按普通文本显示，不能因格式变化丢失日志。

## 5. 数据流

```text
real PowerShell TTY
  -> LocalOpsSupervisor detects TTY
  -> child env ZHICE_FORCE_TERMINAL_COLOR=1
  -> Gateway emits ANSI to PIPE
  -> supervisor writes raw text to PowerShell
  -> supervisor strips ANSI for bounded browser buffer
  -> Ops page safely builds colored DOM spans
```

## 6. 变更文件

- `agent/app/logging.py`
- `agent/console.py`
- `agent/operations/local_supervisor.py`
- `tests/unit_test/app/test_logging.py`
- `tests/unit_test/operations/test_local_supervisor.py`
- `tests/unit_test/app/test_case.md`
- `tests/unit_test/operations/test_case.md`
- `README.md`
- `docs_design/zhice-agent-part18-skill-runtime-and-server-ops-design.md`
- `docs_design/README.md`

## 7. 测试方案

- 内部强制颜色变量在 PIPE 场景启用 formatter color。
- Uvicorn 复用相同的显式颜色 override，不因 child PIPE 单独丢色。
- `NO_COLOR` 的优先级高于内部强制颜色。
- supervisor 只在 TTY 场景向 child 注入内部变量。
- 原终端 tee 保留 ANSI，浏览器缓冲移除 ANSI。
- Ops 页面包含语义颜色 class，使用安全 DOM API，且保持 follow/scroll 行为。
- Ruff、全量 pytest、前端 lint/typecheck/test/build。

## 8. 验收标准

1. 本地 PowerShell 中 Gateway 日志恢复原配色。
2. Ops 浏览器日志存在清晰的时间、级别、动作和字段颜色差异。
3. 浏览器页面不出现 ANSI 字符或未转义日志 HTML。
4. 输出重定向和 `NO_COLOR` 仍得到纯文本。
