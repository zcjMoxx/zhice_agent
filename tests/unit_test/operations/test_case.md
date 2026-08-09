# Operations 运行态与本地 Supervisor 测试说明

## 测试目标

- 验证本地终端 Ops endpoint 只发布到受控运行态状态文件，并拒绝损坏、越界或陈旧记录。
- 验证 Docker/服务器启动器只能通过固定环境字段投影当前 mode、target 与 URL。
- 验证本地 supervisor 使用有界 loopback 端口、固定 Gateway child，并且 restart 回收旧进程树后重建。

## 关键检查点

- `operations.json` 原子写入，只有相同 `instance_id` 的 supervisor 能清理。
- `local_process/local_docker` 只接受 loopback HTTP，`server_docker` 只接受 HTTPS。
- 本地端口范围固定为 `17681..17690`，不会监听 `0.0.0.0`。
- status 不接受外部 PID/进程名，目标固定为 `zcagent-gateway`。
- restart 调用完整进程树回收并复用原始 child argv。
- 日志页默认每秒跟随最新 500 行并滚动到底部；用户向上滚动时暂停跟随，点击 Continue follow 后恢复，长 JSON 行必须自动换行而不是横向撑开。
- 本地 supervisor 必须 tee Gateway child 的 stdout/stderr：原 PowerShell 仍看到原终端输出，浏览器缓冲展示去 ANSI 后的同一批 `INFO/WARNING/HTTP/agent.turn.*` 行；禁止回退读取 `trace.log` JSONL。
- supervisor 输出目标为真实 TTY 时，原 PowerShell 保留时间/动作/告警 ANSI 配色；重定向或 `NO_COLOR` 时不强制颜色。Ops 浏览器对去 ANSI 文本使用安全 DOM 分段着色，禁止用 `innerHTML` 注入日志。
- Windows supervisor 与 Gateway 子进程固定使用 UTF-8 stdout/stderr 契约，中文输入和回复预览在原终端与 Ops 浏览器中均不得因系统代码页变成替换字符。
- Ops 页面删除与每秒自动跟随重复的 `Refresh now`；暂停按钮同步选中态和 `aria-pressed`，恢复跟随立即拉取日志；重启按钮提供 loading/success/failure、禁用防重和 hover/active/focus-visible 反馈。
- Ops 页面声明暗色 `color-scheme`，Firefox 与 Chromium/WebKit 均使用细圆角暗色 scrollbar，页面外层和日志窗口不出现白色原生轨道。
- 本地进程 Ops 使用共享“监控面板 / 运维终端”双视图；监控面板用状态卡而不是原始 JSON，终端只接受 status/logs/logs-follow/diagnose/restart/help/exit，拒绝 Bash、Docker、sudo、服务器 config 和额外参数。
- 双视图切换只改变现有 DOM 的可见状态，不重建终端；本地 `logs-follow` 使用有界轮询，Ctrl+C 只停止本次跟随。
