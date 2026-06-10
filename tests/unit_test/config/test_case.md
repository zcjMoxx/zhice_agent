# Config 单元测试用例

## 测试目标

验证第一阶段配置加载能从 workspace 和环境变量稳定派生运行目录。

## 测试用例

### Case 1: 显式 workspace

- **输入**: `load_config(tmp_path)`
- **预期**: 所有目录从该 workspace 派生
- **检查点**:
  - `config_dir` 指向 `config`
  - `prompts_dir` 指向 `prompts`
  - `sessions_dir` 指向 `contexts/sessions`

### Case 2: 环境变量覆盖

- **输入**: 设置 `ZHICE_AGENT_*` 路径变量后调用 `load_config()`
- **预期**: 显式环境变量优先
- **检查点**:
  - workspace 被覆盖
  - 各目录被覆盖
  - sessions 仍从 contexts 派生

### Case 3: 创建运行目录

- **输入**: 调用 `config.ensure_dirs()`
- **预期**: 第一阶段需要的目录都存在
- **检查点**:
  - `config`
  - `prompts`
  - `contexts/sessions`
  - `skills`
  - `logs`
