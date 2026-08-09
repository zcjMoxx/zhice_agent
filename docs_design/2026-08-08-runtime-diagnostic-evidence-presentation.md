# 运行诊断证据与运行记录可读化设计

## 背景

运行诊断时间线混合展示中文文案和内部事件标识，证据标识用途不明；近期运行记录只显示 Session 标题、状态和错误码，无法展开查看 Session、Turn、Request 等关联标识，空耗时还会被错误显示为零毫秒。

## 目标

让管理员先看懂发生了什么，再按需查看可关联日志的技术标识；统一所有事件的“可读名称 + 内部标识”展示。

## 范围边界

- 不增加原始 Secret、Tool 参数或未脱敏日志正文。
- 不把证据标识描述成错误原因。
- 不改变事故聚合和 Trace 真值，只调整管理读取字段与展示。
- 时间线默认只看异常证据，完整生命周期事件仍可切换查看。

## 模块设计

### 诊断证据时间线

- “跨组件时间线”更名为“诊断证据时间线”。
- 默认展示带错误码或 `is_error=true` 的异常证据，可切换“全部上下文”。
- 每条事件同时展示中文含义和内部事件标识。
- 展开项解释 Evidence ID 用于日志关联，并显示已脱敏的 Request、Session、Turn、错误消息等字段。

### 近期运行记录

- 明确第一列为 Session 会话标题。
- 每条记录可展开查看问题说明、影响、建议处理、Session ID、Turn ID 和 Request ID。
- 管理读取接口补充 `request_id`。
- `null`、空字符串或缺失耗时显示为 `—`，不再转换为零毫秒。

## 变更文件

- `agent/auth/store.py`
- `web/frontend/src/layouts/AdminLayout.vue`
- `web/frontend/src/styles/app.css`
- `web/frontend/src/layouts/AdminLayout.test.ts`
- `tests/unit_test/app/test_auth_routes.py`

## 测试方案

- 后端断言运行记录包含 Request ID。
- 前端断言时间线默认隐藏正常事件、可切换全部上下文，并同时显示中文名称和内部标识。
- 前端断言运行记录可展开查看 Session、Turn、Request 标识及错误说明。
- 前端断言空耗时显示为 `—`。
- 执行 Ruff、Pytest、前端测试、类型检查、Lint 和生产构建。

## 验收标准

1. 管理员能区分“可读解释”和“内部标识”。
2. Evidence ID 的用途有明确说明。
3. 失败运行可以展开并关联到 Session、Turn 和 Request。
4. 默认视图不再被正常启动/停止事件淹没。
5. 空耗时不显示为零毫秒。
