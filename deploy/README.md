# ZhiCe-Agent 私有镜像部署

`deploy/` 是部署入口，不是第二份 workspace。根目录只保留三个可双击入口和容器定义；真实配置放在 `private/`，完整 PowerShell 流程放在 `pipelines/`，底层步骤放在 `scripts/`。仓库已有的 Python、Prompt、Skill、Vue 和微信 sidecar 源码由 Docker 多阶段构建直接带入镜像。

## 私有文件

普通 `zcagent init` 已默认在当前 workspace 生成 `config/.env`、`config.yml` 和 `models.json`：缺失文件会补齐，已有文件默认保留，只有 `--force` 才覆盖；旧 `--write-env` 仅作为兼容参数保留，不再是生成 env 的前提。

构建镜像前，先从当前 workspace 准备以下三个被 Git 忽略的文件：

```text
deploy/private/.env
deploy/private/config.yml
deploy/private/models.json
```

- `.env` 从 `${workspace}/config/.env` 复制；它只保存运行环境变量。
- `config.yml` 从 `${workspace}/config/config.yml` 复制。
- `models.json` 从 `${workspace}/config/models.json` 复制。
- 不要把 Session、Memory、数据库、渠道登录态、索引或日志复制到 `deploy/`。
- 示例文件只能使用占位值；真实文件和构建出的镜像只能进入受控环境与私有 registry。

默认 Windows workspace 的复制示例：

```powershell
Copy-Item "$env:USERPROFILE\.zhice\config\.env" deploy\private\.env
Copy-Item "$env:USERPROFILE\.zhice\config\config.yml" deploy\private\config.yml
Copy-Item "$env:USERPROFILE\.zhice\config\models.json" deploy\private\models.json
```

对应路径是：

```text
C:\Users\<user>\.zhice\config\.env
C:\Users\<user>\.zhice\config\config.yml
C:\Users\<user>\.zhice\config\models.json
```

只有尚未迁移的旧环境才从项目源码目录 `config/.env` 复制到 `deploy/private/.env`；这是 legacy migration，不是当前推荐布局。无论 `.env` 来自当前 workspace 还是旧项目目录，复制后都必须确认 `deploy/private/.env` 不包含 `ZHICE_AGENT_WORKSPACE`，避免把本机路径写入容器镜像。`ZHICE_AGENT_SKILL_REPO` 通常也不需要配置：缺失或为空时自动使用镜像内 `/app/skill_repo`；如需覆盖，只能填写容器内可访问的 source 仓库路径，不能填写 Windows 宿主机路径或 Git URL。默认 `config.yml` 只使用该本地 source，不配置假的远端 Git 地址。

复制后可确认忽略规则：

```powershell
git check-ignore deploy/private/.env deploy/private/config.yml deploy/private/models.json deploy/private/cloud-target.json
```

## 本机构建与烟测

Windows 资源管理器中可直接双击：

```text
deploy\build-and-deploy-local.cmd
```

窗口会显示完整流水线输出，并在成功或失败后暂停，不会一闪而过。终端中则运行无参数 PowerShell 入口：

```powershell
.\deploy\pipelines\build-and-deploy-local.ps1
```

流水线固定使用阿里云 APT 镜像、`zhice-agent:local`、正式端口 `10086` 和隔离 smoke 端口 `10087`，依次完成 Docker 检查、构建、smoke、Compose 更新与健康等待。它不会删除 `deploy_zhice-*` 命名卷，也不会上传镜像。

需要单独排障时，才直接使用底层脚本：

```powershell
.\deploy\scripts\build-image.ps1 -Image zhice-agent -Tag local
.\deploy\scripts\run-local.ps1 -Image zhice-agent:local
```

底层构建脚本默认使用 Debian 官方软件源。如果 Docker Desktop 所在网络访问 `deb.debian.org` 返回 `502`，可显式指定一个仅含主机名的镜像站：

```powershell
.\deploy\scripts\build-image.ps1 -Image zhice-agent -Tag local -AptMirror mirrors.aliyun.com
```

`AptMirror` 只影响 Docker 构建阶段的 APT 下载，不写入容器运行环境。它不接受 `http://`、路径或 shell 字符；不传参数时 Dockerfile 保持基础镜像自带的 Debian 官方源。直接使用 Compose 构建时，对应参数为 `ZHICE_APT_MIRROR`。

Dockerfile 会在隔离阶段执行 Vue `npm ci && npm run build` 与微信 sidecar `npm ci && npm run build`，然后安装 Python 应用。最终镜像以专用非 root `zhice` 用户运行，并与本地统一使用 `Path.home() / ".zhice"`：

```text
HOME=/home/zhice
/home/zhice/.zhice/config/.env
/home/zhice/.zhice/config/config.yml
/home/zhice/.zhice/config/models.json
```

