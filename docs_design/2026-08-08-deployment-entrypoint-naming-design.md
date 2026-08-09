# 部署入口显式命名设计

## 背景

2026-08-04 的私有镜像发布流水线形成了本地部署、已有镜像上云、源码完整上云三个入口，但 `deploy-local`、`deploy-cloud-image`、`deploy-cloud` 不能直接表达“是否重新构建镜像”。操作者容易把 `deploy-cloud-image` 理解为“构建云端镜像并部署”，实际却会直接发布已有的 `zhice-agent:local`。

## 目标

- 文件名同时表达是否构建镜像和部署目标。
- Windows 双击入口与 PowerShell 流水线保持同名。
- 不改变构建、smoke、Compose、ACR、Digest 或远端部署行为。

## 范围边界

- 只重命名三个日常入口并同步文档与测试。
- `deploy/scripts/` 底层脚本和 `invoke-cloud-release.ps1` 保持不变。
- 不保留旧名兼容入口，避免目录中继续出现含义相近的重复命令。

## 模块设计

入口映射如下：

| 新入口 | 输入 | 行为 |
| --- | --- | --- |
| `build-and-deploy-local` | 当前源码与私有配置 | 构建镜像、smoke、本地 Compose 部署 |
| `deploy-existing-image-to-cloud` | 已有 `zhice-agent:local` | 不构建镜像，直接生成发布标签、推送并部署云端 |
| `build-and-deploy-cloud` | 当前源码与私有配置 | 构建镜像、smoke、推送并部署云端 |

每个根目录 `.cmd` 只调用 `deploy/pipelines/` 下同名 `.ps1`，继续透传退出码并暂停窗口。流水线内部调用关系和参数保持原样。

## 数据流

```text
build-and-deploy-local
  -> build-image -> smoke -> local Compose

deploy-existing-image-to-cloud
  -> existing zhice-agent:local -> cloud release

build-and-deploy-cloud
  -> build-image -> smoke -> cloud release
```

## 变更文件

- 重命名 `deploy/*.cmd` 三个用户入口。
- 重命名 `deploy/pipelines/*.ps1` 三个编排入口。
- 更新 `deploy/README.md`、Part 17 活文档和设计索引。
- 更新部署单元测试与 `test_case.md`。
- 在被本方案替代的日期设计记录标题下补充说明，不改写历史正文。

## 测试方案

- 部署资产清单只接受六个新入口文件。
- CMD 薄入口必须指向同名 pipeline，且不包含 Docker 实现。
- 三条 pipeline 的 build、smoke、本地 Compose和云端发布边界保持原测试覆盖。
- 运行部署主题单测和 Ruff。

## 验收标准

- `deploy/` 中三个 CMD 仅凭文件名即可区分行为。
- 旧入口文件不存在，仓库当前文档不再指导用户运行旧名。
- 已有镜像入口仍不调用 `build-image.ps1`；两个 build 入口仍分别完成本地或云端目标链路。
- 相关测试全部通过。
