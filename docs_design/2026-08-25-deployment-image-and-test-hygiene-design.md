# 云部署镜像与测试环境收敛设计

## 背景

完整云部署验收落地后，服务器仍会保留历次拉取的 ZhiCe-Agent 镜像。当前镜像包含浏览器运行时，单个版本占用较大，持续发布会耗尽服务器系统盘。同时，前端 lint 仍有 5 个 warning；Windows 上 pytest 继续使用历史不可访问的 cache 目录和系统临时目录，会产生 `PytestCacheWarning` 与退出期 ACL 异常。

## 目标

- 云部署仅在核心验收成功后收敛 ZhiCe-Agent 历史镜像，保留当前版本和最近一个历史版本。
- 不清理其他仓库镜像、运行中容器使用的镜像、数据卷或构建缓存。
- 前端 lint 达到 0 error、0 warning，不放宽生产源码的全局规则。
- pytest cache 与临时测试目录统一落在仓库内可控路径，避免依赖 Windows `%TEMP%` 和历史 ACL 异常目录。

## 范围边界

- 修改云端 `deploy.sh` 的成功后留存逻辑及对应部署文档、测试。
- 修正工作流页面模板格式，并对测试专用的共置 stub 组件做局部 lint 说明。
- 调整 pytest 配置；不修改产品运行时临时目录策略。
- 不删除服务器数据卷、runtime 配置、部署报告或非 ZhiCe-Agent 镜像。

## 模块设计

### 云端镜像留存

部署脚本从不可变 `IMAGE_REF` 推导固定仓库名，按镜像创建时间倒序去重。当前镜像始终优先保留，再保留一个最近版本；其余同仓库镜像使用镜像 ID逐个删除。删除动作放在核心验收、报告留存和部署规格写入成功之后。Docker 若判定某镜像仍被容器使用则拒绝删除，脚本输出告警但不破坏已经验收通过的发布。

### 前端 lint

工作流页面按 Vue 模板规则展开多行标签。测试文件中的两个 Vue Flow stub 是同一测试夹具的一部分，使用文件级、带原因的局部规则豁免，不改变项目 ESLint 全局规则。

### Windows pytest 路径

pytest 的 `cache_dir` 改到新的 `.tmp/pytest-cache-v2`，并在默认 `addopts` 中设置 `--basetemp=.tmp/pytest-runtime`。两个路径都是现有 `.tmp` 下的一级目录，首次运行不依赖递归建目录且不会互相清理；`.tmp/` 已被 Git 忽略，xdist worker 继续由 pytest 在该 basetemp 下隔离。

## 数据流

1. 云部署完成容器健康检查和核心/外部验收。
2. 写入报告、runtime 备份留存并更新部署规格。
3. 删除 previous 固定容器后，对当前仓库镜像执行数量留存。
4. 本地 pytest 从仓库内 runtime 目录创建 cache 和 worker 临时目录，不再访问系统 pytest numbered temp。

## 变更文件

- `deploy/scripts/deploy.sh`
- `deploy/README.md`
- `tests/unit_test/deploy/test_deploy_assets.py`
- `tests/unit_test/deploy/test_case.md`
- `tests/conftest.py`
- `tests/unit_test/context_builder/test_context_builder.py`
- `tests/unit_test/skills/test_skill_executor.py`
- `pyproject.toml`
- `web/frontend/src/pages/WorkflowPage.vue`
- `web/frontend/src/pages/WorkflowPage.test.ts`

## 测试方案

- 部署专项单测验证仓库范围、保留数量、执行时机和禁止全局 prune。
- 执行部署 Shell 语法检查。
- 执行前端 lint、typecheck、全量测试和生产 build。
- 执行后端全量 pytest，确认无 cache/Temp ACL warning、调用者终端变量污染或高负载实时进度误报；执行 Ruff。

## 验收标准

- 成功部署后同仓库仅保留当前和最近一个版本；失败回滚路径不触发镜像清理。
- 不出现 `docker system prune`、全仓库 `docker image prune -a` 或数据卷清理。
- 前端 lint 输出 0 warning。
- Windows 默认 `python -m pytest` 无 `PytestCacheWarning` 和 `cleanup_numbered_dir` ACL 异常。
- 全量测试、typecheck、lint、Ruff 与生产 build 均通过。
