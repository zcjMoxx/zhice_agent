# ZhiCe-Agent 私有镜像云端发布流水线设计

> 说明：本文保留 2026-08-04 的入口命名和真实验收记录。当前代码已将三个入口改为 `build-and-deploy-local`、`deploy-existing-image-to-cloud` 和 `build-and-deploy-cloud`，流水线行为不变；应参考 `2026-08-08-deployment-entrypoint-naming-design.md` 和 Part 17 活文档。

> 2026-08-04 结构收敛：在代码尚未提交期间继续更新本文。`deploy/` 根目录只保留三个可双击 CMD 入口和容器定义；真实配置统一放入 `deploy/private/`，PowerShell 编排统一放入 `deploy/pipelines/`，底层构建、烟测、推送和远端 Shell 操作保留在 `deploy/scripts/`。云目标配置由不直观的 `cloud.local.psd1` 改为 `private/cloud-target.json`，公开示例使用中文待填写值。

## 背景

Part 17 已提供私有镜像构建、隔离 smoke、registry 推送、云端按 Digest 部署、健康等待和失败回滚脚本，并已完成阿里云 ACR 私有仓库到腾讯云单机实例的真实部署验收。当前操作仍需要人工依次完成镜像标记、推送、复制运维脚本、SSH 执行和公网健康检查，日常升级步骤长且容易混淆镜像名称、标签和 Digest。

本次将现有底层脚本组合为三个明确的用户入口，使本地验证、已有镜像上云和从源码完整上云各自保持单一语义，同时不引入 CI 平台或多节点编排。按当前单机运维约束，SSH 密码允许保存在 Git 忽略的本机私有 JSON 中，由 Paramiko 从文件读取，绝不进入命令行、环境变量或日志。

## 目标

- 保留 `deploy-local` 作为源码到本地 Docker Compose 的日常入口。
- 新增 `deploy-cloud-image`，将已经存在且由操作者确认的本地镜像直接推送并部署到云端，默认不重复 smoke。
- 新增 `deploy-cloud`，完成源码构建、隔离 smoke、私有 registry 推送、SSH 部署和公网健康检查。
- 镜像名称固定为 `zhice-agent`；本地日常标签固定为 `local`；云端发布标签默认按时间戳和 Git 短提交号生成；正式部署只使用不可变 Digest。
- 复用现有 build、smoke、push 和远端 shell 脚本，不在三个入口中复制底层实现。
- ACR 凭证继续由 Docker credential store 管理；SSH 密码由私有配置保存并仅由 Paramiko 读取，流水线不回显密码。

## 范围边界

本次只覆盖 Windows 本机到单台 Linux 云服务器的私有镜像发布。暂不包含：

- GitHub Actions、Jenkins 或其他托管 CI/CD；
- 多服务器、滚动发布、Kubernetes 或 overlay；
- 自动创建 registry、DNS、Caddy、证书和云防火墙规则；
- 自动输入 ACR 密码；
- 自动清理远端数据卷和 registry 历史版本；
- 成功发布后的自动业务数据迁移。

## 入口设计

目录结构固定为：

```text
deploy/
  README.md
  Dockerfile
  docker-compose.yml
  .gitignore
  deploy-local.cmd
  deploy-cloud-image.cmd
  deploy-cloud.cmd
  private/
    .env
    config.yml
    models.json
    cloud-target.json
    cloud-target.example.json
  pipelines/
    deploy-local.ps1
    deploy-cloud-image.ps1
    deploy-cloud.ps1
    invoke-cloud-release.ps1
  scripts/
    build-image.ps1
    run-local.ps1
    push-image.ps1
    deploy.sh
    status.sh
    logs.sh
    stop.sh
    restart.sh
    remote_ops.py
```

根目录 CMD 是用户入口；`pipelines/` 是完整流程；`scripts/` 不承担用户入口语义；`private/` 中除公开 example 外均由 Git ignore。

### `deploy-local.ps1` / `deploy-local.cmd`

保持现有行为：

