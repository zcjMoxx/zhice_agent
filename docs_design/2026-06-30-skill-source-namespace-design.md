# Skill source 命名空间与同名技能加载设计

> 关联规范：`AGENTS.md`
>
> 承接文档：`docs_design/zhice-agent-part5-skill-loader-design.md`
>
> 当前状态：已落地。当前代码已按本文收敛 Skill source schema、source/name 命名空间、整仓库同步和 source-aware SkillLoader。

## 1. 背景

当前 Skill runtime 已经从单一 `workspace/skills` 演进为 source-based 结构：

```text
config/skill_sources.yml
  -> SkillSourceSync
  -> choose local repo root or remote git repo
  -> materialize repo root to ${ZHICE_AGENT_WORKSPACE}/extends/{source}
  -> SkillLoader
  -> ContextBuilder / load_skills
  -> exec
```

目录上已经有 source/repository 分层，但加载层仍然按 `SKILL.md` frontmatter 中的 `name` 做全局唯一索引。两个不同 Skill source 如果都提供同名技能，例如：

```text
extends/
  zhice-official/
    skills/
      review/
        SKILL.md
  team/
    skills/
      review/
        SKILL.md
```

在本设计落地前，加载逻辑会把 `review` 视为全局重复，并从可用 Skill 列表中移除。这不符合多仓库 Skill 的正常使用方式。当前实现已改为以 `source/name` 作为运行时身份：不同 source 下的同名 Skill 可以同时加载，裸名只在无歧义时兼容可用。

本设计只解决不同 Skill source 之间的同名加载问题，不引入 Skill 市场、覆盖优先级或多用户隔离。

## 2. 目标

1. 允许不同 Skill source 内存在相同技能目录名。
2. 保持同一 source 内的技能唯一性由文件系统目录名天然保证。
3. 将运行时 Skill 真实身份从裸 `name` 调整为 `source/name`。
4. 保持用户层无感：用户仍然只用自然语言表达需求，不需要知道 Skill 名称或 source 名称。
5. 让模型层看到并优先使用限定名，避免同名技能调用歧义。
6. 保留旧的裸名调用兼容能力：无歧义时继续可用，有歧义时返回模型可处理的结构化错误。
7. 收敛 `skill_sources.yml` 的 source 配置语义，使本地仓库优先、远端仓库兜底、运行时落盘目录固定可预期。

## 3. 非目标

- 不实现 Skill 仓库优先级、覆盖链或自动选择最佳版本。
- 不把 source 前缀写入 `SKILL.md` 的 `name` 字段。
- 不要求用户手动输入 `source/name`。
- 不让 `AgentLoop` 识别具体 Skill 业务。
- 不实现后台轮询执行，只保留 `sync.background` 配置结构作为后续扩展入口。
- 不新增市场安装、签名校验或依赖自动安装。
- 不自动修改不规范的 `SKILL.md` 文件。

## 4. 核心命名规则

### 4.1 Canonical identity

运行时采用以下身份规则：

```text
canonical skill name = 技能外层目录名
canonical skill id = source/技能外层目录名
```

示例：

```text
extends/zhice-official/skills/review/SKILL.md
```

对应：

```text
source = zhice-official
name = review
qualified_name = zhice-official/review
```

### 4.2 frontmatter name 的定位

`SKILL.md` frontmatter 中的 `name` 不再覆盖目录身份。

推荐规范仍然是：

```text
skills/{skill_name}/SKILL.md
frontmatter.name == {skill_name}
```

但如果出现不一致：

```text
skills/code-review/SKILL.md
frontmatter.name: review
```

运行时仍以外层目录 `code-review` 作为 canonical skill name，并记录元数据告警：

```text
code = SKILL_NAME_MISMATCH
directory_name = code-review
frontmatter_name = review
canonical_name = code-review
```

该情况视为 Skill 包元数据不规范，而不是同仓库正常重名能力。第一版只做加载兜底和告警，不自动改源文件。

### 4.3 source 标识

