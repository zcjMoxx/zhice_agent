# Python 3.11 Subagent Dataclass 兼容修复设计记录

> 日期：2026-07-30
>
> 状态：已实现并通过真实 Docker 烟测
>
> 归属：Part 17 私有镜像真实烟测修复

## 背景

本地 Python 3.12 全量测试通过，但 Debian Bookworm 镜像中的 Python 3.11 在导入 `SubagentConfig` 时拒绝 `MappingProxyType({})` 作为 dataclass 字段默认值，Gateway 因 `ValueError: mutable default ... mappingproxy` 在启动前退出。修复后第二次 smoke 已确认 Gateway、`/health` 和 Web 首页正常，但 `run-local.ps1` 使用 `$home` 保存首页响应；PowerShell 变量不区分大小写，该名称撞上只读自动变量 `$HOME`，赋值异常又被轮询内 `catch` 吞掉，导致成功服务被误报为超时。

## 目标

- 保持 `profiles` 默认值为空且不可变。
- 使用 dataclass `default_factory` 创建每实例独立的空 mapping proxy。
- 保持配置加载、Profile 顺序和 fail-closed 行为不变。
- 避免 PowerShell smoke 使用保留自动变量名，确保 200 响应能结束轮询。
- 用最终 Docker 镜像重新完成 health、Web 和 gateway check 烟测。

## 范围边界

- 只修改 Subagent 配置 dataclass 的默认值构造方式。
- 不改变协议、配置 schema、AgentLoop 或部署拓扑。
- 不引入新依赖。

## 模块设计与数据流

`SubagentConfig()` 由 `field(default_factory=...)` 为 `profiles` 创建新的 `MappingProxyType({})`；显式加载配置时仍使用校验后的 mapping，不改变现有数据流。

## 变更文件

- `agent/subagents/config.py`
- `tests/unit_test/subagents/test_subagent_config.py`
- `tests/unit_test/subagents/test_case.md`
- `deploy/scripts/run-local.ps1`
- `tests/unit_test/deploy/test_deploy_assets.py`
- `tests/unit_test/deploy/test_case.md`

## 测试方案

- 验证两个默认配置实例获得不同的空 mapping proxy。
- 验证默认 mapping 不可修改。
- 运行 Subagent 配置主题测试和 Ruff。
- 静态验证 smoke 脚本不再写入 `$HOME`，并使用独立首页响应变量。
- 重建镜像并执行真实容器 smoke。

## 验收标准

- Python 3.11 可导入并实例化 `SubagentConfig`。
- 默认 `profiles` 仍为空、不可变且实例间不共享。
- 既有 Subagent 测试无回归。
- health 与 Web 均返回 200 时，PowerShell smoke 能立即进入容器内 gateway check。
- 最终镜像 `/health`、Web 首页和 `gateway --check` 通过。

## 实施与验证结果

- Python 3.11 容器内导入并实例化 `SubagentConfig`：通过。
- Subagent 配置与 deploy 静态定向测试：`19 passed`。
- 定向 Ruff 与 PowerShell parser：通过。
- 全量 Ruff：通过；Python 全量测试：`798 passed, 1 skipped`。
- `zhice-agent:local` 镜像构建：通过。
- 最终镜像 smoke：`/health` 200、Web 首页 200、容器内 `gateway --check` 通过。
- Docker Compose 本地部署：容器 `healthy`，宿主机 `/health` 与 `/` 均返回 200。
