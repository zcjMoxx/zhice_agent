# 工作流调度器跨容器锁与部署回滚恢复设计

## 背景

云端核心验收失败触发容器回滚时，新容器被强制删除并遗留 `workflow-scheduler.lock`。新旧容器都以容器内 PID 1 运行，旧 Linux 实现只检查锁文件 PID 是否存活，因此把回滚后的旧容器自身误判为原锁持有者，造成主容器反复退出与公网 502。

## 目标

- Linux 调度器锁由内核持有，进程退出或容器被强制删除后自动释放。
- 同一 workspace 仍只允许一个工作流调度器运行。
- 部署回滚兼容旧镜像的 PID 文件实现，在启动 previous 容器前清理已停止新容器留下的固定锁文件。
- Windows 继续使用包含进程创建时间的现有 PID 身份校验。

## 范围边界

不改变工作流定义、调度数据、执行语义或部署验收边界；只修复调度器互斥锁和回滚恢复顺序。只删除 `zhice-state` 卷中的固定 `workflow-scheduler.lock`，不扫描或删除其他状态文件。

## 模块设计与数据流

Linux 启动时打开固定锁文件并以非阻塞 `flock` 获取排他锁，成功后在保持文件描述符存活的同时写入诊断 PID；正常 shutdown 显式解锁并关闭描述符，异常退出由内核释放。持久锁文件可以保留，后续进程以实际内核锁状态判断所有权，不信任可能复用的容器 PID。

部署回滚顺序为：删除失败的新主容器 → 通过受限的一次性容器删除固定调度器锁 → 恢复旧 runtime → 重命名并启动 previous 主容器 → 恢复 sidecar。此清理发生在主容器均已停止的窗口，不会破坏活跃调度器。

## 变更文件

- `agent/workflows/scheduler.py`：Linux 使用内核文件锁并持有描述符。
- `deploy/scripts/deploy.sh`：回滚启动 previous 主容器前清理固定陈旧锁。
- `tests/unit_test/workflows/test_workflow_scheduler.py`、`tests/unit_test/workflows/test_case.md`：覆盖容器 PID 1 复用等价场景。
- `tests/unit_test/deploy/test_deploy_assets.py`：约束远端回滚清理目标与挂载范围。

## 测试方案

- 在 POSIX 创建 PID 等于当前进程的陈旧锁文件，验证无内核持有者时仍可启动。
- 启动两个同 workspace 调度器，验证第二个继续 fail closed。
- 运行工作流与部署专项测试、全量 pytest、Ruff、Shell 语法和现有前端门禁。
- 真实云端制造过的失败现场先恢复公网，再重新发布并验证核心验收、外部告警报告及无临时工作流泄漏。

## 验收标准

- 强制删除新容器后，previous 主容器不再因 `WORKFLOW_SCHEDULER_ALREADY_RUNNING` 循环退出。
- 公网 `/health` 恢复并保持通过。
- 核心部署验收成功时新镜像稳定运行；失败时旧主容器、sidecar 与 runtime 均可恢复。
