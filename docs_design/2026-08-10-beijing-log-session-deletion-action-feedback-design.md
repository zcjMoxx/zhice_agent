# ZhiCe-Agent 北京时间日志、外部 Session 删除与操作反馈修正设计

> 日期：2026-08-10
>
> 状态：已实现并部署

## 1. 背景

云端容器默认使用 UTC，现有终端日志、JSONL trace 时间与每日文件名依赖进程本地时区，导致服务器 Ops 中显示时间比北京时间少 8 小时。外部渠道 Session 的删除入口虽然在 Web 中可见，后端却因渠道路由仍指向该 Session 返回 `409 SESSION_CHANNEL_READ_ONLY`；删除弹窗没有展示捕获到的错误，也没有 pending 状态。渠道设置和管理页的部分异步按钮同样缺少一致的按下、执行中、成功或失败反馈。

## 2. 目标

- 人类终端日志、JSONL trace `ts`、每日 `log-日期.jsonl` 与诊断文件选择统一使用固定北京时间 `UTC+08:00`，不依赖宿主机或容器 timezone。
- 微信、QQ 私聊和 QQ 群 Session 均可由其内部账号所有者删除。
- 删除外部 Session 时一并删除指向它的 `channel_conversations` 路由；下一条外部消息按现有 resolve 流程创建全新 Session，账号绑定不受影响。
- 删除按钮展示 pending、成功后的列表变化和可见失败原因。
- 所有按钮具有统一按下态和 disabled 反馈；设置/管理中的异步 mutation 至少有可见错误，关键动作有执行中或成功提示。

## 3. 范围边界

- 不增加删除 trace 日志的 Web 能力；运行日志继续作为受控运维证据按既有保留策略管理。
- 不删除 QQ/微信账号绑定、receipt、审计或已发送平台消息。
- 不开放跨用户删除；仍通过 `SessionAccessService.resolve_session(..., delete=True)` 执行所有权校验。
- 不改变 JSONL Session 真值格式，只改变日志时区和外部 Session 删除策略。

## 4. 模块设计

### 4.1 北京时间

日志模块定义固定 `UTC+08:00` 时区。`TerminalLogFormatter` 用该时区渲染 `[YYYY-MM-DD HH:MM:SS]`；`JsonlTraceFormatter` 输出带 `+08:00` offset 的 ISO-8601；`DailyTraceFileHandler` 和诊断 `_trace_paths` 使用同一日期边界。结构化 Activity/Audit 数据仍可在数据库中保存 UTC，由前端按浏览器本地时间显示。

### 4.2 外部 Session 删除

`SQLiteAuthStore.session_index_delete()` 在同一数据库事务中先删除所有 `current_session_id` 命中的渠道路由，再删除 session index。`SessionAccessService.delete_session()` 不再按 channel 返回 409，但仍先完成 actor 所有权校验。删除 JSONL/metadata 和派生 Context 后，下一条外部消息因路由不存在而创建新的渠道 Session。

### 4.3 操作反馈

Session 删除弹窗增加 `deleteBusy`、`role=alert` 错误和 disabled 状态。渠道 Store 为生成绑定码、QQ/微信解绑和微信重连补齐 busy/error。全局按钮样式增加短促 `:active` 位移/缩放以及统一 disabled cursor/opacity；管理和设置现有错误区域增加 `aria-live`，让失败不会静默。

## 5. 变更文件

- `agent/app/logging.py`、`agent/auth/diagnostics.py`
- `agent/auth/store.py`、`agent/auth/session_access.py`
- `web/frontend/src/components/SessionSidebar.vue`
- `web/frontend/src/components/SettingsCenter.vue`、`web/frontend/src/stores/channels.ts`
- `web/frontend/src/layouts/AdminLayout.vue`、`web/frontend/src/styles/app.css`
- 对应 Python/Vitest 测试和 `test_case.md`
- README、Part 8/9/14/16 活文档与设计索引

## 6. 测试与验收

- UTC epoch 在终端日志中固定渲染为北京时间，在 JSONL 中带 `+08:00`。
- UTC 服务器环境下每日文件名仍按北京时间跨日。
- 微信/QQ Session 删除后 JSONL、metadata、index、route 均消失；下一次 resolve 创建新 Session。
- 非所有者删除继续拒绝。
- Session 删除 409/网络失败在弹窗中可见，重复点击被禁止。
- 渠道 mutation 失败进入页面错误区；按钮存在 active/disabled 状态。
- Ruff、全量 pytest、前端 lint/typecheck/test/build 和真实页面交互 smoke 通过。

## 7. 实现与部署结果

- 后端专项测试：`101 passed`。
- 后端全量验收：Ruff 通过，pytest `988 passed, 2 skipped`。
- 前端全量验收：lint、typecheck、production build 通过，Vitest `59 passed`。
- 云端隔离镜像 smoke、公网 `/health` 与服务器容器健康检查通过。
- 生产镜像 Digest：`sha256:8d504428db57b4509e82a2a5eb545b14e7aae135ae23da1ab3d494ae4ab4a43f`。
- 服务器真实 JSONL Trace `ts` 验收值：`2026-08-10T16:27:13.997+08:00`。
