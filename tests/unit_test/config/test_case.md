# Config 单元测试用例

## 测试目标

验证双文件配置加载、运行时目录派生、dotenv读取、模型路由和`zcagent init`都遵守workspace边界和Secret管理约定。

Part 9 额外检查 `state/auth.sqlite3`、`contexts/users` 和 `contexts/shared/readonly` 都从 workspace 派生。

## 用例覆盖

### Case 1: 显式 workspace

- 输入：调用 `load_config(tmp_path)`。
- 预期：所有运行路径都从该 workspace 派生。
- 检查点：`config_dir`、`prompts_dir`、`contexts_dir`、`sessions_dir`、`extends_dir`、`logs_dir` 路径正确。

### Case 2: 环境变量覆盖

- 输入：设置 `ZHICE_AGENT_*` 路径变量后调用 `load_config()`。
- 预期：显式环境变量优先于默认派生路径。
- 检查点：workspace 和各运行目录被正确覆盖，sessions 仍从 contexts 派生。

### Case 3: 默认 workspace

- 输入：既不传入 workspace，也不设置 `ZHICE_AGENT_WORKSPACE`。
- 预期：使用 `Path.home() / ".zhice"`。
- 检查点：Windows、Linux 和 Docker 共用同一派生规则。

### Case 3.1: workspace runtime env

- 输入：`${workspace}/config/.env`包含普通变量和冲突的`ZHICE_AGENT_WORKSPACE`。
- 预期：加载普通变量，但不允许runtime env反向改变已解析workspace。
- 检查点：显式`--env-file`仍可作为一次性bootstrap覆盖。

### Case 4: 创建运行目录

- 输入：调用 `config.ensure_dirs()`。
- 预期：第一阶段需要的运行目录全部存在。
- 检查点：`config`、`prompts`、`contexts/sessions`、`extends`、`logs` 被创建。

### Case 5: Models端点与路由解析

- 输入：读取`${ZHICE_AGENT_WORKSPACE}/config/models.json`。
- 预期：解析为 `LLMEndpoint`。
- 检查点：Chat/Compaction/Embedding用途分离；支持`endpoint`和`endpoint/model`；显式模型必须命中`supported_models`；支持明文和`${ENV_VAR}`；价格非负；缺Secret、未知端点、非法预算明确失败；旧`llm_endpoints.json`存在也不读取。

### Case 6: dotenv 读取

- 输入：读取 UTF-8、UTF-8 BOM 或 UTF-16 的 `.env`。
- 预期：能加载合法 `KEY=VALUE`，不覆盖已有进程环境变量。
- 检查点：不支持的编码抛出 `DotenvConfigurationError`。

### Case 7: 初始化运行时文件

- 输入：调用 `init_runtime_files()`。
- 预期：默认生成`${ZHICE_AGENT_WORKSPACE}/config/.env`、`models.json`、`config.yml`和prompts；env逐字来自仓库`config/.env.example`。
- 检查点：公开env模板不含workspace key；已有文件默认保留，`force=True`统一刷新三个主模板；旧`create_env=False`不能关闭标准env初始化；模板缺失返回稳定错误；不会生成旧分散配置文件。

### Case 8: config.yml分区隔离

- 输入：分别加载Context、Skills、Subagents、Channels、Hooks和MCP分区。
- 预期：缺失分区使用安全默认或禁用可选能力；错误类型只使对应能力失败。
- 检查点：YAML根结构和`schema_version`统一校验；各模块继续严格校验本领域字段；不从旧文件懒读取。

### Case 9: Ops 终端公开投影配置

- 输入：读取`operations.terminal`中的`enabled/url/presentation`。
- 预期：缺省关闭；生产地址使用显式HTTPS；本机loopback可用HTTP调试。
- 检查点：启用时URL必填；拒绝credential、query、fragment、非本机HTTP和未知展示模式；配置不包含或推导宿主机权限。
