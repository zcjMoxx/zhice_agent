# 安全审计事件、结果与游标分页设计

## 背景

现有安全审计以具体 action 和授权 decision 筛选。成功、失败被编码进部分 action，`allow/deny` 又主要表达权限决策，无法支持“事件类型=登录、执行结果=成功/失败”的排错视角。前端还使用“加载更多”拼接记录。

## 目标

提供逻辑事件类型、成功失败结果筛选，并把审计列表改为真实游标分页，同时保留原始 action 用于技术排查。

## 范围边界

不修改审计表结构和既有历史数据；新增查询层派生规则。原有精确 `action`、`decision` 参数继续兼容。

## 模块设计

- `event_type` 将登录、注册、密码修改、账号管理等逻辑事件映射到一个或多个既有 action。
- `outcome` 根据 `status_code` 优先派生成功或失败，并对无状态码事件使用 decision 兜底。
- API 和 CSV 导出接受新筛选参数。
- 前端保存每页 cursor 历史，提供上一页、当前页码和下一页。
- 审计列表继续展示原始 action，不将技术记录翻译掉。

## 数据流

筛选选项写入 `event_type` 和 `outcome`；API 查询层转换为 SQL 条件；响应返回当前页和下一页 cursor；前端用 cursor 历史前后翻页。

## 变更文件

- `agent/auth/store.py`
- `agent/app/api/routes.py`
- `web/frontend/src/stores/admin.ts`
- `web/frontend/src/layouts/AdminLayout.vue`
- `web/frontend/src/styles/app.css`
- 相关后端与前端测试

## 测试方案

覆盖逻辑事件类型、成功失败派生、查询兼容和前端上一页/下一页状态，并运行 ruff、pytest、前端 lint、typecheck、测试与构建。

## 验收标准

- “登录”同时覆盖登录成功和登录失败记录。
- 结果筛选使用成功、失败语义。
- 列表展示原始 action。
- 不再出现“加载更多”，分页可前进和返回。
