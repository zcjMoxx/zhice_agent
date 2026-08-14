# 小红书 RedNote Cookie 兼容与自动重载设计

## 背景

本地小红书登录器扫码后会写入包含 `version=2`、双域名 Cookie 与 fingerprint seed 的 `cookies.json`。现有 `LocalXhsSidecarSupervisor` 却固定优先启动通用 `xiaohongshu-mcp-windows-amd64.exe`。真实对照验证显示：同一份刚更新的 Cookie 在通用 sidecar 中持续返回未登录，而 `xiaohongshu-mcp-rednote-v2.4.3.exe` 能返回登录成功，并用“洪崖洞”取得三条结果。因此用户遇到的“扫码成功、刷新又未登录”不是操作问题，而是登录器与运行时二进制不兼容。

此外，扫码登录会在 sidecar 运行期间更新 Cookie 文件。即使使用兼容二进制，如果上游只在进程启动时加载 Cookie，也需要由 supervisor 感知文件变化并自动重启自有 sidecar，不能要求用户手工重启 Gateway。

## 目标

- 本地 Windows supervisor 优先选择 workspace 中版本最高的 RedNote 兼容 MCP 二进制。
- RedNote 二进制不存在时继续回退现有通用二进制，保持旧部署可用。
- 自有 sidecar 运行期间检测 Cookie 文件更新时间或大小变化，并自动重启以加载新登录态。
- 外部占用端口的非自有进程继续只复用、不重启。
- 不读取、记录或输出 Cookie 值。

## 范围边界

- 本次只改变本地 loopback sidecar 的二进制选择和 Cookie 文件变更响应，不改变 Docker 私网 sidecar。
- 不自动发起扫码、不持久化二维码、不把 Cookie 内容写入日志或 Session。
- Cookie 文件删除不触发无意义重启；文件重新出现或内容更新后才重载。
- 不在 AgentLoop 中加入登录业务判断。

## 模块设计

`_binary_path` 在平台固定 bin 目录中先查找 RedNote 兼容文件，并按文件名中的数字版本元组选择最新版本；找不到时回退平台通用文件名。所有候选路径继续 `resolve` 并校验位于 workspace 内。

Supervisor 从 MCP spec 的 `XHS_READONLY_COOKIE_FILE` 派生受限 Cookie 路径，只保存 `(mtime_ns, size)` 签名。watcher 在确认端口健康且进程为自己创建时检查签名：发现有效文件变化后终止自有进程树并按既有有界启动流程重启。外部 listener 因 `_tree is None` 不进入该分支。

## 数据流

```text
扫码登录器
  -> 原子更新 cookies.json
  -> supervisor watcher 发现文件签名变化
  -> 仅重启自有 RedNote sidecar
  -> 新进程加载 Cookie v2
  -> check_login_status / search_notes 正常
```

## 变更文件

- `agent/applications/travel/xhs_sidecar.py`
- `tests/unit_test/travel/test_xhs_sidecar.py`
- `tests/unit_test/travel/test_case.md`
- `docs_design/2026-08-14-xhs-rednote-cookie-compatibility-design.md`

## 测试方案

- RedNote 与通用二进制同时存在时选择最高版本 RedNote。
- RedNote 不存在时回退通用二进制。
- Cookie 文件首次创建、内容更新时间变化和未变化的签名判断正确。
- 端口由外部进程占用时不接管、不重启。
- 现有 supervisor 启动、停止和缺文件测试保持通过。
- 运行目标 Ruff/Pytest，并用真实 Cookie 做登录状态与单关键词搜索 smoke。

## 验收标准

- 当前本地 18063 正式 sidecar 使用 `xiaohongshu-mcp-rednote-v2.4.3.exe`。
- 刚扫码更新的 Cookie 经正式适配器返回登录成功。
- “洪崖洞”只读搜索能返回候选结果。
- 后续重新扫码后无需刷新页面或手工重启 Gateway，sidecar 自动重载 Cookie。
