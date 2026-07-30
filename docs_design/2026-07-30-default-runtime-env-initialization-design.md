# ZhiCe-Agent 默认生成 Runtime Env 设计记录

> 日期：2026-07-30
>
> 状态：已实现并通过全量验证
>
> 归属：Part 17 配置初始化与部署文档收敛
>
> 承接：`docs_design/2026-07-30-default-workspace-and-runtime-env-design.md`

> 实施说明：普通 `zcagent init`、`init_runtime_files`、`config/.env.example` 唯一模板、`--force` 覆盖和 `--write-env` 兼容语义已落地；README、deploy README 与相关活文档已同步。config、CLI init 和 deploy 静态定向测试共 `81 passed`；主验收 Ruff 通过，Python 全量 `796 passed, 1 skipped`。

## 1. 背景

默认 workspace 与 runtime env 统一方案已经落地：workspace 默认使用 `Path.home() / ".zhice"`，运行态 dotenv 位于 `${workspace}/config/.env`，解析优先级为 `--workspace > 进程 ZHICE_AGENT_WORKSPACE > 默认目录`。自动发现的 workspace env 不得反向定义 workspace，显式 `--env-file` 继续承担兼容入口。

本方案提出时仍有一处初始化语义不一致：`config.yml`、`models.json` 和 Prompt 在普通 `zcagent init` 中生成，而 `${workspace}/config/.env` 只有显式传入 `--write-env` 才生成。对用户而言，这三个文件都属于标准运行配置，env 不应成为需要额外发现并记忆的特殊分支；该差异现已按本文方案消除。

本次设计只统一初始化行为，不改变已经落地的 workspace 解析、dotenv 加载优先级、Secret 边界或 deploy 私有覆盖层。

## 2. 目标

1. 将 `config/.env.example` 设为公开仓库中 runtime env 的唯一模板真值。
2. 普通 `zcagent init` 默认生成 `${workspace}/config/.env`，与 `config.yml`、`models.json` 一视同仁。
3. 保持初始化幂等：已有 env 默认不覆盖，显式 `--force` 才覆盖。
4. 保留 `--write-env` CLI 兼容性，但它不再是生成 env 的前提。
5. 同步 `init_runtime_files` 的默认语义，避免 CLI 与底层初始化函数形成两套行为。
6. 更新 deploy 操作文档，清楚区分当前 workspace 布局与旧项目 env 的迁移路径。

## 3. 范围边界

### 3.1 本次包含

- `config/.env.example` 的公开模板边界。
- `zcagent init` 与 `init_runtime_files` 的默认 env 生成行为。
- `--write-env` 兼容语义。
- `--force` 对 env 的覆盖语义。
- README、deploy README 和相关活文档的初始化说明。
- CLI/config/deploy 文档测试及正常、兼容、覆盖、边界测试。

### 3.2 本次不包含

- 不改变 workspace 解析优先级。
- 不允许 `${workspace}/config/.env` 定义或切换 workspace。
- 不改变显式 `--env-file` 可以兼容提供 workspace 的行为。
- 不删除项目 `config/.env` 的遗留迁移 fallback。
- 不把真实 Secret 写入公开模板、Git 或普通日志。
- 不改变 `deploy/.env`、`deploy/config.yml`、`deploy/models.json` 的私有 Git 边界。

## 4. 模板设计

`config/.env.example` 是公开唯一模板：

- 必须提交到 Git。
- 不包含 `ZHICE_AGENT_WORKSPACE`。
- 不包含真实 API key、密码、token、cookie 或渠道凭据。
- Secret 字段只使用空值或明显占位符。
- 本地初始化和部署说明都引用这一份模板，不在 `deploy/` 或其它目录维护第二份会漂移的 runtime env 模板。

`zcagent init` 将该模板复制为：

```text
${workspace}/config/.env
```

生成后的文件属于本地运行态配置，不进入远端仓库。

## 5. 初始化语义

### 5.1 普通初始化

```text
zcagent init
  -> resolve workspace
  -> create workspace/config
  -> initialize config.yml
  -> initialize models.json
  -> initialize config/.env
  -> initialize prompts and other runtime assets
```

普通初始化对 env 的行为与其它标准运行配置一致：

- 目标不存在：从 `config/.env.example` 生成。
- 目标已存在：保留用户文件。
- 缺少公开模板：返回明确初始化错误，不生成不完整或猜测内容。

### 5.2 强制覆盖

```text
zcagent init --force
```

`--force` 对 `config/.env` 使用与 `config.yml`、`models.json` 一致的覆盖规则：使用当前公开模板重建文件。命令执行前后的提示不得打印 env 内容或 Secret。

### 5.3 `--write-env` 兼容

`--write-env` 保留在 CLI 参数中，避免旧脚本和用户命令失效，但不再控制是否生成 env：

```text
zcagent init
zcagent init --write-env
```

两者的文件结果相同。`--write-env` 成为兼容 no-op / 兼容别名，可以在帮助文本中标记为 deprecated compatibility option，但当前阶段不删除、不报错，也不引入不同覆盖行为。

### 5.4 `init_runtime_files`

底层 `init_runtime_files` 默认就必须包含 env 初始化，CLI 不应再通过额外布尔分支开启它。若为源码兼容暂时保留旧参数：

