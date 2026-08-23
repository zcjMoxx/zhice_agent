# 工作流调度器锁进程身份修复

> 日期：2026-08-21
>
> 状态：已实施
>
> 前序：`docs_design/2026-08-21-workflow-scheduler-windows-lock-fix.md`

## 背景

前序修复将 Windows 进程探测从 `os.kill(pid, 0)` 改为只读 Win32 API，但锁文件只保存 PID。残留锁中的 PID `22156` 后来被 Windows 分配给无关的 `LenovoPCMKeyService`，单纯的存活判断将其误认为仍在运行的工作流调度器，Gateway 因 `WORKFLOW_SCHEDULER_ALREADY_RUNNING` 再次启动失败。

## 目标

- 新锁同时保存 PID 与 Windows 进程创建时间，使用稳定进程身份判断锁归属。
- 兼容只有 PID 的旧锁，并识别“锁文件早于当前 PID 对应进程”的 PID 复用场景。
- 活跃 Gateway 的锁继续阻止第二实例；不结束或干扰复用该 PID 的无关进程。
- 不新增依赖，不改变单 workspace 单调度器边界。

## 范围边界

- 仅扩展本地调度器锁载荷和 Windows 锁归属判断。
- 不修改 APScheduler、WorkflowStore、Gateway 生命周期或运行态配置。
- POSIX 继续沿用现有 PID 存活判断；本次身份增强针对已复现的 Windows PID 复用。

## 模块设计与数据流

新建锁时，通过 `GetProcessTimes` 读取当前进程创建时间的 Windows FILETIME tick，并写入 `process_created_at`。读取新锁时，当前 PID 的创建时间必须与锁内值一致才视为同一进程。

读取旧锁时没有创建时间可比对，因此使用锁文件修改时间作为保守兼容证据：若当前 PID 对应进程的创建时间晚于锁文件时间，则该进程不可能创建过这把锁，判定为 PID 已复用；否则仍按活跃锁处理。无法查询受保护进程的创建时间时保守保留锁。

## 变更文件

- `agent/workflows/scheduler.py`：保存并校验 Windows 进程创建时间。
- `tests/unit_test/workflows/test_workflow_scheduler.py`：覆盖 PID 复用和新锁身份载荷。
- `tests/unit_test/workflows/test_case.md`：记录新增回归场景。
- 前序日期记录增加后续方案指引。

## 测试方案

- 构造早于当前进程创建时间的旧格式锁，使用当前存活 PID 模拟 PID 复用，验证自动恢复。
- 验证新锁包含当前 PID 和创建时间。
- 保留不存在 PID 恢复、第二实例拒绝和调度重建测试。
- 用实际残留锁 `PID 22156` 做只读归属判断，并运行工作流聚焦测试、Ruff 和全量 pytest。

## 验收标准

- 实际旧锁不再把 `LenovoPCMKeyService` 识别成 WorkflowScheduler。
- 新锁可区分 PID 相同但创建时间不同的进程。
- 活跃调度器仍保持单实例约束。
- 全量静态检查和测试通过。
