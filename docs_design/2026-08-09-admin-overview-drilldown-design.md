# ZhiCe-Agent 管理概览异常下钻设计

> 日期：2026-08-09
>
> 状态：方案已确认，随本文落地
>
> 归属：Part 16 管理后台与 Part 17 运行诊断交互修正

## 1. 背景

管理概览中的“近期失败”和“当前事故”只显示计数，视觉上虽然有 attention 状态，但不能点击进入对应证据。用户必须先找到“运行诊断”，再手动定位失败记录或事故列表。Skill source 的 Target 等技术值在统一等宽字体时使用了 500 字重，使 `master` 在浅色主题中显得不必要地加粗。

## 2. 目标

1. 技术值使用常规 400 字重，保留等宽字体但不强调普通值。
2. “近期失败”卡片点击后进入运行诊断的失败运行记录区域。
3. “当前事故”卡片点击后进入近 60 分钟事故证据区域。
4. 跳转卡片使用原生 button，支持鼠标、Enter、Space、focus-visible 和 disabled。
5. 下钻后不仅切换 tab，还滚动到明确目标区域。

## 3. 范围边界

- 不新增路由或 URL query 状态。
- 不改变诊断 API、事故聚合规则或失败运行定义。
- Gateway 和当前模型卡片仍为只读状态，不伪装成可点击。
- 当前账号没有运行诊断权限时，下钻卡片禁用。

## 4. 数据流

```text
overview recent failures
  -> recentRunStatus = error
  -> loadTab(monitor)
  -> scroll #monitor-runs into view

overview current incidents
  -> reset diagnostic filters
  -> minutes = 60
  -> loadTab(monitor)
  -> scroll #monitor-incidents into view
```

## 5. 变更文件

- `web/frontend/src/layouts/AdminLayout.vue`
- `web/frontend/src/layouts/AdminLayout.test.ts`
- `web/frontend/src/styles/app.css`
- `docs_design/zhice-agent-part16-web-product-design.md`
- `docs_design/zhice-agent-part17-reliability-diagnostics-deployment-design.md`
- `docs_design/README.md`

## 6. 测试方案

- Target `master` computed style 为 400 字重。
- 两张异常卡片是 button 并具有稳定的可访问名称。
- 失败卡片切换 monitor、保持 error 过滤并定位运行记录。
- 事故卡片重置过滤、使用 60 分钟并定位事故区域。
- 前端 lint、typecheck、Vitest、build 和真实浏览器点击验收。

## 7. 验收标准

1. `master` 不再呈现半粗体。
2. 两个异常计数卡片有明确 hover/focus 反馈。
3. 点击后直接到达对应证据区域，无需用户二次寻找。
