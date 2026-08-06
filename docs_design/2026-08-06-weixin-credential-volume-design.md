# 微信账号凭据持久卷设计

## 背景

微信账号绑定凭据由 `WeixinCredentialStore` 写入
`${ZHICE_AGENT_WORKSPACE}/config/channels/weixin/accounts/*.json`。当前 Docker 云部署只持久化
`contexts`、`state`、`logs` 和 `extends`；私有 `.env`、`config.yml`、`models.json` 则由镜像提供。

2026-08-06 云端排障确认：容器重建后 `auth.sqlite3` 中的微信账号仍为 `active`，但账号凭据文件随旧容器可写层被删除。Gateway 随后在每次启动时记录 `channel.start_failed`，微信无法恢复长轮询。

## 目标

- 微信扫码生成的账号凭据跨容器重建和镜像升级保留。
- 不把整个 `config/` 变成持久卷，避免镜像内 `.env`、`config.yml`、`models.json` 无法随发布更新。
- 保持凭据目录只对容器内非 root `zhice` 用户可写。
- 云端部署失败回滚时不删除凭据卷。

## 范围边界

- 只持久化 `config/channels/weixin/accounts`。
- 不迁移已经丢失的 token；现有故障账号需要重新扫码一次。
- 不改变微信 token 格式、数据库 schema、状态机或 OpenClaw Transport。
- 不把真实凭据写入镜像、Git、日志或部署参数。

## 模块设计

新增命名卷 `zhice-weixin-credentials`，固定挂载到：

```text
/home/zhice/.zhice/config/channels/weixin/accounts
```

Dockerfile 在切换到 `USER zhice` 前创建该目录并统一 `chown`。Compose 和云端 `deploy.sh` 都显式挂载同一个语义的命名卷。云端脚本把该卷纳入幂等 `docker volume create`，并通过当前镜像的 root 入口把挂载目录初始化为 `zhice:zhice`、`0700`；因此旧镜像首次热修复和新镜像部署都能写入。任何 stop、restart、rollback 和成功清理都不删除该卷。

完整 `config/` 继续来自当前镜像，因此发布仍能更新静态私有配置；只有运行时生成的微信账号 JSON 覆盖为持久目录。

## 数据流

```text
微信扫码成功
  -> WeixinCredentialStore.stage/promote
  -> /home/zhice/.zhice/config/channels/weixin/accounts/<account>.json
  -> zhice-weixin-credentials named volume
  -> 容器重建
  -> WeixinCredentialStore.read
  -> sidecar account.start
```

## 变更文件

- `deploy/Dockerfile`
- `deploy/docker-compose.yml`
- `deploy/scripts/deploy.sh`
- `deploy/README.md`
- `tests/unit_test/deploy/test_deploy_assets.py`
- `tests/unit_test/deploy/test_case.md`
- `docs_design/README.md`
- `docs_design/zhice-agent-part17-reliability-diagnostics-deployment-design.md`

## 测试方案

- 静态断言 Dockerfile 创建并声明微信凭据目录。
- 静态断言 Compose 只额外挂载微信账号子目录，不挂整个 `config/`。
- 静态断言云端脚本创建并挂载 `zhice-weixin-credentials`。
- 运行 deploy 单元测试和 Ruff。
- 云端以当前不可变镜像重建容器，核对命名卷挂载、容器健康和微信重新扫码后的凭据文件存在。
- 再次重启容器，验证账号仍可启动和收发。

## 验收标准

1. 容器健康，Gateway 和 sidecar 进程存在。
2. 微信扫码后账号凭据文件位于命名卷挂载点。
3. 容器 restart 和下一次 recreate 后凭据文件仍存在。
4. `channel.weixin` 恢复 `available`，微信消息能够完成入站、Agent Turn 和出站发送。
5. `.env`、`config.yml`、`models.json` 仍由镜像更新，真实微信凭据不进入 Git 或日志。
