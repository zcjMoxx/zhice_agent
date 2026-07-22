# 智策 Agent 第五部分详细设计文档：Skill 同步、加载与执行

> 关联规范：`AGENTS.md`
>
> 文档类型：阶段活文档。本文档始终按当前代码和当前阶段口径维护。
>
> 承接文档：`docs_design/zhice-agent-part4-exec-tool-design.md`
>
> 当前状态：本文档已按当前落地代码更新。Skill source 不再先进入 `cache_dir` 再复制到 `skills/`，而是直接同步到 workspace 的 `extends/{source}`，并从 `{source}/skills/*` 加载和执行。
>
> 相关设计记录：`docs_design/2026-06-30-skill-source-namespace-design.md` 记录了 Skill source schema 与同名 Skill 命名空间的收敛背景；本文中的配置和数据流已同步为当前口径。

---

## 1. 背景

第五部分的目标不是把业务逻辑写进 `AgentLoop`，而是给 ZhiCe-Agent 增加一层可配置、可同步、可审计的 Skill 能力。

本轮确定后的关键边界是：

- 项目根目录 `skill_repo/` 是当前阶段的官方通用 Skill 本地源仓库，应该纳入版本管理。
- 运行时不直接扫描项目根目录 `skill_repo/skills/`，而是扫描 workspace 里的扩展仓库目录。
- 一个 Skill source 对应一个运行时目录：`${ZHICE_AGENT_WORKSPACE}/extends/{source.name}`。
- Skill 包保持参考项目风格：`{source_repo}/skills/{skill_name}/SKILL.md`，并可带 `scripts/`、`references/`、`assets/`。
- 本地 source 会把 `local_dir` 指向的整个技能仓库根目录镜像到 `extends/{source.name}`。
- git source 会直接 clone/fetch 到 `extends/{source}`，不再使用单独的 `cache_dir`。
- 第一版只做启动时一次同步和手动同步，不做后台轮询，避免网关启动后持续刷“拉取仓库”日志。

实际链路：

```text
configured Skill source
  -> SkillSourceSync 同步到 ${ZHICE_AGENT_WORKSPACE}/extends/{source}
  -> SkillLoader 扫描 extends/{source}/skills/{skill_name}
  -> ContextBuilder 注入 Skill 摘要
  -> load_skills 读取完整 SKILL.md
  -> exec 按 SKILL.md 示例执行 scripts/{entry}.py
```

---

## 2. 目标

1. 建立 `SkillProvider` 协议，让上下文构建和工具调用只依赖接口。
2. 支持从配置的 Skill source 同步完整 Skill 包到 workspace `extends`。
3. 支持启动时按配置自动同步一次。
4. 支持 `/skills` 查看已发现 Skill。
5. 支持 `/skills sync` 手动刷新并同步配置来源。
6. 支持 `/skills sync --verbose` 展开同步明细。
7. 支持 `sync_skills` 工具，让后续“更新一下技能仓库”这类自然语言可以走受控工具调用。
8. 支持 `load_skills` 工具读取完整 `SKILL.md`。
9. 支持通过 `exec` 按 `SKILL.md` 示例执行 Skill 自带脚本。
10. 保持 `AgentLoop` 只负责通用 LLM/tool/session 循环，不识别具体 Skill 业务。

---

## 3. 范围边界

本阶段包含：

- `agent/protocols/skill.py`
- `agent/skills/markdown.py`
- `agent/skills/loader.py`
- `agent/skills/sync.py`
- `agent/tools/skill.py`
- `config/skill_sources.example.yml`
- `skill_repo/skills/README.md`
- CLI `/skills` 与 `/skills sync`
- Tool：`load_skills`、`sync_skills`
- 脚本执行复用通用 `exec`
- 单元测试覆盖同步、加载、工具、CLI、上下文注入和 AgentLoop 工具链路

本阶段不包含：

- 后台轮询同步。
- Skill 市场。
- 多层 Skill 覆盖优先级。
- Skill 签名校验。
- 自动安装 Skill 依赖。
- 多用户权限隔离。
- 通过自然语言执行任意斜杠命令。
- 管理员诊断类“查看项目代码、终端日志并判断问题原因”的 Tool 或 Skill。

管理员诊断能力后续更适合做成受权限控制的 Tool，再按需配 Skill 工作流说明。