`source` 固定使用 `skill_sources.yml` 中配置的 `sources[].name`。该名称是逻辑 source id，用于日志、`/skills sync <source_name>`、模型可见的 `qualified_name` 和运行时命名空间。

`local_dir` 不再表示运行时落盘目录名，而表示本地技能仓库根目录。它只参与同步来源选择，不参与展示、调用和 Skill 命名空间。

运行时仓库落盘目录固定为：

```text
${ZHICE_AGENT_WORKSPACE}/extends/{sources[].name}
```

SkillLoader 只从该仓库下的 `skills/` 识别 Skill：

```text
${ZHICE_AGENT_WORKSPACE}/extends/{sources[].name}/skills/{skill_name}/SKILL.md
```

这样 source id、运行时目录和模型侧限定名保持一致。

### 4.4 skill_sources.yml 结构

新版模板：

```yaml
sync:  # skill source sync behavior
  on_startup: always        # never | always
  log: changes_only         # changes_only | always
  background:               # reserved
    enabled: false
    interval_seconds: 0

sources:
  - name: zhice-official    # source id
    sync: true              # default true
    local_dir: "${ZHICE_AGENT_SKILL_REPO}"  # local repo root, preferred when exists
    git_url: "https://example.com/skills.git"  # remote repo fallback
    target: "master"        # branch, default master
```

字段收敛规则：

- 默认模板只表达 `sources[].name`、`sources[].sync`、`sources[].local_dir`、`sources[].git_url`、`sources[].target`。
- `local_dir` 固定表示本地技能仓库根目录。
- 技能仓库结构固定为 `skills/{skill_name}/SKILL.md`。
- `target` 固定表示 Git branch，默认 `master`。
- 不在模板中暴露 `extends_dir`，代码默认使用 `${ZHICE_AGENT_WORKSPACE}/extends`；仍可作为内部配置能力保留，但不进入默认心智模型。
- `sync.on_startup` 只支持 `never` 和 `always`，删除 `if_missing`。
- `sync.background` 只保留结构，当前不启动后台轮询。

## 5. 模块设计

### 5.1 SkillInfo

扩展 `SkillInfo`：

```python
@dataclass(frozen=True)
class SkillInfo:
    source: str
    name: str
    qualified_name: str
    description: str
    root: Path
    skill_file: Path
    scripts_dir: Path
    summary: str
    metadata: dict[str, Any]
```

兼容关系：

- `name` 表示 canonical skill name，即外层目录名。
- `qualified_name` 表示 `source/name`。
- `metadata["frontmatter_name"]` 保留原始 frontmatter name。
- `metadata["name_matches_directory"]` 继续保留，用于调试和 lint。

### 5.2 SkillSourceSync

`SkillSourceSync` 需要先完成配置 schema 收敛，再把 source-aware root 交给 `SkillLoader`。

配置解析规则：

- `sync.on_startup` 只接受 `never`、`always`。
- `sync.background` 解析为预留结构：`enabled` 和 `interval_seconds`。
- `sources[].name` 必填且唯一。
- `sources[].sync` 可选，默认 `true`。
- `sources[].local_dir` 可选，表示本地技能仓库根目录。
- `sources[].git_url` 可选，表示远端 Git 技能仓库。
- `sources[].target` 可选，表示 Git branch，默认 `master`。
- 至少需要配置可用的 `local_dir` 或 `git_url`。

同步来源选择规则：

```text
if local_dir is configured and exists:
    source_repo_root = local_dir
else:
    source_repo_root = clone_or_fetch(git_url, branch=target or "master")

runtime_repo_root = extends_dir / source.name
materialize source_repo_root -> runtime_repo_root
skill_package_root = runtime_repo_root / "skills"
```

说明：

