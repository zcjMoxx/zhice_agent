# ZhiCe-Agent 打包与入口设计

## 背景

参考项目通过 `pyproject.toml` 的 `[project.scripts]` 暴露 `xagent` 命令，并使用 `hatchling` 作为 build backend。开发环境里执行一次 `pip install -e .` 后，命令会安装到当前 Python 环境的 `Scripts` 目录，因此日常运行不需要手动激活 `.venv`。

当前 ZhiCe-Agent 已经通过 `[project.scripts]` 暴露 `zcagent`，但使用 `setuptools` backend，editable install 后源码目录会出现 `zcagent.egg-info`。为了贴近参考项目，改为 `hatchling`。

## 目标

- 保持 `zcagent` 单一命令入口。
- 保持一次安装后可直接运行 `zcagent`、`zcagent gateway`。
- 避免源码目录出现 setuptools editable install 的 `*.egg-info` 元数据。
- 不把 `.venv` 作为日常使用的强制步骤。

## 方案

- `[build-system]` 改为 `hatchling.build`。
- `[tool.hatch.build.targets.wheel]` 指定 `packages = ["agent"]`。
- 继续保留 `[project.scripts] zcagent = "agent.cli:main"`。

## 使用方式

开发机全局命令体验：

```bash
python -m pip install -e .
zcagent
zcagent gateway
```

隔离环境体验仍然可用：

```bash
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e .
.\.venv\Scripts\zcagent.exe
```

## 验收标准

- `python -m ruff check .` 通过。
- `python -m pytest` 通过。
- `pyproject.toml` 与参考项目一样使用 `hatchling` backend。
