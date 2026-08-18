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

镜像不设置 `ZHICE_AGENT_WORKSPACE` 和 `ZHICE_AGENT_SKILL_REPO`；通用默认规则自然得到 `/home/zhice/.zhice`，Skill 同步器根据镜像内代码位置得到 `/app/skill_repo`。镜像声明 `contexts`、`state`、`travel`、`logs`、`extends` 运行数据 volume，其中 `zhice-travel-data` 专门持久化已保存的 TravelPlanV1；并为运行时扫码生成的微信账号凭据声明独立的 `config/channels/weixin/accounts` volume。一个 workspace 只允许一个 Gateway 容器写入。

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
| `OpsUrl` | 既有 Cloudflare Tunnel 转发、由服务器 Caddy 长期 Cookie 登录与 loopback ttyd Basic Auth 保护的独立 Ops HTTPS origin，不带路径 |
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

Dockerfile 安装 `.[gateway,qq,hotel-browser]` 和 Playwright bundled Chromium，保证容器内携程只读酒店查询可用；它仍不会把只供发布端使用的 Paramiko 打入运行镜像。Linux 容器显式使用 bundled Chromium，不依赖系统 Chrome。浏览器安装与 `/opt/zhice` 权限收敛固定在同一 layer，跨 stage 运行产物直接使用 `COPY --chown`，避免后续递归改权把 Chromium 复制成额外的大层。携程浏览器 profile 位于 `state/browser_profiles/ctrip`，随 `zhice-state` 命名卷跨重启保留；账号密码继续通过 `deploy/private/.env` 或平台 Secret 注入，不能写入仓库。
`deploy` extra 支持 Paramiko `2.8` 至 `3.x`；流水线通过 helper 的安全 preflight 检查依赖，helper 只精确抑制 Paramiko 导入阶段的 `CryptographyDeprecationWarning`，普通导入异常仍会明确提示安装 `.[deploy]`。远端 sudo/deploy 等待最多 1200 秒，超时会关闭 SSH channel 并失败退出，避免一键发布永久挂起。小红书 sidecar 首次启动会下载并解压约 140–190 MB 的固定浏览器，部署与固定拓扑重启均允许最多 15 分钟就绪；后续复用 `zhice-xhs-cache` 命名卷，失败前输出最多 80 行、64 KiB 的有界日志。云端 sidecar 覆盖主镜像的 Gateway 健康检查，固定探测容器内 `127.0.0.1:18060`，避免业务已就绪却被 Docker 标为 unhealthy。

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

## 旅行外部服务

私有镜像固定包含：

- `mcp-amap`，来源 `@amap/amap-maps-mcp-server@0.0.8`；
- `12306-mcp`，固定 `12306-mcp@0.3.1`，只用于查询；
- RedNote 兼容的小红书 MCP/Login Linux 二进制，固定上游提交 `c2fc4dde2c45f26f6f9de288b7423a2bdfa7af1c` 并应用仓库内可审计 patch；
- 小红书内置浏览器所需 Debian 运行库。

### 准备私有运行配置

本机 durable Secret 写在 `${ZHICE_AGENT_WORKSPACE}/config/.env`。至少确认：

```dotenv
AMAP_MAPS_API_KEY=<高德 Web 服务 Key>
TAVILY_API_KEY=<Tavily API Key>
```

高德浏览器地图使用另外一组构建凭据：`VITE_AMAP_JS_API_KEY` 和 `VITE_AMAP_JS_SECURITY_CODE`。它们与 `AMAP_MAPS_API_KEY` 不同，不能混用。两项都必须写入 Git 忽略的 `deploy/private/.env`；`build-image.ps1` 会在缺失、空值或重复时前置失败，并仅将它们注入 Vite web-build。真实值不写公开模板或构建摘要。直接使用 Compose 的 `--build` 时，需要先把两项投影为当前进程环境变量；标准本地/云端流水线会从私有文件安全读取。

服务器使用镜像内固定 binary，`deploy/private/config.yml` 的旅行 MCP 形态应为：

