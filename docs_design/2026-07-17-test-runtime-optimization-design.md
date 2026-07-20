# 测试运行时间优化设计

> 日期：2026-07-17
> 状态：已落地

## 背景

仓库全量 pytest 有 431 个用例，实测约 204 秒。耗时榜显示主要成本来自生产级 PBKDF2 600,000 次迭代在大量认证单测中重复执行；其次是 MCP stdio、Streamable HTTP、SSE 和 OAuth 测试真实启动子进程或本地 Server。

## 目标

- 不改变生产密码哈希参数和运行行为。
- 默认单元测试保持快速、稳定，适合每次开发后执行。
- 真实 MCP transport 仍保留可显式运行的集成测试。
- 消除 pytest cache 写入仓库根目录时的 Windows 权限警告。

## 方案

1. `tests/conftest.py` 通过 autouse fixture 仅在 pytest 用例内把 PBKDF2 迭代数降为 2,000；生产模块默认值仍为 600,000。
2. 真实启动 stdio/HTTP/SSE/OAuth Server 的用例标记为 `integration`。
3. 默认 `python -m pytest` 排除 `integration`；提交前或修改 MCP transport 时显式运行：

   ```text
   python -m pytest -m integration tests/unit_test/mcp
   ```

4. pytest cache 改到 `.tmp/pytest_cache`。
5. 默认使用 `pytest-xdist` 的 4 个 worker 并行执行互相隔离的单元测试。
6. HTTP fake Server 等待逻辑检查子进程提前退出，并放宽高负载下的就绪时间，减少偶发失败。

## 验收标准

- 生产 `PBKDF2_ITERATIONS` 保持 600,000。
- 默认全量 pytest 通过且明显快于优化前 204 秒。
- MCP integration 测试可单独运行通过。
- Ruff 通过。