---

## 4. 目录模型

### 4.1 Source 目录

当前项目仓库里的官方 Skill 源：

```text
skill_repo/
  skills/
    README.md
    {skill_name}/
      SKILL.md
      scripts/
      references/
      assets/
```

如果后续官方 Skill 独立成 git 仓库，只需要改 `${ZHICE_AGENT_WORKSPACE}/config/skill_sources.yml`，让 source 指向真实 git 仓库。

### 4.2 Runtime 目录

运行工作区：

```text
${ZHICE_AGENT_WORKSPACE}/extends/
  zhice-official/
    skills/
      {skill_name}/
        SKILL.md
        scripts/
```

`extends/{source}` 是 source 仓库落盘目录。SkillLoader 固定从每个启用 source 的 `skills/` 扫描 Skill 包。这样本地 source 和 git source 的运行形态一致，也更接近参考项目的 `extends/{repo}/skills` 模型。

---

## 5. 配置设计

模板文件：`config/skill_sources.example.yml`

运行文件：`${ZHICE_AGENT_WORKSPACE}/config/skill_sources.yml`

当前模板：

```yaml
sync:
  # Whether zcagent syncs Skill sources on startup.
  # Values: never | always
  on_startup: always

  # Sync log policy.
  # changes_only: print only changed or failed syncs.
  # always: print every sync summary, including up-to-date sources.
  log: changes_only

  # Reserved background sync structure. Current code does not start polling.
  background:
    enabled: false
    interval_seconds: 0

sources:
  - name: zhice-official
    # Source name. Used in logs, namespaces, and commands, for example:
    # /skills sync zhice-official

    # Whether this source participates in sync.
    sync: true

    # Local source repository root. Preferred when it exists.
    # Repository layout is fixed as local_dir/skills/{skill_name}/SKILL.md.
    local_dir: "${ZHICE_AGENT_SKILL_REPO}"

    # Remote fallback when local_dir is absent or missing.
    git_url: "https://example.com/skills.git"

    # Git branch. Defaults to master.
    target: "master"
```

字段说明：

- `extends_dir`：代码内部默认使用 `${ZHICE_AGENT_WORKSPACE}/extends`；不再出现在默认模板里。
- `sync.on_startup`：启动同步策略，只支持 `never`、`always`。
- `sync.background.enabled`：预留后台定时同步开关；当前代码不启动轮询。
- `sync.background.interval_seconds`：预留后台同步间隔秒数。
- `sync.log`：同步日志策略；`changes_only` 只打印变化或失败，`always` 每次都打印摘要。
- `sources[].name`：source 名称，用于日志、命名空间、`/skills sync <name>` 和 `source/name` 限定名。
- `sources[].sync`：是否同步该 source。
- `sources[].local_dir`：本地技能仓库根目录。存在时优先使用，不参与展示、调用或运行时目录命名。
- `sources[].git_url`：远端 git 技能仓库兜底地址。
- `sources[].target`：Git branch，默认 `master`。

git source 示例：

```yaml
sources:
  - name: zhice-official
    sync: true
    git_url: "https://github.com/your-org/zhice-official-skills.git"
    target: "master"
```

`zcagent init` 默认从仓库模板补齐 `${ZHICE_AGENT_WORKSPACE}/config/skill_sources.yml`。重复执行 init 时，已有配置会保留，只有 `--force` 会覆盖已有文件。

如果启动时缺少该配置，CLI 打印：

```text
缺少 `skill_sources.yml` 表示 Skill source 未启用，CLI/Gateway 静默使用空 SkillLoader；文件存在但非法或配置要求同步而失败时才输出结构化 warning。
```

这表示当前没有配置 Skill source，自动同步被跳过。该缺失不阻断基础聊天，因为 Skill source 属于可选扩展能力；workspace、prompts、LLM endpoint 仍属于聊天必需配置，缺失或非法时直接报错。

---

## 6. 模块设计

### 6.1 `agent/skills/sync.py`

核心类型：

- `SkillSource`
- `SkillSyncSettings`
- `SkillSourceResult`
- `SkillSyncResult`
- `SkillSourceSync`

职责：