```yaml
mcp:
  servers:
    open-meteo:
      command: python
      args: [-m, integrations.open_meteo_mcp.server]
    amap-maps:
      command: mcp-amap
      args: []
      env:
        AMAP_MAPS_API_KEY: "${AMAP_MAPS_API_KEY}"
    tavily:
      url: https://mcp.tavily.com/mcp
      transport: streamable_http
      headers:
        Authorization: "Bearer ${TAVILY_API_KEY}"
    12306:
      command: 12306-mcp
      args: []
    xhs-readonly:
      command: python
      args: [-m, integrations.xhs_readonly_mcp.server]
      env:
        XHS_READONLY_UPSTREAM_URL: "${XHS_READONLY_UPSTREAM_URL}"
        XHS_READONLY_HTTP_HOST_ALLOWLIST: "${XHS_READONLY_HTTP_HOST_ALLOWLIST}"
        XHS_READONLY_COOKIE_DIR: "${XHS_READONLY_COOKIE_DIR}"
        XHS_READONLY_COOKIE_FILE: "${XHS_READONLY_COOKIE_FILE}"
        XHS_READONLY_TIMEOUT_SECONDS: "120"

travel:
  enabled: true
  max_evidence_items: 40
  max_plan_bytes: 524288
```

服务器 `.env` 中的小红书容器值固定为：

```dotenv
XHS_READONLY_UPSTREAM_URL=http://zhice-xhs-readonly:18060/mcp
XHS_READONLY_HTTP_HOST_ALLOWLIST=zhice-xhs-readonly
XHS_READONLY_COOKIE_DIR=/home/zhice/.zhice/integrations/xhs/data
XHS_READONLY_COOKIE_FILE=/home/zhice/.zhice/integrations/xhs/data/cookies.json
```

本地源码运行仍可使用 `npx -y @amap/amap-maps-mcp-server@0.0.8` 和 `npx -y 12306-mcp@0.3.1`；不要把这个需要在线解析的形态复制到服务器私有配置。

### 首次迁移小红书 Cookie

小红书 Cookie 不进入镜像或 `deploy/private/`。在本地完成登录并确认只读 smoke 通过后，使用受控 SSH/SCP 上传到服务器 root-only 种子路径：

```powershell
scp "$env:USERPROFILE\.zhice\integrations\xhs\data\cookies.json" `
  <ssh-user>@<server>:/tmp/zhice-xhs-cookies.json
ssh <ssh-user>@<server> `
  "sudo install -d -o root -g root -m 0700 /etc/zhice-agent/xhs && sudo install -o root -g root -m 0600 /tmp/zhice-xhs-cookies.json /etc/zhice-agent/xhs/cookies.json && rm -f /tmp/zhice-xhs-cookies.json"
```

不要把 Cookie 内容粘贴到终端参数、日志或对话中。`deploy.sh` 只在 `zhice-xhs-data` volume 尚无有效 `cookies.json` 时导入该种子；后续发布不会用旧种子覆盖 sidecar volume 中的新登录态。

### 容器边界

云发布仍只推送一个 `zhice-agent@sha256:...` 私有镜像。服务器运行两个固定容器：

- `zhice-agent`：Gateway 与 Python 只读适配器；
- `zhice-xhs-readonly`：浏览器自动化 sidecar。

两个容器加入固定 `zhice-travel` bridge network。sidecar 容器监听容器内 `18060`，没有 `-p`，安全组和反向代理都不应发布此端口。Cookie volume 在 sidecar 中读写，在主容器中只读；浏览器 cache 使用独立 volume，避免每次 Digest 发布重新下载约 140～190MB 浏览器。

受限运维 `restart` 会先重启 sidecar 并等待其 `18060` 就绪，再重启主容器。Digest 发布若在主容器启动或健康检查阶段失败，会同时回滚主容器与 sidecar；首次部署无旧版本可回滚时会移除失败容器，避免保留不完整拓扑。Cookie seed 临时容器由退出 trap 清理，已有持久 Cookie 始终不会被旧 seed 覆盖。

### 发布后验证

完整多日旅行计划在 optimizer 通过后可能需要较长的结构化输出时间。服务器 `models.json` 中所有启用 endpoint 应统一设置：

```json
{
  "request_timeout_seconds": 240,
  "total_deadline_seconds": 300,
  "max_attempts": 1
}
```

反向代理、平台请求和健康检查不得把旅行规划请求截断在 300 秒以内；客户端可使用异步事件流等待结果。不要只修改一个 endpoint，因为 Failover 总 deadline 取所有启用 endpoint 的最小值。

正常的一键发布入口不变。完成后在服务器检查：