```text
源码与 deploy 私有配置
  -> build zhice-agent:local
  -> isolated smoke
  -> local Compose recreate
  -> local health
```

它不执行 registry push 和 SSH 操作。

### `deploy-cloud-image.ps1` / `deploy-cloud-image.cmd`

默认输入为 `zhice-agent:local`：

```text
existing local image
  -> image existence and linux/amd64 checks
  -> unique release tag
  -> private registry push
  -> exact target RepoDigest
  -> sync remote shell scripts
  -> remote deploy.sh by Digest
  -> remote status and public HTTPS health
```

该入口的契约是“操作者确认现有镜像已经验证过”，因此默认不重复本地 smoke。可选 `-Smoke` 仅用于来源不确定的镜像。

### `deploy-cloud.ps1` / `deploy-cloud.cmd`

完整发布入口：

```text
源码与 deploy 私有配置
  -> build zhice-agent:local
  -> isolated smoke
  -> shared cloud release
```

它不调用 `deploy-local.ps1`，避免云端发布顺带重建本地正式 Compose 服务。

## 本地目标配置

云端目标属于本机私有编排信息，不进入容器运行 `.env`。新增 Git 忽略文件：

```text
deploy/private/cloud-target.json
```

公开仓库只提交 `private/cloud-target.example.json`。待填写字段直接使用中文值，不使用容易被误认为真实地址的 `example.com`：

```json
{
  "Registry": "阿里云镜像仓库路径",
  "SshHost": "云服务器地址",
  "SshUser": "云服务器登录用户名",
  "SshPassword": "云服务器SSH登录密码",
  "RemoteOpsDir": "云服务器运维脚本目录",
  "PublicUrl": "公网访问地址",
  "Port": 10086
}
```

需要操作者替换的字符串字段全部使用中文占位；`Port=10086` 是项目默认端口，可直接保留或按实际端口修改。流水线发现中文占位仍未替换时必须在任何 push/SSH 操作前失败。`RemoteOpsDir` 是必填的唯一远端目录字段，只保存五个轻量运维脚本的分版本 release 与 `current` 链接，不是 Docker 镜像、容器、数据卷或整个 `deploy/` 的目录。

凭证存储边界：Windows 本机 ACR 登录由 Docker Credential Store 管理，Docker Desktop 通常通过 `%USERPROFILE%/.docker/config.json` 中的 `credsStore=desktop` 引用系统凭证；云端使用 `sudo docker login` 时由 root Docker 配置 `/root/.docker/config.json` 管理，未安装 credential helper 时 `auth` 仅为 Base64 编码而非加密。`SshPassword` 是本机明文 Secret，只能保存在 Git 忽略的 `private/cloud-target.json` 并依赖本机文件权限保护。Paramiko 必须从 `%USERPROFILE%/.ssh/known_hosts` 加载已核验主机密钥并使用 `RejectPolicy`，不得自动信任新主机。当前实现假设 SSH 登录密码同时是该用户的 sudo 密码，通过 PTY 的 `sudo -S` stdin 提供，并在任何输出前脱敏。

## 标签与 Digest

- 固定镜像名称：`zhice-agent`。
- 本地开发标签：`local`。
- 默认发布标签：`yyyyMMdd-HHmmss-{git short sha}`。
- 显式 `-ReleaseTag` 允许语义版本，例如 `0.1.1`，但必须通过 Docker tag 字符白名单。
- ACR 仓库始终为 `{Registry}/zhice-agent`，不为每次发布创建新仓库。
- 云端只接收 `{Registry}/zhice-agent@sha256:...`。

`push-image.ps1` 不再取 `RepoDigests[0]`，而是从目标镜像的 RepoDigests 中精确选择与目标 repository 前缀匹配的 Digest，避免一个本地 image ID 同时存在多个 registry Digest 时误部署。

## 共享云端发布模块

新增 `deploy/pipelines/invoke-cloud-release.ps1`，负责：

