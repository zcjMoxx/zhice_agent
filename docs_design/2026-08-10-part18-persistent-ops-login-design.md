# ZhiCe-Agent Part 18 服务器 Ops 长期登录设计

> 日期：2026-08-10
>
> 状态：已实现并部署；服务器自动验收已通过，真实浏览器重启与 WebSocket/iframe 交互继续单列

## 1. 背景

服务器 Ops 当前由 Caddy 与 ttyd 使用同一组随机 Basic Auth credential。credential 在首次安装时生成并仅保存在服务器 root-only 文件中，升级时不会轮换；但是浏览器是否长期缓存 Basic Auth 不受服务端控制。浏览器进程关闭、缓存失效或 iframe 重新挑战后，操作者可能需要再次进入云服务终端读取不可记忆的随机密码。这违背了 Ops 在 Agent 容器故障时仍可直接救援、日常无需云厂商终端的产品目标。

## 2. 目标

- 首次输入现有 Ops credential 后建立长期浏览器登录，浏览器重启后继续有效。
- 登录态默认持续到主动退出、浏览器清理站点数据、credential 轮换或十年绝对上限。
- ttyd 的 15 分钟 idle timeout 只结束当前 PTY，不结束浏览器登录态。
- credential 不进入网页、JavaScript、Cookie、URL、日志或 Gateway。
- Agent 容器停止时，宿主机登录与 restricted Ops 仍然可用。

## 3. 范围边界

本次不引入 Cloudflare Access、OAuth/SSO、Web Owner 到宿主机的代理信任、Secret Manager、多服务器或通用宿主机 Shell。本地进程和本地 Docker Ops 继续使用 loopback 无登录模式。服务器首次登录或 Cookie 丢失后的 credential 恢复仍属于宿主机管理员动作。

## 4. 模块设计

```text
Browser
  -> private OpsUrl
  -> Cloudflare Tunnel
  -> loopback Caddy :7681
       -> /auth/* -> dashboard auth endpoint :7683
       -> forward_auth validates signed HttpOnly Cookie
       -> /api/* -> dashboard fixed operations :7683
       -> /terminal/* -> ttyd :7682 with proxy-injected Basic credential
       -> /* -> shared Ops static page
```

dashboard adapter 使用 `ZHICE_OPS_CREDENTIAL` 的 secret 部分作为 HMAC-SHA256 key，签发只包含版本、签发时间、绝对到期时间和签名的 opaque Cookie。Cookie 固定为 `__Host-zhice_ops_session`，带 `Secure`、`HttpOnly`、`SameSite=Strict`、`Path=/`，不保存明文或可逆 credential。credential 轮换后旧 Cookie 的签名自动失效。

登录页由宿主机 dashboard adapter 提供，不依赖 Agent 容器。用户名固定为 `owner`；密码使用恒定时间比较，失败只返回统一错误。退出端点只清除 Cookie。`/auth/check` 只向 Caddy 返回认证结果，不返回 credential。

从旧 Basic Auth 版本升级时，`/auth/check` 可在服务端验证浏览器仍主动携带的旧 Authorization header，并返回一次带长期 Cookie 的同源 `303`；浏览器跟随原地址后立即进入 Cookie 登录，不把 Authorization 或 credential 交给页面。浏览器已丢失旧缓存时再显示登录页，完成最后一次显式登录。

Caddy 不再向浏览器发起 Basic Auth challenge。通过 Cookie 检查后，Caddy 从 root-only `ops.env` 读取预先生成的 Basic header 值并只在 loopback 反向代理到 ttyd 时注入；浏览器和前端 JavaScript均不可见。ttyd 继续保留自己的 `--credential` 防线。

## 5. 数据流

```text
first login -> owner/password form -> constant-time verify
            -> signed long-lived HttpOnly Cookie -> redirect /

later visit -> Cookie -> Caddy forward_auth -> dashboard verifies HMAC
            -> page/API/terminal without another password prompt

logout      -> clear Cookie -> redirect login
rotation    -> HMAC key changes -> all existing Cookies fail closed
```

## 6. 变更文件

- `deploy/ops/libexec/zhice_ops_dashboard.py`
- `deploy/ops/config/Caddyfile`
- `deploy/ops/install.sh`
- `deploy/ops/config/ops.env.example`
- `agent/operations/static/ops.html`
- `tests/unit_test/deploy/` 与同目录 `test_case.md`
- `deploy/ops/README.md`、`deploy/README.md`、`README.md`
- Part 18 活文档、总体设计、`docs_design/README.md` 与旧日期设计说明

## 7. 测试方案

- 单元测试覆盖 Cookie 签发、正确验证、篡改、过期、credential 轮换、Cookie 属性和安全 next path。
- HTTP 测试覆盖登录页、错误登录、成功登录、check、logout、既有固定 dashboard API。
- 静态测试覆盖 Caddy `forward_auth`、仅对 ttyd 注入 Authorization、ttyd 继续强制 credential、安装器生成 Basic header 且不输出 Secret。
- Ruff 与全量 pytest 通过；Shell 使用 `sh -n`，Caddy 配置在真实 Linux 安装时继续 `caddy validate`。
- 云端真实验收覆盖首次登录、浏览器重启复用、iframe/ttyd WebSocket、15 分钟 PTY 退出后免登录重连、主动退出、错误密码拒绝和升级保留。

## 8. 验收标准

1. 正常浏览器重启后访问 Ops 不再要求重新输入随机密码。
2. Cookie 不包含 credential，且脚本无法读取。
3. 未认证请求不能访问页面、API 或 ttyd；直接访问 loopback ttyd 仍需 Basic Auth。
4. Agent 容器停止不影响登录页、Cookie 校验和 restricted terminal。
5. 主动退出、Cookie 篡改、过期和 credential 轮换全部 fail closed。
6. 部署、日志与 Git 中不出现真实 credential 或 Cookie。
