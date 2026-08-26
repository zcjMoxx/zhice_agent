# 云部署烟测改为显式启用设计

## 背景

当前云发布虽然已有部分 `SkipExternalSmoke` 开关，但默认仍等待小红书 sidecar readiness、主容器 healthy，执行核心工作流 acceptance、外部 MCP/LLM/SMTP smoke、远端公网 health，并在发布端再次请求公网 health。常规迭代因此被多个耗时验证阻塞，且“跳过外部 smoke”仍不能跳过核心工作流、容器 readiness 与公网检查。

## 目标

- 云发布默认不执行部署后烟测。
- 只有操作者显式传入 `-Smoke` 时才执行本地镜像 smoke、sidecar readiness、主容器 health、核心工作流、外部依赖和公网 health。
- 默认路径仍保留镜像格式、Digest、配置、SSH、Docker 启动返回值和 Ops 安装等发布必需校验。
- 容器自身 Docker healthcheck 配置继续存在，默认发布只是不等待其转为 healthy。

## 范围边界

- 不改变本地开发入口 `build-and-deploy-local.ps1` 的现有验证语义。
- 不删除 `deployment_smoke.py`，显式 `-Smoke` 时继续复用完整 acceptance。
- 不改变运行容器、网络、卷、只读配置和失败容器的即时启动回滚逻辑。
- 默认路径不保证返回时服务已 ready；操作者需要验证时必须显式传 `-Smoke`。

## 模块设计

三个云发布 PowerShell 层统一使用正向 `[switch]$Smoke`。`build-and-deploy-cloud.ps1` 与 `deploy-existing-image-to-cloud.ps1` 仅在开关存在时运行本地镜像 smoke，并把同一开关传给 `invoke-cloud-release.ps1`。

`invoke-cloud-release.ps1` 仅在 `-Smoke` 时向 Paramiko helper 传 `--smoke`，且仅在该模式执行发布端公网 health。`remote_ops.py` 把布尔值传入远端 `deploy.sh`；默认不附加 `status.sh` 和远端公网 health，显式 smoke 才执行。

远端 `deploy.sh` 的第五个参数改为 `run-smoke`：

- `0`：启动 XHS sidecar 后立即启动主容器，不等待 readiness/healthy，不运行 `deployment_smoke.py`。
- `1`：保留原有 sidecar readiness、主容器 healthy、核心工作流及全部外部 acceptance。

两种路径都保留 Docker `run` 失败处理、旧容器暂存、配置回滚、部署规格写入和历史清理。

## 数据流

```text
默认云发布
  -> build/push/digest
  -> upload/switch scripts
  -> start sidecar and main container
  -> install Ops
  -> return without smoke

显式 -Smoke
  -> local image smoke
  -> build/push/digest
  -> sidecar readiness
  -> main container healthy
  -> core workflow plus external acceptance
  -> remote and local public health
```

## 变更文件

- `deploy/pipelines/build-and-deploy-cloud.ps1`
- `deploy/pipelines/deploy-existing-image-to-cloud.ps1`
- `deploy/pipelines/invoke-cloud-release.ps1`
- `deploy/scripts/build-image.ps1`
- `deploy/scripts/remote_ops.py`
- `deploy/scripts/deploy.sh`
- `deploy/README.md`
- `tests/unit_test/deploy/test_deploy_assets.py`
- `tests/unit_test/deploy/test_remote_ops.py`
- `tests/unit_test/deploy/test_case.md`

## 测试方案

- 静态契约确认所有云入口默认无 smoke，只有 `-Smoke` 才逐层传递。
- helper 命令构造测试确认默认不执行 `status.sh`，显式 smoke 才执行。
- Shell 静态检查和 PowerShell parser 检查。
- 部署单元测试与 Ruff。

## 验收标准

- 不带参数的两个云入口均不运行本地、容器、核心工作流、外部依赖或公网 smoke。
- 显式 `-Smoke` 一次恢复全部原验证路径。
- 默认发布仍能完成不可变 Digest 部署和 Ops 安装。
- README 清楚说明默认路径的 ready 风险和显式验证命令。
