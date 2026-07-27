# 六套主题家族与独立明暗模式设计

## 1. 背景

Part 16 当前把 `system / light / dark` 同时当作主题选项和明暗偏好，设置页也把它们展示为“跟随系统 / 浅色曜石 / 暗色曜石”。这使视觉风格与明暗模式无法独立扩展，也无法在保留系统跟随能力的同时选择其他配色。

本次在不改变聊天、管理、鉴权和 Gateway 边界的前提下，将主题拆成“主题家族”和“外观模式”两个正交维度，并落地六套同时具备浅色、暗色 token 的主题。

## 2. 目标

- 主题家族固定为：经典黑白、象牙曜石、深海蓝灰、森雾浅绿、雾紫极光、琥珀暖砂。
- 外观模式固定为：跟随系统、浅色、暗色。
- 选择主题只改变配色家族；切换明暗只在当前家族内部切换。
- 默认保持现有产品观感：象牙曜石 + 跟随系统。
- 兼容原有按身份保存的 `theme` 本地偏好，不让升级后的用户丢失明暗选择。
- 设置中心采用 3×2 大预览卡片，清楚展示各主题差异。

## 3. 范围边界

- 只修改 Vue 前端主题状态、CSS token、设置中心展示和对应测试。
- 不增加后端字段、数据库表或跨设备同步。
- 不改变 QuickPreferences 的职责；快捷按钮仍只在当前主题内切换浅色/暗色。
- 不改变布局尺寸、组件结构、Session 行为、RBAC 或渠道逻辑。
- 不提交或暂存本次工作区改动。

## 4. 状态与持久化设计

Pinia UI store 使用两个独立字段：

```ts
themeFamily: "classic" | "obsidian" | "ocean" | "sage" | "aurora" | "amber"
colorMode: "system" | "light" | "dark"
```

按身份写入：

```text
zhice.ui.{userId}.themeFamily
zhice.ui.{userId}.colorMode
```

加载时若没有 `colorMode`，读取旧键 `zhice.ui.{userId}.theme` 并迁移合法的 `system / light / dark` 值。没有合法主题家族时回退到 `obsidian`。应用主题时同时写入：

```text
html[data-theme="light|dark"]
html[data-theme-family="classic|obsidian|ocean|sage|aurora|amber"]
```

系统主题变化时，只有 `colorMode === "system"` 才重新解析明暗模式；主题家族保持不变。

## 5. Token 设计

`tokens.css` 继续只暴露既有语义 token，业务组件不直接感知具体主题色。每个主题家族各有浅色和暗色覆盖：

- 经典黑白：纯中性、高对比、边界硬朗，不使用彩色色相。
- 象牙曜石：沿用当前浅色象牙近白与暗色雾银炭灰，是升级后的默认主题。
- 深海蓝灰：冷白、低饱和蓝灰和深海蓝强调。
- 森雾浅绿：近白底、低饱和灰绿结构面，不让正文区域整体泛绿。
- 雾紫极光：灰白底与克制雾紫层次，不使用高饱和彩色大块。
- 琥珀暖砂：纸张感暖白、浅砂与琥珀棕，暗色保持低亮度暖棕层级。

主题必须完整覆盖背景、表面、文字、边界、强调、焦点、环境光、侧栏、登录页、Hero、状态色和阴影，避免切换后继承其他家族的残留色。

## 6. 设置界面

个性化页面分为两组：

1. 外观模式：三个紧凑按钮，包含系统、太阳和月亮图标。
2. 主题风格：六张 3×2 大预览卡片，每张包含当前明暗模式下的主题缩略图和四字中文名称。

移动端主题卡片降为两列，窄屏降为一列。选中态使用当前主题的 `accent` 与 `accent-soft`，不新增硬编码品牌色。

## 7. 数据流

```text
设置中心选择主题 -> setThemeFamily -> localStorage -> applyTheme
设置中心选择模式 -> setColorMode   -> localStorage -> resolvedTheme -> applyTheme
快捷明暗按钮       -> toggleTheme    -> setColorMode(light|dark)
系统明暗变化       -> colorMode=system 时重新 applyTheme
applyTheme          -> data-theme + data-theme-family -> semantic CSS tokens -> 全站组件
```

## 8. 变更文件

- `web/frontend/src/stores/ui.ts`
- `web/frontend/src/components/SettingsCenter.vue`
- `web/frontend/src/components/SettingsCenter.test.ts`
- `web/frontend/src/components/QuickPreferences.test.ts`
- `web/frontend/src/styles/tokens.css`
- `web/frontend/src/styles/app.css`
- `web/frontend/src/styles/tokens.test.ts`
- `web/frontend/src/test/setup.ts`
- `docs_design/zhice-agent-part16-web-product-design.md`
- `docs_design/README.md`

## 9. 测试方案

- UI store/组件测试覆盖主题家族与外观模式分别持久化。
- 覆盖旧 `theme` 键迁移到 `colorMode`。
- 覆盖 QuickPreferences 切换明暗时不改变主题家族。
- Token 测试确认六个家族和浅暗选择器全部存在。
- 运行 ESLint、TypeScript、Vitest、production build 和 `git diff --check`。

## 10. 验收标准

1. 设置中心显示三个外观模式和六张主题大卡片。
2. 任意主题均可在跟随系统、浅色、暗色之间切换。
3. 刷新后按当前登录身份恢复两个偏好。
4. 旧版明暗偏好自动迁移，默认主题为象牙曜石。
5. 快捷明暗按钮不改变已选主题。
6. 登录、聊天、设置和管理后台统一消费同一组语义 token。
7. 前端全部验证通过，工作区不暂存、不提交。
