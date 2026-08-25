# MCP配置隔离与XHS Cookie收口设计

## 背景

本地、Private和公共示例对齐后，XHS只读服务将两个可选空字段写成严格`${VAR}`引用。MCP加载器把所有此类引用视为必须非空，导致本地合法的空HTTP host allowlist首先触发`MCP_CONFIG_INVALID`；启动边界又把整个MCP server列表作为原子配置处理，使高德、Tavily和12306一并不可用。与此同时，XHS当前统一通过Cookie文件维持上游登录，`XHS_READONLY_UPSTREAM_AUTHORIZATION`及其请求头代码已经多余。`gateway --check`只检查Gateway基础配置，没有执行MCP配置检查，因此无法在启动前发现问题。

## 目标

- 删除XHS上游Authorization环境字段、配置引用和请求头逻辑，统一Cookie认证口径。
- 支持`${VAR:-}`表示可选的空字符串，使本地HTTP host allowlist可以为空，云端仍可注入容器DNS。
- MCP根结构错误继续整体失败；单个server配置错误只隔离该server，其余合法server继续注册。
- `gateway --check`检查MCP配置，完整或部分配置错误均返回失败。

## 范围边界

- 不改变MCP传输协议、运行时重连或工具调用逻辑。
- 不放宽普通`${VAR}`的必填语义，缺少模型、API key等必填引用仍明确失败。
- 不输出Secret、环境变量值或可能包含凭据的原始异常。
- 本地、Private和公共示例继续保持相同env字段全集与顺序。

## 模块设计

### 可选环境占位

MCP文本展开同时识别`${NAME}`和`${NAME:-}`。前者要求环境变量存在且非空；后者在变量缺失或为空时展开为空字符串。XHS的`XHS_READONLY_HTTP_HOST_ALLOWLIST`使用可选形式。URL、Cookie路径和API key等字段继续使用严格形式。

### Server级隔离

严格`load_mcp_server_specs`接口保持原行为，供显式配置校验和既有调用使用。新增隔离加载结果：先验证YAML、MCP根结构和server映射，再逐个解析server；合法spec进入运行目录，失败项只记录安全的server id，不记录异常正文。

若存在合法server与失败server，状态为`degraded/MCP_CONFIG_PARTIAL`；若所有已配置server都失败，状态为`unavailable/MCP_CONFIG_INVALID`；根结构错误仍为`unavailable/MCP_CONFIG_INVALID`。

### Gateway检查

`gateway --check`在基础目录检查后调用同一MCP启动检查。`available`和没有配置时通过；`degraded`或`unavailable`打印脱敏状态并返回非零，阻止错误配置进入正式启动。

### XHS Cookie收口

删除`.env`、YAML、README、部署烟测和XHS适配器中的`XHS_READONLY_UPSTREAM_AUTHORIZATION`。上游身份只来自持久化Cookie目录/文件；本地loopback无需HTTP allowlist，云端容器DNS继续显式allowlist。

## 数据流

1. dotenv加载本地或Private环境字段。
2. MCP加载器验证根结构并逐server解析。
3. 可选allowlist为空时展开为空字符串；XHS适配器对loopback使用内置允许列表。
4. 合法server启动，错误server进入安全降级状态。
5. `gateway --check`对任何server配置错误返回失败，正式Gateway运行时则保留其余合法MCP能力。

## 变更文件

- `agent/mcp/config.py`
- `agent/mcp/startup.py`
- `agent/cli.py`
- `integrations/xhs_readonly_mcp/server.py`
- `config/.env.example`
- `config/config.example.yml`
- `deploy/private/.env`
- `deploy/private/config.yml`
- `C:\Users\84953\.zhice\config\.env`
- `C:\Users\84953\.zhice\config\config.yml`
- `deploy/README.md`
- `tests/integration_test/travel/test_external_smoke.py`
- `tests/unit_test/mcp/test_mcp_config.py`
- `tests/unit_test/mcp/test_mcp_startup.py`
- `tests/unit_test/cli/test_cli_init.py`
- 对应`test_case.md`与配置/部署字段合同测试

## 测试方案

- 单元测试严格占位、可选空占位、根结构失败和单server隔离。
- CLI测试`gateway --check`对部分/全部MCP错误返回失败，对合法或未配置MCP返回成功。
- XHS测试确认Cookie模式不发送Authorization，loopback空allowlist可用，容器HTTP仍要求显式allowlist。
- 安全比较本地、Private和example字段名及顺序，不输出值。
- Ruff、后端全量pytest、前端lint/typecheck/test/build。
- 使用本地真实配置执行`gateway --check`和短时启动，确认MCP不再整体不可用。

## 验收标准

- 三套env均不存在`XHS_READONLY_UPSTREAM_AUTHORIZATION`且字段合同一致。
- 本地空allowlist合法；Private容器allowlist仍生效。
- 一个无效server不会移除其他合法MCP spec。
- `gateway --check`能提前发现部分或全部MCP配置错误。
- 本地真实配置检查通过，启动不再记录`mcp.runtime_unavailable/MCP_CONFIG_INVALID`。
