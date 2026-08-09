# ZhiCe-Agent 每日 JSONL 日志文件命名调整设计

> 日期：2026-08-09
>
> 状态：方案已确认，随本文落地
>
> 归属：Part 8 Gateway / Agent 运行日志

## 1. 背景

当前结构化运行日志按 `${ZHICE_AGENT_WORKSPACE}/logs/YYYY-MM-DD/trace.log` 保存。内容本身已经是一行一个 JSON object 的 JSONL，但扩展名仍为 `.log`，并为每天额外创建目录。Windows 文件管理器和常见编辑器无法从文件名直接识别其 JSONL 类型，按日期浏览也不够直观。

本调整承接 Part 8 的日志职责划分，只改变每日结构化日志的落盘路径，不改变终端的人类可读日志、事件字段、脱敏规则或诊断权限边界。

## 2. 目标

1. 新产生的结构化运行日志统一写入 `${ZHICE_AGENT_WORKSPACE}/logs/log-YYYY-MM-DD.jsonl`。
2. 文件仍保持 NDJSON/JSONL：每行一个完整 JSON object。
3. 长时间运行的 Gateway 跨过本地午夜后自动切换到新日期文件。
4. 现有 workspace 中的历史日志一次性迁移为新文件名，代码不保留旧路径兼容分支。
5. Gateway 启动提示、测试和当前活文档统一使用新路径。

## 3. 范围边界

- 不把终端可读日志改成 JSONL；本地 Ops 页面仍展示 Gateway stdout/stderr。
- 不改变 JSONL 字段、日志等级、截断或 Secret 脱敏。
- 历史 `trace.log` 在本次升级时一次性迁移；迁移后旧日期目录删除。
- 不增加日志压缩、保留期限、集中采集或日志管理页面。
- 不修改 Session 自身的 JSONL 持久化格式。

## 4. 模块设计

新增中性路径模块 `agent/log_paths.py`，由写入端和诊断读取端共同使用：

- `daily_trace_path(logs_dir, day)` 生成唯一的 `logs/log-YYYY-MM-DD.jsonl`；
- 写入端和诊断读取端只使用该路径；
- workspace 历史文件在代码升级时完成一次性迁移，不在运行时代码中维护双格式。

`DailyTraceFileHandler.emit()` 保持每条记录重新计算当天路径，因此进程无需重启即可跨日切换。

## 5. 数据流

```text
Gateway event
  -> JsonlTraceFormatter
  -> daily_trace_path(today)
  -> logs/log-YYYY-MM-DD.jsonl

Diagnostics
  -> each requested day
  -> logs/log-YYYY-MM-DD.jsonl (if present)
  -> parse bounded JSON objects
```

## 6. 变更文件

- `agent/log_paths.py`
- `agent/app/logging.py`
- `agent/app/gateway.py`
- `agent/auth/diagnostics.py`
- `tests/unit_test/log_paths/`
- `tests/unit_test/app/`
- `tests/unit_test/auth/`
- `README.md`
- `docs_design/zhice-agent-part8-gateway-agent-logging-design.md`
- `docs_design/zhice-agent-part9-user-auth-permission-design.md`
- `docs_design/zhice-agent-overall-design.md`
- `docs_design/README.md`

## 7. 测试方案

- 路径 helper 对指定日期生成稳定的新路径。
- Gateway 写入真实 `log-YYYY-MM-DD.jsonl`，每行可被 `json.loads()` 解析。
- trace 关闭时不创建文件。
- 诊断只读取新格式日志。
- Gateway 启动提示使用新命名。
- Ruff、全量 pytest 和前端既有验收全部通过。

## 8. 验收标准

1. 新启动 Gateway 后，日志目录根下出现 `log-YYYY-MM-DD.jsonl`。
2. 不再为新日志创建 `YYYY-MM-DD/trace.log`。
3. JSONL 每个非空行都是合法 JSON object。
4. 实际 workspace 的旧 `trace.log` 已迁移，旧日期目录不再存在。
5. 当前文档不再把旧路径描述为现行写入路径。
