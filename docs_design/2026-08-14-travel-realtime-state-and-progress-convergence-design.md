# 旅行规划实时状态与进度收敛设计

## 背景

旅行规划页面目前存在四类由结果格式差异和前端状态收敛时序导致的问题：小红书已返回笔记但无法生成可读摘要；需求对话等待模型回复时无法新建独立计划；转主聊天选项需要切页或刷新后才出现；已完成计划会被历史进度缓存回写为“求解”阶段。

## 目标

- 兼容小红书只读服务实际返回的 camelCase 笔记字段，并以紧凑、可换行的来源卡片展示筛选摘要。
- 允许用户在当前需求消息仍处理中时新建独立旅行计划，旧请求继续归属于原 Session。
- 在需求回合结束时以服务端草稿为权威状态立即收敛转主聊天卡片，不依赖单个实时事件必达。
- 打开已保存的完成计划时始终呈现完整进度，历史缓存不得覆盖终态。

## 范围边界

- 不修改 AgentLoop 的通用循环，不增加旅行业务硬编码到核心层。
- 不取消用户已经发出的需求请求；新建计划只分离当前前端工作区。
- 不改变小红书查询参数和外部服务协议，仅增强用户可见结果投影。
- 不启动或重启本地 Gateway、前端或 MCP 服务。

## 模块设计

### 小红书结果投影

`agent/applications/travel/progress.py` 的小红书投影同时识别 `note_card` / `noteCard`、`display_title` / `displayTitle`、`nickname` / `nickName`。通用嵌套文本读取器也兼容这些字段，使 feed 卡片可提取标题与作者。

前端 `TravelProgress` 将来源、查询词和结果数拆为语义明确的标题区，查询词允许自然换行，结果条目保持有界展示。

### 独立新建计划

`TravelPlannerPage` 的“新建旅行计划”按钮不再由 `intakeBusy` 禁用，`travel.startNew()` 也不再因需求回复进行中而提前返回。旧 Session 的晚到事件继续走既有后台 Session 分支，不污染新工作区。

### 转主聊天状态收敛

收到当前需求 Session 的 `channel_status=done/stopped` 后，前端除刷新计划列表外，再读取一次该 Session 的旅行草稿。服务端持久化的 `handoff_question` 成为最终权威状态，即使 `travel.main_chat_handoff` 实时事件丢失或乱序，选项也能在本回合结束时立即出现。

### 完成进度终态

打开已保存计划时先加载历史进度，再强制写入 `complete` 终态；若时间线缺少完成记录，则补一条且避免重复。进度组件在 `complete` 阶段将最后一步同时标记为完成，使全部阶段呈现闭环。

## 数据流

```text
工具结果 -> TravelProgressHookRuntime -> search_results -> 来源结果卡片

需求回合结束 -> channel_status(done/stopped)
             -> loadDraft(session_id)
             -> handoff_question / draft / conversation 收敛

打开已保存计划 -> loadProgress(source_session_id)
               -> 强制 complete + 补终态记录
               -> 六阶段全部完成
```

## 变更文件

- `agent/applications/travel/progress.py`
- `web/frontend/src/stores/travel.ts`
- `web/frontend/src/pages/TravelPlannerPage.vue`
- `web/frontend/src/components/travel/TravelProgress.vue`
- `web/frontend/src/styles/travel.css`
- `tests/unit_test/travel/test_progress.py`
- `tests/unit_test/travel/test_case.md`
- `web/frontend/src/stores/travel.test.ts`
- `web/frontend/src/pages/TravelPlannerPage.test.ts`
- `web/frontend/src/components/travel/TravelProgress.test.ts`

## 测试方案

- 后端单测覆盖小红书 camelCase `noteCard/displayTitle/nickName` 返回格式。
- Store 单测覆盖需求回复期间新建计划、旧 Session 晚到事件隔离、回合结束主动恢复 handoff、完成计划覆盖陈旧 `solve` 缓存。
- 页面单测覆盖需求回复和后台生成两种情况下新建按钮均可用。
- 组件单测覆盖来源卡片语义和 `complete` 时六个步骤全部完成。
- 运行 Ruff、Pytest，以及前端 lint、typecheck、Vitest 和 build。

## 验收标准

- 小红书返回公开笔记时能看到标题、作者/摘要和结果数，不再误报“无法生成摘要”。
- 模型等待回复期间可立即新建计划，旧回复不会覆盖新工作区。
- 非旅行问询在当次回复结束后立即出现转主聊天选项，无需刷新或切页。
- 已保存完成计划打开后显示到“完成”，全部进度节点为完成态。
