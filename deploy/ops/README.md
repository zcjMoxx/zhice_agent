# ZhiCe 独立服务器 Ops

该目录安装独立于 `zhice-agent` 容器的受限运维面。共享页面提供“监控面板 / 运维终端”切换；Caddy 在 loopback 统一认证并路由固定 dashboard API 与 `/terminal/` ttyd。ttyd 唯一后端是 `zhice-ops-shell`；它不是 Bash，也不接受任意 Docker、任意容器名或任意文件路径。

## 安全模型

- ttyd 固定为 `1.7.7` x86_64，并在安装前校验仓库记录的 SHA-256。
- systemd 以 `zhice-operator` 分别运行 Caddy、dashboard adapter 与 ttyd；该用户使用 `nologin`，安装时会从 `docker` group 移除。
- 需要 root 的动作只通过 root-owned Python wrapper；sudoers 不允许 `sudo -i`、shell、Docker CLI 或其它 Python 参数。
- wrapper 固定容器为 `zhice-agent`，固定配置目录为 `/etc/zhice-agent/runtime`。
- Caddy 与 ttyd 强制同一个独立 `owner` Basic Auth；首次安装在服务器生成 48 位十六进制密码，root-only 保存且升级保留。Caddy bcrypt hash 通过 stdin 生成，不把明文放入参数；配置正文只显示在已认证终端中，dashboard API 不提供配置正文；journald 仅记录动作、固定目标和结果。
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
3. 不创建 Cloudflare Access application；公网页面/API 先由 Caddy Basic Auth 拒绝，`/terminal/` 再由 ttyd 使用同 credential 认证。
4. 首次安装后，只在自己的 SSH 终端读取一次 credential：

   ```sh
   sudo grep '^ZHICE_OPS_CREDENTIAL=' /etc/zhice-ops/ops.env
   ```

   输出格式为 `ZHICE_OPS_CREDENTIAL=owner:<password>`。不要粘贴到聊天、仓库、shell history 或日志。独立窗口首次输入后，当前浏览器会话通常会缓存 Basic Auth，随后 iframe 可复用；不能复用时继续使用独立窗口。

Cloudflare 记录 Tunnel/DNS 访问证据；宿主机 journald 记录 gateway/terminal/container/config 动作，不记录 credential 或完整终端字节流。禁止通过公网安全组直接暴露 `7681..7683`；Caddy/dashboard 显式绑定 loopback，ttyd 的全地址 socket 由 systemd `IPAddressDeny/Allow` 实测阻断私网。拥有宿主机 root/进程检查权限的管理员属于服务器信任边界。

## 双视图

认证后首页默认进入监控面板，展示固定容器状态、健康、诊断、格式化日志跟随和确认式重启；切换“运维终端”时打开同源 `/terminal/` restricted ttyd。iframe 首次设置后保持挂载，来回切换不会主动销毁 PTY。服务器配置查看/编辑/校验/diff/备份/恢复/apply 仍只存在于 restricted ttyd，不暴露网页配置 API。

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

## 外部验收

Windows 静态测试不能替代：systemd 重启、ttyd resize/idle/max-session、无/错误 Basic Auth 拒绝、正确认证和 iframe/PTY 复用、真实 Docker 状态/日志/重启、容器退出时的独立救援、credential 与三份配置跨 Digest 保留，以及 journald/Cloudflare 审计中无 Secret。当前目标服务器已验证三个服务 active、loopback/public 匿名 `401`、认证页面/API/ttyd index `200`、公网监控页可达和三个端口私网不可达；真实浏览器 WebSocket/iframe 仍需交互验收。
