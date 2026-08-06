# ZhiCe-Agent 本地部署入口层级调整设计

> 说明：本文记录当时把 PowerShell 入口移出 `scripts/` 的方案。当前结构已于 2026-08-04 进一步收敛：根目录只保留 `deploy-local.cmd` 双击入口，实际 PowerShell 编排位于 `deploy/pipelines/deploy-local.ps1`；应参考 `2026-08-04-private-registry-cloud-release-pipeline-design.md` 和 Part 17 活文档。

## 背景

2026-07-31 已实现无参数本地部署流水线，但入口位于 `deploy/scripts/deploy-local.ps1`，与 build、smoke、push 和云端运维等底层脚本混在同一目录。用户需要先理解 `scripts/` 才能找到日常入口，违背“一条命令完成本地部署”的目标。

## 目标

- 将日常入口移动到 `deploy/deploy-local.ps1`。
- 提供可从 Windows 资源管理器直接双击的 `deploy/deploy-local.cmd`。
- `deploy/scripts/` 只保留底层构建、烟测、推送和云端运维脚本。
- 保持流水线步骤、默认阿里云 APT、镜像名、端口、健康检查和数据卷语义不变。
- README 的首选命令只展示根目录入口。

## 范围边界

本次只调整脚本层级及引用，不改变 Dockerfile、Compose 服务、镜像内容、Secret 处理、命名卷或云端部署流程，也不执行真实部署。

## 模块设计

`deploy/deploy-local.ps1` 使用自身目录作为 `deployRoot`，再从 `deploy/scripts/` 定位 `build-image.ps1` 和 `run-local.ps1`。其余流水线逻辑保持不变。

Windows 默认可能把 `.ps1` 关联到记事本，因此 `deploy-local.cmd` 作为图形化入口：使用 `%~dp0` 定位同目录 PowerShell 脚本，以当前进程级 `ExecutionPolicy Bypass` 执行，透传退出码，并在成功或失败后暂停窗口。它不提升权限、不修改系统 Execution Policy，也不复制流水线逻辑。

真实双击验收发现 Windows PowerShell 会把原生程序写入 stderr 的内容包装为 `NativeCommandError`：当 `zhice-agent-smoke` 正常不存在时，直接执行 `docker rm` 会输出 `No such container`，并在 `$ErrorActionPreference = "Stop"` 下提前终止流水线。`run-local.ps1` 因此必须先通过成功返回的 `docker ps` 查询固定名称，只有容器存在时才执行 stop/remove；启动前和 `finally` 复用同一清理函数。

首次修正继续暴露了 PowerShell 的零输出语义：命令替换没有输出时可能得到 `$null`，不能调用 `.Trim()`。容器名称查询统一包装为数组，并使用空数组安全的 `-contains` 判断；零匹配不再需要字符串方法或特殊异常处理。

目录职责调整为：

```text
deploy/
  deploy-local.cmd        # Windows 双击入口
  deploy-local.ps1        # 日常本地部署唯一入口
  Dockerfile
  docker-compose.yml
  scripts/
    build-image.ps1       # 底层构建
    run-local.ps1         # 底层 smoke
    push-image.ps1        # registry 推送
    *.sh                  # 云端运维
```

## 数据流

```text
deploy/deploy-local.ps1
  -> deploy/scripts/build-image.ps1
  -> deploy/scripts/run-local.ps1
  -> deploy/docker-compose.yml
  -> healthy local container

deploy/deploy-local.cmd
  -> deploy/deploy-local.ps1
```

## 变更文件

- `deploy/deploy-local.cmd`
- `deploy/deploy-local.ps1`
- `deploy/scripts/deploy-local.ps1`（移除）
- `deploy/README.md`
- `tests/unit_test/deploy/test_deploy_assets.py`
- `tests/unit_test/deploy/test_case.md`
- `docs_design/2026-07-31-configurable-apt-mirror-design.md`
- `docs_design/zhice-agent-part17-reliability-diagnostics-deployment-design.md`

## 测试方案

- 验证根目录入口存在，旧 `scripts/` 入口不存在。
- 验证新入口正确解析 `deploy/scripts/` 和 Compose 文件。
- 验证 CMD 使用自身目录定位 PS1、保留退出码并暂停窗口，且不包含部署逻辑副本。
- 验证 smoke 清理先查询固定容器名称，不把“容器不存在”当作失败，也不使用忽略所有原生错误的宽泛处理。
- 验证 Docker 名称查询使用数组和 `-contains`，零条输出时不调用实例字符串方法。
- 运行 deploy 单元测试、Ruff、PowerShell 语法解析和 `git diff --check`。
- 不运行流水线，不创建镜像或容器。

## 验收标准

- 用户只需记忆 `.\deploy\deploy-local.ps1`。
- Windows 用户可直接双击 `deploy-local.cmd`，终端用户仍可执行 PowerShell 入口。
- 流水线仍调用原有底层脚本并保持无参数。
- 文档和测试不再引用旧路径。
- Docker 运行状态不因本次文件移动而改变。
- 首次部署时即使没有历史 smoke 容器，流水线也能继续进入临时容器启动阶段。
