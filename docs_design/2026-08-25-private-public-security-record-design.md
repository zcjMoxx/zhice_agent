# 公安备案信息 Private Runtime 配置设计

## 背景

当前认证页把真实公安备案编号、展示文案和查询链接直接写入 Vue 源码、测试、设计记录和生产 source map。即使图片被 Git ignore，真实编号仍会进入 GitHub 历史和通用构建产物，其他人 clone 后也会默认展示同一备案信息。备案编号在正式网站上依法公开，但不应成为仓库默认内容或被第三方部署自动复用。

## 目标

- 仓库只保留通用备案展示能力和公共官方图标，不包含真实备案编号、真实展示文案或真实查询 URL。
- 真实备案信息只存在于本地 workspace runtime 与 `deploy/private/config.yml`。
- 前端运行时从匿名只读接口加载备案信息，通用静态构建不嵌入真实值。
- 备案只在配置允许的请求域名上返回；clone 后默认关闭、不展示。
- 标准云部署继续通过现有 private 三文件同步、备份、校验、回滚和烟测链路发布，不新增人工同步脚本。

## 范围边界

- 公安备案图标是公共官方素材，可作为通用 UI 资产进入仓库。
- 域名限制用于避免错误部署和默认复用，不构成对公开备案号的保密机制；恶意第三方仍可从正式网页人工抄写。
- 只公开备案展示所需的 `label` 与由 `code` 派生的官方查询 URL，不公开 private 配置正文、允许域名列表或其他运行配置。
- 不改变认证、注册、Cookie 或 RBAC 行为。

## 模块设计

### Runtime 配置

在 `config.yml` 增加：

```yaml
site:
  public_security_record:
    enabled: false
    code: 请填写公安备案编号
    label: 请填写公安备案展示文案
    allowed_hosts:
      - 请填写允许展示的域名
```

- `enabled=false` 时允许示例占位并完全不展示。
- `enabled=true` 时要求14位数字编号、包含编号的非空文案，以及至少一个合法 host。
- 官方查询 URL 由服务端固定域名和编号派生，private 不接受任意跳转 URL。

### 服务端

- 新增独立 site config loader，根配置缺失时默认关闭，结构非法时启动/check失败。
- Gateway 启动时只加载一次配置，并保存在 app state。
- 新增匿名 `GET /api/site`，按 `request.url.hostname` 与 `allowed_hosts` 精确匹配。
- host 不匹配或功能关闭时返回 `public_security_record: null`，不泄露真实值或 allowlist。

### 前端

- Auth store 启动时读取 `/api/site`。
- 只有接口返回备案对象时才渲染一体化备案页脚。
- 备案链接和文案全部来自响应；官方通用图标继续使用仓库静态资源。
- 页脚未启用时认证卡片保持居中，不预留空白区域。

### 部署

- 本地真实值写入 `${ZHICE_AGENT_WORKSPACE}/config/config.yml`，允许 `localhost` 与 `127.0.0.1`。
- 云端真实值写入 `deploy/private/config.yml`，只允许正式站点 host。
- `config/config.example.yml` 默认关闭并使用中文占位。
- 部署烟测通过正式公网 URL 请求 `/api/site`，要求备案对象存在、编号格式有效、label包含编号、URL指向公安备案官方域名；失败视为核心发布失败并触发既有回滚。

## 数据流

```text
private config.yml
  -> 镜像内 private runtime
  -> 服务器原子同步 /etc/zhice-agent/runtime/config.yml
  -> Gateway 加载与校验
  -> GET /api/site + Host匹配
  -> AuthLayout 条件渲染备案页脚
```

## 变更文件

- `agent/app/site_config.py`
- `agent/app/gateway.py`
- `agent/app/api/routes.py`
- `agent/app/api/schemas.py`
- `agent/cli.py`
- `config/config.example.yml`
- `${ZHICE_AGENT_WORKSPACE}/config/config.yml`
- `deploy/private/config.yml`
- `web/frontend/src/api/client.ts`
- `web/frontend/src/stores/auth.ts`
- `web/frontend/src/layouts/AuthLayout.vue`
- 对应 App、CLI、配置、前端和部署烟测测试及测试说明
- `deploy/scripts/deployment_smoke.py`
- 受影响的设计记录与生产静态构建产物

## 测试方案

- Site config loader覆盖缺失/关闭、合法启用、非法编号、非法label、空/非法host。
- API覆盖允许host返回、错误host隐藏、关闭状态隐藏和不需要登录。
- CLI `gateway --check`覆盖非法site配置失败。
- 前端覆盖接口成功展示、关闭/失败隐藏、链接与文案来自响应。
- 部署烟测覆盖合法备案响应通过、缺失或异常响应导致核心失败。
- 搜索仓库源码、测试、设计文档和生产资产，确认不再包含真实编号或真实备案文案。
- 运行后端Ruff/pytest、前端lint/typecheck/test/build，并用真实手机和窄桌面viewport验收。

## 验收标准

- `git grep`和未忽略生产静态产物不包含真实备案编号或真实展示文案。
- 本地允许host页面仍展示真实备案信息，错误host页面不展示。
- 云端private仍处于Git ignore，正式部署烟测校验备案展示并保留既有失败回滚。
- 通用仓库在无private配置时能正常构建运行，认证页不出现空备案区域。
