# 运行镜像分层瘦身设计

## 背景

Part 19 为携程只读房价查询加入 Playwright bundled Chromium 后，云端当前镜像从约 912 MB 增长到 3.45 GB。服务器 `docker history` 显示，依赖与浏览器安装层约 1.18 GB，随后独立执行的 `chown -R /opt/zhice` 又生成约 718 MB 的 copy-up 层。浏览器是功能所需内容，但跨层递归修改所有权造成的重复不是必要体积。

## 目标

- 保留携程 Playwright、12306、高德、小红书和微信现有运行能力。
- 消除 `/opt/zhice` 与微信 sidecar 的跨层递归所有权复制。
- 不改变容器用户、入口、命名卷、登录态路径或云端部署拓扑。
- 用真实镜像构建和 layer inspection 验证体积下降，再按不可变 Digest 单独更新云服务器。

## 范围边界

本次只优化同一运行镜像的 Docker 分层，不拆分新的浏览器 sidecar，也不删除 Chromium、Node、Python 或旅行 MCP。服务器历史镜像清理不随部署自动执行，避免未经确认删除回滚资产。

## 模块设计

1. 小红书二进制和微信 sidecar 产物使用 `COPY --chown=zhice:zhice`，在进入最终镜像时直接取得目标所有权。
2. Playwright 安装与 `/opt/zhice` 所有权收敛放在同一个 `RUN` 中，使 Chromium 文件只出现在一个最终 layer。
3. 最后的运行目录初始化只处理该层新建的 home/workspace 目录，不再递归触碰 `/opt/zhice` 或 `/app/integrations/weixin_sidecar`。

## 数据流

构建产物和依赖仍按原路径进入最终镜像；运行时继续以 `zhice` 用户读取 `/opt/venv`、`/opt/zhice/playwright`、旅行二进制和微信 sidecar。`zhice-state`、`zhice-xhs-data` 等命名卷不参与镜像构建，云端更新不会覆盖携程 profile 或小红书 Cookie。

## 变更文件

- `deploy/Dockerfile`
- `deploy/README.md`
- `tests/unit_test/deploy/test_deploy_assets.py`
- `tests/unit_test/deploy/test_case.md`
- 本设计记录

## 测试方案

- 静态测试确认跨 stage COPY 使用目标用户，Playwright 安装层内完成 `/opt/zhice` 权限收敛，末尾目录初始化不再递归触碰大型路径。
- 运行 deploy 定向测试与 Ruff。
- 真实构建 `linux/amd64` 镜像，运行既有 smoke，并通过 `docker history` 对比当前 3.45 GB 镜像。
- 云端按 Digest 更新后验证两个容器 health、restart count、公网 `/health`，以及小红书、携程登录状态。

## 验收标准

- 功能测试和镜像 smoke 通过。
- 不再出现独立的数百 MB `chown -R /opt/zhice` layer。
- 新镜像解压体积明显低于 3.45 GB。
- 云端两个容器使用新 Digest、健康且登录态保留。