- 旧参数不得关闭默认 env 生成。
- 应在内部归一为相同结果。
- 新调用方不再依赖该参数决定是否写 env。

这样直接调用初始化函数、`zcagent init` 和后续部署辅助入口都共享同一语义。

## 6. 数据流

```text
repository config/.env.example
          |
          | zcagent init / init_runtime_files
          v
${workspace}/config/.env
          |
          | next process startup, after workspace is resolved
          v
runtime environment expansion
```

边界保持不变：

- workspace 仍在加载 workspace env 之前确定。
- `${workspace}/config/.env` 中即使出现 `ZHICE_AGENT_WORKSPACE` 也不得反向改变 workspace。
- 已存在进程环境变量不被 dotenv 覆盖。
- 模板复制和完成提示不得泄露值。

## 7. Deploy README 迁移说明

`deploy/README.md` 必须区分两种来源：

### 7.1 当前布局

新版本本地运行配置从当前 workspace 复制：

```text
${workspace}/config/.env   -> deploy/.env
${workspace}/config/config.yml -> deploy/config.yml
${workspace}/config/models.json -> deploy/models.json
```

默认 Windows workspace 是 `C:\Users\<user>\.zhice`。

### 7.2 遗留迁移

只有旧版本环境尚未迁移时，才从项目源码目录的 `config/.env` 复制到 `deploy/.env`。文档必须把这一路径标记为 legacy migration，而不是当前推荐路径。

无论来自哪种布局，复制后都必须检查并删除 `ZHICE_AGENT_WORKSPACE`：

- deploy 镜像使用容器用户默认 workspace `/home/zhice/.zhice`。
- 不允许把本机 Windows workspace locator 烘入镜像。
- 不改变其它真实 Secret 与部署配置的私有边界。

## 8. 变更文件

代码与模板：

```text
agent/config.py
agent/cli.py
config/.env.example
```

测试：

```text
tests/unit_test/config/test_config.py
tests/unit_test/config/test_case.md
tests/unit_test/cli/test_cli_init.py
tests/unit_test/cli/test_case.md
tests/unit_test/deploy/test_deploy_static.py
tests/unit_test/deploy/test_case.md
```

当前文档同步：

```text
README.md
deploy/README.md
docs_design/README.md
docs_design/zhice-agent-overall-design.md
docs_design/zhice-agent-part2-no-tool-chat-design.md
docs_design/zhice-agent-part17-reliability-diagnostics-deployment-design.md
```

如实际文件名或测试拆分与当前仓库略有不同，以相同主题目录中的既有文件为准，不新增重复初始化实现。

## 9. 测试方案

### 9.1 正常路径

- 普通 `zcagent init` 在空 workspace 生成 `config/.env`、`config.yml`、`models.json` 和 Prompt。
- 生成的 env 与 `config/.env.example` 内容一致。
- 默认 workspace 与显式 workspace 均生成到各自的 `config/.env`。

### 9.2 幂等与覆盖

- 已有 `config/.env` 时普通 init 保持内容和时间语义不变。
- 缺少 env、但其它配置已存在时，普通 init 只补齐 env。
- `--force` 使用当前模板覆盖已有 env。
- env 覆盖或保留不影响 `config.yml`、`models.json` 的既有规则。

### 9.3 CLI 兼容

- `zcagent init` 与 `zcagent init --write-env` 生成结果一致。
- `--write-env` 继续被 parser 接受，不产生未知参数错误。
- 兼容参数不绕过 `--force`，也不触发第二次特殊写入。

### 9.4 安全与边界

- `config/.env.example` 不含 `ZHICE_AGENT_WORKSPACE`。
- 公开模板不包含形似真实 Secret 的值。
- `${workspace}/config/.env` 不能反向切换 workspace。
- 初始化日志和 CLI 完成提示不回显 env 内容。
- deploy README 的当前路径和 legacy 路径均要求复制后移除 workspace key。

### 9.5 回归

至少运行：

```text
python -m ruff check .
python -m pytest
```

并执行 CLI/config/deploy 主题定向测试，确认默认 workspace、显式 workspace、显式 `--env-file` 和项目 env fallback 的既有优先级不变。

## 10. 验收标准

1. 空 workspace 执行普通 `zcagent init` 后必有 `${workspace}/config/.env`。
2. env、`config.yml`、`models.json` 使用一致的“缺失补齐、默认保留、`--force` 覆盖”语义。
3. `--write-env` 继续可用，但不再是生成 env 的前提，且与普通 init 结果一致。
4. `init_runtime_files` 默认生成 env，CLI 和直接调用方没有语义分叉。
5. `config/.env.example` 是唯一公开模板，不含 workspace locator 和真实 Secret。
6. `deploy/README.md` 优先指导从 `${workspace}/config/.env` 复制，并将项目 `config/.env` 明确标为 legacy migration。
7. 任一复制来源进入 `deploy/.env` 后均明确要求移除 `ZHICE_AGENT_WORKSPACE`。
8. workspace 解析和 dotenv 加载优先级保持现状，无回归。
9. Ruff、全量 Python 与相关静态检查通过；如真实 Docker 环境不可用，明确记录未执行的镜像验收，不以静态检查冒充真实 smoke。