镜像不设置 `ZHICE_AGENT_WORKSPACE` 和 `ZHICE_AGENT_SKILL_REPO`；通用默认规则自然得到 `/home/zhice/.zhice`，Skill 同步器根据镜像内代码位置得到 `/app/skill_repo`。镜像声明 `contexts`、`state`、`logs`、`extends` 四个通用运行数据 volume，并为运行时扫码生成的微信账号凭据声明独立的 `config/channels/weixin/accounts` volume。一个 workspace 只允许一个 Gateway 容器写入。

私有镜像里的 `.env`、`config.yml`、`models.json` 现在只作为云服务器首次迁移和灾难恢复基线。云端第一次执行 `deploy.sh` 时，会在不显示正文的前提下，从受控 Digest 临时容器复制并校验三份文件，再原子建立宿主机权威目录：

```text
/etc/zhice-agent/runtime/.env
/etc/zhice-agent/runtime/config.yml
/etc/zhice-agent/runtime/models.json
/etc/zhice-agent/runtime/backups/
```

后续 Digest 默认保留宿主机副本，三个文件分别以只读 bind mount 进入容器原路径。缺少任一文件、出现 symlink 或校验失败都会 fail closed，不会把不同镜像的配置混合初始化。本地 Windows Compose 仍使用镜像内私有基线，不强制依赖 Linux `/etc` 路径。

## 三个日常入口

`deploy/` 对外提供三个互不混淆的入口：

| 入口 | 输入 | 行为 |
| --- | --- | --- |
| `build-and-deploy-local` | 当前源码与私有配置 | 重新构建镜像、smoke、本地 Compose 部署 |
| `deploy-existing-image-to-cloud` | 已验证的本地 `zhice-agent:local` | 不构建镜像；生成 release tag、推送 ACR、按 Digest 云端部署；默认不重复 smoke |
| `build-and-deploy-cloud` | 当前源码与私有配置 | 重新构建镜像、smoke、推送 ACR、按 Digest 云端部署 |

Windows 资源管理器可以分别双击：

```text
deploy\build-and-deploy-local.cmd
deploy\deploy-existing-image-to-cloud.cmd
deploy\build-and-deploy-cloud.cmd
```

终端入口分别为：

```powershell
.\deploy\pipelines\build-and-deploy-local.ps1
.\deploy\pipelines\deploy-existing-image-to-cloud.ps1
.\deploy\pipelines\build-and-deploy-cloud.ps1
```

已有镜像入口的契约是“操作者已经确认该本地镜像可发布”，因此默认不再次 smoke；来源不确定时可显式执行：

```powershell
.\deploy\pipelines\deploy-existing-image-to-cloud.ps1 -Smoke
```

完整云端入口不会调用本地 Compose，不会顺带重建本地正式容器。

## 云端目标配置

复制公开示例并填写本机目标：

```powershell
Copy-Item deploy\private\cloud-target.example.json deploy\private\cloud-target.json
```

`private/cloud-target.json` 保存 registry 路径、SSH 目标与密码、远端运维脚本目录、主站/Ops HTTPS 地址和端口，已被 Git ignore。公开的 `cloud-target.example.json` 对所有需要替换的字符串直接使用中文占位，复制后必须填写真实目标；未替换时流水线会在推送或连接云端之前拒绝执行。

| 字段 | 应填写内容 |
| --- | --- |
| `Registry` | 阿里云 ACR 登录地址加命名空间，不含 `https://`，也不包含末尾的 `zhice-agent` 镜像名 |
| `SshHost` | 云服务器公网 IP 或可解析的主机名 |
| `SshUser` | 云服务器的 Linux 登录用户名，例如实际创建实例时选择的用户 |
| `SshPassword` | SSH 登录密码；当前流程同时假设它也是该用户的 sudo 密码 |
| `RemoteOpsDir` | 云服务器上的绝对运维脚本目录，必须填写 |
| `PublicUrl` | Caddy 对外提供的 HTTPS 访问地址，不带 `/health` |
| `OpsUrl` | 既有 Cloudflare Tunnel 转发、由服务器 Caddy/ttyd Basic Auth 保护的独立 Ops HTTPS origin，不带路径 |
| `Port` | Gateway 在云服务器 loopback 上监听的宿主机端口；默认 `10086`，没有端口冲突可直接保留 |

这个文件允许保存 SSH 密码，但它是本机明文 Secret：只依赖 `deploy/.gitignore` 与本机文件权限保护，不会进入 Docker 镜像。不要复制到公开位置、提交 Git、粘贴到日志或对话中。

首次使用一键入口前需要分别完成：

