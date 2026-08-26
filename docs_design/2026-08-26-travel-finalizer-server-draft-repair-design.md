# 旅行终稿服务端草稿与整体修复设计

## 背景

`finalize_travel_plan` 当前只接受完整 `TravelPlanV1`。模型第一次提交终稿后，领域校验采用 fail-fast，只返回一个字段错误；虽然 Prompt 要求“只修所指字段”，下一次 Tool 调用仍必须重新序列化完整计划。长计划一次结构化输出约需数分钟，且重写会让已经正确的交通名称、来源、证据引用和数组长度再次回退。交通字段归一能够兜住部分症状，但不能消除完整 JSON 重写、逐项报错和静默数据修正风险。

## 目标

- 第一次仍允许提交完整计划，失败后由服务端保存有修订号的规范化草稿。
- 同一 `finalize_travel_plan` 后续只提交一个或多个受限 JSON Pointer 修正，不再重写完整计划。
- 一次返回全部 JSON Schema 结构问题，并附领域校验问题，允许模型一批修正。
- 规范化草稿持久化在 actor-scoped 旅行 SQLite 中；父 Session Tool 历史只用于兼容升级前的旧失败调用，进程重启或跨 Turn 后仍使用同一服务端 revision 继续。
- 最终持久化仍经过候选一致性、来源账本和严格 `TravelPlanV1` 校验。
- 交通归一不静默删除无效项、不生成无语义占位名称、不按数量截断真实方案。

## 范围边界

- 不把草稿放进 AgentLoop；草稿属于旅行应用领域状态，进程态由 `TravelSourceLedger` 有界缓存，耐久态写入 actor-scoped `TravelPlanStore`，不会把完整草稿放进 ToolResult 重新喂给模型。
- 不允许修正 `request`、`schema_version`、`plan_id` 或 `owner_user_id`；确认需求和身份仍由可信服务端控制。
- 不提供任意 JSON Patch：只允许 `set`、`remove`，路径必须位于终稿可编辑顶层集合，操作数、深度、路径和值大小均有界。
- 不降低 `TravelPlanV1`、证据来源、候选确认或研究完成门槛。
- 不自动修复无法可靠推导的酒店、车次、活动、价格和证据；这些问题进入批量 issue 列表，由模型一次修正。

## 模块设计

### 1. 草稿协议

`finalize_travel_plan` 支持两种互斥输入：

```json
{"plan": {"完整 TravelPlanV1": "..."}, "selected_candidate_id": "candidate-..."}
```

```json
{
  "draft_revision": "sha256:...",
  "repairs": [
    {"op": "set", "path": "/transport_options/3/name", "value": "G8606 二等座"},
    {"op": "remove", "path": "/transport_options/21"}
  ]
}
```

草稿修订号是规范 JSON 的 SHA-256。每次修正必须携带当前修订号；不匹配返回 `TRAVEL_PLAN_DRAFT_CONFLICT` 和最新修订号，防止基于旧数组下标覆盖新草稿。

### 2. 受限修正

新增纯领域 helper：

- 解析 RFC 6901 转义；拒绝空路径、`..` 语义、超深路径和不存在的父节点。
- 只允许编辑 `assumptions`、`freshness_summary`、`transport_options`、`stay_recommendations`、`days`、`budget`、`weather_summary`、`fallbacks`、`avoidance_tips`、`evidence`、`unknowns`、`generated_at`。
- `set` 可替换既有值或为对象增加字段；数组只接受已有下标或 `-` 追加。
- `remove` 只允许删除已存在的对象字段或数组下标。
- 单次最多 50 个操作，修正载荷最多 64 KiB；外部研究补齐后允许空操作数组，仅让服务端把新账本事实重新合并到同一草稿。

### 3. 批量诊断

使用 `jsonschema.Draft202012Validator.iter_errors()` 对 Tool 中的完整计划 Schema 收集全部结构错误，转换为稳定的 `plan.*` 字段路径。随后执行严格领域校验；当前领域异常作为额外 issue 合并并去重。失败 ToolResult 返回：

```json
{
  "status": "repair_required",
  "code": "TRAVEL_PLAN_SCHEMA_INVALID",
  "draft_revision": "sha256:...",
  "issues": [{"field": "plan.transport_options[3].name", "message": "..."}]
}
```