- 读取并校验 `${ZHICE_AGENT_WORKSPACE}/config/skill_sources.yml`。
- 展开 `${ZHICE_AGENT_WORKSPACE}`、`${ZHICE_AGENT_SKILL_REPO}` 和环境变量占位符。
- 校验 `extends_dir` 必须在 workspace 内。
- 校验 source 名称唯一。
- 对本地 source，把 `local_dir` 指向的完整技能仓库根目录镜像到 `extends/{source.name}`。
- 对 git source，直接 clone/fetch 到 `extends/{source}` 并 checkout/reset 到配置 target。
- 返回按 source 组织的结构化结果：`synced`、`up_to_date`、`skipped`、`failed`。

安全边界：

- `extends_dir` 必须位于 workspace 内。
- `sync_skills` 只能选择已配置 source 名称，不能传入任意 git URL。
- git 操作使用 `subprocess.run(["git", ...], shell=False)`。
- 本地同步会忽略 `.git`、`.gitignore`、`tests`、`test`、`__pycache__`、`.pytest_cache`、`.ruff_cache`、`.mypy_cache`、`.venv`、`node_modules` 和 `.pyc`。

### 6.2 `agent/skills/loader.py`

职责：

- 支持扫描一个或多个 Skill root。
- 当前 CLI 传入的是每个启用 source 的 `extends/{source}/skills`。
- 每个 Skill 必须有 `SKILL.md`。
- frontmatter 只要求 `description`；`name` 缺失或与目录名不一致时，使用目录名作为 canonical name 并记录 warning。
- 返回带 `source`、`name`、`qualified_name` 的 `SkillInfo`。
- 记录无效 Skill 的 `load_errors`，但不阻断 CLI 启动。

`SkillLoader` 不负责同步，也不执行脚本。

### 6.3 Skill 脚本执行约定

本阶段不新增独立脚本执行器。LLM 使用 Skill 时先通过 `load_skills` 读取完整 `SKILL.md`，再按文档里的示例调用通用 `exec` 执行 workspace 内脚本。

约定：

- Skill 脚本建议位于 `${ZHICE_AGENT_WORKSPACE}/extends/{source}/skills/{skill_name}/scripts/`。
- `SKILL.md` 必须写清楚可执行命令、参数 JSON、返回格式、错误码和边界情况。
- 脚本推荐通过 `--params '{JSON}'` 接收输入。
- 脚本推荐 stdout 最后一行输出结构化 JSON。
- 脚本禁止 import `agent.*`。
- 安全边界由 `exec` 的 workspace guard、命令策略、超时和输出截断统一承担。

### 6.4 `agent/tools/skill.py`

Skill 专属工具：

```text
load_skills
sync_skills
```

`load_skills` 用于读取完整 `SKILL.md`。

`sync_skills` 用于同步配置中的 Skill source：

```json
{
  "source": "zhice-official"
}
```

`source` 可省略，省略时同步所有配置 source。`sync_skills` 不接受临时来源地址或任意命令。

工具返回 source 级 JSON，例如：

```json
{
  "status": "success",
  "sources": [
    {
      "name": "zhice-official",
      "status": "synced",
      "skills": 3,
      "new": ["file-summary"],
      "changed": ["code-review"],
      "removed": [],
      "unchanged": ["csv-cleanup"],
      "message": "",
      "error": ""
    }
  ]
}
```

### 6.5 `agent/cli.py`

启动流程：

```text
load_config
  -> ensure_dirs
  -> SkillSourceSync.sync_on_startup()
  -> SkillLoader(skill_sync.skill_roots())
  -> ContextBuilder(skills=skill_loader)
  -> create_default_tool_registry(..., skill_sync=skill_sync)
  -> AgentLoop
```

CLI 命令：

```text
/skills
/skills sync
/skills sync --verbose
/skills sync zhice-official
/skills sync --verbose zhice-official
```

默认输出按 source 展示：

```text
skills synced: zhice-official (3 skills, 1 new, 2 changed)
skills up to date: my-local-skills (2 skills)
skills skipped: zhice-official (sync=false)
skills failed: zhice-official (git fetch failed)
```

`--verbose` 展开 Skill 名称：

```text
skills synced: zhice-official (3 skills, 1 new, 2 changed)
  new: file-summary
  changed: code-review, csv-cleanup
  unchanged: 0
```

