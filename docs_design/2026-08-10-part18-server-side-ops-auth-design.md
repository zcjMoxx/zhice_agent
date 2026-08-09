# ZhiCe-Agent Part 18 服务器侧 Ops 认证收敛设计

> 说明：本文确定的“既有 Tunnel + 服务器 Basic Auth”继续有效；当前代码已按 `2026-08-10-part18-unified-ops-dual-view-design.md` 在 ttyd 前增加 loopback Caddy 统一认证入口，并在同一 origin 提供监控面板与 `/terminal/` restricted ttyd。本文正文保留认证收敛时的单 ttyd 拓扑记录。

> 日期：2026-08-10
>
> 状态：已确认，替代 Part 18C 中 Cloudflare Access/MFA 作为首选认证层的口径
>
> 前置记录：`2026-08-09-part18-skill-runtime-and-server-ops-design.md`、`2026-08-09-part18-multi-runtime-ops-correction-design.md`

## 1. 背景

目标云服务器已经运行一个健康的 Cloudflare Tunnel connector，主站通过同一 Tunnel 转发到宿主机 loopback Gateway。为 Ops 再创建 connector、Access application、IdP 与 MFA 会增加个人服务器的部署和 iframe 使用复杂度。当前操作者确认改为复用既有 Tunnel，并把独立认证收敛到服务器 ttyd。

## 2. 目标

- 复用现有 Cloudflare Tunnel，把私有 `OpsUrl` 转发到 `127.0.0.1:7681`。
- ttyd 强制使用独立 Basic Auth，用户名固定 `owner`。
- 首次安装在服务器生成高熵密码，后续安装、重启和新 Digest 保留。
- credential 只保存在 `/etc/zhice-ops/ops.env`，权限为 `root:root 0600`，不进入仓库、镜像、命令参数、部署输出或 journald。
- 本地进程和本地 Docker 继续只绑定 loopback，不增加认证步骤。

## 3. 范围边界

继续保留 restricted `zhice-ops-shell`、固定容器、单会话、idle timeout、root-owned wrapper、精确 sudoers、配置事务和 Secret 脱敏。不新增通用 Shell、任意 Docker、多个服务器、keyring、Secret Manager 或 Web Owner 到宿主机的代理信任。

Cloudflare Tunnel 继续提供 HTTPS 与公网到 loopback 的传输，但当前部署不创建 Cloudflare Access application，不要求 IdP/MFA，也不直接开放安全组 `7681`。

## 4. 模块设计

```text
Browser
  -> private OpsUrl
  -> existing Cloudflare Tunnel
  -> 127.0.0.1:7681
  -> ttyd Basic Auth
  -> zhice-ops-shell
  -> fixed root wrapper
  -> zhice-agent only
```

`install.sh` 优先读取并校验既有 `ZHICE_OPS_CREDENTIAL=owner:<48 hex>`；缺失或非法时从 `/dev/urandom` 生成 24 bytes 并编码为 48 位十六进制。临时文件原子替换 `ops.env`，最终权限固定 `0600`。安装输出只说明 credential 已保留或生成，不回显正文。

systemd 从 root-only `EnvironmentFile` 加载 credential，ttyd 使用固定 `--credential` 参数实施 HTTP Basic Auth，并传入 Linux 接口名 `--interface lo`。由于部分 ttyd/libwebsockets 构建仍会建立全地址 socket，systemd 同时以 `IPAddressDeny=any`、`IPAddressAllow=localhost` 的 cgroup 网络策略作为强制边界；验收必须从宿主机私网地址确认连接失败，不能只看 `ss` 的监听文本。拥有宿主机进程检查权限的管理员仍属于服务器信任边界；公网匿名访问必须在进入 restricted shell 前被 ttyd 拒绝。

## 5. 数据流

```text
first install -> generate owner credential -> root-only ops.env -> restart ttyd
upgrade       -> validate existing credential -> preserve -> restart ttyd
browser       -> HTTP 401 challenge -> owner credential -> restricted terminal
```

主 Web 独立窗口首次完成浏览器 Basic Auth 后，后续 iframe 可复用当前浏览器认证缓存；浏览器不复用时继续使用独立窗口，不把 credential 交给 Gateway。

## 6. 变更文件

- `deploy/ops/install.sh`
- `deploy/ops/systemd/zhice-ops.service`
- `deploy/ops/config/ops.env.example`
- `deploy/scripts/remote_ops.py`
- `deploy/ops/README.md`、`deploy/README.md`
- Part 18 活文档、总体设计与交叉引用
- `tests/unit_test/deploy/`

## 7. 测试与验收

- Shell 静态语法和 Ruff/pytest 通过。
- 静态断言 ttyd 强制 credential、安装器生成并保留 credential、文件权限为 `0600` 且安装输出不含正文。
- Linux 验收：无 Authorization 返回 `401`，错误密码拒绝，正确密码进入；systemd restart 与重新安装后密码不变。
- Tunnel 验收：私有 `OpsUrl` 指向 loopback `7681`，安全组不开放 `7681`。
- Agent 容器退出后 Ops 仍可登录、诊断和恢复；通用 Shell/Docker/路径逃逸继续失败。

## 8. 验收标准

1. 公网不能绕过 ttyd Basic Auth。
2. credential 不进入 Git、镜像、流水线输出、journald 或 ZhiCe 审计。
3. 服务器升级和 Agent Digest 更新不轮换 credential。
4. 本地启动体验不增加登录步骤。
5. Cloudflare Access/MFA 不再作为当前生产验收阻塞项；服务器侧认证和 Tunnel HTTPS 成为当前真实验收口径。