- `local_dir` 是本地仓库根目录，不是 `skills/` 目录，也不是单个 Skill 目录。
- 本地仓库和远端仓库都必须遵守统一结构：`repo_root/skills/{skill_name}/SKILL.md`。
- 同步层物化整个技能仓库，而不是只复制 `skills/` 子目录。这样仓库级 `hooks/`、`config/`、`shared/`、`assets/` 等运行时辅助文件可以随 Skill 一起落盘。
- 本地仓库同步时应排除开发和 VCS 噪声，例如 `.git`、`.gitignore`、`tests`、`test`、`__pycache__`、`.pytest_cache`、`.ruff_cache`、`.mypy_cache`、`.venv`、`node_modules`。
- 远端 Git 仓库可以直接 clone/fetch 到 `extends/{source.name}`；加载时仍只从 `extends/{source.name}/skills` 识别 Skill。
- 运行时可加载目录固定为 `extends/{source.name}/skills`。
- `sync_skills` 仍然只能按已配置的 `sources[].name` 同步，不接受临时 URL 或任意路径。

`SkillSourceSync.skill_roots()` 当前返回 source-aware root 数据结构：

```python
@dataclass(frozen=True)
class SkillRoot:
    source: str
    root: Path
```

并提供：

```python
def skill_roots(self) -> list[SkillRoot]:
    ...
```

其中 `source` 来自 `skill_sources.yml` 的 `sources[].name`，`root` 指向运行时可扫描目录 `extends/{source.name}/skills`。`SkillRoot` 当前只作为 `agent/skills/sync.py` 和 `agent/skills/loader.py` 之间的内部结构，不放入 `agent/protocols/skill.py`。

### 5.3 SkillLoader

`SkillLoader` 内部索引调整为：

```python
by_qualified_name: dict[str, SkillInfo]
by_name: dict[str, list[SkillInfo]]
load_errors: list[dict[str, Any]]
```

扫描规则：

1. 遍历每个 `SkillRoot(source, root)`。
2. 每个 root 下只扫描直接子目录。
3. 技能 canonical name 取子目录名。
4. frontmatter `name` 缺失或不一致时，不改变 canonical name。
5. 同一 source 内相同目录名由文件系统保证不会同时存在；大小写冲突等异常按加载错误记录。
6. 不同 source 的同名 canonical name 都正常进入 `list_skills()`。

查询规则：

```text
get_skill("zhice-official/review")
  -> 按 qualified_name 精确查找

get_skill("review")
  -> 如果只有一个 source 有 review，兼容返回
  -> 如果多个 source 有 review，返回 AMBIGUOUS_SKILL
```

`AMBIGUOUS_SKILL` 面向模型工具链，不是直接让用户处理的错误。

### 5.4 SkillProvider

协议保持简洁，但需要支持限定名：

```python
class SkillProvider(Protocol):
    def list_skills(self) -> list[SkillInfo]: ...
    def get_skill(self, name: str, source: str | None = None) -> SkillInfo: ...
    def get_skill_body(self, name: str, source: str | None = None) -> str: ...
```

`name` 支持两种形式：

```text
review
zhice-official/review
```

当 `source` 参数存在时，优先使用 `source/name` 精确查找。

### 5.5 load_skills tool

工具 schema 增加可选 `source`，保留 `name`：

```json
{
  "name": "review",
  "source": "zhice-official"
}
```

也允许模型传：

```json
{
  "name": "zhice-official/review"
}
```

推荐模型优先使用 `qualified_name`。当裸名歧义时，工具返回：

```json
{
  "code": "AMBIGUOUS_SKILL",
  "message": "Skill name is ambiguous. Use a qualified skill name.",
  "skill": "review",
  "candidates": [
    "zhice-official/review",
    "team/review"
  ]
}
```

AgentLoop 继续按现有机制把工具结果回填给模型，由模型二次选择。只有候选描述无法区分时，模型才需要向用户确认。

### 5.6 ContextBuilder

Skill 摘要注入改为展示 `qualified_name`：

```text
- `zhice-official/review`: ...
- `team/review`: ...
```

这样模型第一次调用时就能直接使用限定名，减少 `AMBIGUOUS_SKILL` 纠偏次数。

