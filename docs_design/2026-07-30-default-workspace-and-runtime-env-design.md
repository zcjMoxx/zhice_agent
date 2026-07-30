# ZhiCe-Agent 默认 Workspace 与 Runtime Env 统一设计记录

> 日期：2026-07-30
>
> 状态：已实现并验证
>
> 归属：Part 17 配置与部署收敛

## 1. 背景

当前源码启动先读取项目仓库 `config/.env`，从中获取 `ZHICE_AGENT_WORKSPACE`，再读取 `${ZHICE_AGENT_WORKSPACE}/config/config.yml` 和 `models.json`。这避免了技术上的循环查找，但把“workspace 定位”和“运行环境变量”混在了项目目录，与运行配置统一进入 workspace 的规则不一致；Docker 又单独固定 `/opt/zhice`，形成第二套默认语义。

## 2. 目标

1. Windows、Linux 和 Docker 使用同一条默认规则：`Path.home() / ".zhice"`。
2. 保留 `--workspace` 和 `ZHICE_AGENT_WORKSPACE` 显式覆盖。
3. 运行环境变量统一位于 `${workspace}/config/.env`。
4. workspace 内 `.env` 不再配置 `ZHICE_AGENT_WORKSPACE`，避免自指和路径分裂。
5. Docker 使用专用非 root `zhice` 用户，默认 workspace 自然成为 `/home/zhice/.zhice`。
6. 保留项目 `config/.env` 的短期 legacy fallback，避免现有本地环境立即失效；新环境不再依赖它。

## 3. 路径与优先级

Workspace 解析：

```text
CLI --workspace
  > process ZHICE_AGENT_WORKSPACE
  > Path.home() / ".zhice"
```

Dotenv 加载：

```text
显式 --env-file
  > 已解析 workspace/config/.env
  > 项目 config/.env（legacy fallback，仅迁移兼容）
```

显式 `--env-file` 继续允许提供 `ZHICE_AGENT_WORKSPACE`，用于一次性启动和兼容部署工具；自动发现的 `${workspace}/config/.env` 不允许反向覆盖 workspace 根。

## 4. 目录

```text
Windows: C:\Users\<user>\.zhice
Linux:   /home/<user>/.zhice
Docker:  /home/zhice/.zhice
```

内部结构完全一致：

```text
.zhice/
+-- config/
|   +-- .env
|   +-- config.yml
|   +-- models.json
+-- prompts/
+-- contexts/
+-- state/
+-- logs/
+-- extends/
```

## 5. Docker

Docker runtime 创建专用 `zhice` 用户及 `/home/zhice` home，不再声明 `ZHICE_AGENT_WORKSPACE=/opt/zhice`。程序使用相同的 `Path.home() / ".zhice"` 默认逻辑。私有配置复制到 `/home/zhice/.zhice/config/`，运行数据 volume 同步迁移到该根下。

`HOME=/home/zhice` 是容器用户主目录声明，不是 ZhiCe-Agent workspace 配置；workspace 仍由通用代码从 home 派生。

## 6. 兼容与迁移

- 已显式设置 `ZHICE_AGENT_WORKSPACE` 的环境继续使用原路径。
- 已使用 `--workspace` 的命令继续保持最高优先级。
- 项目 `config/.env` 暂时作为 legacy fallback；当默认 workspace 已有 `config/.env` 时不再读取项目文件。
- `zcagent init --write-env` 改为生成 `${workspace}/config/.env`，且模板不包含 `ZHICE_AGENT_WORKSPACE`。
- `deploy/.env` 删除本地 Windows workspace 行；Docker image 不依赖该行定位目录。

## 7. 变更文件

```text
agent/config.py
agent/cli.py
config/.env.example
deploy/Dockerfile
deploy/docker-compose.yml
deploy/README.md
deploy/scripts/*
tests/unit_test/config/*
tests/unit_test/cli/*
tests/unit_test/deploy/*
README.md
docs_design/zhice-agent-overall-design.md
docs_design/zhice-agent-part17-reliability-diagnostics-deployment-design.md
```

## 8. 测试与验收

1. 无任何覆盖时，Windows/Linux 使用 `Path.home()/.zhice`。
2. 环境变量和 CLI workspace 继续覆盖默认值。
3. 自动加载 `${workspace}/config/.env`，但忽略其中的 `ZHICE_AGENT_WORKSPACE`。
4. 显式 `--env-file` 仍可提供 workspace。
5. legacy 项目 `.env` 只在新位置不存在时回退。
6. `init --write-env` 写入 `config/.env` 且不包含 workspace locator。
7. Docker/Compose/脚本全部使用 `/home/zhice/.zhice`，不再出现 `/opt/zhice` 或 `/home/node/.zhice`。
8. 私有 deploy 文件继续被 Git 忽略。
9. Ruff、全量 Python、前端与 deploy 静态检查通过。

实施验证结果：

- `python -m ruff check .`：通过。
- Python 全量测试：`791 passed, 1 skipped`。
- Docker Compose 配置校验：通过。
- PowerShell 部署脚本 parser 校验：通过。
- 当前 Docker Desktop daemon 不可用，因此未执行真实 image build 和容器 smoke；该项保留为生产环境验收，不影响本次默认 workspace 与 runtime env 代码落地结论。
