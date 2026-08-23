# 工作流任务化工具输入设计

> 日期：2026-08-21
>
> 状态：已实施并通过真实天气工作流烟测
>
> 关联：`2026-08-21-workflow-user-facing-productization-design.md`

## 背景

工作流虽然已经隐藏工具名和 schema key，但天气仍要求用户填写经纬度，小红书详情仍要求填写 feed ID 与访问 token。这些是工具协议参数，不是用户完成“查上海天气”或“查看这篇笔记”时自然掌握的信息。只替换字段文案会导致界面看似友好、真实执行仍失败。

## 目标

- 天气查询只要求用户填写地点名称和日期；执行器自动把地点解析为经纬度。
- 小红书搜索使用关键词、排序、内容类型和结果数量等任务字段，并用中文选项编辑枚举。
- 小红书详情只要求笔记链接，执行器从链接安全提取笔记编号和访问参数。
- 内部 MCP schema、schema hash、RBAC 和 Query allowlist 继续作为最终执行边界。

## 范围边界

- 不修改外部 MCP Tool schema，不伪造经纬度、笔记编号或访问参数。
- 地点解析只调用同一 Open-Meteo MCP 的只读 geocode Tool；无结果时明确失败，不猜测坐标。
- Open-Meteo 公共只读 API 客户端不继承终端代理变量，避免本机代理 TLS 中断；仍保留 HTTPS 证书校验、超时和错误降级。
- 小红书链接只接受 HTTPS 小红书域名，并要求链接包含详情查询所需参数。
- 高级 JSON 仍可用于协议级配置；默认表单只展示任务字段。

## 模块设计与数据流

前端按 Tool name 选择输入配置：天气隐藏 latitude/longitude，新增 `place_name`；小红书详情隐藏 `feed_id`/`xsec_token`，新增 `note_url`；枚举字段渲染为中文下拉框。就绪检查使用任务字段，不再要求被系统解析的内部字段。

执行器在调用目标 Tool 前执行受限适配：

```text
地点名称 -> allowlisted geocode_place -> 首个真实结果坐标 -> weather Tool
小红书链接 -> HTTPS 域名校验与 URL 解析 -> feed_id/xsec_token -> detail Tool
```

## 变更文件

- `agent/workflows/tool_inputs.py`
- `agent/workflows/nodes.py`
- `config/config.example.yml`
- `web/frontend/src/pages/WorkflowPage.vue`
- `web/frontend/src/utils/workflow-presentation.ts`
- 对应 Python/前端测试与当前 Part 20 活文档

## 测试与验收

- 单测覆盖地点解析成功、无结果、无效小红书链接、中文枚举和值转换。
- Python workflow 单测、前端 lint/typecheck/Vitest、production build 通过。
- 真实浏览器验证天气只显示地点和日期，小红书不再显示 feed ID/token，普通 DOM 不出现 latitude、longitude、feed_id、xsec_token。

## 实施与验收结果

- 天气预报默认表单仅展示“地点”和可选“查询几天”；默认动态查询今天和明天，不要求用户填写经纬度或日期。
- 历史天气展示地点、开始日期和结束日期；坐标仍由系统解析。
- 小红书搜索的排序方式、内容类型使用中文下拉选项；详情只要求浏览器地址栏中的完整笔记链接和可选评论开关。
- 执行器只在目标天气工具被允许时自动加入隐藏的只读 geocode helper，helper 不出现在普通工具选择器中。
- 修复 MCP 同时返回 structured content 与重复 text 时的结构化解析，避免合法 JSON 后附诊断文本造成执行失败。
- Open-Meteo 客户端显式 `trust_env=False`，真实诊断确认可避开本机代理 TLS 中断，同时保留 HTTPS 校验。
- 工作流后端 23 项测试、相关天气/MCP 40 项测试、前端 182 项测试、lint、typecheck 和 production build 全部通过。
- 真实在线烟测以“上海”为输入，成功完成地点解析并返回带经纬度的天气结果。
