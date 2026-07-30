# Deploy 测试说明

## 测试目标

验证 Part 17 私有镜像部署资产的静态契约，避免真实配置进入 Git、完整 workspace 被复制进镜像或生产拓扑意外扩展为多 writer。

## 用例覆盖

- 公开 Docker、Compose、说明和运维脚本齐全。
- `config/.env.example` 是唯一公开 runtime env 模板，`deploy/` 不维护第二份 `.env.example`。
- `.env`、`config.yml`、`models.json` 三个私有文件被目录级 Git ignore 覆盖。
- Deploy README 优先从当前 `${workspace}/config/` 复制三个私有文件，给出默认 Windows `.zhice` 路径，将项目 `config/.env` 标为 legacy migration，并要求任一来源的 `deploy/.env` 删除 `ZHICE_AGENT_WORKSPACE`。
- Dockerfile 从仓库构建 Python、Vue、Prompt、Skill 和微信 sidecar，只从 `deploy/` 复制三个私有文件。
- 容器以专用非 root `zhice` 用户运行，使用与本地一致的 `Path.home()/.zhice` 默认目录，并通过显式 `--env-file` 加载镜像内私有环境变量。
- Compose 只持久化 `contexts`、`state`、`logs`、`extends`。
- 云端部署要求不可变 digest，且只启动单个容器。

## 关键检查点

- 禁止 `COPY . .`。
- 禁止把 config 或完整 workspace 作为云端 volume。
- 禁止在测试中读取或输出三个真实私有文件内容。
- 文档静态测试只读取公开 `deploy/README.md`，不得探测或打开 `deploy/.env`、`config.yml`、`models.json`。
