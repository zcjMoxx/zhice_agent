# ZhiCe 独立服务器 Ops

该目录安装独立于 `zhice-agent` 容器的受限运维面。共享页面提供“监控面板 / 运维终端”切换；Caddy 在 loopback 统一认证并路由固定 dashboard API 与 `/terminal/` ttyd。ttyd 唯一后端是 `zhice-ops-shell`；它不是 Bash，也不接受任意 Docker、任意容器名或任意文件路径。

## 安全模型

- ttyd 固定为 `1.7.7` x86_64，并在安装前校验仓库记录的 SHA-256。
- systemd 以 `zhice-operator` 分别运行 Caddy、dashboard adapter 与 ttyd；该用户使用 `nologin`，安装时会从 `docker` group 移除。
- 需要 root 的动作只通过 root-owned Python wrapper；sudoers 不允许 `sudo -i`、shell、Docker CLI 或其它 Python 参数。
- wrapper 固定容器为 `zhice-agent`，固定配置目录为 `/etc/zhice-agent/runtime`。
- 首次安装在服务器生成独立 `owner` 的 48 位十六进制密码，root-only 保存且升级保留。首次登录后 dashboard adapter 用 credential 派生 HMAC 签发长期 `Secure`/`HttpOnly`/`SameSite=Strict` Cookie；Cookie 不含密码，credential 轮换会自动撤销旧登录态。Caddy 认证通过后只向 loopback ttyd 注入固定 Basic header，ttyd 自身仍强制同 credential；配置正文只显示在已认证终端中，dashboard API 不提供配置正文；journald 仅记录动作、固定目标和结果。
- `config edit` 使用 restricted shell 内置输入，不启动 vim、Bash 或任意 `$EDITOR`。

## 安装

在受控 Linux 服务器上先完成一次 `deploy.sh`（建立 `/etc/zhice-agent/runtime`），并安装 `caddy`、`curl`、`python3` 和发行版的 PyYAML 包（Debian/Ubuntu 为 `python3-yaml`），再以 root 从本次 release 执行：

```sh
sh deploy/ops/install.sh "${PublicUrl}" "${OpsUrl}"
```

安装会启动三个 loopback systemd 服务，但不会自动启用 Cloudflare Tunnel。确认文件均为 root-owned，且 `zhice-operator` 不在 docker group：

```sh
systemctl status zhice-ops
systemctl status zhice-ops-dashboard
systemctl status zhice-ops-terminal
id zhice-operator
sudo -l -U zhice-operator
ss -ltn | grep 7681
```

## Cloudflare Tunnel 与服务器认证

1. 复用服务器已经健康运行的 Cloudflare Tunnel connector，不创建第二个 Tunnel 或 systemd connector。
2. 在该 Tunnel 增加已发布应用程序路由：`${OpsUrl} -> http://127.0.0.1:7681`，DNS 由 Cloudflare 自动创建。
3. 不创建 Cloudflare Access application；公网页面/API/终端先由 Caddy `forward_auth` 校验长期签名 Cookie，`/terminal/` 的 loopback 后端再由 ttyd 使用同 credential 认证。
4. 首次安装后，只在自己的 SSH 终端读取一次 credential：

   ```sh
   sudo grep '^ZHICE_OPS_CREDENTIAL=' /etc/zhice-ops/ops.env
   ```

   输出格式为 `ZHICE_OPS_CREDENTIAL=owner:<password>`。不要粘贴到聊天、仓库、shell history 或日志。只需在 Ops 登录页输入一次；服务端会设置不含密码的长期 `HttpOnly` Cookie，浏览器重启、Agent 容器重启、Ops systemd 重启和新 Digest 发布后继续有效。15 分钟 idle 只结束当前 PTY，再打开运维终端不需要重新登录。只有点击页面“退出登录”、主动清理站点数据或服务器 credential 被轮换时才需要再次输入。

Cloudflare 记录 Tunnel/DNS 访问证据；宿主机 journald 记录 gateway/terminal/container/config 动作，不记录 credential 或完整终端字节流。禁止通过公网安全组直接暴露 `7681..7683`；Caddy/dashboard 显式绑定 loopback，ttyd 的全地址 socket 由 systemd `IPAddressDeny/Allow` 实测阻断私网。拥有宿主机 root/进程检查权限的管理员属于服务器信任边界。

## 双视图

认证后首页默认进入监控面板，展示固定容器状态、健康、诊断、格式化日志跟随、确认式重启和显式“退出登录”；切换“运维终端”时打开同源 `/terminal/` restricted ttyd。iframe 首次设置后保持挂载，来回切换不会主动销毁 PTY。服务器配置查看/编辑/校验/diff/备份/恢复/apply 仍只存在于 restricted ttyd，不暴露网页配置 API。

## 命令

允许命令只有：

```text
status
logs [1..500]
logs-follow
diagnose
config view <config.yml|models.json|.env>
config edit <config.yml|models.json|.env>
config validate
config diff
config backup
config restore <backup-id>
config apply
restart
help
exit
```

`restart` 要求再次输入 `restart`。`config edit` 保存时自动备份并写入 root-only pending；`config apply` 校验三份配置后原子替换，并根据 root-owned `/etc/zhice-agent/deployment.spec` 使用当前 immutable Digest 与固定 mounts/ports/volumes 重建容器、等待 health，失败时恢复备份后再次重建。它不能用单纯 `docker restart` 代替。`restore` 也只写 pending，必须显式 `apply`。

## 真实环境验收

长期 Cookie 版本已部署为 `20260810-095829-ops-persistent-login-final2`：三个服务 active，旧 Basic Authorization 自动迁移返回 `303` 并签发长期安全 Cookie，Cookie 访问 dashboard/ttyd index 为 `200`，三个 Ops 服务重启后继续为 `200`，主动退出后受保护 API 恢复 `303`，错误 Basic 拒绝且 loopback ttyd 匿名为 `401`。宿主机权威配置、只读挂载、固定容器 recreate 与跨 Digest 保留也已通过真实发布链验收。

真实浏览器重启、WebSocket/iframe、resize、15 分钟 idle 后交互、max-session 和容器退出时的独立救援仍需人工操作验证。Windows 静态测试不能替代这些交互场景；它们也不表示 Ops 核心实现或 Linux 部署尚未完成。