用户自然语言输入不变，用户不需要看到或理解这些内部标识。

### 5.7 CLI /skills

`/skills` 只展示限定名和描述，不展示短 alias 状态。短 alias 只是系统内部兼容机制：无重名时可继续使用裸名，有重名时必须使用 `source/name`。

```text
zhice-official/review    Review code and report issues.
team/review              Review project-specific documents.
```

如果 frontmatter name 与目录名不一致，作为 warning 输出：

```text
skipped/warn skill [SKILL_NAME_MISMATCH] ...: frontmatter name differs from directory name; directory name is used
```

第一版可只保留 warning，不阻断加载。

## 6. 数据流

```text
skill_sources.yml
  -> parse sources
  -> choose source repo root from local_dir or git_url
  -> materialize repo_root to extends/{source}
  -> SkillRoot(source="zhice-official", root=".../extends/zhice-official/skills")
  -> SkillLoader scans direct child dirs
  -> SkillInfo(source="zhice-official", name="review", qualified_name="zhice-official/review")
  -> ContextBuilder injects qualified summaries
  -> model calls load_skills with qualified name
  -> LoadSkillsTool resolves exact SkillInfo
  -> model reads SKILL.md and calls exec when needed
```

## 7. 错误码与兜底行为

| 场景 | 行为 | code |
| --- | --- | --- |
| `source/name` 不存在 | 返回未知 Skill | `UNKNOWN_SKILL` |
| 裸 `name` 不存在 | 返回未知 Skill | `UNKNOWN_SKILL` |
| 裸 `name` 命中多个 source | 返回候选项给模型 | `AMBIGUOUS_SKILL` |
| frontmatter name 缺失 | 用目录名作为 canonical name，记录 warning | `SKILL_NAME_MISMATCH` 或 `MISSING_SKILL_FIELD` warning |
| frontmatter name 与目录名不一致 | 用目录名作为 canonical name，记录 warning | `SKILL_NAME_MISMATCH` |
| source root 不是目录 | 跳过该 root 并记录错误 | `INVALID_PARAM` |
| `SKILL.md` 无法读取 | 跳过该 Skill 并记录错误 | `SKILL_READ_ERROR` |

说明：

- `SKILL_NAME_MISMATCH` 不应导致普通聊天失败。
- `AMBIGUOUS_SKILL` 是模型侧纠偏信号，不是用户必须处理的交互错误。

## 8. 兼容策略

1. 旧的单 source 场景继续支持 `load_skills({"name": "review"})`。
2. 已有 `SKILL.md` frontmatter name 与目录名一致的 Skill 无需迁移。
3. `skill_sources.yml` 使用当前轻量字段：`name`、`sync`、`local_dir`、`git_url`、`target`。
4. 历史配置文件需要按当前模板重建或手动迁移；新设计文档不再维护历史配置映射表。
5. `sync_skills` 行为不变，仍按 source 名称同步已配置 source。
6. Context 中展示的 Skill 名称从裸名变为限定名，这属于模型侧提示优化，不改变用户输入方式。
7. 如果外部测试直接断言 `SkillInfo.name` 来自 frontmatter，需要改为断言目录名；frontmatter 原值从 metadata 读取。

## 9. 变更文件

落地实现涉及：

- `agent/protocols/skill.py`
- `agent/skills/loader.py`
- `agent/skills/sync.py`
- `agent/tools/skill.py`
- `agent/context.py`
- `agent/cli.py`
- `prompts/skills_intro.md`
- `tests/unit_test/skills/test_skill_loader.py`
- `tests/unit_test/skills/test_skill_sync.py`
- `tests/unit_test/tools/test_skill_tools.py`
- `tests/unit_test/context_builder/test_context_builder_skills.py`
- `tests/unit_test/agent_loop/test_agent_loop_skill_tools.py`
- `tests/unit_test/skills/test_case.md`
- 同步更新 `README.md` 和 `skill_repo/skills/README.md`

## 10. 测试方案

