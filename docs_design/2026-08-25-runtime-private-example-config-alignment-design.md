# 本地、Private 与示例配置对齐设计

## 背景

本地运行态、云部署 private 和仓库示例配置在持续加入邮件、XHS、部署验收和模型字段后出现漂移：本地 `.env` 有 SMTP 与凭据加密主密钥但 private 缺失；private 有 XHS sidecar 与部署烟测字段但本地缺失；示例仍使用 OpenAI 模型字段并将部分正式能力保留为注释片段。这会导致本地验证通过的功能部署后缺配置，也让新环境无法从示例得到完整字段清单。

## 目标

- 本地 `.env`、`deploy/private/.env` 与 `config/.env.example` 使用相同字段全集和分组顺序。
- 本地专用、云端专用字段在不适用环境中保留空值并用中文说明，不复制错误的跨环境地址。
- XHS运行参数统一从 `.env` 注入，`config.yml` 不再硬编码本机路径。
- `config.example.yml` 与 `models.example.json` 覆盖当前正式运行结构和实际模型环境变量名。
- 自动测试阻止三套配置字段再次漂移，且不读取或输出真实值。

## 范围边界

- 修改本地工作区 `C:\Users\84953\.zhice\config` 的三份运行配置。
- 修改被 Git 忽略的 `deploy/private` 三份真实部署配置。
- 修改仓库 `config` 下的公共示例及配置测试、部署测试。
- 不提交真实 Secret，不把 Windows代理值或本机路径复制到云端。
- 微信渠道配置不在本次范围内。

## 模块设计

### 环境变量字段全集

字段按模型、初始化与验收、凭据加密、渠道、旅行数据源、XHS、地图前端、酒店、官方邮件、代理分组。三份 `.env` 字段名与顺序一致：真实环境保留各自已验证值，不适用字段为空；示例全部为空并提供中文用途和安全说明。

### XHS配置

本地已有的 loopback URL与本机 Cookie路径迁移到本地 `.env`。本地和private `config.yml`均通过相同的六个 `XHS_READONLY_*` 环境字段注入URL、HTTP allowlist、Cookie目录、Cookie文件、Authorization和timeout；云端继续使用容器 DNS和容器内路径，本地继续使用loopback和工作区路径。

### 示例配置

示例模型使用当前实际采用的 `ZHICE_LLM_DEEPSEEK_API_KEY`。示例 YAML启用并完整展示正式 MCP服务器、工作流 allowlist、官方邮件与当前子代理字段；Secret仅使用环境变量占位。

### 漂移防护

测试只解析字段名、引用名和结构，不读取真实值进入断言输出。校验 `.env.example` 覆盖本地/private字段全集，模型示例使用当前密钥名，XHS配置统一使用环境变量引用。

## 数据流

1. 开发者在本地 `.env` 填写本机值，本地 `config.yml` 解析引用。
2. 发布前将已验证的跨环境 Secret明确同步到private；云端专用拓扑保留private值。
3. 镜像构建复制private三份配置，部署同步到服务器runtime。
4. 新环境从仓库example复制，字段完整但不包含任何真实值。

## 变更文件

- `C:\Users\84953\.zhice\config\.env`
- `C:\Users\84953\.zhice\config\config.yml`
- `C:\Users\84953\.zhice\config\models.json`
- `deploy/private/.env`
- `deploy/private/config.yml`
- `deploy/private/models.json`
- `config/.env.example`
- `config/config.example.yml`
- `config/models.example.json`
- `tests/unit_test/config/test_config.py`
- `tests/unit_test/config/test_case.md`
- `tests/unit_test/deploy/test_deploy_assets.py`
- `tests/unit_test/deploy/test_case.md`

## 测试方案

- 解析三套 env字段名并比较全集和顺序，不输出值。
- 加载三套 YAML/JSON，校验 schema、XHS引用、模型密钥引用与正式能力结构。
- 执行配置、部署专项测试以及后端全量测试。
- 执行 Ruff、前端 lint、typecheck、测试与生产 build，确认配置变化没有破坏发布链路。

## 验收标准

- 本地、private、example `.env` 字段名和顺序一致。
- private包含 SMTP与凭据加密主密钥；本地包含 XHS与部署烟测字段。
- 本地/private XHS值分别适配本机和容器环境，YAML形式一致。
- example使用中文说明、无真实 Secret，且不再引用未使用的 OpenAI密钥名。
- 所有规定测试和构建通过。
