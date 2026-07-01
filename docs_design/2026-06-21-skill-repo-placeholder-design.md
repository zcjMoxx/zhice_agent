# Skill 仓库占位符同步设计

> 说明：这是一份历史设计记录。当前代码不采用 `enabled`、`type`、`path`、`skills_subdir`、`target_type` 这组 source 字段，也不只复制 `skills/` 子目录；当前 schema 使用 `name`、`sync`、`local_dir`、`git_url`、`target`，`${ZHICE_AGENT_SKILL_REPO}` 默认指向项目根目录的 `skill_repo/`，同步层会把完整 source 仓库物化到 `${ZHICE_AGENT_WORKSPACE}/extends/{source}`，加载层再扫描其中的 `skills/`。

## 背景

Skill source 同步需要一个默认本地仓库入口，方便开发阶段在项目仓库中维护官方 Skill，同时又能在运行时把 Skill 同步到用户 workspace。

默认入口使用 `${ZHICE_AGENT_SKILL_REPO}`。它用于指向项目里的默认 Skill 仓库或 Skill 占位目录，`zcagent init` 生成的 `skill_sources.yml` 可以直接引用该占位符，避免把开发机绝对路径写进模板。

## 目标

1. 提供一个稳定的本地 Skill 仓库占位符：`${ZHICE_AGENT_SKILL_REPO}`。
2. 让默认 `skill_sources.yml` 能表达“优先用本地仓库，必要时走远端仓库”。
3. 让 Skill source 配置能够描述本地路径、Skill 包所在子目录和 Git 同步目标。
4. 避免用户手动维护项目源码路径。
5. 保持 `zcagent init` 生成的模板可读、可改。

## 范围边界

- 本文只处理 Skill 仓库占位符和默认同步配置。
- 不设计 Skill 市场、安装审批、签名校验或版本选择。
- 不改变 `SkillLoader` 的职责边界，`SkillLoader` 仍只扫描运行时 Skill 目录。
- 不让 AgentLoop 直接感知 Skill source 的同步细节。

## 配置草案

默认 source 配置如下：

```yaml
sources:
  - name: zhice-official
    enabled: true
    type: local
    path: "${ZHICE_AGENT_SKILL_REPO}"
    skills_subdir: "skills"
    git_url: "https://example.com/skills.git"
    target_type: branch
    target: "master"
```

字段含义：

- `name`：source 名称，用于日志和运行时目录命名。
- `enabled`：是否启用该 source。
- `type`：source 类型，初步支持 `local` 或 `git`。
- `path`：本地 Skill 仓库路径，可使用 `${ZHICE_AGENT_SKILL_REPO}` 占位符。
- `skills_subdir`：Skill 包所在子目录，例如 `skills`。
- `git_url`：远端仓库地址，用于没有本地仓库时拉取。
- `target_type`：Git target 类型，初步支持 branch 或 tag。
- `target`：Git branch 或 tag 名称。

## 同步思路

同步流程：

```text
skill_sources.yml
  -> expand ${ZHICE_AGENT_SKILL_REPO}
  -> choose local path or git repo
  -> find {path}/{skills_subdir}
  -> copy Skill packages into workspace runtime directory
  -> SkillLoader scans runtime skills
```

本地仓库存在时优先使用本地仓库。没有本地仓库，且配置了 `git_url` 时，再考虑从远端仓库同步。

同步后的运行时目录仍然放在 workspace 下，Agent 不直接扫描项目源码目录。

## 目录设想

项目仓库中：

```text
skills/
  README.md
  {skill_name}/
    SKILL.md
    scripts/
```

运行 workspace 中：

```text
${ZHICE_AGENT_WORKSPACE}/extends/
  zhice-official/
    skills/
      {skill_name}/
        SKILL.md
        scripts/
```

`skills_subdir` 用于把本地仓库中的 `skills/` 子目录映射到运行时目录。

## 模块设计

### 配置初始化

`zcagent init` 在生成 `${ZHICE_AGENT_WORKSPACE}/config/skill_sources.yml` 时写入默认 source 配置。模板里的本地路径使用 `${ZHICE_AGENT_SKILL_REPO}`，不写绝对路径。

### 占位符展开

`SkillSourceSync` 读取配置后展开占位符：

```text
${ZHICE_AGENT_WORKSPACE}
${ZHICE_AGENT_SKILL_REPO}
```

其他环境变量也可以按统一占位符规则解析。

### 同步执行

同步层负责：

- 判断 source 是否启用。
- 展开本地路径。
- 检查本地仓库和 `skills_subdir` 是否存在。
- 必要时执行 git clone/fetch。
- 把 Skill 包同步到 workspace 运行时目录。
- 返回 source 级同步结果。

### Skill 加载

`SkillLoader` 只接收运行时 Skill 根目录，不关心 Skill source 从哪里来。

```text
SkillSourceSync -> workspace runtime skills dir -> SkillLoader
```

## 安全边界

- runtime 写入目录必须位于 `${ZHICE_AGENT_WORKSPACE}` 内。
- `zcagent init` 不主动访问网络。
- `sync_skills` 只能同步配置中已有的 source，不接受临时任意 URL。
- git 操作使用非 shell 命令参数执行。
- 同步时忽略 `.git`、缓存目录、测试目录等开发噪声。

## 测试方案

需要覆盖：

1. `${ZHICE_AGENT_SKILL_REPO}` 能展开到项目默认 Skill 仓库路径。
2. `zcagent init` 生成的 `skill_sources.yml` 使用占位符，不写本机绝对路径。
3. 本地仓库存在时优先从本地同步。
4. 本地仓库缺失且配置 `git_url` 时走远端同步。
5. `skills_subdir` 不存在时返回结构化错误。
6. runtime 写入目录不能逃逸 workspace。
7. `sync_skills` 不能传入临时 URL。
8. 同步后 `SkillLoader` 能从 workspace runtime 目录发现 Skill。

## 验收标准

- 默认模板不含开发机绝对路径。
- 本地默认 Skill 仓库可以通过 `${ZHICE_AGENT_SKILL_REPO}` 被找到。
- 同步结果写入 workspace runtime 目录。
- Agent 启动时不直接扫描项目源码目录。
- `/skills sync` 能刷新配置 source。
- 相关单元测试通过。