```text
本机 docker login 私有 registry
云端 sudo docker login 私有 registry
Python 已安装 Paramiko，并已在 Windows `%USERPROFILE%\.ssh\known_hosts` 中核验服务器主机密钥
SSH 登录密码可用于该用户的 sudo；若两者不同，当前一键流程不适用
```

云发布前还必须检查 `deploy/private/config.yml`：每个启用的 QQ `accounts` 项都要显式配置真实公网 HTTPS `web_base_url`，并与私有 `cloud-target.json` 的 `PublicUrl` 对齐；不能依赖未配置时的本地默认值 `http://127.0.0.1:10086`，否则 QQ 私聊裸 `/bind` 会向远端用户返回不可访问的 loopback 链接。本地和云端也不要同时运行同一 QQ Bot 账号，避免两个 WebSocket 实例竞争消息。

本地 `docker compose -f deploy/docker-compose.yml up --build` 会同时启动固定 `zhice-agent` 和独立 `zhice-agent-ops`；Gateway 与 Ops 端口都只发布到 `127.0.0.1`。主 Web 会投影 `local_docker` endpoint；sidecar 使用共享“监控面板 / 运维终端”，终端只接受 status/logs/logs-follow/diagnose/restart/help/exit，所有 Docker API 仍固定为 `zhice-agent`，不接受浏览器提交容器名、Docker 参数、路径或服务器配置命令。

### 密码与凭证保存在哪里

部署涉及的凭证彼此独立；只有 SSH/sudo 密码保存在 `cloud-target.json`：

| 凭证 | 实际保存位置 | 流水线如何使用 |
| --- | --- | --- |
| 本机阿里云 ACR 固定密码 | 执行 `docker login` 后交给 Docker Credential Store。Windows Docker Desktop 通常在 `%USERPROFILE%\.docker\config.json` 中声明 `credsStore=desktop`，该文件只保留 registry 和 credential-store 引用，不应手工写密码；具体以本机 `credsStore` 为准 | 本机 `docker push` 自动调用 Docker credential helper |
| 云服务器阿里云 ACR 固定密码 | 使用 `sudo docker login` 时属于 root Docker 用户，通常写在 `/root/.docker/config.json`；Linux 未配置 credential helper 时其中的 `auth` 只是 Base64 编码，并非加密 | 远端 `sudo docker pull` 自动读取 root 的 Docker 配置 |
| 云服务器 SSH/sudo 密码 | 明文保存在 Git 忽略的 `deploy/private/cloud-target.json` 的 `SshPassword`；必须限制本机文件访问 | Paramiko 直接从 JSON 读取，通过 SSH 密码认证，并经 PTY 的 `sudo -S` stdin 提供；不进入命令行、环境变量或输出 |
| SSH 主机密钥 | 经人工核验后保存在 `%USERPROFILE%\.ssh\known_hosts` | Paramiko 使用 `RejectPolicy`，主机未登记或密钥变化时立即拒绝，绝不自动接受 |

`deploy/private/.env` 和 `models.json` 中的 LLM、QQ、微信等运行 Secret 属于另一类应用配置：它们被 Git 忽略并打入受控私有镜像，与 ACR 登录密码、SSH 密钥和 sudo 密码无关。

镜像名称始终为 `zhice-agent`，本地日常标签始终为 `local`。云端默认标签按 `yyyyMMdd-HHmmss-Git短提交号` 生成；显式 `-ReleaseTag 0.1.1` 可用于正式语义版本。标签只用于人类识别，云端部署始终锁定精确 `@sha256:` Digest。

安装仅供 Windows 发布端使用的依赖：

```powershell
python -m pip install ".[deploy]"
```

Dockerfile 仍只安装 `.[gateway,qq]`，不会把 Paramiko 打入运行镜像。
`deploy` extra 支持 Paramiko `2.8` 至 `3.x`；流水线通过 helper 的安全 preflight 检查依赖，helper 只精确抑制 Paramiko 导入阶段的 `CryptographyDeprecationWarning`，普通导入异常仍会明确提示安装 `.[deploy]`。远端 sudo/deploy 等待最多 300 秒，超时会关闭 SSH channel 并失败退出，避免一键发布永久挂起。

## 底层推送与云端部署

需要单独排障时，可以直接调用底层推送脚本：

```powershell
.\deploy\scripts\push-image.ps1 -Registry "阿里云镜像仓库路径" -Image zhice-agent -Tag 0.1.0
```

脚本会精确返回目标 repository 的 Digest，不使用本地 RepoDigests 索引猜测。一键发布不会上传私有配置或整个 workspace，只会把六个 Shell 运维脚本（包含宿主机 `diagnose.sh`）和公开的固定 Ops 安装资产上传到 `RemoteOpsDir/releases/<release>`，完成 Shell/Python 静态校验后原子切换 `RemoteOpsDir/current`：

