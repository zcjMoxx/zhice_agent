# Skill 内置仓库环境变量覆盖设计

## 背景

`config.yml` 使用 `local_dir: "${ZHICE_AGENT_SKILL_REPO}"` 指向内置 Skill source 仓库。当前同步器虽然先读取进程环境变量，但随后会用代码推导出的默认路径覆盖同名变量，导致 `.env` 中显式配置的 `ZHICE_AGENT_SKILL_REPO` 不生效，行为与普通环境变量的直觉不一致。

## 目标

- 显式传给 `SkillSourceSync` 的 `skill_repo` 保持最高优先级。
- 未显式传参时，优先使用进程环境中的 `ZHICE_AGENT_SKILL_REPO`。
- 环境变量缺失或为空时，自动定位随项目或镜像提供的 `skill_repo/`。
- 本地和 Docker 使用相同的优先级规则，仅绝对路径随运行环境变化。

## 范围边界

- 不改变 `sources[].local_dir`、`git_url`、`target` 的既有含义。
- 不允许在 `ZHICE_AGENT_SKILL_REPO` 中填写 Git URL；它仍然只表示本地 source 仓库根目录。
- 不改变 source 到 `${ZHICE_AGENT_WORKSPACE}/extends/{source}` 的同步结构。

## 模块设计

`SkillSourceSync.__init__` 统一解析内置仓库路径，优先级为：

```text
显式 skill_repo 参数
  -> ZHICE_AGENT_SKILL_REPO 进程环境变量
  -> 根据 agent/skills/sync.py 位置推导项目 skill_repo/
```

空字符串按未配置处理。占位符展开继续使用已经解析完成的 `self.skill_repo`，避免加载阶段出现第二套优先级。

## 数据流

```text
.env --bootstrap_dotenv--> os.environ
                              |
SkillSourceSync.__init__ <-----+
  -> self.skill_repo
  -> 展开 config.yml 中的 ${ZHICE_AGENT_SKILL_REPO}
  -> 同步 source 到 workspace/extends/{source}
```

## 变更文件

- `agent/skills/sync.py`：实现环境变量覆盖与空值回退。
- `tests/unit_test/skills/test_skill_sync.py`：覆盖显式参数、环境变量和默认值优先级。
- `tests/unit_test/skills/test_case.md`：补充配置优先级测试说明。
- `deploy/Dockerfile`、`tests/unit_test/deploy/*`：删除会抢占 dotenv 配置的镜像级同名环境变量并固定部署契约。
- `config/.env.example`：说明可选覆盖语义。
- `README.md`、`docs_design/zhice-agent-part5-skill-loader-design.md`：同步当前配置口径。

## 测试方案

- 环境变量指向临时 Skill source 时，占位符展开并同步该目录。
- 显式构造参数与环境变量同时存在时，显式参数胜出。
- 环境变量为空时，仍使用项目内置默认仓库。
- Dockerfile 不预设该变量，容器未配置时由同一代码路径推导 `/app/skill_repo`。
- 运行 Skill 同步单元测试和 Ruff 静态检查。

## 验收标准

- `.env` 中显式配置的本地 Skill 仓库路径能够生效。
- 未配置时，本地自动使用项目 `skill_repo/`，Docker 自动使用镜像内对应目录。
- 既有显式参数测试和默认仓库测试保持通过。
