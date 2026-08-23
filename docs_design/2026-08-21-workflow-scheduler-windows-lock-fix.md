# 工作流调度器 Windows 锁恢复修复

> 说明：本方案落地后发现仅判断 PID 存活无法识别 Windows PID 复用；当前实现追加进程创建时间身份校验，见 `2026-08-21-workflow-scheduler-lock-identity-fix.md`。

> 日期：2026-08-21
>
> 状态：已实施
>
> 关联：`docs_design/zhice-agent-part20-visual-workflow-scheduler-design.md`

## 背景

Gateway 启动工作流调度器时会读取 `${ZHICE_AGENT_WORKSPACE}/state/workflow-scheduler.lock`，并检查锁内 PID 是否仍存活。当前实现统一使用 `os.kill(pid, 0)`；该调用在 Windows 不是无副作用的进程探测 API，旧 PID 会触发 `WinError 87`，并可能由 CPython 表现为未被 `except OSError` 捕获的 `SystemError`，导致过期锁无法清理、Gateway 启动失败。

## 目标

- Windows 上使用只读 Win32 API 判断 PID 是否仍在运行。
- 保留 POSIX 上 `os.kill(pid, 0)` 的现有行为。
- 过期锁自动删除并重新创建；当前进程或活跃进程持有的锁继续拒绝第二个调度器。
- 不新增第三方依赖，不改变工作流、APScheduler 或 SQLite 的职责边界。

## 范围边界

- 仅修改工作流调度器的进程存活判断。
- 不删除运行态锁文件，不修改 Gateway 启停协议，不引入跨进程分布式锁。
- 本次修复不处理 PID 创建时间匹配；锁的既有单进程语义保持不变。

## 模块设计与数据流

Windows 分支通过 `OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION)` 获取进程句柄，再通过 `GetExitCodeProcess` 判断退出码是否为 `STILL_ACTIVE`，最后始终调用 `CloseHandle`。句柄无法打开或退出码无法读取时按进程不存在处理，使过期锁可恢复。POSIX 分支继续使用 signal 0 探测，并将参数或系统错误视为不存在。

启动数据流保持为：读取锁 PID → 只读判断进程状态 → 活跃则拒绝启动，失效则删除旧锁 → 以 `O_EXCL` 创建当前进程锁 → 启动 APScheduler。

## 变更文件

- `agent/workflows/scheduler.py`：增加 Windows 只读 PID 探测。
- `tests/unit_test/workflows/test_workflow_scheduler.py`：覆盖 Windows 过期 PID 锁恢复。
- `tests/unit_test/workflows/test_case.md`：记录新增测试目标。

## 测试方案

- 在 Windows 创建包含不存在 PID 的锁文件，启动调度器并验证锁被当前 PID 替换。
- 保留现有同 workspace 第二实例拒绝测试，防止削弱单实例约束。
- 运行工作流调度器聚焦测试和 Ruff 检查。

## 验收标准

- 原错误中的残留 PID 不再触发 `SystemError` 或 `WinError 87`。
- 过期锁不阻塞 Gateway 启动。
- 活跃锁仍返回 `WORKFLOW_SCHEDULER_ALREADY_RUNNING`。
- 无新增依赖，聚焦测试与静态检查通过。