```sh
cd /填写的RemoteOpsDir/current
sudo sh deploy.sh '阿里云镜像仓库路径/zhice-agent@sha256:镜像摘要' 10086
sudo sh status.sh
sudo sh logs.sh 200
sudo sh restart.sh
```

手工排障也统一从 `RemoteOpsDir/current` 执行并使用 `sudo sh`，确保 Docker 权限与一键流水线一致。

`deploy.sh` 固定启动名为 `zhice-agent` 的一个容器、一个 Gateway 进程和一个 worker，并在新容器健康检查失败时恢复上一容器。浏览器、环境变量和运维终端都不能覆盖容器名。脚本用当前镜像初始化 `zhice-weixin-credentials` 的 `zhice:zhice` 所有权和 `0700` 目录权限，且不会删除该卷；微信扫码凭据因此能跨容器重建和镜像升级保留。Gateway 只绑定宿主机 `127.0.0.1:10086`，公网必须通过 Caddy/Nginx 的 80/443 进入；TLS、可信代理头和访问控制由云端反向代理负责。

共享 `pipelines/invoke-cloud-release.ps1` 通过 Python Paramiko helper 自动同步六个远端运维脚本和公开 Ops 安装资产、执行部署、安装/升级 root-owned Ops 服务并读取远端状态。部署与 status 成功后，同一 Paramiko 连接会从云服务器执行受控 `curl --fail --silent --show-error --max-time 20` 访问 `${PublicUrl}/health`，解析 JSON 并强制要求 `status=ok`；该远端公网 HTTPS 检查失败仍会使发布失败。本机随后再做一次附加 health 检查：成功会明确输出 passed；若本机代理、TUN DNS 或 TLS 环境异常，或返回状态异常，只输出 warning，因为远端公网链路已经通过。`RemoteOpsDir` 不是 Docker 部署目录：镜像、容器和命名卷仍由 Docker 管理。任一 build、smoke、push、SSH、远端容器健康、Ops 安装或远端公网 HTTPS 步骤失败都会以非零退出码结束；容器健康失败由 `deploy.sh` 恢复上一容器，数据卷不会删除。

也可以用 `docker compose -f deploy/docker-compose.yml up -d --build` 做单机开发验证；正式云端发布仍应使用 digest。

## 独立受限服务器 Ops

`deploy/ops/` 提供独立于 Agent 容器的宿主机运维面：共享双视图静态页、loopback Caddy 统一入口、固定版本并校验 SHA-256 的 ttyd、固定 dashboard adapter、`zhice-operator` systemd 服务、非 shell 的 `zhice-ops-shell`、root-owned wrapper、精确 sudoers和 ZhiCe 深色视觉覆盖。安装入口：

```sh
sudo sh deploy/ops/install.sh
```

正式公网入口复用服务器已有 Cloudflare Tunnel，发布一条 `${OpsUrl} -> http://127.0.0.1:7681` 路由；不再为 Ops 新建 connector 或 Access/IdP/MFA。Caddy 在 `7681` 强制独立 `owner` Basic Auth并同源提供监控页、`/api/*` 和 `/terminal/`，ttyd 在 `7682` 保留第二层同 credential 认证，dashboard adapter 在 `7683`；三端口均受 loopback/systemd 网络边界保护。首次安装生成高熵密码并保存于 root-only `/etc/zhice-ops/ops.env`，升级时保留且不进入发布日志。安全组禁止裸露这些端口；真实认证、iframe 缓存和 WebSocket Origin 验收见 `deploy/ops/README.md`。

终端只允许 `status`、有界 `logs`、`logs-follow`、`diagnose`、固定三文件的 `config view/edit/validate/diff/backup/restore/apply`、二次确认的 `restart`、`help` 和 `exit`。它不提供 Bash、`sudo -i`、任意 Docker、任意容器名或任意路径。`zhice-operator` 不加入 docker group；需要提权的动作只能经过参数结构化校验后的 root wrapper。

配置编辑先进入 `/var/lib/zhice-ops/pending`，保存时自动备份。只有三份配置一起校验成功后才能 `config apply`；apply 原子替换宿主机权威文件并重启固定容器，失败会恢复备份。journald 只记录动作和结果，不记录配置正文；原始容器日志和 diagnose 输出还会经过已知 Secret 与敏感键模式的二次脱敏。

真实 Linux/Cloudflare 验收必须覆盖：systemd 独立存活、ttyd resize/15 分钟 idle/最多一个会话、无/错误 Basic Auth 拒绝、正确认证与跨升级保留、Agent 容器退出后的直接救援、Docker unavailable 诊断、只读挂载、跨 restart/Digest 保留、备份恢复、通用 Shell/Docker/路径逃逸全部失败，以及私有 `PublicUrl` 的 `/health` 恢复。Windows 静态与单元测试不能替代这些外部验收。
