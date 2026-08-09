# ZhiCe-Agent 本地 Ops 按钮反馈收敛设计

> 日期：2026-08-09
>
> 状态：方案已确认，随本文落地
>
> 归属：Part 18 本地 restricted Ops 页面交互修正

## 1. 背景

本地 Ops 页面日志已经每秒自动跟随，额外的 `Refresh now` 与自动刷新职责重复。现有按钮只有静态底色，缺少 hover、按下、焦点、执行中和完成状态；尤其 Gateway 重启需要数秒，点击后页面没有即时反馈，操作者无法判断是否已触发。

## 2. 目标

1. 删除无必要的 `Refresh now`。
2. 保留每秒自动跟随；暂停后恢复跟随时立即拉取最新日志并滚动到底部。
3. 所有按钮具备 hover、active、focus-visible 和 disabled 反馈。
4. 暂停跟随时按钮呈现稳定选中态，并通过 `aria-pressed` 表达状态。
5. 重启按钮依次显示 `Restarting…`、`Restarted` 或 `Restart failed`，执行期间禁止重复点击。

## 3. 范围边界

- 不改变重启 API、固定目标进程或 supervisor 生命周期。
- 不增加通知组件、前端依赖或复杂状态管理。
- 不改变日志刷新频率和有界缓冲。
- 用户取消确认时不进入 loading 状态。

## 4. 模块设计

页面仍由 `agent/operations/local_supervisor.py` 内嵌提供：

- 删除手动刷新按钮；
- CSS 增加按钮过渡、悬停、按下、焦点、暂停和禁用状态；
- `renderFollow()` 同步文字、class 和 `aria-pressed`；
- `restart()` 使用 `try/catch/finally` 管理按钮执行反馈；
- 成功或失败状态短暂可见后恢复默认文字。

## 5. 变更文件

- `agent/operations/local_supervisor.py`
- `tests/unit_test/operations/test_local_supervisor.py`
- `tests/unit_test/operations/test_case.md`
- `README.md`
- `docs_design/zhice-agent-part18-skill-runtime-and-server-ops-design.md`
- `docs_design/README.md`

## 6. 测试方案

- 页面不再包含 `Refresh now`。
- 暂停按钮同步 `is-paused` 与 `aria-pressed`。
- 重启按钮存在 loading/success/failure/disabled 状态。
- 按钮 CSS 包含 hover、active、focus-visible 和 disabled。
- Ops 内嵌 JavaScript 通过 Node 语法检查。
- Ruff、全量 pytest 和既有前端验收通过。

## 7. 验收标准

1. 页面只保留必要的日志跟随按钮。
2. 鼠标悬停和按下具有立即可感知反馈。
3. 重启期间不能重复提交，执行结果在按钮上可见。
4. 暂停与继续状态无需猜测。