普通聊天启动时自动同步保持安静；gateway 启动或后续显式入口可以按 `sync.log` 打印摘要。`gateway --check` 只检查配置，不做同步。

`/skills` 只展示 Skill 名称和短描述。`category`、`readonly` 不作为核心 frontmatter 字段，也不作为 CLI、上下文摘要或 tool metadata 的展示字段。兜底安全边界放在 `exec`、workspace guard 和 `SKILL.md` 行为说明里。

---

## 7. 数据流

### 7.1 启动同步

```mermaid
flowchart TD
    A["zcagent"] --> B["load_config"]
    B --> C["config.ensure_dirs"]
    C --> D["SkillSourceSync.load"]
    D --> E{"sync.on_startup"}
    E -->|"never"| F["skip"]
    E -->|"always"| G["sync configured sources"]
    G --> H["extends/{source}/skills"]
    H --> I["SkillLoader scans configured skill roots"]
```

### 7.2 手动同步

```mermaid
flowchart TD
    A["/skills sync"] --> B["SkillSourceSync.sync()"]
    C["sync_skills tool"] --> B
    B --> D["local source or git source"]
    D --> E["extends/{source}/skills"]
    E --> F["return source-level result"]
```

### 7.3 Skill 使用

```mermaid
flowchart TD
    A["ContextBuilder injects summaries"] --> B["LLM decides Skill may apply"]
    B --> C["tool_call: load_skills"]
    C --> D["return full SKILL.md"]
    D --> E["LLM builds params"]
    E --> F["tool_call: exec"]
    F --> G["exec runs workspace script"]
    G --> H["ToolResult returned to AgentLoop"]
    H --> I["LLM answers user"]
```

---

## 8. 安全策略

- AgentLoop 不 import SkillLoader 或 SkillSourceSync。
- `skills/*/scripts/` 禁止 import `agent.*`。
- Skill script 通过通用 `exec` 执行。
- `sync_skills` 不接收任意 URL。
- Runtime 写入目录限制在 workspace。
- 破坏性操作仍由工具层和用户确认策略控制，不能由 Skill 自动绕过。

---

## 9. 测试方案

已覆盖的测试方向：

- Skill frontmatter 解析。
- SkillLoader 单 root、多 root、跨 source 同名、限定名查询、非法名、缺失 `SKILL.md`。
- `exec` 的 workspace guard、超时、输出截断、危险命令拦截和脚本执行。
- `load_skills` 与 `sync_skills` 工具。
- `sync_skills` 只同步配置 source，不接受任意 URL。
- `SkillSourceSync` 整仓库本地 source 镜像、unchanged、changed、removed、startup always、非法 `sync.on_startup` 值拒绝、未知 source、runtime path guard。
- ContextBuilder 注入 Skill 摘要。
- AgentLoop Fake LLM 覆盖 `load_skills -> exec` 的 Skill 使用链路。
- CLI `/skills`、`/skills sync`、`/tools`。
- `zcagent init` 能补齐 `${ZHICE_AGENT_WORKSPACE}/config/skill_sources.yml`，已有文件默认保留。

验收命令：

```bash
python -m ruff check .
python -m pytest --basetemp .tmp/pytest_basetemp
```

Windows 本机可能因为系统临时目录或 `.pytest_cache` 权限导致普通 `python -m pytest` 出现缓存 warning，因此本仓库测试推荐使用 repo 内 `.tmp` basetemp。

---

## 10. 后续演进

后续可以单独设计：

- git source 周期轮询，默认关闭，并使用 change-only 日志。
- `/skills status` 查看 source 最近同步时间和 commit。
- `sync_skills` 支持多个 source 名称。
- Skill 版本、签名和依赖声明。
- 用户私有 Skill source、source 权限过滤和优先级覆盖；这些要等用户权限系统设计清楚后再做。
- 管理员诊断 Tool：受权限控制地查看项目代码、终端日志、运行状态，再让 Agent 归因分析；它依赖 turn 运行单元、运行日志和用户权限审计，不并入当前 Skill 加载阶段。
- 自然语言“更新技能仓库”到 `sync_skills` 的意图路由。

当前阶段先保持轻量闭环：配置来源、同步到 `extends`、扫描加载、按需读取说明、通过 `exec` 受控执行脚本。