新增或更新以下测试：

1. 配置 schema：
   - 模板包含当前 source 字段：`name`、`sync`、`local_dir`、`git_url`、`target`。
   - `sync.on_startup` 只接受 `never`、`always`。
   - `sync.background.enabled` 和 `sync.background.interval_seconds` 可解析，但不启动后台轮询。
   - `target` 缺省为 `master`。

2. source 同步来源选择：
   - `local_dir` 存在时，从本地仓库根目录同步，不访问 `git_url`。
   - `local_dir` 未配置或不存在时，从 `git_url` clone/fetch，并 checkout `target` 分支。
   - 本地和远端都先物化整个仓库到 `extends/{source.name}`。
   - `hooks/`、`config/`、`shared/` 等仓库级运行时文件会随仓库落盘。
   - 开发和 VCS 噪声不会被复制到运行时目录。
   - SkillLoader 从 `extends/{source.name}/skills/{skill_name}` 识别 Skill。

3. 单 source 单 Skill：
   - 目录名 `demo`，frontmatter name `demo`。
   - `list_skills()` 返回 `name=demo`、`qualified_name=official/demo`。

4. 跨 source 同名：
   - `official/review` 和 `team/review` 同时存在。
   - `list_skills()` 返回两个 Skill。
   - `get_skill("official/review")` 精确命中。
   - `get_skill("team/review")` 精确命中。
   - `get_skill("review")` 返回 `AMBIGUOUS_SKILL`，metadata 含 candidates。

5. 裸名兼容：
   - 只有一个 `demo` 时，`get_skill("demo")` 继续命中。

6. frontmatter name 与目录名不一致：
   - 目录名 `code-review`，frontmatter name `review`。
   - canonical name 是 `code-review`。
   - metadata 保留 `frontmatter_name=review`。
   - load_errors 或 warning 中记录 `SKILL_NAME_MISMATCH`。

7. `load_skills` 工具：
   - 支持 `{"name": "official/review"}`。
   - 支持 `{"source": "official", "name": "review"}`。
   - 裸名歧义时返回 JSON candidates。

8. ContextBuilder：
   - 注入摘要使用 `qualified_name`。

9. CLI `/skills`：
   - 输出限定名。
   - 不输出短 alias 状态。
   - 不因为跨 source 同名隐藏 Skill。

建议验证命令：

```bash
python -m ruff check .
python -m pytest --basetemp .tmp/pytest_skill_namespace
```

## 11. 验收标准

- 不同 source 下同名目录的 Skill 都能被发现并展示。
- 模型上下文中展示的是 `source/name`，模型可直接用限定名调用 `load_skills`。
- 裸名在无歧义时保持兼容。
- 裸名在有歧义时返回结构化 `AMBIGUOUS_SKILL`，候选项完整。
- frontmatter name 不再覆盖目录身份。
- frontmatter name 与目录名不一致不会导致加载成错误身份。
- `skill_sources.yml` 使用新版轻量结构，只暴露当前 source 字段。
- `local_dir` 表示本地技能仓库根目录，存在时优先；`git_url` 是远端兜底；`target` 是分支名，默认 `master`。
- 同步层物化整个技能仓库到 `extends/{source.name}`，不是只复制 `skills/`。
- `/skills` 只展示 `qualified_name`，不展示短 alias 状态。
- `AgentLoop` 不增加 Skill 业务判断。

## 12. 已确认结论

1. 已确定：`source` 固定使用 `sources[].name`；`local_dir` 表示本地技能仓库根目录，不参与展示、调用和模型命名空间。
2. 已确定：frontmatter name 缺失或与目录名不一致时，使用目录名作为 canonical name，允许加载并记录 warning。
3. 已确定：`/skills` 只显示 `qualified_name`，不显示短 alias 状态；短 alias 只作为系统内部兼容逻辑。
4. 已确定：`SkillRoot` 第一版只作为 sync/loader 内部类型，不放入 `agent/protocols/skill.py`。
