# 2026-08-25 private 配置与部署同步设计

## 背景

本地运行时配置位于 `C:\\Users\\84953\\.zhice\\config`，云端镜像构建读取 `deploy/private`，服务器运行时又以 `/etc/zhice-agent/runtime` 为权威配置。三者未形成清晰的校验与更新边界，导致新增工作流、邮件和 MCP 配置可能只存在于本地。

## 目标

- 明确 `.env`、`config.yml`、`models.json` 的 private 部署来源；不处理微信账号凭据。
- 复用仓库已有的 `config/.env.example`、`config/config.example.yml` 和 README 模型示例，不在 `deploy/private` 重复维护 example。
- 将工作流、个人邮件和新增 MCP 所需配置纳入 private 配置校验。
- 保持本地开发配置不自动上传；部署始终使用 `deploy/private`。
- 每次完整云部署覆盖服务器运行配置，并在覆盖前备份，失败时恢复。

## 范围边界

本次不复制微信账号文件、不改变服务器 Docker volume，不把本地 Windows 路径写入云端配置；不将任何真实 Secret 提交到 Git。

## 方案

1. 公开配置模板统一维护在 `config/` 和 README，`deploy/private` 只存 Git 忽略的真实部署文件。
2. `build-image.ps1` 校验 private 配置的 schema、必需环境变量和云端禁止的本地路径。
3. 云端发布只构建并部署 `deploy/private`，每次先备份并替换服务器 `/etc/zhice-agent/runtime`；容器或 sidecar 健康失败时恢复旧配置和旧容器。
4. 云端入口统一执行部署后验收：健康检查后使用固定低权限 `deployment-smoke` 账号，经真实 HTTPS API 创建、保存、读取、发布、执行并删除确定性工作流。核心验收失败时，在删除旧容器和配置备份之前恢复旧容器与旧 runtime。
5. 高德、Tavily、12306、小红书、默认 LLM 和 SMTP 作为独立外部集成检查；单项失败记录 `warning`，未配置记录 `skipped`，均不触发发布回滚。云端入口只允许通过 `-SkipExternalSmoke` 跳过该组检查，核心验收不可跳过。
6. 脱敏报告写入 `/etc/zhice-agent/deployment-reports/`。成功发布后保留最近 5 份 runtime 备份和 30 份报告；失败发布不清理历史现场。

## 数据流

```text
镜像内 private 配置
  -> 服务器 runtime 备份与替换
  -> 新 sidecar / 主容器健康
  -> 公网 HTTPS 核心工作流验收
  -> 外部集成告警型验收
  -> 写入脱敏报告
  -> 成功后删除 previous 容器并执行固定目录保留策略
```

核心工作流固定为 `schedule_trigger -> template`，模板输出携带本次发布唯一标识，不依赖 LLM、MCP 或通知渠道。临时工作流无论成功失败都尝试删除；清理失败自身属于核心失败并记录资源 ID，已有原始失败时不被清理异常覆盖。

## 变更文件

- `deploy/scripts/deployment_smoke.py`：真实 HTTPS 核心与外部集成验收、脱敏报告。
- `deploy/scripts/deploy.sh`：把核心验收纳入容器/runtime 回滚事务并执行保留策略。
- `deploy/scripts/remote_ops.py`、`deploy/pipelines/invoke-cloud-release.ps1`：上传 Python 验收资产并传递外部检查开关。
- `deploy/pipelines/build-and-deploy-cloud.ps1`、`deploy/pipelines/deploy-existing-image-to-cloud.ps1`：暴露统一 `-SkipExternalSmoke`。
- `config/.env.example`、`deploy/README.md`：记录固定低权限账号的私有配置与预创建要求。
- `tests/unit_test/deploy/`：覆盖上传、回滚时序、报告、跳过开关和保留边界。

## 测试与验收

- private 配置校验拒绝占位符、缺失必需键、Windows 本地路径和非法 JSON/YAML。
- 公开 example 继续复用 `config/` 和 README，不在 `deploy/private` 重复维护。
- 运行前端测试、类型检查、lint、生产 build，以及部署脚本静态测试。
- 核心工作流任何步骤失败都会恢复旧容器与旧 runtime；外部检查失败只产生告警报告。
- 成功发布只保留 5 份 runtime 备份和 30 份脱敏报告，失败发布不清理历史。