1. 加载并校验 `private/cloud-target.json`；
2. 校验 Docker、Python、Paramiko、known_hosts 和目标本地镜像；
3. 生成或校验 release tag；
4. 调用 `push-image.ps1` 并接收精确 Digest；
5. 由 `remote_ops.py` 用 SFTP 上传 `deploy.sh`、`status.sh`、`logs.sh`、`stop.sh`、`restart.sh` 到 versioned release；
6. 逐个 `sh -n` 校验后原子切换 `current` 符号链接；
7. 通过 PTY 和 `sudo -S` 从 stdin 提供密码，执行 `current/deploy.sh` 与 `current/status.sh`；
8. 使用同一 Paramiko 连接从云服务器执行受控 curl 请求 `${PublicUrl}/health`，解析 JSON 并强制验证 `status=ok`；
9. 从本机附加请求同一 health；本机代理、TUN DNS、TLS 或状态异常只告警，不覆盖已经通过的远端公网判定；
10. 输出 release tag、Digest、URL 和远端运维目录。

sudo/deploy channel 使用 300 秒有界等待并在轮询间短暂休眠；超时主动关闭 channel，不允许因 sudo 或 Docker 异常永久占用流水线。仓库通过 `.gitattributes` 强制五个 `.sh` 在 Windows checkout 后仍为 LF，再上传到 Linux 执行。

共享模块不接受任意远端 shell 片段；主机、用户、端口、目录、release id 和镜像引用分别经过格式校验并使用 shell quoting。外部 `ssh.exe`、`scp.exe` 不再参与流程。

## 云端安全边界

`deploy.sh` 将容器端口从：

```text
0.0.0.0:${HOST_PORT}:10086
```

收紧为：

```text
127.0.0.1:${HOST_PORT}:10086
```

公网只通过宿主机 Caddy/Nginx 的 80/443 进入，避免未来误开云防火墙时绕过 HTTPS 和反向代理直接访问 Gateway。

远端发布继续保留四个命名卷和单容器语义。新容器在健康检查前不会删除旧容器；启动或健康失败时恢复旧容器；成功后清理旧容器。公网健康检查失败只报告代理/DNS/TLS 故障，不擅自删除新容器或数据卷。

## 失败语义

- Docker、配置、镜像、Python/Paramiko、known_hosts 或 SSH 前置检查失败：不推送、不修改云端。
- build 或 smoke 失败：不推送、不修改云端。
- registry push 失败：云端旧实例保持运行。
- SSH 或脚本同步失败：已推送镜像保留，云端旧实例保持运行。
- 新容器启动或健康失败：`deploy.sh` 自动恢复旧容器。
- 云服务器侧公网 HTTPS 检查失败：保留容器，以非零退出并输出 status/logs/Caddy 排查提示。
- 云服务器侧公网 HTTPS 已通过、仅本机附加检查失败：输出本机代理/DNS/TLS warning，发布保持成功。

## 变更文件

- `docs_design/2026-08-04-private-registry-cloud-release-pipeline-design.md`
- `deploy/pipelines/deploy-cloud.ps1`
- `deploy/deploy-cloud.cmd`
- `deploy/pipelines/deploy-cloud-image.ps1`
- `deploy/deploy-cloud-image.cmd`
- `deploy/private/cloud-target.example.json`
- `deploy/.gitignore`
- `deploy/pipelines/invoke-cloud-release.ps1`
- `deploy/scripts/push-image.ps1`
- `deploy/scripts/deploy.sh`
- `deploy/scripts/status.sh`
- `deploy/scripts/logs.sh`
- `deploy/scripts/stop.sh`
- `deploy/scripts/restart.sh`
- `deploy/scripts/remote_ops.py`
- `pyproject.toml`
- `deploy/README.md`
- `tests/unit_test/deploy/test_deploy_assets.py`
- `tests/unit_test/deploy/test_case.md`
- `docs_design/zhice-agent-part17-reliability-diagnostics-deployment-design.md`

## 测试方案

