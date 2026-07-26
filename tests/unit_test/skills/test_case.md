# Skills 单元测试用例

## 测试目标

验证第五部分 Skill 体系的本地发现、`SKILL.md` 解析、source 命名空间、source 同步、运行时目录边界和错误处理，确保 Skill 能力作为扩展层接入，而不污染 AgentLoop 或协议边界。

## 用例覆盖

### Case 1: SkillLoader 空目录与合法 Skill

- 输入：缺失的 Skill 根目录，或包含合法 `SKILL.md` 的直接子目录。
- 预期：缺失目录返回空列表；合法 Skill 能被发现并读取完整正文。
- 检查点：`SkillInfo` 包含名称、描述、root、scripts_dir 和摘要。

### Case 2: 无效 Skill 目录

- 输入：缺少必需 frontmatter、目录名非法、frontmatter name 与目录名不一致的 Skill 目录。
- 预期：无效 Skill 不进入可用列表；frontmatter name 不一致时仍按目录名加载并记录 warning。
- 检查点：非法目录名、未知 Skill 查询、`SKILL_NAME_MISMATCH` 都会返回或记录稳定错误码。

### Case 3: SKILL.md frontmatter 解析

- 输入：合法 frontmatter、可选字段、缺失 frontmatter、缺失必需字段。
- 预期：合法内容能拆分 metadata、body 和 summary；非法内容抛出 `SkillError`。
- 检查点：`description` 是解析器必需字段；`name` 可缺失并交由 SkillLoader 用目录名兜底；可选字段只作为文本元信息保留，不进入核心业务判断。

### Case 4: 摘要截断

- 输入：超长 `SKILL.md` 正文。
- 预期：用于上下文注入的 summary 会被截断。
- 检查点：截断文本包含 `[truncated]` 标记。

### Case 5: 缺失 Skill source 配置

- 输入：workspace没有`config/config.yml`或没有`skills`分区。
- 预期：启动同步保持禁用；手动同步返回明确错误。
- 检查点：错误信息提示运行 `zcagent init`。

### Case 6: source 命名空间

- 输入：两个 source 都包含 `review` Skill。
- 预期：`official/review` 和 `team/review` 都进入可用列表；裸 `review` 查询返回 `AMBIGUOUS_SKILL`。
- 检查点：`SkillInfo.name` 是目录名，`SkillInfo.qualified_name` 是 `source/name`，候选项完整。

### Case 7: 本地 source 同步

- 输入：配置好的本地 source 仓库根目录，包含 `skills/`、`hooks/`、`shared/`。
- 预期：完整仓库被镜像到 workspace `extends/{source}` 下，加载仍只看 `extends/{source}/skills`。
- 检查点：`SKILL.md`、`scripts/`、`references/`、仓库级辅助文件都会被复制，`.git`、`tests` 等噪声不会复制。

### Case 8: `${ZHICE_AGENT_SKILL_REPO}` 占位符

- 输入：`config.yml.skills`使用`local_dir: "${ZHICE_AGENT_SKILL_REPO}"`。
- 预期：占位符展开到技能仓库根目录。
- 检查点：运行时 Skill 根目录为 `extends/{source}/skills`。

### Case 9: 同步状态变化

- 输入：重复同步、修改 Skill、删除 Skill。
- 预期：同步结果能区分 `new`、`changed`、`removed`、`unchanged` 和 `up_to_date`。
- 检查点：目标目录内容与 source 保持一致，删除的 Skill 会从运行时目录移除。

### Case 10: 启动同步策略

- 输入：`sync.on_startup=always`、`never` 或旧值 `if_missing`。
- 预期：`always` 会刷新已有运行时 Skill；`never` 跳过；`if_missing` 被拒绝。
- 检查点：只支持设计文档确认的启动策略。

### Case 11: 配置边界与错误

- 输入：未知 source 名、`sync=false`、旧字段 `enabled`/`type`/`path`/`skills_subdir`/`target_type`、空 source 目录、workspace 外 extends_dir。
- 预期：返回结构化失败、跳过或抛出配置错误。
- 检查点：runtime 写入目录必须留在 workspace 内，旧字段不会被悄悄兼容。
