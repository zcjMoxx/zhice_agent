# ZhiCe-Agent 可配置 APT 镜像源与本地部署流水线设计

> 说明：本文保留 2026-07-31 落地时的原始路径。当前本地入口已改为 `deploy/build-and-deploy-local.cmd` 与 `deploy/pipelines/build-and-deploy-local.ps1`，以显式表达从源码构建并部署到本地；应参考 `2026-08-08-deployment-entrypoint-naming-design.md` 和 Part 17 活文档。

## 背景

Part 17 的私有镜像构建默认使用 `node:22-bookworm-slim` 中的 Debian 官方软件源。Windows + Docker Desktop 环境下，Docker 内部代理访问 `deb.debian.org` 可能返回 `502 Bad Gateway`，导致 runtime 阶段无法安装 Python。Vue 与微信 Sidecar 构建均已成功，失败与项目源码、私有配置无关。

此前仅能临时复制 Dockerfile 并替换软件源，操作不可复现，也无法通过仓库脚本表达。当前部署入口需要在保留官方默认值的同时，允许受限网络显式选择镜像站。

镜像成功生成后进一步检查发现，`run-local.ps1` 会删除临时 smoke 容器，但没有携带 volume 删除参数。由于镜像声明了四个 `VOLUME`，每次 smoke 都可能留下无用匿名卷；这些匿名卷与 Compose 的四个命名数据卷无关，应随临时容器一起清理。

本地真实部署教学还暴露出入口层缺失：`build-image.ps1`、`run-local.ps1` 和 Compose 各自职责清楚，但用户必须记忆镜像名、标签、APT 镜像、端口和调用顺序。日常部署需要一个无参数总入口，把这些底层步骤组成可重复执行的本地流水线。

## 目标

- Dockerfile 默认继续使用基础镜像自带的 Debian 官方源。
- Windows 构建脚本提供可选 `-AptMirror` 参数。
- Compose 直接构建时可通过 `ZHICE_APT_MIRROR` 传入同一参数。
- 镜像源只接受主机名或 `主机名:端口`，不接受协议、路径或 shell 字符。
- 参数只影响镜像构建，不写入运行时环境，也不改变容器配置。
- smoke 正常完成、失败或清理旧同名容器时，都删除该临时容器关联的匿名卷。
- 不删除或修改 `deploy_zhice-*` Compose 命名卷。
- 不新增本地部署配置文件；日常入口为无参数 `deploy-local.ps1`。
- 本地流水线固定使用 `zhice-agent:local`、正式端口 `10086`、smoke 端口 `10087`，并默认使用 `mirrors.aliyun.com`。
- 构建和 smoke 通过后才重建正式 Compose 容器；已有正式容器在前置阶段保持运行。

## 范围边界

本次只处理 Debian APT 下载源选择，不修改 Docker Desktop 全局代理，不改变 npm、pip、模型 API 或渠道网络配置，不引入多环境 overlay，也不自动根据地域选择镜像站。

## 模块设计

### Dockerfile

runtime 阶段新增空值 `APT_MIRROR` build argument。空值时不修改 `/etc/apt/sources.list.d/debian.sources`；非空时先用 POSIX shell 白名单校验，再只替换其中的 `deb.debian.org` 主机名。

### Windows 构建脚本

`build-image.ps1` 新增空值 `AptMirror` 参数。脚本在调用 Docker 前再次校验值，并仅在非空时追加：

```text
--build-arg APT_MIRROR=<host>
```

默认命令行为保持不变；国内网络可显式使用 `mirrors.aliyun.com`。

### Compose

Compose build args 将 `ZHICE_APT_MIRROR` 映射为 Dockerfile 的 `APT_MIRROR`。该环境变量只在用户选择 `docker compose ... --build` 时生效，不会注入运行容器。

### 本地 smoke 清理

`run-local.ps1` 继续先优雅停止 smoke 容器，再通过 `docker rm --volumes` 删除容器及其匿名卷；启动前对残留同名容器执行 `docker rm --force --volumes`。命令只以固定名称 `zhice-agent-smoke` 为目标，不操作 Compose 容器和命名卷。

### 无参数本地部署流水线

新增 `deploy/scripts/deploy-local.ps1`，不声明用户参数，内部按固定顺序执行：

1. 检查 Docker Engine 可用。
2. 调用 `build-image.ps1`，传入本地固定镜像名与阿里云 APT 镜像主机。
3. 调用 `run-local.ps1`，在 `10087` 完成隔离 smoke。
4. 使用 `docker compose up -d --force-recreate --no-build` 创建或更新正式容器。
5. 在有界时间内轮询容器 health；失败时输出 Compose 日志并返回失败。
6. 成功时输出镜像、容器、访问地址和数据卷保留说明。

流水线不执行 `down -v`、`docker volume rm` 或 registry push。已运行的正式容器只在新镜像完成 build 与 smoke 后才进入重建阶段。

## 数据流

```text
-AptMirror / ZHICE_APT_MIRROR
  -> Docker build arg APT_MIRROR
  -> runtime stage validates host
  -> optional deb.debian.org host replacement
  -> apt-get update/install
  -> final private image
```

## 变更文件

- `deploy/Dockerfile`
- `deploy/scripts/build-image.ps1`
- `deploy/docker-compose.yml`
- `deploy/README.md`
- `deploy/scripts/run-local.ps1`
- `deploy/scripts/deploy-local.ps1`
- `tests/unit_test/deploy/test_deploy_assets.py`
- `tests/unit_test/deploy/test_case.md`
- `docs_design/zhice-agent-part17-reliability-diagnostics-deployment-design.md`

## 测试方案

- 静态测试确认 Dockerfile 默认参数为空、执行双层白名单校验并只替换主机名。
- 静态测试确认 PowerShell 参数只在非空时传给 Docker。
- 静态测试确认 Compose 使用 `ZHICE_APT_MIRROR`，README 同步公开命令。
- 静态测试确认 smoke 启动前和 `finally` 清理均带 volume 删除参数，且目标名称固定。
- 静态测试确认总入口无参数、固定调用顺序、smoke 使用独立端口、Compose 禁止再次构建且不删除命名卷。
- 运行 deploy 单元测试、Ruff 和 `docker compose config`。
- 用户随后亲自使用阿里云镜像参数执行真实构建。

## 验收标准

- 不传镜像参数时，构建语义与原先一致。
- `-AptMirror mirrors.aliyun.com` 能把 runtime APT 请求切换到阿里云镜像。
- 非法值在 PowerShell 或 Dockerfile 内被拒绝。
- build arg 不出现在容器运行环境中。
- 公开仓库不包含本地临时 Dockerfile或真实 Secret。
- smoke 临时容器不会遗留匿名卷，Compose 命名卷保持不变。
- 用户只运行一条无参数命令即可完成本地构建、验收、更新和健康确认。