模型可在一次修正调用中处理所有已知问题。严格领域校验继续 fail closed；若修正后出现新的跨字段语义问题，再返回新 revision 和 issue，但不再重新生成完整 JSON。

### 4. 跨 Turn 与重启恢复

`TravelPlanStore` 增加 `travel_plan_drafts` 表，以 `(session_id, owner_user_id)` 为主键保存规范化 `plan_json`、revision 和 candidate id。repair 使用 revision 条件更新，避免并发请求覆盖较新的数组下标；最终计划保存成功时在同一 SQLite 事务中删除草稿。

`TravelSourceLedger.plan_attempts` 仍增加 `draft_revision` 和 `selected_candidate_id`，用于当前进程内的来源合并。父 Session 重放只作为旧版本兼容路径，按消息顺序处理同名 Tool：

- 遇到 `plan` 建立新草稿。
- 遇到 `draft_revision + repairs` 时校验 revision 并应用到前一草稿。
- 每个重放后的草稿继续记录当时 weather/transit 验证状态；发现成功的 finalizer ToolResult 后清空此前失败草稿，防止已完成 Session 在重启后复活旧草稿。

新调用优先读取 SQLite 中服务端归一后的草稿，revision 不依赖模型原始参数重算。旧 Session 只有完整计划调用时仍可从历史恢复一次，之后首次 repair 会迁移进持久化草稿表。

### 5. 交通归一完整性

- 非对象交通项原样保留，交给严格校验报告，不静默丢弃。
- 名称仅在车次或“起点→终点 + 方式”可可靠推导时补齐；否则保留无效值并进入 issue。
- 引用铁路证据时来源固定为 12306；引用其他外部证据时来源固定为 provider，避免保留与 evidence 矛盾的模型标签。
- 语义相同项不把 `evidence_ids` 纳入身份指纹，重复项合并证据引用；信息不足的空壳项不自动去重。
- 不再自动截断到 20 项；超限作为批量 issue，模型用一次 repair 明确删除多余项。

## 数据流

```text
完整终稿首次提交
  -> 账本事实合并与安全归一
  -> 服务端持久化 revision 草稿
  -> 批量结构诊断 + 严格领域校验
  -> 成功则原子保存计划并删除草稿
  -> 失败则返回 revision + issues

一次或多次 repair
  -> revision 冲突检查
  -> 服务端对保存草稿应用有界操作
  -> 生成新 revision
  -> 同一完整校验链
  -> 成功保存或返回新 issues
```

## 变更文件

- `agent/applications/travel/drafts.py`
- `agent/applications/travel/source_ledger.py`
- `agent/applications/travel/store.py`
- `agent/applications/travel/tools.py`
- `agent/applications/travel/service.py`
- `agent/app/runtime.py`
- `prompts/travel_planning.md`
- `prompts/travel_planning_continuation.md`
- `docs_design/zhice-agent-part19-intelligent-travel-planner-design.md`
- `tests/unit_test/travel/test_store_and_tool.py`
- `tests/unit_test/travel/test_source_ledger.py`
- `tests/unit_test/travel/test_intake_runtime.py`
- `tests/unit_test/travel/test_case.md`

## 测试方案

- 初次完整终稿失败返回 revision 和多个结构 issue。
- 一次 repair 同时修改多个字段并成功保存，修正参数不含完整计划。
- revision 过期、非法路径、越界数组、过多操作、过大值全部 fail closed。
- repair 不能修改 request、schema 或身份字段。
- Session 重放完整计划加多次 repair 后恢复最新草稿与 candidate id。
- 旧完整计划历史仍可恢复。
- 交通归一不丢非对象、不造空壳名称、不截断；重复项合并证据且来源与 evidence 一致。
- 旅行单元测试、相关 runtime 测试、Ruff 与 `git diff --check`。

## 验收标准

- 第一次失败后，后续 Tool 参数不再包含完整 TravelPlanV1。
- 多个已知结构问题在一个 ToolResult 中返回，并能通过一次 repair 一起修正。
- 跨 Turn/重启后使用最新 revision 继续，不重跑来源或重新生成计划。
- 最终保存计划通过原有严格领域校验，Owner、plan_id、candidate 和 evidence 边界不降低。
- 不再通过静默删除、无语义占位或数组截断让无效草稿“看似通过”。