```sh
sudo sh <RemoteOpsDir>/current/status.sh
sudo docker inspect zhice-agent zhice-xhs-readonly \
  --format '{{.Name}} {{.State.Status}} {{.Config.Image}}'
sudo docker network inspect zhice-travel \
  --format '{{range .Containers}}{{.Name}} {{end}}'
sudo docker volume inspect zhice-travel-data zhice-xhs-data zhice-xhs-cache >/dev/null
sudo docker port zhice-xhs-readonly
```

最后一条必须无输出，证明 sidecar 没有发布宿主机端口。随后通过主应用运行真实登录状态、搜索和详情 smoke。若返回 `TRAVEL_SOURCE_AUTH_REQUIRED`，使用服务器内兼容登录程序完成一次扫码：

```sh
sudo docker run --rm -it \
  --network zhice-travel \
  -e COOKIES_PATH=/home/zhice/.zhice/integrations/xhs/data/cookies.json \
  -v zhice-xhs-data:/home/zhice/.zhice/integrations/xhs/data \
  -v zhice-xhs-cache:/home/zhice/.cache/xiaohongshu-mcp \
  --entrypoint /opt/zhice/bin/xiaohongshu-login-rednote \
  "$(sudo docker inspect --format '{{.Config.Image}}' zhice-xhs-readonly)"
```

无 GUI 服务器优先从已验证本地 Cookie 做首次迁移；CLI 登录是否能显示可扫码内容取决于上游当前版本，不能把未实际显示的二维码宣称为成功。

## 独立受限服务器 Ops

`deploy/ops/` 提供独立于 Agent 容器的宿主机运维面：共享双视图静态页、loopback Caddy 统一入口、固定版本并校验 SHA-256 的 ttyd、固定 dashboard adapter、`zhice-operator` systemd 服务、非 shell 的 `zhice-ops-shell`、root-owned wrapper、精确 sudoers和 ZhiCe 深色视觉覆盖。安装入口：

```sh
sudo sh deploy/ops/install.sh
```

正式公网入口复用服务器已有 Cloudflare Tunnel，发布一条 `${OpsUrl} -> http://127.0.0.1:7681` 路由；不再为 Ops 新建 connector 或 Access/IdP/MFA。Caddy 在 `7681` 先通过 dashboard adapter 校验长期签名 `HttpOnly` Cookie，再同源提供监控页、`/api/*` 和 `/terminal/`；只在 loopback 代理到 `7682` ttyd 时注入后端 Basic Auth，dashboard adapter 位于 `7683`。三端口均受 loopback/systemd 网络边界保护。首次安装生成高熵密码并保存于 root-only `/etc/zhice-ops/ops.env`，升级时保留且不进入 Cookie、网页脚本或发布日志。首次输入一次后浏览器重启继续登录；主动退出、清理站点数据或 credential 轮换才会失效。安全组禁止裸露这些端口；真实认证、长期 Cookie 和 WebSocket Origin 验收见 `deploy/ops/README.md`。

终端只允许 `status`、有界 `logs`、`logs-follow`、`diagnose`、固定三文件的 `config view/edit/validate/diff/backup/restore/apply`、二次确认的 `restart`、`help` 和 `exit`。它不提供 Bash、`sudo -i`、任意 Docker、任意容器名或任意路径。`zhice-operator` 不加入 docker group；需要提权的动作只能经过参数结构化校验后的 root wrapper。

配置编辑先进入 `/var/lib/zhice-ops/pending`，保存时自动备份。只有三份配置一起校验成功后才能 `config apply`；apply 原子替换宿主机权威文件并重启固定容器，失败会恢复备份。journald 只记录动作和结果，不记录配置正文；原始容器日志和 diagnose 输出还会经过已知 Secret 与敏感键模式的二次脱敏。

真实 Linux/Cloudflare 已完成 systemd 独立存活、首次/错误登录、长期 Cookie 签发、主动退出、loopback ttyd 无/错误 Basic Auth 拒绝、只读挂载、配置事务、跨 restart/Digest 保留、固定容器重建及私有 `PublicUrl` `/health` 恢复验收。仍需在真实浏览器中人工覆盖 Cookie 跨浏览器重启复用、ttyd resize/15 分钟 idle 后免登录重连/最多一个会话、iframe 回退，以及 Agent 容器退出或 Docker unavailable 时的直接救援；这些是环境交互验收，不是待实现功能。通用 Shell、任意 Docker/容器名/路径逃逸必须始终失败。
