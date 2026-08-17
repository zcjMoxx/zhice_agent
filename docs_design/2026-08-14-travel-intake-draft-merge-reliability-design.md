# 旅行接待草稿增量合并可靠性设计

## 背景

真实 Session 中第一轮 `update_travel_draft` 已保存出发地、目的地、日期和人数；第二轮用户只补充
美食、夜景与公交地铁时，模型仍在 patch 中携带其它字段的空字符串、空数组和 `null`。旧实现直接
执行 `merged.update(patch)`，导致累计核心条件被空占位覆盖，随后错误要求用户重新输入。

## 目标

- 用户补充偏好时保留之前已确认的累计条件。
- 模型输出冗余空占位时由服务端确定性防护，不依赖模型每次严格遵守 Prompt。
- 用户明确删除条件时仍可通过 `clear_fields` 完成。
- 每轮向接待 Agent 注入服务端当前草稿，减少历史裁剪或模型误读。

## 范围边界

- 只调整确认前接待阶段的草稿 patch 合并和上下文装配。
- 不改变正式规划、候选确认、酒店来源或计划协议。
- 非空字段仍按最新用户表达覆盖；空值清除只认结构化 `clear_fields`。

## 模块设计与数据流

1. Runtime 从 actor-owned travel Session 读取 `travel_draft`，作为权威累计草稿注入本轮系统上下文。
2. 模型调用 `update_travel_draft`，只应传本轮明确新增或修正字段。
3. Tool 丢弃与字段标准空值相同的占位项，再应用 `clear_fields` 和有效非空 patch。
4. Tool 校验完整合并结果，返回完整 draft、missing fields、ready 和真实 changed fields。
5. 草稿协议升级为 v2；读取 v1 Session 时按历史 `update_travel_draft` 调用重放非空 patch 与显式清空，自动修复已经被旧空占位覆盖的草稿。

## 变更文件

- `agent/app/runtime.py`
- `agent/applications/travel/tools.py`
- `prompts/travel_intake.md`
- `tests/unit_test/travel/test_store_and_tool.py`
- `tests/unit_test/travel/test_intake_runtime.py`
- `tests/unit_test/travel/test_case.md`

## 测试方案

- 复现完整草稿后追加偏好且携带其它空占位，断言核心条件不丢失且仍为 ready。
- 断言 changed fields 只包含真实新增偏好。
- 断言接待 Runtime 每轮注入服务端当前草稿。
- 断言旧 v1 Session 可从历史 Tool 调用恢复核心条件和后续偏好，并迁移到 v2。
- 保留现有 `clear_fields`、空 patch、确认与阶段隔离测试。

## 验收标准

- “8 月 15 日重庆一日游，1 人”后再说“美食夜景，地铁公交”，无需重复输入核心条件。
- 任意字段只有非空新值或显式 `clear_fields` 才能改变累计草稿。
- 服务端返回的 missing fields 和 ready 始终基于完整累计状态。
- 已被旧逻辑写空的现有 Session 在读取或继续对话时自动恢复，无需用户重复输入。
