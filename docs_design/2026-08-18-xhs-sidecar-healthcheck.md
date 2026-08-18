# 小红书 Sidecar 专用健康检查

## 背景

首次启动窗口修复部署成功后，云端 `zhice-xhs-readonly` 虽已监听 18060 并完成 MCP 初始化，Docker 状态仍长期停留在 `starting`，随后会按主镜像继承的 Gateway `/health` 检查转为 `unhealthy`。Compose 已覆盖该检查，但云端 `docker run` 漏配。

## 目标与边界

为云端 sidecar 配置与 Compose 一致的 TCP 健康检查，使 Docker、Ops 和真实服务状态一致。不改变主容器健康检查、XHS 协议、网络或持久卷。

## 模块设计与数据流

- `docker run` 使用镜像内 Python 连接 sidecar 容器自身的 `127.0.0.1:18060`。
- 检查间隔 30 秒、超时 5 秒、重试 3 次，首次启动保护窗口 15 分钟。
- 部署脚本原有的 2 秒主动 readiness 检查继续作为切换门禁；Docker HEALTHCHECK 服务于部署后的持续监控。

## 变更文件

- `deploy/scripts/deploy.sh`
- `deploy/README.md`
- `tests/unit_test/deploy/test_deploy_assets.py`
- `tests/unit_test/deploy/test_case.md`

## 测试方案

- Shell 语法与部署静态契约测试。
- 云端重新发布后检查 sidecar `health=healthy`、`restarts=0`。
- 同时确认主容器、五个 MCP 和公网健康不退化。

## 验收标准

- XHS sidecar 在 18060 就绪后进入 `healthy`，不会错误探测 10086。
- 主容器与 sidecar 使用同一目标不可变 Digest，均无重启循环。
