# ZhiCe 官方 Skill

此目录是本仓库内置的官方 Skill source 的 Skill 包目录，用于本地开发时维护可复用的 ZhiCe-Agent Skill。

默认 `${ZHICE_AGENT_SKILL_REPO}` 指向项目根目录下的 `skill_repo/`，仓库结构固定为：

```text
skill_repo/
  skills/
    {skill_name}/
      SKILL.md
      scripts/
```

运行时发现和执行 Skill 时，会读取工作区中配置好的 `extends` 目录。每个 source 会同步成一个运行时仓库目录：

```text
${ZHICE_AGENT_WORKSPACE}/extends/{source}/skills/{skill_name}/SKILL.md
${ZHICE_AGENT_WORKSPACE}/extends/{source}/skills/{skill_name}/scripts/...
```

source 到工作区的同步由以下文件控制：

```text
${ZHICE_AGENT_WORKSPACE}/config/skill_sources.yml
```

仓库模板文件是 `config/skill_sources.example.yml`。
