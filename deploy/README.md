# ZhiCe-Agent 私有镜像部署

`deploy/` 是本机私有配置覆盖层，不是第二份 workspace。仓库已有的 Python、Prompt、Skill、Vue 和微信 sidecar 源码由 Docker 多阶段构建直接带入镜像。

## 私有文件

普通 `zcagent init` 已默认在当前 workspace 生成 `config/.env`、`config.yml` 和 `models.json`：缺失文件会补齐，已有文件默认保留，只有 `--force` 才覆盖；旧 `--write-env` 仅作为兼容参数保留，不再是生成 env 的前提。

构建镜像前，先从当前 workspace 准备以下三个被 Git 忽略的文件：

```text
deploy/.env
deploy/config.yml
deploy/models.json
```

- `.env` 从 `${workspace}/config/.env` 复制；它只保存运行环境变量。
- `config.yml` 从 `${workspace}/config/config.yml` 复制。
- `models.json` 从 `${workspace}/config/models.json` 复制。
- 不要把 Session、Memory、数据库、渠道登录态、索引或日志复制到 `deploy/`。
- 示例文件只能使用占位值；真实文件和构建出的镜像只能进入受控环境与私有 registry。

默认 Windows workspace 的复制示例：

```powershell
Copy-Item "$env:USERPROFILE\.zhice\config\.env" deploy\.env
Copy-Item "$env:USERPROFILE\.zhice\config\config.yml" deploy\config.yml
Copy-Item "$env:USERPROFILE\.zhice\config\models.json" deploy\models.json
```

对应路径是：

```text
C:\Users\<user>\.zhice\config\.env
C:\Users\<user>\.zhice\config\config.yml
C:\Users\<user>\.zhice\config\models.json
```

只有尚未迁移的旧环境才从项目源码目录 `config/.env` 复制到 `deploy/.env`；这是 legacy migration，不是当前推荐布局。无论 `.env` 来自当前 workspace 还是旧项目目录，复制后都必须确认 `deploy/.env` 不包含 `ZHICE_AGENT_WORKSPACE`，避免把本机路径写入容器镜像。

复制后可确认忽略规则：

```powershell
git check-ignore deploy/.env deploy/config.yml deploy/models.json
```

## 本机构建与烟测

```powershell
.\deploy\scripts\build-image.ps1 -Image zhice-agent -Tag local
.\deploy\scripts\run-local.ps1 -Image zhice-agent:local
```

Dockerfile 会在隔离阶段执行 Vue `npm ci && npm run build` 与微信 sidecar `npm ci && npm run build`，然后安装 Python 应用。最终镜像以专用非 root `zhice` 用户运行，并与本地统一使用 `Path.home() / ".zhice"`：

```text
HOME=/home/zhice
/home/zhice/.zhice/config/.env
/home/zhice/.zhice/config/config.yml
/home/zhice/.zhice/config/models.json
```

镜像不设置 `ZHICE_AGENT_WORKSPACE`；通用默认规则自然得到 `/home/zhice/.zhice`。镜像只声明四个运行数据 volume：`contexts`、`state`、`logs`、`extends`。一个 workspace 只允许一个 Gateway 容器写入。

## 推送与云端部署

登录私有 registry 后推送：

```powershell
.\deploy\scripts\push-image.ps1 -Registry registry.example.internal/team -Image zhice-agent -Tag 0.1.0
```

记录脚本输出的 digest。在云端复制 `deploy/scripts/*.sh` 后，必须按不可变 digest 部署：

```sh
sh deploy.sh registry.example.internal/team/zhice-agent@sha256:... 10086
sh status.sh
sh logs.sh 200
```

`deploy.sh` 固定启动一个容器、一个 Gateway 进程和一个 worker，并在新容器健康检查失败时恢复上一容器。生产入口前的 TLS、可信代理头和访问控制由云端反向代理负责。

也可以用 `docker compose -f deploy/docker-compose.yml up -d --build` 做单机开发验证；正式云端发布仍应使用 digest。
