# Deploy 测试说明

## 测试目标

验证 Part 17 私有镜像部署资产的静态契约，避免真实配置进入 Git、完整 workspace 被复制进镜像或生产拓扑意外扩展为多 writer。

## 用例覆盖

- 公开 Docker、Compose、说明和运维脚本齐全。
- `config/.env.example` 是唯一公开 runtime env 模板，`deploy/` 不维护第二份 `.env.example`。
- `private/.env`、`private/config.yml`、`private/models.json` 三个镜像私有文件以及本机云目标 `private/cloud-target.json` 被目录级 Git ignore 覆盖；公开 `private/cloud-target.example.json` 的 registry、SSH 主机、SSH 用户、SSH 密码、运维脚本目录和公网地址全部使用中文占位，未替换时流水线明确拒绝。
- Deploy README 优先从当前 `${workspace}/config/` 复制三个私有文件到 `deploy/private/`，给出默认 Windows `.zhice` 路径，将项目 `config/.env` 标为 legacy migration，并要求任一来源的 `deploy/private/.env` 删除 `ZHICE_AGENT_WORKSPACE`；`ZHICE_AGENT_SKILL_REPO` 缺失时自动使用 `/app/skill_repo`，默认 source 不携带虚假远端地址。
- Dockerfile 从仓库构建 Python、Vue、Prompt、Skill 和微信 sidecar，只从 `deploy/` 复制三个私有文件。
- 微信 Sidecar 使用 Node `pathToFileURL` 判断 Linux/Windows 直接入口；Docker 通过 `.[gateway,qq]` 显式安装 Gateway `websockets` runtime 和 QQ 渠道依赖，不依赖传递依赖碰巧可用。
- 容器以专用非 root `zhice` 用户运行，使用与本地一致的 `Path.home()/.zhice` 默认目录，并通过显式 `--env-file` 加载镜像内私有环境变量。
- Dockerfile 不预设 `ZHICE_AGENT_SKILL_REPO`；私有 `.env` 显式配置时使用配置值，未配置或为空时由运行时代码自动定位镜像内 `/app/skill_repo`。
- Compose 持久化 `contexts`、`state`、`logs`、`extends`，并用独立命名卷持久化微信运行时账号凭据子目录；完整 `config/` 仍由镜像提供。
- Dockerfile 预创建微信账号凭据目录并交给非 root `zhice` 用户；云端部署幂等创建和挂载 `zhice-weixin-credentials`，并用镜像内身份初始化目录所有权和 `0700` 权限，回滚与重启不删除该卷。
- 云端部署要求不可变 digest，且只启动单个容器；Gateway 端口只发布到宿主机 `127.0.0.1`，公网必须经过反向代理。
- `push-image.ps1` 先用 `ConvertFrom-Json -InputObject` 解析 RepoDigests，再从变量管道展开数组并精确选择目标 repository 的唯一 Digest，不使用可能命中其他 registry 的索引 0；回归测试通过真实 Windows PowerShell 验证双元素 JSON 数组不会嵌套成单个 `System.Object[]`。
- 本地 smoke 使用独立 `$homeResponse` 保存首页响应，不写入 PowerShell 只读自动变量 `$HOME`；health 与 Web 均为 200 后进入容器内 gateway check。
- APT 镜像源默认留空并继续使用 Debian 官方源；Windows 脚本与 Compose 可显式传入同一个纯主机名参数，Dockerfile 和脚本均拒绝协议、路径及 shell 字符。
- 本地 smoke 在启动前及 `finally` 清理中删除固定临时容器关联的匿名卷，不调用 `docker volume rm`，不影响 Compose 命名卷。
- smoke 容器首次不存在时，脚本先用 `docker ps -a` 成功查询再决定是否清理，避免 Windows PowerShell 将 `docker rm` 的 `No such container` stderr 转为终止性 `NativeCommandError`。
- `docker ps` 零条输出按空数组处理，并通过 `-contains` 判断固定名称，不对可能为 `$null` 的命令结果调用 `.Trim()`。
- `deploy/deploy-local.cmd` 是根目录双击入口，实际无参数 PowerShell 编排位于 `deploy/pipelines/deploy-local.ps1`，`deploy/scripts/` 只保留底层脚本；流水线固定串联 Docker 检查、阿里云 APT 构建、`10087` smoke、Compose `--no-build` 更新和有界 health 等待，失败不删除命名卷，也不执行 registry push。
- `deploy/deploy-local.cmd` 是 Windows 双击薄入口，只定位并调用同目录 PS1、保留退出码和暂停窗口，不复制 Docker 流程、不提升权限或修改系统 Execution Policy。
- `deploy/pipelines/deploy-cloud-image.ps1` 复用已经存在的 `zhice-agent:local`，默认不重复 smoke；只有显式 `-Smoke` 才调用隔离烟测，然后进入共享云端发布。
- `deploy/pipelines/deploy-cloud.ps1` 从源码 build、smoke 后调用共享云端发布，不调用本地 Compose；两个云端 CMD 都是指向 `pipelines/` 的双击薄入口。
- `pipelines/invoke-cloud-release.ps1` 从 Paramiko helper 取得脱敏后的公开目标，固定镜像名、生成时间戳与 Git 短提交号标签，校验 `linux/amd64`、Docker、Python/Paramiko，精确取得 Digest，同步固定五个 shell 脚本，远端部署后验证公网 HTTPS `/health`。
- `scripts/remote_ops.py` 自行读取私有 JSON 的 `SshPassword` 并强制要求 `RemoteOpsDir`；只加载 Windows `~/.ssh/known_hosts` 并使用 `RejectPolicy`，密码不进入参数、环境或输出，目录字段缺失时前置失败。
- 五个脚本通过 SFTP 写入 versioned release，经 `sh -n` 后原子切换 `current`；`status` 对不存在容器友好并展示 image/status/health/created/restarts，`logs` 拒绝非正整数，`stop` 幂等，`restart` 明确检查容器，`deploy` 失败恢复旧容器。
- `.gitattributes` 强制 `deploy/scripts/*.sh` 使用 LF，避免 Windows checkout 后上传 CRLF 脚本；helper 单元测试使用 fake client/SFTP/channel 覆盖配置校验、Secret 脱敏、五脚本上传、语法校验、原子切换、sudo stdin 和有界超时，不读取真实私有 JSON，也不连接网络。
- Paramiko helper 只在导入 Paramiko 时精确抑制 `CryptographyDeprecationWarning`；PowerShell preflight 捕获 native stderr 和退出码，避免 Windows PowerShell 5.1 的 `ErrorActionPreference=Stop` 将弃用警告误判为发布失败，同时缺依赖仍返回明确安装提示。
- 远端部署与 status 成功后，helper 使用同一 SSH client 执行固定参数 curl 请求公网 `/health`，解析 JSON 并强制要求 `status=ok`；远端检查失败保持发布失败。本机 PowerShell health 只作附加诊断，本机代理、TUN DNS、TLS 或状态异常使用 warning，不覆盖远端已经通过的公网判定。

## 关键检查点

- 禁止 `COPY . .`。
- 禁止把完整 config 或完整 workspace 作为云端 volume；只允许微信账号凭据子目录使用专用命名卷。
- 禁止 Dockerfile 预设 `ZHICE_AGENT_SKILL_REPO`，避免镜像环境抢占 dotenv 覆盖值。
- 禁止在测试中读取或输出三个真实私有文件内容。
- 禁止在测试中读取或输出真实 `private/cloud-target.json`；只能检查公开 example 和 ignore 规则。
- 文档静态测试只读取公开 `deploy/README.md`，不得探测或打开 `deploy/private/.env`、`config.yml`、`models.json`。
