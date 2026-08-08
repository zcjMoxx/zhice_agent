# Web 安全审计日期时间选择组件设计

> 说明：当前组件已进一步采用可切换的月历与年月选择面板，并增加结束时间下限约束；交互细化见 `2026-08-08-web-audit-datetime-picker-refinement.md`。

## 背景

手机浏览器的原生 `datetime-local` 在不同系统中存在占位格式混杂、整框不可点击和缺少明确确认键的问题。

## 目标

提供中英文一致、整框可点击、带标准月历和明确确认操作的日期时间选择体验。

## 范围边界

仅替换 Web 管理后台安全审计的开始和结束时间输入，不改变后端查询参数与时间存储格式。

## 模块设计

新增受控组件 `DateTimePicker.vue`，通过 `v-model` 读写 `YYYY-MM-DDTHH:mm`。组件包含月份导航、日期网格、小时与分钟选择以及取消、清除、确定操作。

## 数据流

审计筛选状态传入组件；用户在弹窗内编辑临时值；点击确定后组件更新筛选状态；现有筛选和 CSV 导出继续直接消费该状态。

## 变更文件

- `web/frontend/src/components/DateTimePicker.vue`
- `web/frontend/src/components/DateTimePicker.test.ts`
- `web/frontend/src/layouts/AdminLayout.vue`
- `web/frontend/src/styles/app.css`

## 测试方案

覆盖整框打开、中文年月及时分标签、日期时间选择和确定后输出格式；同时运行前端 lint、typecheck、测试与生产构建。

## 验收标准

- 点击整个日期框可打开组件。
- 月历可切换月份并选择日期。
- 时间区域明确显示时、分。
- 有取消、清除、确定按钮。
- 输出格式与现有审计接口兼容。
