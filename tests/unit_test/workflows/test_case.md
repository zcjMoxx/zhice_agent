# 可视化工作流测试说明

## 测试目标

验证 Part 20 的 owner-scoped 定义与运行协议、草稿/发布版本、DAG 执行、单实例调度、真实 Tool Catalog、受限输入适配、外部投递安全边界和 Node-RED 交换格式。默认测试使用 Fake Provider，不产生真实外部副作用。

## 用例覆盖

- 定义与校验：固定节点类型、引用解析、唯一触发器、条件分支、端口、孤立动作、图大小和 DAG 环检测。
- Store 与版本：发布幂等、owner 隔离、已发布版本不可变、编辑后生成下一草稿、遗留同版本内容差异识别。
- 草稿试运行：运行最新保存草稿但不发布、不替换 active version；正式运行继续使用已发布版本。
- DAG 执行：条件分支跳过、RuntimeEvent、节点运行持久化、取消和稳定错误码。
- 直接前驱：智能处理与投递始终消费唯一直接前驱，遗留 `source_ref` 不能导致插入节点后发送旧数据。
- Tool Catalog：只返回当前 actor 已注册且进入 Query/Action allowlist 的 Tool，并绑定当前 schema hash。
- 查询预览：只读测试经过当前 `UserScopedToolProvider` 与 allowlist；非法任务输入映射为稳定 API 错误，Action 不提供免确认预览。
- 任务输入适配：天气地点经 allowlisted geocoder 转坐标，详细地点可回退高德 POI；12306 地名转站码；小红书详情只接受安全、完整的 HTTPS 笔记链接。
- 外部来源：天气默认生成动态两日窗口；结构化来源失败区分鉴权、超时、限流和暂不可用。
- 定时天气可靠性：Open-Meteo 闲置连接断开、超时、429 和 5xx 采用有界重试；MCP timeout、transport、schema/not-found 和 rate-limit 保留稳定分类。
- 发布重检：个人邮箱 ownership、QQ 当前绑定/同意、微信当前上下文/同意均在发布时复查。
- 投递边界：Template、官方邮箱、个人邮箱、QQ、微信共用正文组合与纯文本渲染；外发节点保存有界发送内容和回执。
- 未知结果：QQ timeout 记录 outcome unknown，只尝试一次，不自动重放可能已生效的外部操作。
- 稳定 API：run、pause、resume、delete 等缺失工作流统一返回 `WORKFLOW_NOT_FOUND` 404，不泄漏内部异常。

## 调度锁覆盖

- 启动时按 SQLite active schedule 重建固定 job，第二个同 workspace scheduler 被拒绝。
- 状态条件可检查直接上游是否成功；只读/纯处理节点失败时按 1 至 5 次上限重试，恢复后走“是”分支，耗尽后走“否”分支，并拒绝自动重试通知、邮件和外部写操作。
- date、interval、cron、timezone、暂停/恢复和重启恢复保持稳定。
- 调度运行历史保存真实 `date`/`interval`/`cron` 触发类型和计划时间，不伪装成手动运行。
- Windows 陈旧 PID 锁不调用不兼容的 `os.kill(pid, 0)`，不存在的进程可以安全替换。
- Windows PID 被新进程复用时结合创建时间识别旧锁，不终止无关进程。
- Linux 使用内核文件锁；旧容器被强制删除后，即使新容器仍是 PID 1，也能安全取得锁并完成部署回滚。

## Node-RED 兼容边界

- 受限 round-trip 保留智策审核节点、位置、配置和连线。
- `weixin_notification` 等固定节点可交换；Function、exec、文件系统、任意 HTTP 和其它未审核节点拒绝导入。
- 导入后仍使用当前 owner、SQLite 草稿版本和 ZhiCe Runtime，不执行 Node-RED 自身运行语义。

## 前端联动检查

Vue 测试另覆盖 store 冲突恢复、草稿试运行、模板默认“保留结果”、立即恢复已加载画布、保存确认、capability 刷新、节点详情、运行回执、移动端点击连线、紧凑问题入口和视口内连线操作。前端完整验收执行 `npm run test`、`npm run lint`、`npm run typecheck` 和 `npm run build`。

## 关键检查点

- 模型、客户端或导入文件提供的 owner、连接身份和 schema hash 都不可信，运行前必须从当前 actor 与 live catalog 重建授权事实。
- Action、个人邮件、QQ、微信等外部副作用不能因 timeout 自动重试。
- “Provider accepted”只表示上游受理，不能写成最终送达。
- 调度、定义和运行历史不进入聊天 Session，`AgentLoop` 不感知 cron 或 DAG。
