# ZhiCe 官方 Skill

此目录是本仓库内置的官方 Skill source 的 Skill 包目录，用于本地开发时维护可复用的 ZhiCe-Agent Skill。

`${ZHICE_AGENT_SKILL_REPO}` 表示本地 Skill source 仓库根目录；显式配置时使用配置路径，缺失或为空时自动指向项目或镜像内置的 `skill_repo/`。它不能填写 Git URL。仓库结构固定为：

```text
skill_repo/
  skills/
    {skill_name}/
      SKILL.md
      scripts/  # 需要可执行脚本时提供
```

核心 Loader 只要求 `SKILL.md` frontmatter 包含 `description`；`name` 缺失时使用目录名并记录 warning。仓库内正式 Skill 仍应显式填写 `name`、`description`，并在需要执行代码时提供 `scripts/`。

运行时发现和执行 Skill 时，会读取工作区中配置好的 `extends` 目录。每个 source 会同步成一个运行时仓库目录：

```text
${ZHICE_AGENT_WORKSPACE}/extends/{source}/skills/{skill_name}/SKILL.md
${ZHICE_AGENT_WORKSPACE}/extends/{source}/skills/{skill_name}/scripts/...
```

source 到工作区的同步由统一运行配置控制：

```text
${ZHICE_AGENT_WORKSPACE}/config/config.yml  # skills 分区
```

仓库模板文件是 `config/config.example.yml`。`sources[].local_dir` 配置本地仓库，`sources[].git_url` 配置远程 Git 仓库；两者同时存在时优先使用实际存在的 `local_dir`。