- 静态验证三个用户入口和两个 CMD 薄包装存在，`scripts/` 只放底层实现。
- 验证 cloud local 示例无真实主机、账号、registry 或 Secret，真实文件被 Git ignore。
- 验证已有镜像入口默认不调用 smoke，`-Smoke` 时才调用。
- 验证完整云端入口依次调用 build、smoke 和共享发布模块，不调用本地 Compose。
- 验证 release tag 自动生成规则、显式标签白名单和固定镜像名称。
- 验证 `push-image.ps1` 精确匹配目标 RepoDigest，不使用索引 0。
- 验证 helper 从私有 JSON 读取密码，使用 known_hosts + `RejectPolicy`，不使用 `AutoAddPolicy`，并固定同步五个脚本。
- 验证远端部署只绑定 `127.0.0.1`、要求 Digest、保留健康失败回滚和命名卷。
- 运行 deploy 单元测试、PowerShell Parser 语法检查、Ruff 和 `git diff --check`。
- 不在自动测试中读取 `deploy/private/.env`、`private/cloud-target.json` 或其他真实私有配置。

## 验收标准

- 用户日常只需在三个入口中按目标选择一个，无需手写 tag、registry、scp、ssh 或 Digest 命令。
- 本地镜像名称始终为 `zhice-agent`，本地日常标签为 `local`，发布标签默认唯一。
- 已有镜像入口不重复 smoke；完整云端入口始终先 smoke。
- registry 凭证不进入项目配置；SSH 密码只进入 Git 忽略的本机私有 JSON，不进入命令行、环境、日志和 Git。
- 云端部署锁定精确 Digest，容器仅在宿主机 loopback 暴露 10086。
- 任一前置步骤失败不影响当前云端实例；远端健康失败自动回滚。
- 公网 `/health` 验证覆盖 DNS、TLS、Caddy 和 Gateway 完整链路。

## 真实验收记录

2026-08-04 三个用户入口均在真实本机、阿里云 ACR 与腾讯云单机链路完成验收：

- `deploy-local.ps1`：退出码 `0`；完成真实 build、`10087` 隔离 smoke、Gateway check、Compose recreate，最终本地容器为 healthy，已有命名卷保留。
- `deploy-cloud-image.ps1`：退出码 `0`；默认未重复 smoke，发布标签 `20260804-202303-12d521cf`，不可变 Digest `sha256:fe7bf62055b36d9a81e9a57a45434b4d5a43849c08886eaa95c8d6a1951563f7`；五脚本 versioned 同步与 `current` 原子切换成功，远端容器 running/healthy、restarts=0，云服务器侧公网 HTTPS health 通过。
- `deploy-cloud.ps1`：退出码 `0`；发布标签 `20260804-202841-12d521cf`，不可变 Digest `sha256:aa1a17633342a637c6feb2fdf5be7e07c35f102dc69ec3f608734cd1c3ea7bb2`；从源码 build、隔离 smoke、ACR push、Paramiko 同步、sudo 部署、远端 status 与云服务器侧公网 HTTPS health 全链通过。

真实执行中发现并关闭三个只会在现场暴露的兼容问题：

1. Windows PowerShell 5.1 将 `@($repoDigestsJson | ConvertFrom-Json)` 包成单个嵌套 `System.Object[]`，导致已有两个 RepoDigest、目标前缀实际唯一时仍匹配为零。当前先以 `ConvertFrom-Json -InputObject` 赋给变量，再从变量管道展开并保持目标 repository 唯一匹配。
2. 本机 Paramiko 2.8 导入时向 stderr 写入 `CryptographyDeprecationWarning`，会被 Windows PowerShell 5.1 的 `ErrorActionPreference=Stop` 误判为 native command 失败。当前 helper 只精确抑制 Paramiko 导入阶段的该类警告，PowerShell preflight 安全捕获退出码；普通导入异常仍明确失败。
3. 发布端 TUN 将公网域名解析到 fake DNS 地址 `198.18.1.0`，造成本机 TLS health 假阴性，而云服务器本地 Gateway、Caddy 指定解析和服务器经公网域名访问均为 `200`。当前发布成败由云服务器侧受控 curl 强校验 JSON `status=ok`；本机 health 保留为附加诊断，代理、DNS 或 TLS 异常只输出 warning。
