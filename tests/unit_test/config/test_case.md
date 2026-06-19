# Config 单元测试用例

## 测试目标

验证配置加载、运行时目录派生、dotenv 读取、LLM endpoint 解析和 `zcagent init` 文件初始化都遵守 workspace 边界和 secret 管理约定。

## 用例覆盖

### Case 1: 显式 workspace

- 输入：调用 `load_config(tmp_path)`。
- 预期：所有运行路径都从该 workspace 派生。
- 检查点：`config_dir`、`prompts_dir`、`contexts_dir`、`sessions_dir`、`skills_dir`、`logs_dir` 路径正确。

### Case 2: 环境变量覆盖

- 输入：设置 `ZHICE_AGENT_*` 路径变量后调用 `load_config()`。
- 预期：显式环境变量优先于默认派生路径。
- 检查点：workspace 和各运行目录被正确覆盖，sessions 仍从 contexts 派生。

### Case 3: 缺少 workspace

- 输入：既不传入 workspace，也不设置 `ZHICE_AGENT_WORKSPACE`。
- 预期：抛出 `MissingWorkspaceError`。
- 检查点：错误信息说明如何设置本地 workspace。

### Case 4: 创建运行目录

- 输入：调用 `config.ensure_dirs()`。
- 预期：第一阶段需要的运行目录全部存在。
- 检查点：`config`、`prompts`、`contexts/sessions`、`skills`、`logs` 被创建。

### Case 5: LLM endpoint 解析

- 输入：读取 `config/llm_endpoints.json`。
- 预期：解析为 `LLMEndpoint`。
- 检查点：支持 `api_key` 明文和 `${ENV_VAR}` 占位；支持 keyed object 和顶层 `endpoints` 列表；支持 `default` 别名；支持 `priority`、`enabled`、`role`；缺失字段、非法 JSON、未知 endpoint、未定义环境变量都会给出配置错误。

### Case 6: dotenv 读取

- 输入：读取 UTF-8、UTF-8 BOM 或 UTF-16 的 `.env`。
- 预期：能加载合法 `KEY=VALUE`，不覆盖已有进程环境变量。
- 检查点：不支持的编码抛出 `DotenvConfigurationError`。

### Case 7: 初始化运行时文件

- 输入：调用 `init_runtime_files()`。
- 预期：生成本地 `config/llm_endpoints.json` 和 prompts，可选生成 `.env`。
- 检查点：默认不覆盖已有文件；`--force` 语义由调用方显式开启。
