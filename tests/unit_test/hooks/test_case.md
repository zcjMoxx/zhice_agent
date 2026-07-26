# Hook Runtime 单元测试用例

## 测试目标

验证 Part 12 真实 pre/post Tool Hook 配置、注册、无 shell Runner、workspace guard、受限环境、timeout、输出限制、结构校验和阶段异常策略。

## 用例覆盖

- 缺少`config.yml.hooks`或`entries: []`时返回空Registry，不启动子进程。
- 合法配置按顺序注册；Tool matcher 只接受精确名称或独立 `*`，部分通配符启动失败；`exempt_roles` / `exempt_permissions` 支持缺省、空列表和去重，非法类型或 key 启动失败；重复名称、非法 stage、workspace 外脚本和非法限制值启动失败。
- 真实 Python fixture 通过 stdin/stdout JSON 覆盖 continue、block、modify 和 enrich。
- pre Hook timeout、输出超限、非法 JSON、非法字段和脚本异常 fail closed。
- post Hook timeout、非法输出和脚本异常 fail open，返回空展示 patch。
- Runner 使用 `shell=False`、workspace cwd、最小环境，输入输出均有界。
- timeout fixture 派生长运行子进程，验证 Windows Job Object / POSIX process group 会回收父子完整进程树，测试失败分支也兜底清理。
- post enrichment 只能使用 RuntimeEvent 注册的 display/ui_metadata 结构。
- pre/post Hook 使用相同的单 Hook 角色/权限豁免语义；owner 只有显式配置才跳过，admin 只在有效 `permission_keys` 命中 `exempt_permissions` 时跳过，缺少权限或无身份上下文时仍执行，多 Hook 只跳过命中的那一个并记录 `hook.skipped`。
